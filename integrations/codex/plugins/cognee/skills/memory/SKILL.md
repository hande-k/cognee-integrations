---
name: memory
description: Use when Codex should remember, recall, search, improve, or forget information using Cognee.
---

# Cognee Memory

Use this skill when the user asks Codex to use Cognee as memory, add facts or
documents, search a knowledge graph, recall prior context, or improve existing
memory.

## Rules

- Prefer the server-first paths below (HTTP to the running Cognee server).
- Use `uv run cognee-cli ...` only when the server is genuinely unreachable.
- Choose a clear dataset name with `-d` or `--dataset-name`; ask only if the dataset boundary is genuinely ambiguous.
- Do not ingest secrets, credentials, `.env` files, private keys, token dumps, or unrelated generated artifacts.
- Before destructive commands such as `forget`, `delete`, or `--everything`, get explicit user confirmation.

## Add And Build

**Server-first (one-step ingestion):**

```bash
${CODEX_PLUGIN_ROOT}/scripts/cognee-remember.sh "<text>" --node-set user_context
```

Use `--node-set project_docs` for project/code content, `--node-set agent_actions` for agent notes.

To store a **file** under its real filename (so code files ride the zero-LLM code path instead of being ingested as prose), pass `--file`:

```bash
${CODEX_PLUGIN_ROOT}/scripts/cognee-remember.sh --file src/payments.py --node-set project_docs
```

For a whole repository (cross-file calls/imports, impact analysis), use the **codebase** skill instead. The script POSTs directly to `/api/v1/remember`. A `{"ok": true}` response means the server accepted the data. An error response means the server rejected or failed the request — check `COGNEE_API_KEY` and server logs; do **not** re-run or conclude the data wasn't stored without confirming against the server.

