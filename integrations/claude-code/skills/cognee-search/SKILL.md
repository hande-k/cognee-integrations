---
name: cognee-search
description: Discover and search connected-source memory through Cognee using metadata routing, dataset and node-set filters, citations and paginated document browsing.
---

# Cognee Memory Search

Use the installed plugin for memory reads. Its commands use the same identity
resolver as capture hooks. Never retry denied access with an owner credential,
a direct database read, or another memory store.

## Discover and select

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/cognee-memory.py" status
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/cognee-memory.py" sources --limit 50
# Continue with --offset <next_offset> while next_offset is present.
```

The catalog contains permission-filtered datasets and node sets with stable IDs,
names, source aliases, short descriptions, sample document labels and available
operations. It is derived from current Cognee metadata; there is no provider list
in the plugin. A new connector's imported node sets are discoverable without a
plugin update. Authorized native SQL connections are also discoverable when tool calls
are enabled, even without ingested documents. Other empty connectors are not entries.

Session capture keeps one write dataset. To select several readable graph datasets:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/memory-access.py" read --persist --dataset-id <uuid> --dataset-id <uuid>
```

This selects reads, never grants access or changes writes. Include the session
dataset UUID to retain durable session learnings in graph recall. Persistent
selection is bound to this identity and backend; launch-specific selection wins.
Without a read selection, explicit source searches discover across all readable
datasets. Capture hooks keep their separate session/write scope.
`--all-readable` explicitly expands discovery beyond the saved read selection;
explicit `--dataset-id` values always take precedence. ACLs still apply.

## Route questions and retrieve evidence

```bash
"${CLAUDE_PLUGIN_ROOT}/scripts/cognee-search.sh" "What did we decide about deployment?" 10 --all-readable
"${CLAUDE_PLUGIN_ROOT}/scripts/cognee-search.sh" "What was discussed?" 10 --source "demo channel"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/cognee-memory.py" route "deployment decisions" --source "meeting notes"
```

`--source` accepts natural language, not an enum. Cognee's configured LLM selects
likely targets from authorized metadata, then the SDK uses their declared retrieval
capabilities: native CHUNKS for documents or read-only SQL for database tools.
Routing itself does not read source bodies or open external databases.
The server validates model-selected IDs; model output cannot invent permissions
or arbitrary tool calls. `route` previews targets and reasons without content search.

Routing considers up to 512 catalog entries by default. `--catalog-budget 2048`
raises that explicit budget. Descriptors are processed in batches of at most 64,
with at most three LLM calls concurrently per routing request, followed by joint
re-ranking. This adds LLM cost and latency. At most six targets are searched by
default (`--max-sources`, maximum eight). Catalog limits and uncertain routing are
reported; an empty route is NOT evidence that the answer does not exist.

If no chunks are returned but a selected target has stored documents, browse that
target and read relevant originals first. A stored document can be available before
semantic indexing is ready. Respect response budgets and avoid dumping large
documents into context.

If evidence is insufficient, inspect the routing and catalog, refine the question
and make one further search excluding already searched target IDs with repeated
`--exclude-source-id <uuid>`. Ask for clarification if ambiguity remains. Do not
silently claim that every dataset was searched. Source selection is heuristic.

For known targets, bypass LLM routing:

```bash
"${CLAUDE_PLUGIN_ROOT}/scripts/cognee-search.sh" "deployment" 10 --dataset-id <uuid> --node-set <name>
"${CLAUDE_PLUGIN_ROOT}/scripts/cognee-search.sh" "deployment" 10 --dataset-id <uuid> --exact
```

Repeated node sets use AND by default; `--node-match any` uses OR. Exact search
requires selected dataset UUIDs and cannot be combined with a source hint.
Session-only recall uses `--session`; structural code search uses `--code` and
the codebase skill. Neither uses source routing.

## Browse and read original evidence

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/cognee-memory.py" browse <source-id> --limit 100
# Continue the same source with --cursor <next_cursor> until null.
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/cognee-memory.py" read <document-id> --dataset-id <dataset-id>
```

Browse pages contain stored document metadata, not individual provider messages.
Read fetches the original stored text and its provenance. Pagination uses native
record UUIDs, rechecks permissions each page and does not promise snapshot
isolation during concurrent imports/deletions. Source event timestamps must be
interpreted from the document; an ingestion/update timestamp is not an event date
or a synchronization time. No provider-specific date-window parser is provided.

Report evidence links, the actual searched targets, and coverage limitations.
Ranked chunks are not an exhaustive export. Imported records do not establish
that a source is fully synchronized. Unknown synchronization time stays unknown.
Source search can use stored memory and the existing authorized Cognee tool
connections. An imported database schema is metadata, not live business rows.
Do not silently ingest rows or fetch from a connector to fill missing evidence.

## Compatibility and failures

Source discovery requires the SDK's `/api/v1/datasets/source-catalog`,
`source-route`, `source-search`, `source-documents` and `source-document` routes, plus HTTP node-set
operator forwarding. Missing routes are reported as a server capability error.
Each response is limited to 16 MiB and each command to 64 MiB. Budget errors are
failures, not successful complete results.

`status` reports whether the current credential is an agent or a legacy user
principal. Searches never provision identities, rotate credentials or change ACLs.
For permissions, use the manage-access skill explicitly.


### Connected sources and database questions

For an explicit question about connected sources, use `cognee-memory.py search "question"`
with an optional `--source "name or description"`. The server discovers authorized
sources from metadata and chooses retrieval from their capabilities. A live database
uses Cognee's native read-only SQL tool; do not substitute CHUNKS or assume a schema
catalog contains business rows. SQL evidence includes the connection, generated SQL,
rows, truncation status and query time. Document evidence carries document/dataset IDs.

Use `--documents-only` to exclude database execution. A selected dataset scope excludes
separately permissioned database connections; `--all-readable` explicitly searches the
caller's full authorized catalog. These options do not change capture, identities or
permissions. Route results list capabilities. Never use browse/read on a tool connection
that lacks document capabilities. Partial failures and routing misses are not proof of
absence. Older servers must be upgraded; do not fall back to owner credentials or scripts.
