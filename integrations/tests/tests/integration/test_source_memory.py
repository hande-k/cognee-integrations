"""Real HTTP proves source reads retain the plugin credential and strict filters."""

import json
from uuid import uuid4

import pytest
from werkzeug.wrappers import Response


@pytest.fixture
def memory(suite, hook_module, monkeypatch, httpserver):
    module = hook_module(suite, "cognee-memory.py")
    url = httpserver.url_for("").rstrip("/")
    monkeypatch.setenv("COGNEE_BASE_URL", url)
    monkeypatch.setenv("COGNEE_API_KEY", "owner-test-key")
    module.pc.save_cached_agent_key(
        url, "agent-test-key", "agent-id", principal_key="owner-test-key"
    )
    return module


def test_filtered_search_sends_agent_key_and_no_session_binding(memory, httpserver):
    dataset, document = str(uuid4()), str(uuid4())
    headers = {"X-Api-Key": "agent-test-key"}
    httpserver.expect_request(
        f"/api/v1/datasets/source-document/{dataset}/{document}", headers=headers
    ).respond_with_json({"id": document, "dataset_id": dataset, "node_sets": ["arbitrary:source"]})
    captured = []

    def search(request):
        captured.append(request.json)
        return Response(
            json.dumps(
                [
                    {
                        "objects_result": [
                            {
                                "payload": {
                                    "document_id": document,
                                    "text": "A stored source passage",
                                }
                            }
                        ]
                    }
                ]
            ),
            content_type="application/json",
        )

    httpserver.expect_request(
        "/api/v1/search", method="POST", headers=headers
    ).respond_with_handler(search)
    args = memory.parser().parse_args(
        ["search", "what happened", "--dataset-id", dataset, "--node-set", "arbitrary:source"]
    )
    output = memory.run(args)
    assert output["evidence"][0]["text"] == "A stored source passage"
    assert captured[0]["node_name"] == ["arbitrary:source"]
    assert "session_id" not in captured[0]
    httpserver.check_assertions()


@pytest.mark.parametrize("status", [401, 403])
def test_denied_agent_read_is_terminal(memory, httpserver, capsys, status):
    keys = []

    def denied(request):
        keys.append(request.headers.get("X-Api-Key"))
        return Response("denied", status=status)

    httpserver.expect_request("/api/v1/datasets/source-catalog").respond_with_handler(denied)
    assert memory.main(["sources"]) == 1
    error = json.loads(capsys.readouterr().err)
    assert error["http_status"] == status
    assert error["credential_fallback"] is False
    assert keys == ["agent-test-key"]


def test_original_document_uses_same_identity(memory, httpserver):
    dataset, document = str(uuid4()), str(uuid4())
    headers = {"X-Api-Key": "agent-test-key"}
    httpserver.expect_request(
        f"/api/v1/datasets/source-document/{dataset}/{document}", headers=headers
    ).respond_with_json({"id": document, "dataset_id": dataset, "label": "original"})
    httpserver.expect_request(
        f"/api/v1/datasets/{dataset}/data/{document}/raw", headers=headers
    ).respond_with_data("original stored text")
    result = memory.run(memory.parser().parse_args(["read", document, "--dataset-id", dataset]))
    assert result["text"] == "original stored text"
    httpserver.check_assertions()


def test_status_labels_legacy_user_instead_of_claiming_agent(memory, httpserver, monkeypatch):
    memory.pc.clear_cached_agent_key()
    httpserver.expect_request(
        "/api/v1/users/me", headers={"X-Api-Key": "owner-test-key"}
    ).respond_with_json({"id": "owner-id", "parent_user_id": None})
    result = memory.run(memory.parser().parse_args(["status"]))
    assert result["identity_kind"] == "user"
    assert result["identity"] == "owner-id"


def test_redirect_never_forwards_credential(memory, httpserver):
    httpserver.expect_request("/api/v1/users/me").respond_with_data(
        "", status=302, headers={"Location": "/credential-leak"}
    )
    with pytest.raises(memory.MemoryError, match="redirect"):
        memory.run(memory.parser().parse_args(["status"]))


def test_blocked_identity_cannot_fall_back_to_cached_owner(memory):
    memory.pc.block_cached_agent_key("agent-test-key")
    with pytest.raises(RuntimeError, match="reconnect"):
        memory.resolve()


def test_source_sql_result_uses_agent_key_without_chunk_fallback(memory, httpserver):
    expected = {
        "evidence": [
            {
                "retrieval_method": "sql",
                "structured": {"sql": "SELECT COUNT(*) FROM orders", "rows": [{"count": 9}]},
            }
        ],
        "errors": [],
        "coverage": {"complete": False},
    }
    httpserver.expect_request(
        "/api/v1/datasets/source-search", method="POST", headers={"X-Api-Key": "agent-test-key"}
    ).respond_with_json(expected)
    args = memory.parser().parse_args(
        ["search", "How many orders?", "--source", "arbitrary warehouse"]
    )
    assert memory.run(args) == expected
    httpserver.check_assertions()
