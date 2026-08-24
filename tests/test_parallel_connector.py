"""Production-schema regressions for the Parallel Search MCP connector."""

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from src.connectors.parallel import ParallelConnector


REAL_RESULTS = {
    "results": [
        {
            "url": "https://example.com/evidence",
            "excerpts": ["real first", "real second"],
            "title": "Evidence title",
        },
        {
            "url": "https://example.com/untitled",
            "excerpts": ["other evidence"],
            "title": None,
        },
    ]
}


class StrictClient:
    """Fake only the actual hosted tool name and argument schema."""

    calls = []
    response = SimpleNamespace(
        structured_content=REAL_RESULTS,
        content=[SimpleNamespace(text=json.dumps({"results": []}))],
        is_error=False,
    )

    def __init__(self, url):
        assert url == "https://search.parallel.ai/mcp"

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def call_tool(self, name, arguments):
        assert name == "web_search"
        assert arguments == {
            "objective": "schema check",
            "search_queries": ["schema check"],
        }
        self.calls.append((name, arguments))
        return self.response


@pytest.mark.unit
@pytest.mark.asyncio
async def test_real_tool_schema_joins_excerpts_and_prefers_structured(monkeypatch):
    monkeypatch.setattr("src.connectors.parallel.Client", StrictClient)
    StrictClient.calls.clear()

    result = await ParallelConnector(enabled=True).search("schema check", top_k=10)

    assert StrictClient.calls == [
        (
            "web_search",
            {"objective": "schema check", "search_queries": ["schema check"]},
        )
    ]
    assert result.sources[0].content == "real first\n\nreal second"
    assert result.sources[0].content
    assert result.sources[1].title == "https://example.com/untitled"
    assert len(result.sources) == 2  # text fallback was not appended


@pytest.mark.unit
@pytest.mark.asyncio
async def test_text_fallback_and_nonnegative_local_top_k(monkeypatch):
    class TextClient(StrictClient):
        response = SimpleNamespace(
            structured_content=None,
            content=[SimpleNamespace(text=json.dumps(REAL_RESULTS))],
            is_error=False,
        )

    monkeypatch.setattr("src.connectors.parallel.Client", TextClient)

    populated = await ParallelConnector(enabled=True).search("schema check", top_k=1)
    empty = await ParallelConnector(enabled=True).search("schema check", top_k=-1)

    assert [source.content for source in populated.sources] == ["real first\n\nreal second"]
    assert empty.sources == []


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        SimpleNamespace(structured_content=REAL_RESULTS, content=[], is_error=True),
        SimpleNamespace(structured_content={"not_results": []}, content=[], is_error=False),
        SimpleNamespace(structured_content={"results": [{"url": "x", "excerpts": "not-an-array"}]}, content=[], is_error=False),
        SimpleNamespace(structured_content=None, content=[SimpleNamespace(text="not-json")], is_error=False),
    ],
)
async def test_protocol_and_malformed_fail_loudly_without_request_log(monkeypatch, caplog, response):
    class BrokenClient(StrictClient):
        pass

    BrokenClient.response = response
    monkeypatch.setattr("src.connectors.parallel.Client", BrokenClient)

    with pytest.raises(RuntimeError, match="Parallel MCP request failed"):
        await ParallelConnector(enabled=True).search("schema check")

    assert "schema check" not in caplog.text
    assert "https://search.parallel.ai/mcp" not in caplog.text


@pytest.mark.unit
def test_default_and_user_configuration_are_preserved(monkeypatch):
    monkeypatch.setattr("src.config.settings.parallel_mcp_enabled", False)
    monkeypatch.setattr("src.config.settings.parallel_mcp_url", "https://search.parallel.ai/mcp")
    assert not ParallelConnector().is_configured()

    custom = ParallelConnector(enabled=True, url="https://user.example/mcp")
    assert custom.is_configured()
    assert custom.url == "https://user.example/mcp"
    assert not hasattr(custom, "headers")


@pytest.mark.unit
def test_search_api_exposes_nonempty_parallel_evidence(monkeypatch, tmp_path):
    monkeypatch.setattr("src.config.settings.llm_api_key", "test-key")
    monkeypatch.setattr("src.config.settings.parallel_mcp_enabled", True)
    monkeypatch.setattr("src.connectors.parallel.Client", StrictClient)
    monkeypatch.setattr("src.api.routes.cache.get", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("src.api.routes.cache.set", lambda *_args, **_kwargs: None)
    from src.main import app

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/search",
            json={"query": "schema check", "top_k": 2, "connectors": ["parallel"]},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["connectors_used"] == ["parallel"]
    assert payload["sources"][0]["content"] == "real first\n\nreal second"
    assert payload["sources"][0]["content"]
