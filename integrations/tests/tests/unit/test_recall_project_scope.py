"""Graph recall is scoped to the session's project node set plus the shared sets.

A session pinned to a project tag (COGNEE_PROJECT_NODE_SET) filters the graph
lane with ``node_name=[tag, *shared]`` (OR). Session/trace scopes stay
unfiltered, COGNEE_RECALL_PROJECT_SCOPE=false disables the filter while keeping
capture tagged, and a session without a tag recalls exactly as before.
"""

import pytest

OPENAPI = {
    "components": {
        "schemas": {name: {"properties": {"node_set": {}}} for name in ("QAEntry", "TraceEntry")}
    }
}


def _env(suite, isolated_modules, monkeypatch, tmp_path, tag, **env):
    pm = isolated_modules(suite, "_project_memory")
    common = isolated_modules(suite, "_plugin_common")
    monkeypatch.setattr(common, "resolved_http_endpoint_auth", lambda: ("https://tenant", "key"))
    monkeypatch.setattr(common, "_PLUGIN_DIR", tmp_path)
    # Environment is set after the modules exist: the isolation fixture starts
    # every suite from a clean environment.
    if tag:
        monkeypatch.setenv("COGNEE_PROJECT_NODE_SET", tag)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    pm.begin("primary", "s", str(tmp_path))
    calls = []

    def request(path, payload=None, **kwargs):
        calls.append((path, payload))
        if path == "/openapi.json":
            return OPENAPI
        return []

    monkeypatch.setattr(common, "_json_http_request", request)
    pm.prepare("primary", "s")
    return common, calls


def _recall(common, scope):
    return common.recall_via_http("q", dataset="primary", session_id="s", top_k=3, scope=scope)


def test_graph_scope_is_filtered_to_project_and_shared_sets(
    suite, isolated_modules, monkeypatch, tmp_path
):
    common, calls = _env(suite, isolated_modules, monkeypatch, tmp_path, "project-fixed")
    _recall(common, ["graph"])
    payload = calls[-1][1]
    assert payload["node_name"] == ["project-fixed", "global"]
    assert payload["node_name_filter_operator"] == "OR"


def test_session_scopes_stay_unfiltered(suite, isolated_modules, monkeypatch, tmp_path):
    common, calls = _env(suite, isolated_modules, monkeypatch, tmp_path, "project-fixed")
    _recall(common, ["session"])
    assert "node_name" not in calls[-1][1]


def test_no_project_tag_means_no_filter(suite, isolated_modules, monkeypatch, tmp_path):
    common, calls = _env(suite, isolated_modules, monkeypatch, tmp_path, "")
    _recall(common, ["graph"])
    assert "node_name" not in calls[-1][1]


@pytest.mark.parametrize("value", ["false", "0", "off"])
def test_scope_can_be_disabled_without_dropping_capture_tags(
    suite, isolated_modules, monkeypatch, tmp_path, value
):
    common, calls = _env(
        suite,
        isolated_modules,
        monkeypatch,
        tmp_path,
        "project-fixed",
        COGNEE_RECALL_PROJECT_SCOPE=value,
    )
    _recall(common, ["graph"])
    assert "node_name" not in calls[-1][1]


def test_shared_sets_are_configurable_and_deduplicated(
    suite, isolated_modules, monkeypatch, tmp_path
):
    common, calls = _env(
        suite,
        isolated_modules,
        monkeypatch,
        tmp_path,
        "project-fixed",
        COGNEE_RECALL_SHARED_NODE_SETS="global, team ,project-fixed",
    )
    _recall(common, ["graph"])
    assert calls[-1][1]["node_name"] == ["project-fixed", "global", "team"]
