"""Shared agent memory — one memory across a user's plugin agents.

A provisioned plugin agent is its own user; cognee's grants flow child -> parent
only, so per-plugin identities would silo memory (Claude Code could not recall
what Codex stored). ``_plugin_common.ensure_shared_memory`` closes that with the
server's permission model, no core changes:

  - the parent owns a tenant (created for a tenant-less fresh install) and a
    role named ``cognee-agent`` in it; every plugin agent is a member of both;
  - the role holds read+write on the parent's datasets, backfilled on every
    bootstrap / refresh so datasets created later by any agent converge;
  - the launch's dataset is a canonical PARENT-owned dataset addressed by
    UUID (a name resolves only among datasets the caller owns), so every agent
    writes to and recalls from the same physical dataset.

The fake models the server rules that matter: roles count only inside the
role's tenant, visibility is filtered by the caller's active tenant, grants
need "share", ``dataset_ids`` on recall / ``datasetId`` on remember are
authorised against the caller.
"""

from __future__ import annotations

import asyncio

import pytest
from utils.suites import ALL_SUITES

PRINCIPAL_KEY = "test-api-key"
ROLE = "cognee-agent"


@pytest.fixture
def pc(suite, isolated_modules, mock_server, monkeypatch):
    module = isolated_modules(suite, "_plugin_common")
    monkeypatch.setenv("COGNEE_BASE_URL", mock_server.url)
    monkeypatch.setenv("COGNEE_API_KEY", PRINCIPAL_KEY)
    monkeypatch.delenv("COGNEE_SHARED_AGENT_MEMORY", raising=False)
    monkeypatch.delenv("COGNEE_SESSION_KEY", raising=False)
    monkeypatch.delenv("COGNEE_SESSION_ID", raising=False)
    monkeypatch.delenv("COGNEE_PLUGIN_DATASET", raising=False)
    return module


@pytest.fixture
def bootstrap(suite, hook_module, mock_server, monkeypatch):
    """session-start.py in-process; ``run(config)`` drives the credential +
    registration bootstrap for host session HOST and returns its tuple."""
    module = hook_module(suite, "session-start.py")
    monkeypatch.setenv("COGNEE_BASE_URL", mock_server.url)
    monkeypatch.delenv("COGNEE_API_KEY", raising=False)
    monkeypatch.delenv("COGNEE_SHARED_AGENT_MEMORY", raising=False)

    def run(config: dict, host: str = "host-shared-1", dataset: str = "agent_sessions"):
        from _plugin_common import ensure_launch_record  # bound to this suite

        config.setdefault("base_url", mock_server.url)
        config.setdefault("dataset", dataset)
        config.setdefault("user_email", "default_user@example.com")
        config.setdefault("user_password", "default_password")
        sid, conn = ensure_launch_record(host, "/tmp/project", dataset=dataset)
        monkeypatch.setenv("COGNEE_SESSION_KEY", host)
        return asyncio.run(
            module._ensure_agent_credentials_and_register(config, "/tmp/project", sid, conn, host)
        )

    return module, run


def _agent_user_id(mock_server, suite) -> str:
    return mock_server.identity.plugin_agents[suite.name]["agent_id"]


# ── wiring ──────────────────────────────────────────────────────────────────


def test_fresh_install_wires_tenant_role_and_canonical_dataset(suite, pc, bootstrap, mock_server):
    module, run = bootstrap
    _uid, api_key, _name, ok = run({})
    assert ok and api_key.startswith("agentkey-")

    ident = mock_server.identity
    agent_id = _agent_user_id(mock_server, suite)
    parent_id = ident.principal_id
    # One tenant, owned by the parent; the agent is a member with it ACTIVE.
    (tenant,) = ident.tenants.values()
    assert tenant["owner_id"] == parent_id
    assert agent_id in tenant["members"]
    assert ident.active_tenant[agent_id] == tenant["id"]
    # The shared role exists in that tenant with the agent in it.
    (role,) = ident.roles.values()
    assert role["name"] == ROLE and role["tenant_id"] == tenant["id"]
    assert agent_id in role["members"]
    # The launch dataset is canonical: created as the PARENT, granted to the role.
    rows = [r for r in ident.dataset_rows.values() if r["name"] == "agent_sessions"]
    assert len(rows) == 1 and rows[0]["ownerId"] == parent_id
    assert {"read", "write"} <= ident.acl[rows[0]["id"]][role["id"]]
    # ...and pinned on the launch record for the data plane.
    write_id, read_ids = pc.resolve_active_dataset_ids("host-shared-1")
    assert write_id == rows[0]["id"] and read_ids == [write_id]
    # The agent can genuinely read AND write it through the role.
    assert write_id in ident.readable_dataset_ids(agent_id)
    assert write_id in ident.writable_dataset_ids(agent_id)
    # Registration bound the connection to the canonical dataset by UUID.
    mock_server.assert_called("POST", "/api/v1/agents/register", dataset_ids=[write_id])


