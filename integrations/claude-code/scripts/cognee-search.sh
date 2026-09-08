#!/usr/bin/env bash
# Backward-compatible entry point; identity and retrieval live in the plugin client.
set -euo pipefail
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" >/dev/null 2>&1 && pwd)"
exec python3 "${SELF_DIR}/cognee-memory.py" search "$@"
