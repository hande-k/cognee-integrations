#!/usr/bin/env python3
"""Discover sources, route questions, and read Cognee memory using the plugin identity.

Source names and targets come from the server catalog. No provider names,
connector calls, credentials, or source-format parsers belong in this client.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from urllib.parse import urlencode, urlparse

import _plugin_common as pc
from _command_runtime import resolve
from _dataset_access import recall_fields
from _source_records import MemoryError, document_ref, metadata, uuid

MAX_RESPONSE = 16 * 1024 * 1024


class CanonicalRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        before, after = urlparse(req.full_url), urlparse(newurl)
        if (
            code not in (307, 308)
            or (before.scheme, before.netloc, before.query)
            != (after.scheme, after.netloc, after.query)
            or before.path.rstrip("/") != after.path.rstrip("/")
        ):
            raise MemoryError("Unexpected API redirect; check the configured Cognee endpoint.")
        return urllib.request.Request(
            newurl, data=req.data, headers=dict(req.headers), method=req.get_method()
        )


class Client:
    def __init__(self, runtime):
        self.runtime = runtime
        self.bytes_read = 0
        self.opener = urllib.request.build_opener(
            CanonicalRedirect(), urllib.request.HTTPSHandler(context=pc._https_context())
        )

    def request(self, path, payload=None, *, raw=False):
        headers = {"X-Api-Key": self.runtime["api_key"], "Content-Type": "application/json"}
        req = urllib.request.Request(
            self.runtime["service_url"].rstrip("/") + path,
            data=json.dumps(payload).encode() if payload is not None else None,
            headers=headers,
        )
        timeout = 180 if path == "/api/v1/datasets/source-route" else 45
        with self.opener.open(req, timeout=timeout) as response:
            body = response.read(MAX_RESPONSE + 1)
        if len(body) > MAX_RESPONSE:
            raise MemoryError("Response exceeds 16 MiB; narrow the dataset selection.")
        self.bytes_read += len(body)
        if self.bytes_read > 64 * 1024 * 1024:
            raise MemoryError("Read budget exceeds 64 MiB; narrow the dataset selection.")
        return body.decode("utf-8") if raw else json.loads(body)


def selection(args, runtime):
    if args.dataset_id:
        return [uuid(value) for value in args.dataset_id]
    if args.dataset:
        raise MemoryError("Use --dataset-id for source search; names can collide across owners.")
    fields, _ = recall_fields(runtime["dataset"], ["graph"])
    return fields.get("dataset_ids")


def readable_selection(client, args, runtime):
    if getattr(args, "all_readable", False) and not (args.dataset_id or args.dataset):
        return None
    ids = selection(args, runtime)
    if ids is not None:
        return ids
    # A named source is an explicit request to discover that source among all
    # readable datasets. General questions respect the saved graph read selection.
    if getattr(args, "source", None) or getattr(args, "all_readable", False):
        return None
    rows = client.request("/api/v1/datasets/")
    matches = [r["id"] for r in rows if r.get("name") == runtime["dataset"]]
    if len(matches) != 1:
        raise MemoryError("Select graph read dataset UUIDs with memory-access.py first.")
    return matches


def discover(client, ids=None, offset=0, limit=50):
    params = [("dataset_ids", ident) for ident in ids or []]
    params += [("offset", offset), ("limit", limit)]
    return client.request("/api/v1/datasets/source-catalog?" + urlencode(params))


def route(client, args, runtime):
    return client.request(
        "/api/v1/datasets/source-route",
        {
            "query": args.query,
            "source_hint": args.source,
            "dataset_ids": readable_selection(client, args, runtime),
            "max_sources": args.max_sources,
            "max_catalog_entries": args.catalog_budget,
            "exclude_source_ids": args.exclude_source_id,
        },
    )


def source_details(client, dataset, document):
    return client.request(f"/api/v1/datasets/source-document/{uuid(dataset)}/{uuid(document)}")


def search(client, args, runtime):
    if args.session or args.code:
        if len(args.dataset_id or args.dataset) > 1:
            raise MemoryError("Session and code searches accept one dataset per call.")
        if args.source or args.node_set or args.all_readable:
            raise MemoryError("Source selection and node-set filters require graph search.")
        from _cognee_client import recall

        dataset = (args.dataset_id or args.dataset or [runtime["dataset"]])[0]
        if args.code and not (args.dataset_id or args.dataset):
            from _code_graph import find_indexed_repo

            dataset = find_indexed_repo(os.getcwd()).get("dataset") or dataset
        return recall(
            runtime["service_url"],
            runtime["api_key"],
            args.query,
            runtime["session_id"] if args.session else "",
            json.dumps(["session"] if args.session else ["code"]),
            args.top_k,
            dataset,
            args.code_query or "",
        )
    if args.source and (args.node_set or args.exact):
        raise MemoryError(
            "Use a source hint OR exact targets; inspect sources to refine targets."
        )
    if args.node_set or args.exact:
        ids = readable_selection(client, args, runtime)
        if not ids:
            raise MemoryError("Exact search requires selected dataset UUIDs.")
        targets = [
            {
                "dataset_id": ident,
                "node_sets": args.node_set,
                "name": "explicit selection",
                "reason": "caller-selected",
            }
            for ident in ids
        ]
        routing = {"targets": targets, "status": "explicit", "complete": True}
    else:
        routing = route(client, args, runtime)
        targets = routing.get("targets", [])
        if not targets:
            return {
                "mode": "search",
                "routing": routing,
                "evidence": [],
                "coverage": {"complete": False, "searched_targets": []},
                "next_step": "Inspect sources, narrow the question, or raise the catalog budget. "
                "No content search was performed; this is not a no-results answer.",
            }
    evidence, seen, rejected, metadata_cache = [], set(), 0, {}
    for target in targets:
        nodes = target["node_sets"]
        operator = "OR" if args.node_match == "any" else "AND"
        result = client.request(
            "/api/v1/search",
            {
                "query": args.query,
                "search_type": "CHUNKS",
                "top_k": args.top_k,
                "dataset_ids": [target["dataset_id"]],
                "node_name": nodes or None,
                "node_name_filter_operator": operator,
                "only_context": True,
                "verbose": True,
            },
        )
        if not isinstance(result, list):
            raise MemoryError("Unsupported CHUNKS response from the server.")
        for item in result:
            for hit in item.get("objects_result") or []:
                payload = hit.get("payload") or {}
                document = payload.get("document_id")
                if not document:
                    rejected += 1
                    continue
                key = (target["dataset_id"], document)
                if key not in metadata_cache:
                    metadata_cache[key] = source_details(client, *key)
                row = metadata_cache[key]
                actual = set(row.get("node_sets") or [])
                matches = not nodes or (
                    bool(actual.intersection(nodes))
                    if operator == "OR"
                    else set(nodes).issubset(actual)
                )
                if not matches:
                    raise MemoryError("Returned evidence violates the requested node-set filter.")
                unique = (*key, payload.get("text"))
                if unique not in seen:
                    evidence.append(
                        {
                            **document_ref(row),
                            "text": payload.get("text"),
                            "score": hit.get("score"),
                            "source_target": target["name"],
                        }
                    )
                    seen.add(unique)
    return {
        "mode": "search",
        "source": "stored_cognee_memory",
        "routing": routing,
        "evidence": evidence,
        "coverage": {
            "complete": False,
            "kind": "ranked_chunks",
            "top_k_per_target": args.top_k,
            "searched_targets": targets,
            "rejected_unverifiable_hits": rejected,
            "last_source_sync": None,
            "note": "Routing selects likely sources; unselected sources were not searched.",
        },
    }


def parser():
    root = argparse.ArgumentParser(description=__doc__)
    sub = root.add_subparsers(dest="command", required=True)
    for command in ("sources", "route", "search", "browse", "read", "status"):
        p = sub.add_parser(command)
        p.add_argument("--dataset-id", action="append", default=[])
        p.add_argument("--dataset", "-d", action="append", default=[])
        if command in ("route", "search"):
            p.add_argument("query")
            p.add_argument("--source", help="Natural-language source hint resolved from metadata")
            p.add_argument("--all-readable", action="store_true")
            p.add_argument("--exclude-source-id", action="append", type=uuid, default=[])
            p.add_argument("--max-sources", type=int, default=6)
            p.add_argument("--catalog-budget", type=int, default=512)
        if command == "search":
            p.add_argument("top_k", nargs="?", type=int, default=10)
            mode = p.add_mutually_exclusive_group()
            for flag in ("session", "graph", "code"):
                mode.add_argument("--" + flag, action="store_true")
            p.add_argument("--code-query")
            p.add_argument("--node-set", action="append", default=[])
            p.add_argument("--node-match", choices=["any", "all"], default="all")
            p.add_argument(
                "--exact",
                "--chunks",
                action="store_true",
                help="Search selected targets directly, without LLM routing",
            )
        if command in ("sources", "browse"):
            p.add_argument("--limit", type=int, default=50)
        if command == "sources":
            p.add_argument("--offset", type=int, default=0)
        if command == "browse":
            p.add_argument("source_id", type=uuid)
            p.add_argument("--cursor", type=uuid)
        if command == "read":
            p.add_argument("document_id", type=uuid)
    return root


def run(args):
    runtime = resolve()
    client = Client(runtime)
    if args.command == "status":
        me = client.request("/api/v1/users/me")
        return {
            "identity": me.get("id"),
            "identity_kind": "agent"
            if me.get("parent_user_id") or me.get("parentUserId")
            else "user",
            "credential_source": runtime["credential_source"],
            "write_dataset": runtime["dataset"],
            "graph_read_dataset_ids": pc.load_graph_read_scope(),
            "server": runtime["service_url"],
        }
    if args.command == "route":
        return route(client, args, runtime)
    if args.command == "search":
        if not 1 <= args.top_k <= 100:
            raise MemoryError("top_k must be between 1 and 100.")
        return search(client, args, runtime)
    if args.command == "sources":
        return discover(client, args.dataset_id, args.offset, args.limit)
    if args.command == "browse":
        params = {"limit": args.limit}
        if args.cursor:
            params["after"] = args.cursor
        return client.request(
            f"/api/v1/datasets/source-documents/{args.source_id}?" + urlencode(params)
        )
    if len(args.dataset_id) != 1:
        raise MemoryError("Read requires exactly one --dataset-id.")
    dataset = uuid(args.dataset_id[0])
    row = source_details(client, dataset, args.document_id)
    text = client.request(f"/api/v1/datasets/{dataset}/data/{args.document_id}/raw", raw=True)
    return {**document_ref(row), "metadata": metadata(row), "text": text}


def main(argv=None):
    try:
        result = run(parser().parse_args(argv))
        if result == "UNREACHABLE" or (isinstance(result, dict) and result.get("error")):
            print(json.dumps(result))
            return 1
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except urllib.error.HTTPError as error:
        detail = "Permission denied" if error.code in (401, 403) else "Cognee request failed"
        if error.code == 404:
            detail = "Source API or record unavailable; check server support and selected IDs."
        print(
            json.dumps(
                {
                    "error": detail,
                    "http_status": error.code,
                    "complete": False,
                    "credential_fallback": False,
                }
            ),
            file=sys.stderr,
        )
    except (ValueError, RuntimeError, OSError) as error:
        detail = str(error) if isinstance(error, MemoryError) else type(error).__name__
        print(json.dumps({"error": detail, "complete": False}), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