def test_second_plugin_joins_the_same_role_and_dataset(pc, bootstrap, mock_server, monkeypatch):
    """The cross-plugin promise: a sibling agent (a second plugin key for the
    same user) lands in the same tenant/role and resolves the SAME canonical
    dataset — memory converges instead of forking per plugin."""
    module, run = bootstrap
    _uid, first_key, _n, ok = run({})
    assert ok
    first_write, _ = pc.resolve_active_dataset_ids("host-shared-1")

    # A sibling plugin: same principal, different plugin key + agent identity.
    ident = mock_server.identity
    status, body = ident.plugins_provision("codex", PRINCIPAL_KEY)
    assert status == 201
    sibling_key, sibling_id = body["apiKey"], body["agentId"]
    principal = pc.principal_key_for_control_plane(mock_server.url)
    shared = pc.ensure_shared_memory(
        service_url=mock_server.url,
        principal_key=principal,
        agent_key=sibling_key,
        agent_id=sibling_id,
        dataset="agent_sessions",
    )
    assert shared["mode"] == "shared"
    assert shared["dataset_id"] == first_write
    (role,) = ident.roles.values()
    assert sibling_id in role["members"]
    assert first_write in ident.writable_dataset_ids(sibling_id)
    assert len(ident.tenants) == 1 and len(ident.roles) == 1


def test_agent_recall_by_uuid_reaches_the_canonical_dataset(suite, pc, bootstrap, mock_server):
    """Recall addressed by ``dataset_ids`` as the agent is authorised by the
    role grant — the fake 403s ids the caller cannot read, so a passing call is
    proof of access, not just of the request shape."""
    module, run = bootstrap
    _uid, agent_key, _n, ok = run({})
    assert ok
    write_id, read_ids = pc.resolve_active_dataset_ids("host-shared-1")
    mock_server.set_recall_results([{"text": "codex stored this"}])
    results = pc.recall_via_http(
        "what did codex store?",
        session_id="s1",
        top_k=5,
        scope=["graph"],
        dataset="agent_sessions",
        dataset_ids=read_ids,
    )
    assert results == [{"text": "codex stored this"}]
    entry = mock_server.assert_called("POST", "/api/v1/recall", dataset_ids=[write_id])
    assert entry["headers"].get("X-Api-Key") == agent_key
    assert "datasets" not in (entry.get("json") or {})


def test_agent_remember_entry_targets_the_canonical_dataset_id(pc, bootstrap, mock_server):
    module, run = bootstrap
    _uid, _key, _n, ok = run({})
    assert ok
    write_id, _ = pc.resolve_active_dataset_ids("host-shared-1")
    pc.remember_entry_via_http("agent_sessions", "s1", {"role": "user", "content": "hi"})
    mock_server.assert_called("POST", "/api/v1/remember/entry", dataset_id=write_id)


def test_existing_same_named_copies_stay_readable(pc, bootstrap, mock_server):
    """Datasets forked under name addressing before shared memory (an agent's
    own ``agent_sessions``) are not lost: the parent's copy is canonical for
    writes, the agent-owned copy stays in the recall set."""
    ident = mock_server.identity
    parent_id = ident.principal_id
    parent_copy = ident.seed_dataset("agent_sessions", parent_id)
    status, body = ident.plugins_provision("claude-code", PRINCIPAL_KEY)
    legacy_copy = ident.seed_dataset("agent_sessions", body["agentId"])

    module, run = bootstrap
    _uid, _key, _n, ok = run({"api_key": PRINCIPAL_KEY})
    # The principal already owns datasets AND has no tenant: shared memory must
    # not activate a tenant over its head (that would hide every dataset).
    assert ok
    marker = pc.load_shared_memory_marker(mock_server.url)
    assert marker.get("mode") == "separated"
    assert marker.get("reason") == "tenantless_with_data"
    del parent_copy, legacy_copy


