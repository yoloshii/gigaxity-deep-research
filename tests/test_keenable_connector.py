"""Tests for the Keenable Search connector."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.connectors import KeenableConnector
from src.connectors.keenable import API_URL, MAX_RESULTS, SNIPPET_MAX_CHARS


def _client_returning(payload):
    resp = MagicMock()
    resp.json.return_value = payload
    resp.raise_for_status.return_value = None
    client = MagicMock()
    client.post = AsyncMock(return_value=resp)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=client)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx, client


PAYLOAD = {
    "results": [
        {"title": "A", "url": "https://a.example/", "description": "desc", "snippet": "s"},
        {"title": "B", "url": "https://b.example/", "snippet": "x" * 900},
        {"title": "no url", "description": "dropped"},
    ]
}


def test_unconfigured_without_key(monkeypatch):
    monkeypatch.setattr("src.config.settings.keenable_api_key", "")
    assert KeenableConnector().is_configured() is False
    assert KeenableConnector(api_key="k").is_configured() is True


@pytest.mark.asyncio
async def test_search_posts_query_and_maps_results():
    ctx, client = _client_returning(PAYLOAD)
    with patch("src.connectors.keenable.httpx.AsyncClient", return_value=ctx):
        result = await KeenableConnector(api_key="kn-key").search("rag benchmarks", top_k=50)

    args, kwargs = client.post.call_args
    assert args[0] == API_URL
    assert kwargs["headers"]["X-API-Key"] == "kn-key"
    assert kwargs["json"] == {"query": "rag benchmarks", "max_results": MAX_RESULTS}
    assert [s.url for s in result.sources] == ["https://a.example/", "https://b.example/"]
    assert result.sources[0].content == "desc"
    assert result.sources[1].content == "x" * SNIPPET_MAX_CHARS
    assert result.sources[0].score > result.sources[1].score
    assert all(s.connector == "keenable" for s in result.sources)


@pytest.mark.asyncio
async def test_search_errors_degrade_to_empty():
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(side_effect=RuntimeError("boom"))
    ctx.__aexit__ = AsyncMock(return_value=False)
    with patch("src.connectors.keenable.httpx.AsyncClient", return_value=ctx):
        result = await KeenableConnector(api_key="k").search("q")
    assert result.sources == []
