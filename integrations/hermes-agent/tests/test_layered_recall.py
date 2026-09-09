"""The layered per-prompt recall and the memory-hit header.

With ``dataset_ids`` + ``search_type`` in a single request the server's
``auto`` scope resolves graph-only, so cached Q&A turns, trace lessons and
distilled agent guidance never reached the prompt. The layered fan-out runs
one bounded call per scope, all lanes dispatched concurrently under one shared
deadline, and renders each layer as its own labelled block in canonical
order. Run standalone with ``python3 tests/test_layered_recall.py``.
"""

import asyncio
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from _char_helpers import fake_backend, fake_cognee, make_provider  # noqa: E402
from cognee_integration_hermes.backend import SdkBackend  # noqa: E402

_LAYERED = {"recall_session_layers": True, "recall_budget": 20}


def _settle(provider, timeout=5.0):
    thread = provider._prefetch_thread
    if thread is not None:
        thread.join(timeout=timeout)


def _prefetch(provider, query="q"):
    provider.queue_prefetch(query)
    _settle(provider)
    return provider.prefetch(query)


class TestLayeredFanOut(unittest.TestCase):
    def test_one_call_per_scope(self):
        with fake_backend() as fake:
            provider = make_provider(config=_LAYERED)
            _prefetch(provider)
            scopes = sorted(tuple(kwargs["scope"]) for kwargs in fake.kwargs_for("recall"))
        # Dispatched concurrently, so only the set of lanes is pinned, not an order.
        self.assertEqual(
            scopes, sorted([("session",), ("trace",), ("session_context",), ("graph",)])
        )

    def test_lanes_run_concurrently_under_one_deadline(self):
        # Four lanes sleeping 0.3s each must cost ~0.3s, not 1.2s, and every lane
        # must be handed the same deadline: min(recall_timeout, budget).
        with fake_backend() as fake:
            original = fake.recall

            def slow(**kwargs):
                time.sleep(0.3)
                return original(**kwargs)

            fake.recall = slow
            provider = make_provider(config={**_LAYERED, "recall_timeout": 5, "recall_budget": 2})
            started = time.monotonic()
            _prefetch(provider)
            wall = time.monotonic() - started
            timeouts = {kwargs["timeout"] for kwargs in fake.kwargs_for("recall")}
        self.assertEqual(len(fake.kwargs_for("recall")), 4)
        self.assertLess(wall, 0.9, f"lanes ran back to back: {wall:.2f}s for 4 x 0.3s")
        self.assertEqual(len(timeouts), 1, timeouts)
        self.assertLessEqual(max(timeouts), 2.0)
        self.assertGreater(max(timeouts), 1.5)

    def test_blocks_are_rendered_in_canonical_order_whatever_answers_first(self):
        with fake_backend() as fake:
            original = fake.recall
            delays = {
                ("session",): 0.25,
                ("trace",): 0.15,
                ("session_context",): 0.05,
                ("graph",): 0.0,
            }

            def staggered(**kwargs):
                time.sleep(delays[tuple(kwargs["scope"])])
                original(**kwargs)
                return [{"text": f"from {kwargs['scope'][0]}"}]

            fake.recall = staggered
            provider = make_provider(config={**_LAYERED, "memory_hits": False})
            out = _prefetch(provider)
        order = [
            out.index(tag)
            for tag in ("<session_memory>", "<trace_lessons>", "<agent_guidance>", "<graph_memory>")
        ]
        self.assertEqual(order, sorted(order), out)

    def test_graph_lane_uses_hybrid_completion_and_only_context(self):
        with fake_backend() as fake:
            provider = make_provider(config=_LAYERED)
            _prefetch(provider)
            calls = fake.kwargs_for("recall")
        by_scope = {tuple(kwargs["scope"]): kwargs for kwargs in calls}
        self.assertEqual(by_scope[("graph",)]["query_type"], "HYBRID_COMPLETION")
        self.assertTrue(all(kwargs["only_context"] for kwargs in calls))
        # The session_context lane asks for the distilled agent rendering.
        self.assertEqual(by_scope[("session_context",)]["context_profile"], "agent")
        self.assertIsNone(by_scope[("session",)]["context_profile"])

    def test_every_lane_targets_the_plugin_dataset_and_session(self):
        with fake_backend() as fake:
            provider = make_provider(config=_LAYERED)
            _prefetch(provider)
            for kwargs in fake.kwargs_for("recall"):
                self.assertEqual(kwargs["datasets"], ["hermes"])
                self.assertEqual(kwargs["session_id"], "hermes_s-1")

    def test_results_are_rendered_as_labelled_blocks(self):
        with fake_backend() as fake:
            fake.results["recall"] = [{"text": "remembered", "source": "x"}]
            provider = make_provider(config={**_LAYERED, "memory_hits": False})
            out = _prefetch(provider)
        for label in ("<session_memory>", "<trace_lessons>", "<agent_guidance>", "<graph_memory>"):
            self.assertIn(label, out)
        self.assertIn("</session_memory>", out)

    def test_a_failing_lane_does_not_discard_the_others(self):
        with fake_backend() as fake:
            calls = {"n": 0}
            original = fake.recall

            def flaky(**kwargs):
                calls["n"] += 1
                if kwargs["scope"] == ["trace"]:
                    raise RuntimeError("trace lane down")
                original(**kwargs)
                return [{"text": "kept"}]

            fake.recall = flaky
            provider = make_provider(config={**_LAYERED, "memory_hits": False})
            out = _prefetch(provider)
        self.assertIn("<session_memory>", out)
        self.assertIn("<graph_memory>", out)
        self.assertNotIn("<trace_lessons>", out)

    def test_empty_lanes_leave_nothing_cached(self):
        with fake_backend() as fake:
            fake.results["recall"] = []
            provider = make_provider(config=_LAYERED)
            self.assertEqual(_prefetch(provider), "")

    def test_a_graph_404_is_benign_not_a_breaker_failure(self):
        # A dataset nobody has cognified answers the graph scope with 404 on
        # every prompt of a fresh install; that must not feed the breaker.
        class _Http404(RuntimeError):
            status = 404

        with fake_backend() as fake:
            original = fake.recall

            def not_built(**kwargs):
                original(**kwargs)
                if kwargs["scope"] == ["graph"]:
                    raise _Http404("graph not built")
                return []

            fake.recall = not_built
            provider = make_provider(config=_LAYERED)
            _prefetch(provider)
        self.assertEqual(provider._consecutive_failures, 0)

    def test_all_lanes_failing_counts_one_breaker_failure(self):
        with fake_backend() as fake:
            fake.errors["recall"] = RuntimeError("down")
            provider = make_provider(config=_LAYERED)
            _prefetch(provider)
        self.assertEqual(provider._consecutive_failures, 1)

    def test_zero_budget_skips_every_lane(self):
        with fake_backend() as fake:
            provider = make_provider(config={**_LAYERED, "recall_budget": 0})
            _prefetch(provider)
            self.assertEqual(fake.kwargs_for("recall"), [])