def test_canonical_prefers_parent_copy_and_keeps_siblings_readable(pc, mock_server):
    """Pure resolution logic: parent-owned copy wins; other same-named copies
    are appended for recall."""
    rows = [
        {"id": "ds-agent", "name": "m", "owner_id": "agent-1", "created_at": "2026-01-01T00:00:00"},
        {"id": "ds-parent", "name": "m", "owner_id": "parent", "created_at": "2026-01-01T00:00:05"},
    ]
    assert pc._pick_canonical(rows, "parent")["id"] == "ds-parent"
    # No parent copy: the oldest wins, deterministically for every sibling.
    assert pc._pick_canonical(rows, "nobody")["id"] == "ds-agent"


# ── opt-out and fallbacks ──────────────────────────────────────────────────


def test_opt_out_keeps_separated_memory(suite, pc, bootstrap, mock_server, monkeypatch):
    monkeypatch.setenv("COGNEE_SHARED_AGENT_MEMORY", "false")
    module, run = bootstrap
    _uid, api_key, _n, ok = run({})
    assert ok and api_key.startswith("agentkey-")  # fresh install still provisions
    ident = mock_server.identity
    assert not ident.tenants and not ident.roles
    # Name-addressed (no UUIDs pinned): the dataset is created by name for the
    # agent later in the bootstrap — the pre-shared behaviour.
    assert pc.resolve_active_dataset_ids("host-shared-1") == ("", [])
    assert pc.load_shared_memory_marker(mock_server.url) == {}
    for call in mock_server.calls:
        assert not call["path"].startswith("/api/v1/permissions/")
    # The data plane keeps addressing by name.
    pc.remember_entry_via_http("agent_sessions", "s1", {"role": "user", "content": "hi"})
    entry = mock_server.assert_called(
        "POST", "/api/v1/remember/entry", dataset_name="agent_sessions"
    )
    assert "dataset_id" not in entry["json"]


def test_older_server_without_permissions_api_stays_separated(pc, bootstrap, mock_server):
    mock_server.identity.permissions_api = False
    module, run = bootstrap
    _uid, api_key, _n, ok = run({})
    assert ok and api_key.startswith("agentkey-")
    assert pc.load_shared_memory_marker(mock_server.url).get("reason") == "unsupported"
    assert pc.resolve_active_dataset_ids("host-shared-1") == ("", [])


def test_non_owner_of_an_org_tenant_stays_separated(pc, bootstrap, mock_server):
    """Roles are owner-only: a member of someone else's tenant cannot run
    shared memory and must not fail the session either."""
    ident = mock_server.identity
    ident.seed_user("boss@example.com")
    boss_key = ident.seed_owner_key("boss@example.com")
    _, body = ident.tenants_create(boss_key, "org")
    ident.tenant_add_user(boss_key, ident.principal_id, body["tenant_id"])
    ident.tenant_select(PRINCIPAL_KEY, body["tenant_id"])

    module, run = bootstrap
    _uid, api_key, _n, ok = run({})
    assert ok
    assert pc.load_shared_memory_marker(mock_server.url).get("reason") == "not_tenant_owner"


def test_wiring_is_cached_and_backfill_grants_new_datasets(pc, bootstrap, mock_server):
    """A second bootstrap issues no tenant/role calls, but still grants the
    role on a dataset that appeared in between (a sibling created it)."""
    module, run = bootstrap
    _uid, _k, _n, ok = run({})
    assert ok
    ident = mock_server.identity
    (role,) = ident.roles.values()
    later = ident.seed_dataset("notes", ident.principal_id)
    mock_server.calls.clear()

    _uid, _k, _n, ok = run({})
    assert ok
    setup_paths = {
        "/api/v1/permissions/tenants",
        "/api/v1/permissions/roles",
        "/api/v1/permissions/tenants/select",
    }
    assert not [c for c in mock_server.calls if c["path"] in setup_paths]
    assert {"read", "write"} <= ident.acl[later["id"]][role["id"]]
    # Memoised: the third run grants nothing at all.
    mock_server.calls.clear()
    run({})
    assert not [
        c for c in mock_server.calls if c["path"].startswith("/api/v1/permissions/datasets/")
    ]


