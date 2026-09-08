#!/usr/bin/env bash
# Store text in Cognee's permanent knowledge graph.
#
# Usage:
#   cognee-remember.sh <content> [--node-set <node_set>] [--dataset <dataset>]
#   cognee-remember.sh --file <path> [--node-set <node_set>] [--dataset <dataset>]
#
# --node-set: node set for categorization (default: user_context)
#             user_context | project_docs | agent_actions
# --dataset:  dataset name (default: this launch's active dataset)
# --file:     upload a file under its REAL filename instead of inline text.
#             The extension is the server's routing signal: code files
#             (.py/.ts/.go/...) ride the zero-LLM code-graph route on
#             cognee >= 1.5.x; anything else flows through its normal loader.
#             With --file, positional <content> is not required.

#
# Configuration:
#   Resolves auth from env/api_key.json.
#   Dataset comes from this launch's record (follows a dataset switch), then
#   COGNEE_PLUGIN_DATASET, then agent_sessions.
#   Falls back to cognee-cli only if the server is unreachable.

set -euo pipefail

PLUGIN_DIR="${HOME}/.cognee-plugin/claude-code"
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" >/dev/null 2>&1 && pwd)"
runtime_json="$(python3 "${SELF_DIR}/_command_runtime.py")"

SERVICE_URL="$(python3 - <<'PY' "${runtime_json}" 2>/dev/null || true
import json, sys
try:
    print((json.loads(sys.argv[1] or "{}").get("service_url") or "").strip())
except Exception:
    pass
PY
)"
API_KEY="$(python3 - <<'PY' "${runtime_json}" 2>/dev/null || true
import json, sys
try:
    print((json.loads(sys.argv[1] or "{}").get("api_key") or "").strip())
except Exception:
    pass
PY
)"
DATASET="$(python3 - <<'PY' "${runtime_json}" 2>/dev/null || true
import json, sys
try:
    print((json.loads(sys.argv[1] or "{}").get("dataset") or "").strip())
except Exception:
    pass
PY
)"

[ -z "$SERVICE_URL" ] && SERVICE_URL="${COGNEE_BASE_URL:-${COGNEE_LOCAL_API_URL:-http://localhost:8011}}"
[ -z "$API_KEY" ] && API_KEY="${COGNEE_API_KEY:-}"
[ -z "$DATASET" ] && DATASET="${COGNEE_PLUGIN_DATASET:-agent_sessions}"

# Parse arguments: content is first positional (unless --file leads); flags follow
CONTENT=""
NODE_SET="user_context"
FILE_PATH=""

if [ "${1:-}" != "--file" ]; then
    CONTENT="${1:-}"
    shift || true
fi
while [ $# -gt 0 ]; do
    case "$1" in
        --node-set)
            shift
            NODE_SET="${1:-user_context}"
            ;;
        --dataset|-d)
            shift
            DATASET="${1:-$DATASET}"
            ;;
        --file)
            shift
            FILE_PATH="${1:-}"
            ;;
        *)
            ;;
    esac
    shift || true
done

if [ -n "$FILE_PATH" ] && [ ! -f "$FILE_PATH" ]; then
    echo "Error: --file path not found: $FILE_PATH" >&2
    exit 1
fi
if [ -z "$CONTENT" ] && [ -z "$FILE_PATH" ]; then
    echo "Error: no content provided" >&2
    exit 1
fi

# Server-first: POST to /api/v1/remember via _remember_http.py.
# UNREACHABLE → fall back to cognee-cli and warn.
# Any other result (ok or error) → authoritative; do not fall back.
RESULT="$(python3 "${SELF_DIR}/_remember_http.py" "$SERVICE_URL" "$API_KEY" "$CONTENT" "$DATASET" "$NODE_SET" "$FILE_PATH" || true)"

if [ -n "$RESULT" ] && [ "$RESULT" != "UNREACHABLE" ]; then
    printf '%s\n' "$RESULT"
elif [ -n "$FILE_PATH" ]; then
    # No CLI fallback for file uploads: the CLI path would re-read and rename
    # the content, losing the extension-based code routing this mode exists for.
    echo "[cognee-remember] server unreachable — file upload NOT stored; retry once the server is back" >&2
    echo "UNREACHABLE"
    exit 1
else
    echo "[cognee-remember] falling back to cognee-cli (degraded — server unreachable; verify the store succeeded once the server is back)" >&2
    cognee-cli remember "$CONTENT" -d "$DATASET" --node-set "$NODE_SET" 2>/dev/null || true
fi