class TestSdkBackendLanes(unittest.TestCase):
    """The lanes through the in-process SDK transport.

    ``SdkBackend`` hands every call to one dedicated event loop and waits on
    ``future.result(timeout)``. Four lanes submitted from four pool threads must
    interleave as coroutines on that loop — not queue behind each other — and a
    lane that outlives its deadline must fail alone while the loop keeps
    serving the others.
    """

    def _sdk_provider(self, config=None):
        backend = SdkBackend()
        provider = make_provider(backend=backend, config={**_LAYERED, **(config or {})})
        return backend, provider

    def test_lanes_interleave_on_the_single_sdk_loop(self):
        with fake_cognee() as fake:
            original = fake.recall

            async def slow(**kwargs):
                await asyncio.sleep(0.3)
                return await original(**kwargs)

            sys.modules["cognee"].recall = slow
            backend, provider = self._sdk_provider()
            try:
                started = time.monotonic()
                _prefetch(provider)
                wall = time.monotonic() - started
                calls = fake.kwargs_for("recall")
            finally:
                backend.close(unregister=False)
        self.assertEqual(len(calls), 4, calls)
        self.assertLess(wall, 0.9, f"lanes queued behind each other on the loop: {wall:.2f}s")

    def test_a_lane_past_its_deadline_fails_alone(self):
        with fake_cognee() as fake:
            original = fake.recall

            async def one_hangs(**kwargs):
                # The graph lane is the only one that names a search type (the
                # SDK transport maps it onto whatever SearchType the installed
                # cognee has); the session lanes pass None.
                if kwargs.get("query_type") is not None:
                    await asyncio.sleep(3)
                await original(**kwargs)
                return [{"text": f"from {kwargs.get('query_type')}"}]

            sys.modules["cognee"].recall = one_hangs
            backend, provider = self._sdk_provider({"recall_timeout": 0.5, "memory_hits": False})
            try:
                started = time.monotonic()
                out = _prefetch(provider)
                wall = time.monotonic() - started
            finally:
                backend.close(unregister=False)
        self.assertLess(wall, 1.5, f"the hung lane held the prefetch: {wall:.2f}s")
        self.assertIn("<session_memory>", out)
        self.assertNotIn("<graph_memory>", out)
        # One lane timing out while three answered is proof of life, not failure.
        self.assertEqual(provider._consecutive_failures, 0)