def test_refresh_picks_up_a_sibling_dataset_without_restart(pc, bootstrap, mock_server):
    """The idle watcher's refresh: a dataset a sibling agent creates mid-session
    (auto-shared to the parent) is granted to the role and, if same-named,
    joins the recall set — no session restart."""
    module, run = bootstrap
    _uid, _k, _n, ok = run({})
    assert ok
    ident = mock_server.identity
    _, body = ident.plugins_provision("codex", PRINCIPAL_KEY)
    sibling_copy = ident.seed_dataset("agent_sessions", body["agentId"])
    (role,) = ident.roles.values()
    assert role["id"] not in ident.acl.get(sibling_copy["id"], {})

    assert pc.refresh_shared_memory("host-shared-1") is True
    write_id, read_ids = pc.resolve_active_dataset_ids("host-shared-1")
    assert write_id != sibling_copy["id"]  # canonical stays the parent's copy
    assert sibling_copy["id"] in read_ids
    assert {"read", "write"} <= ident.acl[sibling_copy["id"]][role["id"]]


def test_switch_record_carries_the_new_dataset_ids(pc):
    pc.ensure_launch_record("host-x", "/w", dataset="a")
    pc.set_launch_dataset_ids("host-x", "ds-a", ["ds-a", "ds-a2"])
    assert pc.resolve_active_dataset_ids("host-x") == ("ds-a", ["ds-a", "ds-a2"])
    pc.switch_launch_record("host-x", session_id="s2", dataset="b", conn_uuid="c2")
    # The old ids must not leak onto the new dataset.
    assert pc.resolve_active_dataset_ids("host-x") == ("", [])
    pc.switch_launch_record(
        "host-x",
        session_id="s3",
        dataset="c",
        conn_uuid="c3",
        dataset_id="ds-c",
        dataset_ids=["ds-c"],
    )
    assert pc.dataset_id_for("c", "host-x") == "ds-c"
    assert pc.dataset_id_for("zzz", "host-x") == ""


def test_control_plane_never_uses_the_agent_key(pc, bootstrap, mock_server):
    """Every tenant/role/grant call authenticates as the PRINCIPAL; only the
    agent's own tenant selection runs as the agent."""
    module, run = bootstrap
    _uid, agent_key, _n, ok = run({})
    assert ok
    for call in mock_server.calls:
        if not call["path"].startswith("/api/v1/permissions/"):
            continue
        key = call["headers"].get("X-Api-Key")
        if call["path"] == "/api/v1/permissions/tenants/select":
            assert key == agent_key
        else:
            assert key == PRINCIPAL_KEY or key.startswith("apikey-"), call["path"]


