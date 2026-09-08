# Cognee Memory Plugin for OpenCode

Gives OpenCode persistent memory across sessions using Cognee's knowledge graph. Tool calls and responses are automatically captured into session memory, relevant context is injected on every compaction, and session data is bridged into the permanent knowledge graph when idle.

## Installation

Add this package to your configuration:

1. Specify `@cognee/cognee-opencode` under the `plugin` array in your `opencode.json` configuration file:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "plugin": ["@cognee/cognee-opencode"]
}
```

2. Make sure you have a running Cognee instance locally (`http://localhost:8000`) or configure environment variables:

```bash
export COGNEE_SERVICE_URL="http://localhost:8000"
export COGNEE_API_KEY="your-api-key" # optional
```

## Features

- **Auto-capture**: Listens to `tool.execute.after` to store all completed tool execution parameters and outputs directly into Cognee.
- **Auto-recall**: Injects relevant context into the LLM during context compaction using the `experimental.session.compacting` hook.
- **Custom Tools**:
  - `cognee_remember`: Save custom facts, user preferences, or project details into long-term graph memory.
  - `cognee_search`: Search the graph memory for specific details.


### Connected source search

Use `cognee_search_sources` for questions spanning connected sources. Provide a question and an
optional source name/description; there is no provider list in the plugin. The SDK
routes authorized dataset/node-set descriptors and native database connections,
then executes document retrieval or read-only SQL as appropriate. SQL evidence
includes the query and bounded rows; these rows are not ingested into memory.

This explicit operation is separate from automatic session/project recall. Optional
`dataset_ids` restricts the search to those datasets and excludes live connections;
`include_connections=false` disables live database queries. Without dataset selection,
it discovers the caller's readable catalog. Native connection permissions and
`TOOL_CALLS_ENABLED` still apply. A dataset grant does not grant database access.

Requires the SDK source-search API (SDK draft #4978). Unsupported servers and denied
requests fail explicitly. Inspect routing, evidence and partial errors before treating
a question as answered; unselected sources have not been searched.
