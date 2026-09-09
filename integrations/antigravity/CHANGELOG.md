# Changelog

All notable changes to the Cognee Antigravity plugin are documented in this file.
The version must match `plugin.json` so Antigravity can identify the installed
package version.

The format is based on [Keep a Changelog](https://keepachangelog.com/), and this
project adheres to [Semantic Versioning](https://semver.org/).

## [1.5.1]

### Changed
- **Per-prompt recall dispatches every scope at once.** The scopes (`session`,
  `trace`, `session_context`, `graph`, plus the `code` lane when it is armed)
  were requested one after another, so every cheap scope was a full round trip
  on top of the graph search — three of them against a cloud server — and an
  armed code lane could burn seconds before graph even started. All scopes are
  now in flight together and the prompt waits for the slowest one, not the sum;
  the results are folded into the same injected context, in the same order.
  With the graph search the only expensive call, a prompt's recall now costs
  about what the graph search alone costs.
  - The per-prompt recall now has one knob: `COGNEE_RECALL_BUDGET` (default
    4s) is the deadline every scope gets. With the scopes concurrent, a
    per-scope timeout and a whole-recall budget bounded the same interval, so
    `COGNEE_RECALL_TIMEOUT` is no longer read by this hook (it still bounds the
    explicit `cognee-search` path). `recall_budget_exceeded` fires only when
    the budget is too small for any request at all.
  - A refused connection or a 401/403 no longer cuts the fan-out short (every
    request is already in flight and fails in the same round trip); it is still
    recorded as one verdict per prompt, never one per scope.
  - `per_scope` in the `context_lookup_*` events keeps its canonical order and
    per-scope `elapsed_ms`, which now overlap rather than add up.
  - The `context_lookup_hit` / `context_lookup_empty` events now also carry the
    recall's aggregate `elapsed_ms` (previously Claude Code only): with the
    per-scope timings overlapping, the total is no longer their sum, so it is
    logged outright.

## [1.5.0]

### Added
- **Plugin identity: the plugin can now run as its own cognee agent sub-user.**
  Cognee servers that expose `POST /api/v1/integrations/plugins/antigravity/provision`
  mint a dedicated agent identity (sub-user + labeled API key) per plugin, so the
  dashboard attributes sessions, traces, and datasets to *this plugin* instead of
  the shared principal key. The provisioned key is cached per service URL at
  `~/.cognee-plugin/antigravity/agent_key.json` and outranks the env/cached
  principal for data-plane traffic; datasets the agent creates are auto-shared
  to the parent user.
  - **Identity policy is `COGNEE_PLUGIN_IDENTITY` = `auto` (default) / `true` /
    `false`.** `auto` provisions only in service of shared agent memory (below) and
    reverts to the principal when that cannot be wired, so nothing the principal
    owns is ever stranded; `true` is explicit and strict — provisioning is required
    and never falls back to the owner; `false` runs as the principal and ignores a
    cached identity.
  - **Safe create-only provisioning, credentials bound to server and principal.**
    Provisioning uses the SDK's `create_only` contract and never rotates an existing
    key; a credential the server rejected is blocked and never reused, and one bound
    to another principal is never used. Under `true` those stop with an error; under
    `auto` the plugin runs as the principal and logs why. Local startup is serialized
    with an OS lock; credential files are written atomically with owner-only
    permissions. Servers without `create_only` (or the provision endpoint) leave
    `auto` installs on the principal.
  - the doctor reports the new key source as **Plugin identity**.
- **Shared agent memory (default): one memory across all of your plugin
  agents.** A plugin identity is its own user, and cognee's grants flow
  child→parent only — left alone, per-plugin identities would silo memory
  (Antigravity could not recall what Claude Code stored). Session start now wires
  the agent into a shared `cognee-agent` role in your tenant (created for a
  tenant-less fresh install) with read+write on your datasets, backfilled on
  every launch and every ~60s by the idle watcher so a dataset another plugin
  creates shows up without a restart. The launch's dataset becomes a
  canonical, user-owned dataset addressed by UUID (`dataset_id`/`dataset_ids`
  on the launch record) — a name only resolves among datasets the caller owns,
  which would fork an empty per-agent copy — and recall, remember, the
  session-entry store, improve and the skills all address it that way;
  pre-existing same-named copies stay in the recall set.
  - **Opt out** with `"shared_agent_memory": false` in config.json or
    `COGNEE_SHARED_AGENT_MEMORY=false` for separated, per-plugin memory (the
    previous behaviour, name-addressed). The agent is removed from the
    shared role — it can no longer read or write your datasets — keeps its
    identity, and starts writing to its own, private dataset; what it shared
    before stays in your user's dataset (still yours, still visible in the
    dashboard). Re-enabling puts it back into the same role and dataset.
  - Degrades to separated memory — never fails a session — when the server
    has no permissions API or cannot store session entries by dataset UUID,
    when you are not the owner of your tenant, or when a tenant-less user
    already owns datasets (activating a tenant would hide them). Under
    `auto` an install that hits one of those stays on the principal.
  - Every tenant/role/grant call runs as the *principal*: an agent key can
    never widen its own access (server-enforced, owner-only).
  - the doctor shows **Memory Sharing** (`shared (role: cognee-agent)` /
    `separated (<reason>)` / `principal (...)`).
- **Dataset UUIDs throughout registration, remember, improve, recall, and
  switching.** A UUID-shaped dataset is addressed as an id; effective write
  permissions (not ownership alone) determine the datasets you can switch to,
  and a failed switch persistence keeps the previous session and unregisters
  the unused new connection.
- **Explicit graph read datasets** through `COGNEE_PLUGIN_READ_DATASET_IDS`
  (a JSON array of UUIDs): federated graph recall separate from the session's
  single write dataset; it takes precedence over the datasets shared memory
  resolved.
- **Agent connections now self-declare `type: "antigravity"`** at
  `POST /api/v1/agents/register` (previously the generic `"api"`). Note: the
  server's plugin registry does not list `antigravity` yet, so provisioning
  answers 404 and identity mode `auto` stays on the principal until it does.

## [1.4.3]

### Added

- Native Antigravity package metadata, four named hooks, and Cognee skills.
- A bounded transcript adapter that reads only the final 1 MiB of JSONL to map
  Antigravity invocations, tool output, and completed responses into Cognee memory
  events.
- Plugin-specific backend selection through `COGNEE_ANTIGRAVITY_BACKEND`, shared
  `~/.cognee/.env` configuration, and private hook state under
  `~/.cognee-plugin/antigravity/`.

### Changed

- Align the shared runtime with current Claude Code and Codex: provider extras,
  code-graph indexing, dataset-aware sync, bounded logs, stale-state cleanup,
  recall accounting, and persistent improve cooldowns.
- Remove legacy config-file routing and full-transcript sync fallbacks.
- Support documented `executionNum` Stop payloads, deduplicate retried tool
  steps, and correlate out-of-order tool results by their call identity.
- Renew bootstrap ownership when a conversation resumes after its host exits.

### Safety

- Installing with `agy plugin install` never edits Antigravity settings; the plugin
  is registered through its native manifest and hook declarations.