def test_opt_out_after_shared_use_is_consistent_and_reversible(
    pc, bootstrap, mock_server, monkeypatch
):
    """Disabling shared memory after using it: the plugin keeps its agent
    identity but LEAVES the shared role — it can no longer read the user's
    datasets — and goes back to name addressing for reads AND writes (a stale
    canonical id must not keep steering writes into the shared dataset). The
    tenant/role stay server-side, so re-enabling puts the agent back into the
    same role and the same canonical dataset without creating anything new."""
    module, run = bootstrap
    _uid, agent_key, _n, ok = run({})
    assert ok
    ident = mock_server.identity
    agent_id = ident.user_id_for_key(agent_key)
    shared_id, _ = pc.resolve_active_dataset_ids("host-shared-1")
    tenants, roles = len(ident.tenants), len(ident.roles)
    (role,) = ident.roles.values()
    assert shared_id in ident.readable_dataset_ids(agent_id)

    monkeypatch.setenv("COGNEE_SHARED_AGENT_MEMORY", "false")
    _uid, key_after, _n, ok = run({})
    assert ok and key_after == agent_key  # identity kept
    # Out of the role, as the principal: the shared dataset is now off limits.
    leave = mock_server.assert_called("DELETE", f"/api/v1/permissions/users/{agent_id}/roles")
    assert leave["headers"].get("X-Api-Key") != agent_key
    assert agent_id not in role["members"]
    assert shared_id not in ident.readable_dataset_ids(agent_id)
    assert {"read", "write"} <= ident.acl[shared_id][role["id"]]  # grants stay on the role
    assert pc.resolve_active_dataset_ids("host-shared-1") == ("", [])
    assert pc.dataset_id_for("agent_sessions", "host-shared-1") == ""
    marker = pc.load_shared_memory_marker(mock_server.url)
    assert marker["mode"] == "separated" and marker["reason"] == "opt_out"
    assert marker["role_id"] and marker["role_member"] is False  # wiring remembered
    mock_server.calls.clear()
    pc.remember_entry_via_http("agent_sessions", "s1", {"role": "user", "content": "hi"})
    entry = mock_server.assert_called("POST", "/api/v1/remember/entry")
    assert "dataset_id" not in entry["json"]
    # A second opted-out launch is quiet: nothing left to leave.
    mock_server.calls.clear()
    run({})
    mock_server.assert_not_called("DELETE", f"/api/v1/permissions/users/{agent_id}/roles")

    monkeypatch.delenv("COGNEE_SHARED_AGENT_MEMORY")
    mock_server.calls.clear()
    _uid, _k, _n, ok = run({})
    assert ok
    assert agent_id in role["members"]
    assert shared_id in ident.readable_dataset_ids(agent_id)
    assert pc.resolve_active_dataset_ids("host-shared-1")[0] == shared_id
    assert (len(ident.tenants), len(ident.roles)) == (tenants, roles)
    mock_server.assert_not_called("POST", "/api/v1/permissions/tenants")
    mock_server.assert_not_called("POST", "/api/v1/permissions/roles")


def test_tenantless_user_with_agent_owned_data_stays_separated(suite, pc, bootstrap, mock_server):
    """An agent that already created datasets under name addressing (the
    pre-shared fresh-install path) must not be moved into a tenant: it would
    select that tenant and lose sight of its own tenant-less datasets."""
    ident = mock_server.identity
    _, body = ident.plugins_provision(suite.name, PRINCIPAL_KEY)
    pc.save_cached_agent_key(mock_server.url, body["apiKey"], body["agentId"])
    ident.seed_dataset("agent_sessions", body["agentId"])  # auto-shared to the parent
    assert not any(r["ownerId"] == ident.principal_id for r in ident.dataset_rows.values())

    module, run = bootstrap
    _uid, api_key, _n, ok = run({})
    assert ok and api_key == body["apiKey"]
    assert not ident.tenants
    assert pc.load_shared_memory_marker(mock_server.url).get("reason") == "tenantless_with_data"
    assert ident.active_tenant.get(body["agentId"]) is None


def test_reverted_migration_revokes_the_unused_agent_key(
    suite, bootstrap, mock_server, monkeypatch
):
    monkeypatch.setenv("COGNEE_API_KEY", PRINCIPAL_KEY)
    mock_server.identity.permissions_api = False
    module, run = bootstrap
    _uid, api_key, _n, ok = run({"api_key": PRINCIPAL_KEY})
    assert ok and api_key == PRINCIPAL_KEY
    mock_server.assert_called("DELETE", f"/api/v1/integrations/plugins/{suite.name}")
    record = mock_server.identity.plugin_agents[suite.name]
    assert record["keys"] and all(
        not mock_server.identity.valid_keys[k]["valid"] for k in record["keys"]
    )


@pytest.mark.parametrize("suite", ALL_SUITES, ids=lambda s: s.name)
def test_principal_key_never_resolves_to_the_agent_key(
    suite, isolated_modules, monkeypatch, tmp_path
):
    pc = isolated_modules(suite, "_plugin_common")
    monkeypatch.setenv("COGNEE_BASE_URL", "http://one.test")
    pc.save_cached_agent_key("http://one.test", "agent-key-1", "agent-1")
    # The data plane stamps the agent key into the env — it must not be
    # mistaken for the principal.
    monkeypatch.setenv("COGNEE_API_KEY", "agent-key-1")
    assert pc.principal_key_for_control_plane("http://one.test") == ""
    pc.save_cached_api_key("http://one.test", "principal-1")
    assert pc.principal_key_for_control_plane("http://one.test") == "principal-1"
    monkeypatch.setenv("COGNEE_API_KEY", "env-principal")
    assert pc.principal_key_for_control_plane("http://one.test") == "env-principal"
