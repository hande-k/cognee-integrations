# Configuration precedence

| Integration | Highest precedence → lowest | Details |
|---|---|---|
| Claude Code / Codex / Antigravity | active launch dataset selection → exported environment → `~/.cognee/.env` → defaults | A launch dataset switch overrides the configured dataset for that launch. `COGNEE_CLAUDE_BACKEND` / `COGNEE_CODEX_BACKEND` / `COGNEE_ANTIGRAVITY_BACKEND` overrides shared `COGNEE_BACKEND`. Empty env values use defaults. Legacy `config.json` is ignored and removed at startup. |
| Hermes | non-null `HERMES_HOME/cognee.json` → environment → defaults | `COGNEE_BASE_URL` beats deprecated `COGNEE_SERVICE_URL`; `COGNEE_PLUGIN_DATASET` beats `COGNEE_DATASET`. Saved config intentionally wins over exports. Some empty string settings are retained; the provider defaults an empty dataset at use time. |
| OpenClaw | explicit plugin config → supported environment fallbacks → defaults | Credentials accept `${ENV_VAR}` interpolation, which errors for missing variables. `COGNEE_MODE=cloud` forces cloud even with `mode: local`. Boolean plugin settings such as `autoRecall: false` are honored. Arbitrary env names do not override plugin settings. |

Default session dataset: `agent_sessions`. Default local server port: `8011`.

Tests live in the shared Claude/Codex suite, Hermes `test_config_contract.py`, and OpenClaw unit tests. This replaces the obsolete config-file contract proposed in #169.

## Python version requirements

cognee itself requires Python 3.10 or newer (up to 3.14). What that means for each
integration depends on whether it imports cognee in-process or only talks to a server:

| Integration | Host Python floor | Why |
|---|---|---|
| Claude Code / Codex / Antigravity plugins | **3.9+** for the hooks; **3.10+** only for the uv-less fallback | Hooks are stdlib HTTP clients. In local mode they build a uv-managed **Python 3.12** venv for the Cognee server (fetching uv, and a ~66 MB standalone 3.12 when none is on the machine). Without uv the fallback builds the venv from the host `python3`, which then must be 3.10+; an older host is refused with `host_python_too_old_for_venv` in `hook.log` and a session-start message. |
| OpenClaw | **3.9+** for the bootstrap script; **3.10+** only for the uv-less fallback | Same runtime scheme, driven from TypeScript. A refused fallback is recorded in `~/.cognee-plugin/.venv-error.json` and quoted in the gateway's "server did not become ready" warning. |
| Hermes, LangGraph, CrewAI, Strands, Google ADK, Aider, Obsidian, chat-memory, Slack, Telegram, second-brain, web-widget | **3.10+** | `requires-python = ">=3.10"`; `pip`/`uv` refuse to install on 3.9. |
| Dify, Dify SDK | **3.12+** | Dify plugin runtime requirement. |
| Claude Agent SDK | **3.13+** | Follows `claude-agent-sdk`. |

macOS's Xcode Command Line Tools install Python 3.9.6 as `/usr/bin/python3`. That is
enough for the hook-based plugins and OpenClaw; for the SDK packages install a 3.10+
interpreter (Homebrew, python.org or `uv python install 3.12`).

## Extraction models and authentication

Cognee's backend configures extraction independently of the host assistant.
`LLM_MODEL` passes to the core provider layer; use a model ID documented by that
provider and supported by the installed Cognee/LiteLLM version. Provider-prefixed
IDs such as `anthropic/<provider-model-id>` select that provider; no integration
release is required merely to pass through a new model string. Model/provider
compatibility is tracked in [cognee#4947](https://github.com/topoteretes/cognee/issues/4947).

For hosted APIs, configure a supported API credential in `LLM_API_KEY` and the
backend's provider settings. Local providers can use their supported local
configuration. A remote Cognee server holds its own extraction configuration;
`COGNEE_API_KEY` authenticates the plugin to that server.

Claude Code Pro/Max subscription OAuth tokens authenticate Claude Code itself.
This integration does not extract or reuse those tokens for independent Cognee
LLM calls. Use the provider's supported API or managed-provider authentication,
as described in [Anthropic's authentication terms](https://code.claude.com/docs/en/legal-and-compliance).
