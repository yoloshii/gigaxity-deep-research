"""Tests for the Brave Search connector.

Brave is the stack's CAPTCHA-immune general-web lane: it runs its own index
behind an official keyed API, so unlike the scraped engines in a self-hosted
SearXNG it cannot be served a bot-block page under automated load.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.connectors import BraveConnector
from src.connectors.brave import API_URL, MAX_COUNT


def _response(payload):
    """Build a mock httpx response returning `payload` from .json()."""
    resp = MagicMock()
    resp.json.return_value = payload
    resp.raise_for_status.return_value = None
    return resp


def _client_returning(resp):
    """Patchable async context manager whose .get() returns `resp`."""
    client = MagicMock()
    client.get = AsyncMock(return_value=resp)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=client)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx, client


# Shape mirrors a real api.search.brave.com/res/v1/web/search response.
LIVE_SHAPE = {
    "type": "search",
    "web": {
        "type": "search",
        "results": [
            {
                "title": "7 RAG benchmarks",
                "url": "https://www.evidentlyai.com/blog/rag-benchmarks",
                "description": "We highlight seven RAG benchmarks.",
                "age": "May 6, 2025",
                "page_age": "2025-05-06T00:00:00",
                "language": "en",
            },
            {
                "title": "Evaluation of RAG: A Survey",
                "url": "https://arxiv.org/abs/2405.07437",
                "description": "A survey of RAG evaluation.",
                "page_age": "2024-05-13T00:00:00",
                "language": "en",
            },
        ],
    },
}


class TestBraveConnectorBasics:

    @pytest.mark.unit
    def test_connector_name(self):
        assert BraveConnector(api_key="k").name == "brave"

    @pytest.mark.unit
    def test_is_configured_with_key(self):
        assert BraveConnector(api_key="k").is_configured() is True

    @pytest.mark.unit
    def test_is_not_configured_without_key(self, monkeypatch):
        monkeypatch.setattr("src.config.settings.brave_api_key", "")
        assert BraveConnector(api_key="").is_configured() is False

    @pytest.mark.unit
    def test_probe_url_does_not_spend_a_query(self):
        """Health probe must hit the API root, never the billed search path."""
        probe = BraveConnector(api_key="k")._probe_url()
        assert probe == "https://api.search.brave.com"
        assert "/web/search" not in probe

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_unconfigured_returns_empty_without_network(self):
        """An unset key must short-circuit before any HTTP call."""
        with patch("src.connectors.brave.httpx.AsyncClient") as client_cls:
            result = await BraveConnector(api_key="").search("anything")
        client_cls.assert_not_called()
        assert result.sources == []
        assert result.connector_name == "brave"


class TestBraveConnectorParsing:

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_maps_live_response_shape(self):
        ctx, _ = _client_returning(_response(LIVE_SHAPE))
        with patch("src.connectors.brave.httpx.AsyncClient", return_value=ctx):
            result = await BraveConnector(api_key="k").search("rag benchmarks")

        assert len(result.sources) == 2
        assert result.total_results == 2
        first = result.sources[0]
        assert first.title == "7 RAG benchmarks"
        assert first.url == "https://www.evidentlyai.com/blog/rag-benchmarks"
        # Brave calls the snippet `description`; the Source contract calls it content.
        assert first.content == "We highlight seven RAG benchmarks."
        assert first.connector == "brave"
        assert first.metadata["published_date"] == "2025-05-06T00:00:00"

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_source_ids_are_brave_prefixed_and_distinct(self):
        ctx, _ = _client_returning(_response(LIVE_SHAPE))
        with patch("src.connectors.brave.httpx.AsyncClient", return_value=ctx):
            result = await BraveConnector(api_key="k").search("q")

        ids = [s.id for s in result.sources]
        assert all(i.startswith("br_") for i in ids)
        assert len(set(ids)) == len(ids)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_scores_descend_by_rank(self):
        ctx, _ = _client_returning(_response(LIVE_SHAPE))
        with patch("src.connectors.brave.httpx.AsyncClient", return_value=ctx):
            result = await BraveConnector(api_key="k").search("q")

        assert result.sources[0].score > result.sources[1].score

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_missing_web_key_is_zero_results_not_a_crash(self):
        """Brave omits `web` entirely when it answers only with videos/news.

        That must fuse as a quiet non-contributor, not raise.
        """
        ctx, _ = _client_returning(_response({"type": "search", "videos": {"results": []}}))
        with patch("src.connectors.brave.httpx.AsyncClient", return_value=ctx):
            result = await BraveConnector(api_key="k").search("q")

        assert result.sources == []
        assert result.total_results == 0

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_null_web_key_is_handled(self):
        """`web: null` must behave like an absent key, not raise AttributeError."""
        ctx, _ = _client_returning(_response({"web": None}))
        with patch("src.connectors.brave.httpx.AsyncClient", return_value=ctx):
            result = await BraveConnector(api_key="k").search("q")

        assert result.sources == []


class TestBraveConnectorRequestContract:

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_count_is_clamped_to_brave_maximum(self):
        """Brave rejects count > 20; the connector must clamp, not forward."""
        ctx, client = _client_returning(_response(LIVE_SHAPE))
        with patch("src.connectors.brave.httpx.AsyncClient", return_value=ctx):
            await BraveConnector(api_key="k").search("q", top_k=500)

        params = client.get.call_args.kwargs["params"]
        assert int(params["count"]) == MAX_COUNT

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_count_floor_is_one(self):
        ctx, client = _client_returning(_response(LIVE_SHAPE))
        with patch("src.connectors.brave.httpx.AsyncClient", return_value=ctx):
            await BraveConnector(api_key="k").search("q", top_k=0)

        assert int(client.get.call_args.kwargs["params"]["count"]) == 1

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_key_is_sent_as_subscription_header(self):
        ctx, client = _client_returning(_response(LIVE_SHAPE))
        with patch("src.connectors.brave.httpx.AsyncClient", return_value=ctx):
            await BraveConnector(api_key="secret-key").search("q")

        kwargs = client.get.call_args.kwargs
        assert kwargs["headers"]["X-Subscription-Token"] == "secret-key"
        assert kwargs["headers"]["Accept"] == "application/json"
        # The key must never leak into the query string.
        assert "secret-key" not in str(kwargs["params"])
        assert client.get.call_args.args[0] == API_URL

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_empty_optional_params_are_omitted(self):
        """Blank country must not be sent as country='' — Brave 422s on that."""
        ctx, client = _client_returning(_response(LIVE_SHAPE))
        with patch("src.connectors.brave.httpx.AsyncClient", return_value=ctx):
            await BraveConnector(api_key="k", country="", safesearch="off").search("q")

        params = client.get.call_args.kwargs["params"]
        assert "country" not in params
        assert params["safesearch"] == "off"

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_country_forwarded_when_set(self):
        ctx, client = _client_returning(_response(LIVE_SHAPE))
        with patch("src.connectors.brave.httpx.AsyncClient", return_value=ctx):
            await BraveConnector(api_key="k", country="us").search("q")

        assert client.get.call_args.kwargs["params"]["country"] == "us"


class TestBraveConnectorFailureIsolation:

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_http_error_is_absorbed_not_raised(self):
        """A failing connector must degrade to zero sources so RRF fusion survives."""
        ctx = MagicMock()
        client = MagicMock()
        client.get = AsyncMock(side_effect=Exception("429 rate limited"))
        ctx.__aenter__ = AsyncMock(return_value=client)
        ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("src.connectors.brave.httpx.AsyncClient", return_value=ctx):
            result = await BraveConnector(api_key="k").search("q")

        assert result.sources == []
        assert result.connector_name == "brave"
