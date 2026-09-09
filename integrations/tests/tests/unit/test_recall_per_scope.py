"""Per-scope recall instrumentation and the shared time budget.

Recall fans out over four scopes (session / trace / session_context / graph) on
every single prompt, so it is the plugin's most latency-sensitive path. Two things
have to hold: the record must show what each scope did — including scopes that
found nothing or never ran — and the fan-out must respect one overall budget
rather than letting any scope run a full timeout past the deadline.

Contract:
  * the event carries a ``{hits, elapsed_ms}`` record for all four scopes, in
    canonical order, without disturbing the aggregate ``counts``;
  * per-scope hits are raw attribution — ``graph`` is not folded here, while
    ``counts`` buckets it into ``graph_context``;
  * an open breaker runs nothing yet still reports all four as skipped;
  * the scopes are dispatched concurrently, every one with the same deadline —
    the per-scope timeout clamped to the whole budget — so the prompt waits for
    the slowest scope, not the sum, and no scope waits behind another;
  * a budget too small for any honest attempt dispatches nothing at all;
  * the synchronous prompt hook never drains the warmup buffer.

All registered suites carry this machinery identically (``per_scope``,
``MIN_SCOPE_TIMEOUT``, ``recall_budget_exceeded``), so all are exercised.

Migrated from claude-code/tests/test_per_scope_timing.py, which ran in no CI job
on any platform.
"""

from __future__ import annotations

import time

import pytest
from utils.recall import SCOPES, assert_valid_per_scope, drive_recall


@pytest.fixture
def lookup(suite, hook_module):
    return hook_module(suite, "session-context-lookup.py")


def test_a_hit_reports_every_scope(lookup, monkeypatch):
    """One hit per scope pair, and the aggregate counters still line up."""
    run = drive_recall(
        lookup,
        monkeypatch,
        recall={
            "session": [{"question": "q1", "answer": "a1"}],
            "trace": [],
            "graph": [{"source": "graph", "content": "gg"}],
            "session_context": [],
        },
    )

    detail = run.detail("context_lookup_hit")
    assert detail is not None, f"expected a context_lookup_hit: {run.events}"
    assert "counts" in detail, "the aggregate counts must survive alongside per_scope"

    per_scope = detail["per_scope"]
    assert_valid_per_scope(per_scope)
    assert per_scope["session"]["hits"] == 1
    assert per_scope["trace"]["hits"] == 0
    assert per_scope["graph"]["hits"] == 1
    assert per_scope["session_context"]["hits"] == 0

    # Raw attribution above; bucketed here — graph folds into graph_context.
    assert detail["counts"]["graph_context"] == 1


def test_a_total_miss_still_reports_every_scope(lookup, monkeypatch):
    """Nothing found is not nothing to report: four scopes ran and each says so."""
    run = drive_recall(lookup, monkeypatch, recall={scope: [] for scope in SCOPES})

    detail = run.detail("context_lookup_empty")
    assert detail is not None, f"expected a context_lookup_empty: {run.events}"

    per_scope = detail["per_scope"]
    assert_valid_per_scope(per_scope)
    assert all(record["hits"] == 0 for record in per_scope.values())
    assert not any(record.get("skipped") for record in per_scope.values()), (
        f"every scope ran, so none may be marked skipped: {per_scope}"
    )


def test_an_open_breaker_skips_every_scope_but_still_reports(lookup, monkeypatch):
    """Breaker open means no requests — and a record that says exactly that."""
    run = drive_recall(
        lookup,
        monkeypatch,
        recall={scope: [] for scope in SCOPES},
        breaker_open=(True, 30),
    )

    detail = run.detail("context_lookup_empty")
    assert detail is not None, f"expected a context_lookup_empty: {run.events}"

    per_scope = detail["per_scope"]
    assert_valid_per_scope(per_scope)
    assert all(record.get("skipped") for record in per_scope.values()), per_scope
    assert all(
        record["hits"] == 0 and record["elapsed_ms"] == 0 for record in per_scope.values()
    ), f"a skipped scope cannot have spent time or found anything: {per_scope}"
    assert run.calls == [], f"breaker open must dispatch nothing, got {run.calls}"


def test_every_scope_gets_the_same_deadline_clamped_to_the_budget(lookup, monkeypatch):
    """One deadline for the whole fan-out: min(per-scope timeout, budget).

    With a 0.5s per-call timeout and a 0.8s budget every scope gets 0.5s; with a
    0.3s budget every scope is clamped to ~0.3s. Nobody is handed the budget
    "remaining after earlier scopes" any more, because nothing runs earlier —
    all four are in flight together, so the recall can never outlast the
    smaller of the two knobs.
    """
    monkeypatch.setenv("COGNEE_RECALL_TIMEOUT", "0.5")
    monkeypatch.setenv("COGNEE_RECALL_BUDGET", "0.8")
    run = drive_recall(lookup, monkeypatch, recall={scope: [] for scope in SCOPES})
    assert set(run.timeouts) == set(SCOPES), run.timeouts
    assert all(t == 0.5 for t in run.timeouts.values()), run.timeouts

    monkeypatch.setenv("COGNEE_RECALL_BUDGET", "0.3")
    run = drive_recall(lookup, monkeypatch, recall={scope: [] for scope in SCOPES})
    assert set(run.timeouts) == set(SCOPES), run.timeouts
    assert all(0.2 <= t <= 0.3 for t in run.timeouts.values()), (
        f"expected every scope clamped to the budget: {run.timeouts}"
    )
    assert not run.fired("recall_budget_exceeded"), run.events


