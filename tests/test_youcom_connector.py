"""Tests for the You.com Search connector.

You.com is another keyed-API general-web lane (own index behind
ydc-index.io/v1/search), so like Brave it cannot be served a bot-block page
under automated load. These mirror the Brave connector tests: config gating,
live response-shape mapping, key hygiene, and failure isolation.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.connectors import YouComConnector
from src.connectors.youcom import API_URL, MAX_COUNT


def _response(payload):
    """Build a mock httpx response returning `payload` from .json()."""
    resp = MagicMock()
    resp.json.return_value = payload
    resp.raise_for_status.return_value = None
    return resp


def _client_returning(resp):
    """Patchable async context manager whose .post() returns `resp`."""
    client = MagicMock()
    client.post = AsyncMock(return_value=resp)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=client)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx, client


# Shape mirrors a real ydc-index.io/v1/search response.
LIVE_SHAPE = {
    "results": {
        "web": [
            {
                "title": "7 RAG benchmarks",
                "url": "https://www.evidentlyai.com/blog/rag-benchmarks",
                "description": "We highlight seven RAG benchmarks.",
                "snippets": ["RAG benchmarks covered include RAGAS and ARES."],
                "page_age": "2025-05-06T00:00:00",
            },
            {
                "title": "Evaluation of RAG: A Survey",
                "url": "https://arxiv.org/abs/2405.07437",
                "description": "A survey of RAG evaluation.",
                "snippets": [],
                "page_age": "2024-05-13T00:00:00",
            },
        ],
        "news": [
            {
                "title": "RAG adoption grows",
                "url": "https://news.example.com/rag",
                "description": "Enterprises adopt retrieval-augmented generation.",
                "page_age": "2025-11-15T14:00:00",
            },
        ],
    },
    "metadata": {
        "search_uuid": "942ccbdd-7705-4d9c-9d37-4ef386658e90",
        "query": "rag benchmarks",
        "latency": 0.342,
    },
}


class TestYouComConnectorBasics:

    @pytest.mark.unit
    def test_connector_name(self):
        assert YouComConnector(api_key="k").name == "youcom"

    @pytest.mark.unit
    def test_is_configured_with_key(self):
        assert YouComConnector(api_key="k").is_configured() is True

    @pytest.mark.unit
    def test_is_not_configured_without_key(self, monkeypatch):
        monkeypatch.setattr("src.config.settings.youcom_api_key", "")
        assert YouComConnector(api_key="").is_configured() is False

    @pytest.mark.unit
    def test_probe_url_does_not_spend_a_query(self):
        """Health probe must hit the API root, never the billed search path."""
        probe = YouComConnector(api_key="k")._probe_url()
        assert probe == "https://ydc-index.io"
        assert "/v1/search" not in probe

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_unconfigured_returns_empty_without_network(self):
        """An unset key must short-circuit before any HTTP call."""
        with patch("src.connectors.youcom.httpx.AsyncClient") as client_cls:
            result = await YouComConnector(api_key="").search("anything")
        client_cls.assert_not_called()
        assert result.sources == []
        assert result.connector_name == "youcom"


class TestYouComConnectorParsing:

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_maps_live_response_shape(self):
        ctx, _ = _client_returning(_response(LIVE_SHAPE))
        with patch("src.connectors.youcom.httpx.AsyncClient", return_value=ctx):
            result = await YouComConnector(api_key="k").search("rag benchmarks")

        assert len(result.sources) == 2
        assert result.total_results == 2
        first = result.sources[0]
        assert first.title == "7 RAG benchmarks"
        assert first.url == "https://www.evidentlyai.com/blog/rag-benchmarks"
        assert first.content == "We highlight seven RAG benchmarks."
        assert first.connector == "youcom"
        assert first.metadata["published_date"] == "2025-05-06T00:00:00"

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_news_results_are_not_mixed_into_web_sources(self):
        """The connector reads results.web only; news is a separate array."""
        ctx, _ = _client_returning(_response(LIVE_SHAPE))
        with patch("src.connectors.youcom.httpx.AsyncClient", return_value=ctx):
            result = await YouComConnector(api_key="k").search("rag benchmarks")

        urls = [s.url for s in result.sources]
        assert "https://news.example.com/rag" not in urls

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_snippet_used_when_description_missing(self):
        payload = {
            "results": {
                "web": [
                    {
                        "title": "No description",
                        "url": "https://example.com/nd",
                        "description": None,
                        "snippets": ["First snippet text."],
                    }
                ]
            }
        }
        ctx, _ = _client_returning(_response(payload))
        with patch("src.connectors.youcom.httpx.AsyncClient", return_value=ctx):
            result = await YouComConnector(api_key="k").search("q")

        assert result.sources[0].content == "First snippet text."

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_source_ids_are_youcom_prefixed_and_distinct(self):
        ctx, _ = _client_returning(_response(LIVE_SHAPE))
        with patch("src.connectors.youcom.httpx.AsyncClient", return_value=ctx):
            result = await YouComConnector(api_key="k").search("q")

        ids = [s.id for s in result.sources]
        assert all(i.startswith("yc_") for i in ids)
        assert len(set(ids)) == len(ids)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_scores_descend_by_rank(self):
        ctx, _ = _client_returning(_response(LIVE_SHAPE))
        with patch("src.connectors.youcom.httpx.AsyncClient", return_value=ctx):
            result = await YouComConnector(api_key="k").search("q")

        assert result.sources[0].score > result.sources[1].score

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_missing_web_key_is_zero_results_not_a_crash(self):
        """A response without `web` must fuse as a quiet non-contributor."""
        ctx, _ = _client_returning(_response({"results": {}}))
        with patch("src.connectors.youcom.httpx.AsyncClient", return_value=ctx):
            result = await YouComConnector(api_key="k").search("q")

        assert result.sources == []
        assert result.total_results == 0

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_null_results_key_is_handled(self):
        """`results: null` must behave like an absent key, not raise."""
        ctx, _ = _client_returning(_response({"results": None}))
        with patch("src.connectors.youcom.httpx.AsyncClient", return_value=ctx):
            result = await YouComConnector(api_key="k").search("q")

        assert result.sources == []


class TestYouComConnectorRequestContract:

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_count_is_clamped_to_connector_maximum(self):
        """top_k beyond MAX_COUNT must be clamped, not forwarded."""
        ctx, client = _client_returning(_response(LIVE_SHAPE))
        with patch("src.connectors.youcom.httpx.AsyncClient", return_value=ctx):
            await YouComConnector(api_key="k").search("q", top_k=500)

        payload = client.post.call_args.kwargs["json"]
        assert payload["count"] == MAX_COUNT

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_count_floor_is_one(self):
        ctx, client = _client_returning(_response(LIVE_SHAPE))
        with patch("src.connectors.youcom.httpx.AsyncClient", return_value=ctx):
            await YouComConnector(api_key="k").search("q", top_k=0)

        assert client.post.call_args.kwargs["json"]["count"] == 1

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_key_is_sent_as_api_key_header(self):
        ctx, client = _client_returning(_response(LIVE_SHAPE))
        with patch("src.connectors.youcom.httpx.AsyncClient", return_value=ctx):
            await YouComConnector(api_key="secret-key").search("q")

        kwargs = client.post.call_args.kwargs
        assert kwargs["headers"]["X-API-Key"] == "secret-key"
        assert kwargs["headers"]["Accept"] == "application/json"
        # The key must never leak into the request body or URL.
        assert "secret-key" not in str(kwargs["json"])
        assert client.post.call_args.args[0] == API_URL

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_safesearch_is_forwarded(self):
        ctx, client = _client_returning(_response(LIVE_SHAPE))
        with patch("src.connectors.youcom.httpx.AsyncClient", return_value=ctx):
            await YouComConnector(api_key="k", safesearch="strict").search("q")

        assert client.post.call_args.kwargs["json"]["safesearch"] == "strict"


class TestYouComConnectorFailureIsolation:

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_http_error_is_absorbed_not_raised(self):
        """A failing connector must degrade to zero sources so RRF fusion survives."""
        ctx = MagicMock()
        client = MagicMock()
        client.post = AsyncMock(side_effect=Exception("429 rate limited"))
        ctx.__aenter__ = AsyncMock(return_value=client)
        ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("src.connectors.youcom.httpx.AsyncClient", return_value=ctx):
            result = await YouComConnector(api_key="k").search("q")

        assert result.sources == []
        assert result.connector_name == "youcom"
