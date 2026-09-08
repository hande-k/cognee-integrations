import pytest


def test_imports():
    from cognee_integration_claude import (
        cognee_tools,
        recall,
        remember,
        render_results,
    )

    assert remember is not None
    assert recall is not None
    assert render_results is not None
    assert cognee_tools is not None


def test_cognee_tools_returns_remember_and_recall():
    from cognee_integration_claude import cognee_tools

    tools = cognee_tools()
    assert len(tools) == 3

    sessioned = cognee_tools("test-session")
    assert len(sessioned) == 3


def test_render_results_handles_each_source():
    from types import SimpleNamespace

    from cognee_integration_claude import render_results

    results = [
        SimpleNamespace(source="graph", text="graph hit"),
        SimpleNamespace(source="session", answer="ans", question="q"),
        SimpleNamespace(source="graph_context", content="ctx"),
        SimpleNamespace(source="trace", memory_context="trace blob"),
    ]
    assert render_results(results) == ["graph hit", "ans", "ctx", "trace blob"]
    assert render_results(None) == []
    assert render_results([]) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("inherited", [True, False])
async def test_source_tool_uses_sdk_routing_and_bound_identity(monkeypatch, inherited):
    import json
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    import cognee
    from cognee_integration_claude import cognee_tools

    method = AsyncMock(return_value={"evidence": [{"retrieval_method": "sql"}]})
    monkeypatch.setattr(cognee, "sources", SimpleNamespace(search=method), raising=False)
    user = object()
    tools = cognee_tools(
        "session",
        **(
            {"recall_kwargs": {"user": user}}
            if inherited
            else {"source_search_kwargs": {"user": user}}
        ),
    )
    result = await tools[2].handler({"query": "counts", "source_hint": "arbitrary source"})
    assert json.loads(result["content"][0]["text"])["evidence"][0]["retrieval_method"] == "sql"
    method.assert_awaited_once_with("counts", source_hint="arbitrary source", user=user)