**Background by default + eventual consistency**: the wrapper submits with `run_in_background=true` (so a large cognify never holds one request open past the cloud's ~10-min request ceiling). The POST returns once the work is **enqueued**, with `dataset_id` and `pipeline_run_id`; `status: "running"` means *submitted, not yet in the permanent graph*. The session cache is searchable immediately, but the graph is queryable only after the cognify pipeline **completes**.

By default the wrapper then waits a short, bounded time (`COGNEE_REMEMBER_WAIT_SECONDS`, default `8`) polling `/api/v1/datasets/status` and adds `"queryable": true|false` + `"wait_outcome"` to the result. `queryable: true` means it's now in the graph and an immediate recall will find it. If `queryable: false`, check `wait_outcome`: `"timeout"` means it's still processing (recall later — not an error), `"errored"` means the cognify failed (check server logs), `"unknown"` means completion couldn't be confirmed (e.g. an older server without the status route). Set `COGNEE_REMEMBER_WAIT_SECONDS=0` to skip the wait, or `COGNEE_REMEMBER_BACKGROUND=false` for a fully synchronous, immediately-queryable write (small content only — large content risks the request ceiling).

**Fallback only — server unreachable:**

```bash
uv run cognee-cli remember <text-or-path> -d <dataset-name>
```

For staged work (no HTTP equivalent — CLI only):

```bash
uv run cognee-cli add <text-or-path> -d <dataset-name>
uv run cognee-cli cognify -d <dataset-name>
```

For long processing:

```bash
uv run cognee-cli remember <text-or-path> -d <dataset-name> --background
uv run cognee-cli cognify -d <dataset-name> --background
```

## Recall And Search

Use the installed plugin for memory reads. Its commands use the same identity
resolver as capture hooks. Never retry denied access with an owner credential,
a direct database read, or another memory store.

## Discover and select

```bash
python3 "${CODEX_PLUGIN_ROOT}/scripts/cognee-memory.py" status
python3 "${CODEX_PLUGIN_ROOT}/scripts/cognee-memory.py" sources --limit 50
# Continue with --offset <next_offset> while next_offset is present.
```

The catalog contains permission-filtered datasets and node sets with stable IDs,
names, source aliases, short descriptions, sample document labels and available
operations. It is derived from current Cognee metadata; there is no provider list
in the plugin. A new connector's imported node sets are discoverable without a
plugin update. Empty connections with no imported data are not catalog entries.

Session capture keeps one write dataset. To select several readable graph datasets:

```bash
python3 "${CODEX_PLUGIN_ROOT}/scripts/memory-access.py" read --persist --dataset-id <uuid> --dataset-id <uuid>
```

This selects reads, never grants access or changes writes. Include the session
dataset UUID to retain durable session learnings in graph recall. Persistent
selection is bound to this identity and backend; launch-specific selection wins.
Without a selection, general search uses the write dataset. A free-form source
hint can discover across readable datasets when no read selection exists.
`--all-readable` explicitly expands discovery beyond the saved read selection;
explicit `--dataset-id` values always take precedence. ACLs still apply.

## Route questions and retrieve evidence

```bash
"${CODEX_PLUGIN_ROOT}/scripts/cognee-search.sh" "What did we decide about deployment?" 10 --all-readable
"${CODEX_PLUGIN_ROOT}/scripts/cognee-search.sh" "What was discussed?" 10 --source "demo channel"
python3 "${CODEX_PLUGIN_ROOT}/scripts/cognee-memory.py" route "deployment decisions" --source "meeting notes"
```

`--source` accepts natural language, not an enum. Cognee's configured LLM selects
likely targets from authorized metadata, then the plugin retrieves native CHUNKS
from those targets. Routing does not read source bodies or contact connectors.
The server validates model-selected IDs; model output cannot invent permissions
or arbitrary tool calls. `route` previews targets and reasons without content search.

Routing considers up to 512 catalog entries by default. `--catalog-budget 2048`
raises that explicit budget. Descriptors are processed in batches of at most 64,
with at most three LLM calls concurrently per routing request, followed by joint
re-ranking. This adds LLM cost and latency. At most six targets are searched by
default (`--max-sources`, maximum eight). Catalog limits and uncertain routing are
reported; an empty route is NOT evidence that the answer does not exist.

If evidence is insufficient, inspect the routing and catalog, refine the question
and make one further search excluding already searched target IDs with repeated
`--exclude-source-id <uuid>`. Ask for clarification if ambiguity remains. Do not
silently claim that every dataset was searched. Source selection is heuristic.

For known targets, bypass LLM routing:

```bash
"${CODEX_PLUGIN_ROOT}/scripts/cognee-search.sh" "deployment" 10 --dataset-id <uuid> --node-set <name>
"${CODEX_PLUGIN_ROOT}/scripts/cognee-search.sh" "deployment" 10 --dataset-id <uuid> --exact
```

Repeated node sets use AND by default; `--node-match any` uses OR. Exact search
requires selected dataset UUIDs and cannot be combined with a source hint.
Session-only recall uses `--session`; structural code search uses `--code` and
the codebase skill. Neither uses source routing.

## Browse and read original evidence

```bash
python3 "${CODEX_PLUGIN_ROOT}/scripts/cognee-memory.py" browse <source-id> --limit 100
# Continue the same source with --cursor <next_cursor> until null.
python3 "${CODEX_PLUGIN_ROOT}/scripts/cognee-memory.py" read <document-id> --dataset-id <dataset-id>
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
These commands search stored memory: an imported database schema is searchable,
but fetching live rows requires the existing authorized Cognee tool connection.
Do not silently ingest rows or fetch from a connector to fill missing evidence.

## Compatibility and failures

Source discovery requires the SDK's `/api/v1/datasets/source-catalog`,
`source-route`, `source-documents` and `source-document` routes, plus HTTP node-set
operator forwarding. Missing routes are reported as a server capability error.
Each response is limited to 16 MiB and each command to 64 MiB. Budget errors are
failures, not successful complete results.

`status` reports whether the current credential is an agent or a legacy user
principal. Searches never provision identities, rotate credentials or change ACLs.
For permissions, use the manage-access skill explicitly.

## Improve Memory

**Server-first (session → graph sync):**

```bash
python3 "${CODEX_PLUGIN_ROOT}/scripts/sync-session-to-graph.py"
```

**Fallback only — server unreachable:**

```bash
uv run cognee-cli improve -d <dataset-name>
```

Bridge session feedback or Q&A into the graph:

```bash
uv run cognee-cli improve -d <dataset-name> -s <session-id>
```

For targeted enrichment:

```bash
uv run cognee-cli improve -d <dataset-name> --node-name <entity-name>
```

## Forget

When the user asks to forget or delete something from memory, follow the
**cognee-forget** skill — it walks the full guided flow: sync the live session,
find the dataset id, judge candidate documents by raw content (grouped by
session), confirm, then delete each match through the wrapper:

```bash
${CODEX_PLUGIN_ROOT}/scripts/cognee-forget.sh sync
${CODEX_PLUGIN_ROOT}/scripts/cognee-forget.sh datasets
${CODEX_PLUGIN_ROOT}/scripts/cognee-forget.sh data <dataset_id>
${CODEX_PLUGIN_ROOT}/scripts/cognee-forget.sh raw <dataset_id> <data_id>
${CODEX_PLUGIN_ROOT}/scripts/cognee-forget.sh forget <dataset_id> <data_id>
```

The wrapper uses the same identity resolver as the hooks and prints an `HTTP <status>` trailer per call. Deletion
is irreversible — use the narrowest scope possible and confirm first.

**Fallback only — server unreachable:**

```bash
uv run cognee-cli forget --dataset <dataset-name> --data-id <data-uuid>
uv run cognee-cli forget --dataset <dataset-name>
```

Avoid `uv run cognee-cli forget --everything` unless the user explicitly asks
to delete all Cognee data.