class TestCodeLane(unittest.TestCase):
    def test_configured_code_dataset_arms_the_lane_on_identifiers(self):
        with fake_backend() as fake:
            provider = make_provider(config={**_LAYERED, "code_datasets": "codebase-svc-abc123"})
            provider.queue_prefetch("what calls process_payment?")
            _settle(provider)
            scopes = [tuple(kwargs["scope"]) for kwargs in fake.kwargs_for("recall")]
            self.assertIn(("code",), scopes)
            code_call = next(
                kwargs for kwargs in fake.kwargs_for("recall") if kwargs["scope"] == ["code"]
            )
        self.assertEqual(code_call["datasets"], ["codebase-svc-abc123"])
        self.assertEqual(code_call["code_query"]["operation"], "query_facts")
        self.assertEqual(code_call["code_query"]["name"], "process_payment")

    def test_conversational_prompts_never_arm_the_code_lane(self):
        with fake_backend() as fake:
            provider = make_provider(config={**_LAYERED, "code_datasets": "codebase-svc-abc123"})
            provider.queue_prefetch("how are you today")
            _settle(provider)
            scopes = [tuple(kwargs["scope"]) for kwargs in fake.kwargs_for("recall")]
        self.assertNotIn(("code",), scopes)

    def test_code_graph_recall_off_disables_the_lane(self):
        with fake_backend() as fake:
            provider = make_provider(
                config={
                    **_LAYERED,
                    "code_datasets": "codebase-svc-abc123",
                    "code_graph_recall": False,
                }
            )
            provider.queue_prefetch("what calls process_payment?")
            _settle(provider)
            scopes = [tuple(kwargs["scope"]) for kwargs in fake.kwargs_for("recall")]
        self.assertNotIn(("code",), scopes)


class TestMemoryHitHeader(unittest.TestCase):
    def test_header_reports_hits_and_per_session_totals(self):
        with fake_backend() as fake:
            fake.results["recall"] = [{"text": "remembered"}]
            provider = make_provider(config={**_LAYERED, "memory_hits": True})
            out = _prefetch(provider)
        self.assertIn("4 memory hits this turn", out)
        self.assertIn("(2 beyond this session)", out)  # agent_guidance + graph
        self.assertIn("1/1 turns had hits this session", out)

    def test_totals_accumulate_across_turns(self):
        with fake_backend() as fake:
            fake.results["recall"] = []
            provider = make_provider(config={**_LAYERED, "memory_hits": True})
            self.assertEqual(_prefetch(provider), "")  # turn 1: no hits
            fake.results["recall"] = [{"text": "remembered"}]
            out = _prefetch(provider)  # turn 2: hits
        self.assertIn("1/2 turns had hits this session", out)

    def test_reset_session_switch_clears_the_totals(self):
        with fake_backend() as fake:
            fake.results["recall"] = [{"text": "remembered"}]
            provider = make_provider(config={**_LAYERED, "memory_hits": True})
            _prefetch(provider)
            provider.on_session_switch("s-2", reset=True)
        self.assertEqual(provider._turns_seen, 0)
        self.assertEqual(provider._hits_total, 0)

    def test_header_is_absent_when_disabled(self):
        with fake_backend() as fake:
            fake.results["recall"] = [{"text": "remembered"}]
            provider = make_provider(config={**_LAYERED, "memory_hits": False})
            out = _prefetch(provider)
        self.assertNotIn("memory hit", out)


class TestSessionScopeMapping(unittest.TestCase):
    def test_tool_session_scope_covers_the_three_session_layers(self):
        with fake_backend() as fake:
            provider = make_provider(config={"recall_session_layers": True})
            provider.handle_tool_call("cognee_recall", {"query": "q", "scope": "session"})
            kwargs = fake.only_call("recall")
        self.assertEqual(kwargs["scope"], ["session", "trace", "session_context"])
        self.assertEqual(kwargs["datasets"], ["hermes"])

    def test_legacy_session_scope_with_layers_off(self):
        with fake_backend() as fake:
            provider = make_provider(config={"recall_session_layers": False})
            provider.handle_tool_call("cognee_recall", {"query": "q", "scope": "session"})
            kwargs = fake.only_call("recall")
        self.assertEqual(kwargs["scope"], "session")
        self.assertIsNone(kwargs["datasets"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
