#!/usr/bin/env python3
"""Build a memory anchor before context-window compaction.

Runs on the PreCompact hook. Pulls a compact summary from three
session-cache layers — recent QAs, per-step trace feedback, and the
graph-context snapshot — and emits a markdown block the compactor
preserves.

Everything goes through the Cognee server over HTTP (``/api/v1/recall`` and
``GET /api/v1/sessions/{id}``), so the anchor works the same whether the
plugin booted a local server or is connected to a remote one.
"""

import asyncio
import json
import os
import re
import sys

# Add scripts dir to path for helper imports
sys.path.insert(0, os.path.dirname(__file__))
from _plugin_common import (
    get_session_detail_via_http,
    hook_log,
    load_resolved,
    recall_via_http,
    resolve_runtime_mode,
    resolve_session_key_from_payload,
    server_usable,
    set_session_key,
)
from config import get_dataset, get_session_id, load_config

_MIN_WORD_LEN = 3
_SESSION_TOP_K = 5
_TRACE_TOP_K = 8
_GRAPH_TOP_K = 3


def _load_resolved_fields() -> tuple[str, str]:
    """Return (session_id, dataset) from resolved cache or config."""
    resolved = load_resolved()
    session_id = resolved.get("session_id", "")
    dataset = resolved.get("dataset", "")
    if not session_id or not dataset:
        config = load_config()
        session_id = session_id or get_session_id(config)
        dataset = dataset or get_dataset(config)
    return session_id, dataset


def _extract_query_words(entries: list, max_words: int = 20) -> str:
    """Pull keyword-dense query from recent entries for graph-context search."""
    words: list[str] = []
    for entry in entries[-3:]:
        if not isinstance(entry, dict):
            continue
        blob = " ".join(
            str(entry.get(f, ""))
            for f in ("question", "answer", "origin_function", "session_feedback")
        )
        for w in re.findall(r"\b\w+\b", blob.lower()):
            if len(w) >= _MIN_WORD_LEN:
                words.append(w)
                if len(words) >= max_words:
                    return " ".join(words)
    return " ".join(words)


def _recall(session_id: str, dataset: str, query: str, scope: list[str], top_k: int) -> list:
    """Recall for the anchor over HTTP; tolerates empty/failed recalls.

    The session cache lives on the server, so this is the only place the
    entries can come from. Failures are logged, never raised: a compaction is
    not something the user triggered, and the hook must not disturb it.
    """
    try:
        # GRAPH_COMPLETION only for the graph scope; the session/trace scopes
        # read the cache and must not force a graph query.
        query_type = "GRAPH_COMPLETION" if "graph" in scope else None
        results = recall_via_http(
            query,
            session_id=session_id,
            top_k=top_k,
            scope=scope,
            only_context=True,
            search_type=query_type,
            dataset=dataset,
        )
        return [r for r in (results or []) if isinstance(r, dict)]
    except Exception as exc:
        hook_log("precompact_recall_error", {"scope": scope, "error": str(exc)[:200]})
        return []


def _recent_entries(session_id: str) -> tuple[list, list]:
    """Return (recent QA entries, recent trace entries) straight from the server.

    The seed recall passes an empty query (there is no user question at compact
    time) and ``/recall`` matches nothing on an empty string, so the session
    detail endpoint — which returns the last ~20 QA and trace rows without a
    query — is what actually produces the anchor mid-session.
    """
    detail = get_session_detail_via_http(session_id)
    if not isinstance(detail, dict):
        return [], []
    qas = [r for r in (detail.get("qas") or []) if isinstance(r, dict)]
    traces = [r for r in (detail.get("traces") or []) if isinstance(r, dict)]
    return qas[-_SESSION_TOP_K:], traces[-_TRACE_TOP_K:]


def _format_session_section(entries: list) -> str:
    lines = ["### Session Memory (recent turns)"]
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        q = str(entry.get("question") or "").strip()
        a = str(entry.get("answer") or "").strip()
        if not (q or a):
            continue
        short = (q or a)[:300]
        if len(q or a) > 300:
            short += "..."
        prefix = "Q: " if q else "A: "
        lines.append(f"- {prefix}{short}")
    return "\n".join(lines) if len(lines) > 1 else ""