def test_scopes_run_concurrently_so_the_prompt_waits_for_the_slowest(lookup, monkeypatch):
    """Two slow scopes (0.45s + 0.3s) must cost ~0.45s, not 0.75s.

    Sequential dispatch made every cheap scope a full round trip on top of the
    graph search. Concurrent dispatch is the point of the fan-out, so it is
    pinned by wall time: well under the sum, and every scope still dispatched
    and reported with its own elapsed time.
    """
    sleeps = {"session": 0.45, "trace": 0.3}

    def slow_recall(_prompt, **kw):
        time.sleep(sleeps.get(kw["scope"][0], 0))
        return []

    monkeypatch.setenv("COGNEE_RECALL_TIMEOUT", "5")
    monkeypatch.setenv("COGNEE_RECALL_BUDGET", "5")
    started = time.monotonic()
    run = drive_recall(lookup, monkeypatch, recall=slow_recall)
    wall = time.monotonic() - started

    assert set(run.calls) == set(SCOPES), run.calls
    assert wall < 0.65, f"scopes ran back to back: {wall:.2f}s for 0.45s + 0.3s of sleeps"

    per_scope = run.detail("context_lookup_empty")["per_scope"]
    assert_valid_per_scope(per_scope)
    assert not any(record.get("skipped") for record in per_scope.values()), per_scope
    assert per_scope["session"]["elapsed_ms"] >= 400, per_scope
    assert per_scope["trace"]["elapsed_ms"] >= 250, per_scope


def test_a_budget_below_the_floor_dispatches_nothing(lookup, monkeypatch):
    """Less than MIN_SCOPE_TIMEOUT of budget cannot return anything useful.

    Firing requests with a doomed deadline only loads the server; the hook
    logs ``recall_budget_exceeded`` and reports every scope as skipped.
    """
    monkeypatch.setenv("COGNEE_RECALL_BUDGET", "0.05")
    run = drive_recall(lookup, monkeypatch, recall={scope: [] for scope in SCOPES})

    assert run.calls == [], f"nothing may be dispatched below the floor: {run.calls}"
    assert run.fired("recall_budget_exceeded"), f"budget overrun not logged: {run.events}"
    per_scope = run.detail("context_lookup_empty")["per_scope"]
    assert_valid_per_scope(per_scope)
    assert all(record.get("skipped") for record in per_scope.values()), per_scope


def test_the_injected_context_is_identical_whatever_order_the_scopes_answer_in(lookup, monkeypatch):
    """Golden parity: staggered arrivals produce the byte-identical injection.

    The sections are folded in canonical order after the fan-out, so a run where
    graph answers first and session last must render exactly what an
    all-instant run renders. The header line is stripped before comparing: it
    carries per-session running totals that legitimately differ between two
    consecutive runs on one host.
    """
    hits = {
        "session": [{"question": "q1", "answer": "a1", "time": "t"}],
        "trace": [{"source": "trace", "origin_function": "Bash", "status": "ok"}],
        "session_context": [{"source": "session_context", "content": "standing guidance"}],
        "graph": [{"source": "graph", "content": "graph fact"}],
    }
    delays = {"session": 0.3, "trace": 0.2, "session_context": 0.1, "graph": 0.0}

    def staggered(_prompt, **kw):
        scope = kw["scope"][0]
        time.sleep(delays[scope])
        return list(hits[scope])

    def body(run) -> str:
        text = run.output["hookSpecificOutput"]["additionalContext"]
        return text.split("\n", 1)[1]

    instant = drive_recall(lookup, monkeypatch, recall=hits)
    shuffled = drive_recall(lookup, monkeypatch, recall=staggered)

    assert body(shuffled) == body(instant)
    context = body(shuffled)
    positions = [
        context.index("=== Active agent guidance ==="),
        context.index("=== Knowledge graph snapshot ==="),
        context.index("=== Prior agent trace ==="),
        context.index("=== Prior session turns ==="),
    ]
    assert positions == sorted(positions), context
    for needle in ("standing guidance", "graph fact", "[trace] Bash — ok", "Q: q1"):
        assert needle in context, context


def test_the_prompt_hook_does_not_drain_the_warmup_buffer(lookup):
    """#298: draining here would stall the prompt for 10-30s.

    The drain belongs to the asynchronous sibling (store-user-prompt). Pinned by
    absence — the synchronous hook must not even carry the function.
    """
    assert not hasattr(lookup, "drain_warmup_entries")
