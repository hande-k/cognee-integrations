# cognee-integration-openclaw

OpenClaw plugin that adds Cognee-backed memory with automatic recall and capture.

## Features

- **Auto-recall**: Before each agent run, searches Cognee for relevant memories and injects them as context
- **Auto-capture**: After each agent run, stores conversation transcripts in Cognee for future recall
- **Configurable**: Search type, max results, score filtering, token limits, and more

## Installation

Install the plugin locally for development:

```bash
cd integrations/openclaw
npm install
npm run build
openclaw plugins install -l .
```

Or once published:

```bash
openclaw plugins install @openclaw/memory-cognee
```

## Configuration

Enable the plugin in your OpenClaw config (`~/.openclaw/config.yaml` or project config):

```yaml
plugins:
  entries:
    memory-cognee:
      enabled: true
      config:
        baseUrl: "http://localhost:8000"
        apiKey: "${COGNEE_API_KEY}"
        datasetName: "my-project"
        searchType: "GRAPH_COMPLETION"
        maxResults: 6
        autoRecall: true
        autoCapture: true
```

Set your API key in the environment:

```bash
export COGNEE_API_KEY="your-key-here"
```

## Configuration Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `baseUrl` | string | `http://localhost:8000` | Cognee API base URL |
| `apiKey` | string | `$COGNEE_API_KEY` | API key for authentication |
| `datasetName` | string | `openclaw` | Dataset name for storing memories |
| `searchType` | string | `GRAPH_COMPLETION` | Search mode: `GRAPH_COMPLETION`, `CHUNKS`, `SUMMARIES` |
| `maxResults` | number | `6` | Max memories to inject per recall |
| `minScore` | number | `0` | Minimum relevance score filter |
| `maxTokens` | number | `512` | Token cap for recall context |
| `autoRecall` | boolean | `true` | Inject memories before agent runs |
| `autoCapture` | boolean | `true` | Store transcripts after agent runs |
| `autoCognify` | boolean | `true` | Run cognify after new memories are added |
| `updateExisting` | boolean | `true` | Update existing memory entries when possible |
| `requestTimeoutMs` | number | `60000` | HTTP timeout for Cognee requests |

## How It Works

1. **Before agent start**: Plugin searches Cognee for memories relevant to the user's prompt and prepends them as a `<cognee_memories>` context block
2. **After agent end**: Plugin extracts the conversation transcript and stores it in Cognee, building up the memory over time
3. **Dataset state**: Tracks dataset IDs locally at `~/.openclaw/memory/cognee/datasets.json`

## Development

```bash
cd integrations/openclaw
# Build once, then link for local development with an OpenClaw project
npm install
npm run build
openclaw plugins install -l .
```

For live rebuilds during development:

```bash
npm run dev
```
