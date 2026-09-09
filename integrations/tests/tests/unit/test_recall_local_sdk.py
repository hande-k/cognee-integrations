"""The concurrent recall fan-out on the in-process local-SDK branch.

``session-context-lookup.py`` dispatches every scope at once. In HTTP mode each
scope is a blocking request pushed to a worker thread; on the local-SDK branch
each scope is ``cognee.recall`` awaited directly, so the scopes interleave as
coroutines on the hook's own event loop and each one is bounded by
``asyncio.wait_for``. Same fan-out, different mechanism — and until now the only
driver ran HTTP mode, so this branch shipped on inspection alone.

Contract, mirroring the HTTP tests:
  * every scope is awaited together — the prompt costs the slowest scope;
  * one scope raising drops that scope only; the others are still injected;
  * a scope past the shared deadline is cut there (``recall_error`` with a
    ``slow`` verdict) while the others still land;
  * ``per_scope`` reports every scope, in canonical order.

Only suites that declare ``has_local_sdk_recall`` carry the branch; the others
skip rather than pretend.
"""

from __future__ import annotations

import asyncio
import time

import pytest
from utils.recall import SCOPES, assert_valid_per_scope, drive_recall


@pytest.fixture
def lookup(suite, hook_module):
    if not suite.has_local_sdk_recall:
        pytest.skip(f"{suite.name}: no in-process local-SDK recall branch")
    return hook_module(suite, "session-context-lookup.py")


def _context(run) -> str:
    return run.output["hookSpecificOutput"]["additionalContext"]


def test_every_scope_is_awaited_together(lookup, monkeypatch):
    """Four scopes sleeping 0.3s each must cost ~0.3s, not 1.2s."""

    async def slow(_prompt, **_kw):
        await asyncio.sleep(0.3)
        return []

    monkeypatch.setenv("COGNEE_RECALL_BUDGET", "5")
    started = time.monotonic()
    run = drive_recall(lookup, monkeypatch, mode="local_sdk", sdk_recall=slow)
    wall = time.monotonic() - started

    assert sorted(run.calls) == sorted(SCOPES), run.calls
    assert wall < 0.9, f"scopes ran back to back: {wall:.2f}s for 4 x 0.3s"
    per_scope = run.detail("context_lookup_empty")["per_scope"]
    assert_valid_per_scope(per_scope)
    assert not any(record.get("skipped") for record in per_scope.values()), per_scope
    assert all(record["elapsed_ms"] >= 250 for record in per_scope.values()), per_scope


def test_the_sdk_call_carries_the_scope_and_query_type(lookup, monkeypatch):
    """The wire the SDK branch speaks: scope list, HYBRID_COMPLETION for graph."""
    run = drive_recall(lookup, monkeypatch, mode="local_sdk", sdk_recall={})

    assert run.kwargs["graph"]["query_type"] == "HYBRID_COMPLETION"
    assert run.kwargs["session"]["query_type"] is None
    assert run.kwargs["session_context"]["context_profile"] == "agent"
    assert all(kw["only_context"] is True for kw in run.kwargs.values()), run.kwargs
    assert all(kw["session_id"] == "sid" for kw in run.kwargs.values()), run.kwargs


def test_one_raising_scope_does_not_drop_the_others(lookup, monkeypatch):
    async def flaky(_prompt, **kw):
        scope = kw["scope"][0]
        if scope == "trace":
            raise RuntimeError("trace store exploded")
        if scope == "session":
            return [{"question": "q1", "answer": "a1"}]
        if scope == "graph":
            return [{"source": "graph", "content": "graph fact"}]
        return []

    run = drive_recall(lookup, monkeypatch, mode="local_sdk", sdk_recall=flaky)

    detail = run.detail("context_lookup_hit")
    assert detail is not None, run.events
    assert detail["counts"]["session"] == 1
    assert detail["counts"]["graph_context"] == 1
    assert detail["counts"]["trace"] == 0
    errors = [d for e, d in run.events if e == "recall_error"]
    assert [d["scope"] for d in errors] == [["trace"]], errors
    context = _context(run)
    assert "graph fact" in context and "Q: q1" in context


def test_a_scope_past_the_deadline_is_cut_while_the_others_land(lookup, monkeypatch):
    """The shared deadline bounds the slowest coroutine; nothing waits on it."""

    async def one_hangs(_prompt, **kw):
        scope = kw["scope"][0]
        if scope == "graph":
            await asyncio.sleep(5)
            return [{"source": "graph", "content": "too late"}]
        return [{"question": f"q-{scope}", "answer": "a"}] if scope == "session" else []

    monkeypatch.setenv("COGNEE_RECALL_BUDGET", "0.4")
    started = time.monotonic()
    run = drive_recall(lookup, monkeypatch, mode="local_sdk", sdk_recall=one_hangs)
    wall = time.monotonic() - started

    assert wall < 1.5, f"the hung scope held the prompt: {wall:.2f}s"
    errors = [d for e, d in run.events if e == "recall_error"]
    assert len(errors) == 1 and errors[0]["scope"] == ["graph"], errors
    assert errors[0]["verdict"] == "slow", errors
    per_scope = run.detail("context_lookup_hit")["per_scope"]
    assert 350 <= per_scope["graph"]["elapsed_ms"] < 1200, per_scope
    context = _context(run)
    assert "q-session" in context
    assert "too late" not in context


def test_results_are_folded_in_canonical_order_whatever_finishes_first(lookup, monkeypatch):
    """Graph answers first here and session last; the sections must not care."""
    delays = {"session": 0.3, "trace": 0.2, "session_context": 0.1, "graph": 0.0}

    async def staggered(_prompt, **kw):
        scope = kw["scope"][0]
        await asyncio.sleep(delays[scope])
        return {
            "session": [{"question": "q1", "answer": "a1"}],
            "trace": [{"source": "trace", "origin_function": "Bash", "status": "ok"}],
            "session_context": [{"source": "session_context", "content": "guidance"}],
            "graph": [{"source": "graph", "content": "graph fact"}],
        }[scope]

    run = drive_recall(lookup, monkeypatch, mode="local_sdk", sdk_recall=staggered)
    context = _context(run)
    positions = [
        context.index("=== Active agent guidance ==="),
        context.index("=== Knowledge graph snapshot ==="),
        context.index("=== Prior agent trace ==="),
        context.index("=== Prior session turns ==="),
    ]
    assert positions == sorted(positions), context
