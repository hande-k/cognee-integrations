"""Generic routing and target selection for both plugin distributions."""

from uuid import uuid4

import pytest


@pytest.fixture
def memory(suite, hook_module):
    return hook_module(suite, "cognee-memory.py")


def test_source_hint_is_arbitrary_metadata_not_a_provider_enum(memory):
    args = memory.parser().parse_args(
        ["search", "deployment rules", "--source", "Zephyr field notes"]
    )
    calls = []

    class Client:
        def request(self, path, payload=None):
            calls.append((path, payload))
            return {"targets": [], "status": "inconclusive"}

    runtime = {"dataset": "sessions", "api_key": "agent"}
    memory.route(Client(), args, runtime)
    assert calls[-1][1]["source_hint"] == "Zephyr field notes"
    assert calls[-1][1]["dataset_ids"] is None


def test_inconclusive_routing_is_not_an_empty_content_search(memory):
    args = memory.parser().parse_args(["search", "a vague question", "--all-readable"])
    calls = []

    class Client:
        def request(self, path, payload=None):
            calls.append(path)
            return {"targets": [], "status": "inconclusive"}

    result = memory.search(Client(), args, {"dataset": "sessions"})
    assert calls == ["/api/v1/datasets/source-route"]
    assert "not a no-results answer" in result["next_step"]
    assert result["coverage"]["complete"] is False


def test_explicit_selection_wins_over_saved_scope(memory, monkeypatch):
    chosen = str(uuid4())
    monkeypatch.setattr(memory.pc, "load_graph_read_scope", lambda: [str(uuid4())])
    args = memory.parser().parse_args(["search", "q", "--dataset-id", chosen])
    assert memory.selection(args, {"dataset": "sessions"}) == [chosen]


def test_persistent_read_selection_survives_new_launch(memory, monkeypatch):
    import importlib.util

    pc = memory.pc
    spec = importlib.util.spec_from_file_location(
        "access", memory.__file__.replace("cognee-memory.py", "memory-access.py")
    )
    access = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(access)
    ident = str(uuid4())
    monkeypatch.setenv("COGNEE_API_KEY", "first")
    monkeypatch.setattr(pc, "_json_http_request", lambda *a, **kw: [{"id": ident}])
    access.set_read_scope("", [ident], persistent=True)
    pc.set_session_key("brand-new-launch")
    assert pc.load_graph_read_scope() == [ident]
    monkeypatch.setenv("COGNEE_API_KEY", "second")
    assert pc.load_graph_read_scope() is None


@pytest.mark.parametrize("operator", ["any", "all"])
def test_multiple_node_sets_preserve_requested_operator(memory, operator):
    dataset, document = str(uuid4()), str(uuid4())
    calls = []

    class Client:
        def request(self, path, payload=None):
            calls.append((path, payload))
            if "/source-document/" in path:
                return {"id": document, "dataset_id": dataset, "node_sets": ["topic:a", "topic:b"]}
            return [{"objects_result": [{"payload": {"document_id": document, "text": "hit"}}]}]

    args = memory.parser().parse_args(
        [
            "search",
            "q",
            "--dataset-id",
            dataset,
            "--node-set",
            "topic:a",
            "--node-set",
            "topic:b",
            "--node-match",
            operator,
        ]
    )
    result = memory.search(Client(), args, {"dataset": "sessions"})
    assert result["evidence"][0]["document_id"] == document
    assert calls[0][1]["node_name_filter_operator"] == ("OR" if operator == "any" else "AND")
    assert "session_id" not in calls[0][1]


def test_invalid_server_filter_result_fails_closed(memory):
    dataset, document = str(uuid4()), str(uuid4())

    class Client:
        def request(self, path, payload=None):
            if "/source-document/" in path:
                return {"id": document, "dataset_id": dataset, "node_sets": ["other"]}
            return [{"objects_result": [{"payload": {"document_id": document, "text": "hit"}}]}]

    args = memory.parser().parse_args(
        ["search", "q", "--dataset-id", dataset, "--node-set", "wanted"]
    )
    with pytest.raises(memory.MemoryError, match="violates"):
        memory.search(Client(), args, {"dataset": "sessions"})


def test_general_question_discovers_all_readable_unless_scoped(memory, monkeypatch):
    write = str(uuid4())
    chosen = str(uuid4())
    args = memory.parser().parse_args(["search", "a general question"])
    monkeypatch.setattr(memory.pc, "load_graph_read_scope", lambda: None)
    assert memory.readable_selection(None, args, {"dataset": write}) is None
    monkeypatch.setattr(memory.pc, "load_graph_read_scope", lambda: [chosen])
    assert memory.readable_selection(None, args, {"dataset": write}) == [chosen]
    args.all_readable = True
    assert memory.readable_selection(None, args, {"dataset": write}) is None


def test_explicit_write_only_selection_is_not_widened(memory, monkeypatch):
    write = str(uuid4())
    monkeypatch.setattr(memory.pc, "load_graph_read_scope", lambda: [])
    args = memory.parser().parse_args(["search", "q"])
    assert memory.readable_selection(None, args, {"dataset": write}) == [write]