def _format_trace_section(entries: list) -> str:
    lines = ["### Agent Trace (tool calls & feedback)"]
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        origin = entry.get("origin_function", "?")
        status = entry.get("status", "")
        feedback = str(entry.get("session_feedback") or "").strip()
        if feedback:
            lines.append(f"- {origin} [{status}]: {feedback[:200]}")
        else:
            lines.append(f"- {origin} [{status}]")
    return "\n".join(lines) if len(lines) > 1 else ""


def _format_graph_context_section(entries: list) -> str:
    lines = ["### Knowledge Graph Snapshot"]
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        content = str(entry.get("content") or entry.get("answer") or entry.get("text") or "")
        short = content[:400] + "..." if len(content) > 400 else content
        if short.strip():
            lines.append(short)
    return "\n".join(lines) if len(lines) > 1 else ""


def _format_graph_section(entries: list) -> str:
    lines = ["### Knowledge Graph (search hits)"]
    for entry in entries:
        if not isinstance(entry, dict):
            lines.append(f"- {str(entry)[:300]}")
            continue
        text = entry.get("answer") or entry.get("text") or entry.get("content") or str(entry)
        short = (text[:300] + "...") if len(text) > 300 else text
        lines.append(f"- {short}")
    return "\n".join(lines) if len(lines) > 1 else ""


async def _run():
    session_id, dataset = _load_resolved_fields()
    if not session_id:
        hook_log("no_session_id", {"event": "precompact"})
        return

    # Pin the endpoint the same way every other hook does (URL + optional key
    # into the environment), then bail early on a server already known to be
    # down rather than paying three recall timeouts for nothing.
    runtime = resolve_runtime_mode()
    service_url = runtime.get("base_url", "")
    if not server_usable(service_url):
        hook_log("precompact_server_unusable", {"base_url": service_url})
        return

    # Seed: the session's recent activity, since there is no user question at
    # compact time. Try recall first, then the session detail endpoint, which
    # returns the recent rows without needing a query.
    seed_results = _recall(
        session_id, dataset, query="", scope=["session", "trace"], top_k=_TRACE_TOP_K
    )
    session_entries = [r for r in seed_results if r.get("source") == "session"]
    trace_entries = [r for r in seed_results if r.get("source") == "trace"]
    if not session_entries and not trace_entries:
        session_entries, trace_entries = _recent_entries(session_id)

    session_entries = session_entries[-_SESSION_TOP_K:]
    trace_entries = trace_entries[-_TRACE_TOP_K:]

    query = _extract_query_words(session_entries + trace_entries)

    graph_context_entries: list = []
    graph_entries: list = []
    if query:
        graph_context_entries = _recall(
            session_id, dataset, query=query, scope=["graph_context"], top_k=1
        )
        graph_entries = _recall(
            session_id, dataset, query=query, scope=["graph"], top_k=_GRAPH_TOP_K
        )

    sections = []
    if session_entries:
        s = _format_session_section(session_entries)
        if s:
            sections.append(s)
    if trace_entries:
        s = _format_trace_section(trace_entries)
        if s:
            sections.append(s)
    if graph_context_entries:
        s = _format_graph_context_section(graph_context_entries)
        if s:
            sections.append(s)
    if graph_entries:
        s = _format_graph_section(graph_entries)
        if s:
            sections.append(s)

    if not sections:
        hook_log("precompact_empty")
        return

    header = (
        "## Cognee Memory Anchor\n"
        "Preserved context from session, agent trace, and knowledge graph:\n"
    )
    anchor = header + "\n\n".join(sections)

    hook_log(
        "precompact_anchor",
        {
            "session_entries": len(session_entries),
            "trace_entries": len(trace_entries),
            "graph_context": len(graph_context_entries),
            "graph": len(graph_entries),
        },
    )
    print(anchor)


def main():
    # Read the PreCompact payload to recover the host session id, which lets the
    # session resolver map back to this launch's Cognee session id (the body is
    # otherwise unused — PreCompact is just a trigger).
    payload_raw = sys.stdin.read()
    try:
        payload = json.loads(payload_raw) if payload_raw.strip() else {}
    except json.JSONDecodeError:
        payload = {}
    session_key_candidate, _ = resolve_session_key_from_payload(payload)
    if session_key_candidate:
        set_session_key(session_key_candidate)

    try:
        asyncio.run(_run())
    except Exception as exc:
        hook_log("precompact_run_exception", {"error": str(exc)[:200]})


if __name__ == "__main__":
    main()
