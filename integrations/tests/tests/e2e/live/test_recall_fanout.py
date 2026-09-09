"""The concurrent recall fan-out against a real server (local or cloud).

Every prompt now fires its recall scopes at once instead of one after another.
The unit tier pins the mechanics against fakes; this tier asks the two
questions only a real backend can answer:

* **Does a burst of concurrent requests get throttled or refused?** Serial
  dispatch never produced one, so a 429 (or any per-scope error) on a healthy
  server is the regression to catch — and the cloud tenant is where it would
  show first.
* **Do the scopes actually overlap end to end?** The hook's own aggregate
  ``elapsed_ms`` must track the slowest scope, not the sum of all of them.

Both backends run this module. The parallel-sessions scenario is local-only:
against a cloud tenant several sessions share the tenant's rate limits, which
is the burst question again rather than the graph-store question it asks here.
"""

from __future__ import annotations

import concurrent.futures

import pytest
from utils.live import hook_events

pytestmark = pytest.mark.live

#: Recalls issued back to back in one session — five bursts of four or five
#: concurrent requests, which is what a user typing five prompts produces.
BURSTS = 5


def _recall_summaries(suite, home) -> list[dict]:
    return [
        d
        for e, d in hook_events(suite, home)
        if e in ("context_lookup_hit", "context_lookup_empty")
    ]


def _scope_errors(suite, home) -> list[dict]:
    return [d for e, d in hook_events(suite, home) if e == "recall_error"]


def test_a_burst_of_concurrent_scopes_is_neither_throttled_nor_refused(
    started_session, live_suite, live_home, nonce
):
    """Five prompts' worth of fan-out on a healthy server: zero scope errors."""
    session = started_session("fanout")
    session.prompt(f"Project {nonce} uses a three-node quorum.", turn_id="t1")
    session.answer(f"Noted: {nonce} uses a three-node quorum.", turn_id="t1")

    for i in range(BURSTS):
        run = session.recall(f"What do we know about {nonce}? (round {i})", turn_id=f"r{i}")
        assert run.ok, f"recall {i} failed (rc={run.returncode}): {run.stderr[:500]}"

    errors = _scope_errors(live_suite, live_home)
    throttled = [d for d in errors if "429" in str(d.get("error", ""))]
    assert not throttled, f"the concurrent fan-out was rate-limited: {throttled}"
    # A fresh dataset's graph scope may answer 404 until the first cognify; that
    # is recorded separately (recall_graph_not_built), so anything here is real.
    assert not errors, f"scope errors on a healthy server: {errors}"

    summaries = _recall_summaries(live_suite, live_home)
    assert len(summaries) >= BURSTS, summaries
    for summary in summaries[-BURSTS:]:
        skipped = [k for k, r in summary["per_scope"].items() if r.get("skipped")]
        assert not skipped, f"scopes were never dispatched: {skipped} in {summary}"


def test_the_recall_costs_the_slowest_scope_not_the_sum(
    started_session, live_suite, live_home, nonce
):
    """The aggregate elapsed_ms must sit near max(per_scope), far from sum(per_scope).

    The bound below holds for a concurrent fan-out with up to half a second of
    overhead beyond the slowest scope, and fails for a sequential one whenever
    the cheap scopes add more than ~1s in total — which they do against a remote
    server (three or four round trips), and which the code lane does anywhere.
    """
    session = started_session("fanout-timing")
    session.prompt(f"Service {nonce} retries with exponential backoff.", turn_id="t1")
    session.answer(f"Noted: {nonce} retries with backoff.", turn_id="t1")

    run = session.recall(f"How does {nonce} retry?", turn_id="t2")
    assert run.ok, run.stderr[:500]

    summary = _recall_summaries(live_suite, live_home)[-1]
    per_scope = {k: r for k, r in summary["per_scope"].items() if not r.get("skipped")}
    assert len(per_scope) >= 4, summary
    slowest = max(r["elapsed_ms"] for r in per_scope.values())
    summed = sum(r["elapsed_ms"] for r in per_scope.values())
    total = summary["elapsed_ms"]
    assert total <= slowest + 0.5 * (summed - slowest) + 500, (
        f"recall took {total}ms; slowest scope {slowest}ms, sum {summed}ms — "
        "the scopes did not overlap"
    )


@pytest.mark.local_only
def test_parallel_sessions_recall_at_once_without_errors(
    started_session, live_suite, live_home, nonce
):
    """Three terminals prompting at the same moment: 12+ concurrent graph-store reads.

    Each hook already fans out; several sessions multiply that against one local
    Ladybug. Each session first captures its own fact, so its recall has
    something to find in its own session cache without waiting on a cognify:
    every hook must exit clean, every scope must have run, no scope may report
    an error (a locked or contended store would surface here), and every one of
    the concurrent recalls must actually return its own fact.
    """
    sessions = [started_session(f"fanout-par-{i}") for i in range(3)]
    for i, session in enumerate(sessions):
        session.prompt(f"Session {i} of {nonce} ships on Tuesdays.", turn_id="t1")
        session.answer(f"Noted: session {i} of {nonce} ships on Tuesdays.", turn_id="t1")

    def recall(pair):
        i, session = pair
        return session.recall(f"When does session {i} of {nonce} ship?", turn_id="t2")

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(sessions)) as pool:
        runs = list(pool.map(recall, enumerate(sessions)))

    for i, run in enumerate(runs):
        assert run.ok, f"parallel recall {i} failed (rc={run.returncode}): {run.stderr[:500]}"

    errors = _scope_errors(live_suite, live_home)
    assert not errors, f"scope errors under parallel sessions: {errors}"
    summaries = _recall_summaries(live_suite, live_home)[-len(sessions) :]
    assert len(summaries) == len(sessions), summaries
    for summary in summaries:
        assert all(not r.get("skipped") for r in summary["per_scope"].values()), summary
        found = sum(int(r.get("hits") or 0) for r in summary["per_scope"].values())
        assert found > 0, (
            f"a concurrent recall came back empty for its own captured turn: {summary}"
        )
