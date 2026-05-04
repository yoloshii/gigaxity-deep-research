"""Tests for search aggregator."""

import pytest
from src.search import SearchAggregator
from src.connectors import SearXNGConnector, TavilyConnector, LinkUpConnector
from src.connectors.base import Source


class TestSearchAggregator:
    """Tests for SearchAggregator."""

    @pytest.mark.unit
    def test_init_with_custom_connectors(self):
        """Aggregator accepts custom connectors."""
        connector = SearXNGConnector(host="http://localhost:8888")
        aggregator = SearchAggregator(connectors=[connector])

        assert len(aggregator.connectors) == 1
        assert aggregator.connectors[0].name == "searxng"

    @pytest.mark.unit
    def test_init_filters_unconfigured(self, monkeypatch):
        """Aggregator filters out unconfigured connectors."""
        monkeypatch.setattr("src.config.settings.tavily_api_key", "")
        configured = SearXNGConnector(host="http://localhost:8888")
        unconfigured = TavilyConnector(api_key="")

        aggregator = SearchAggregator(connectors=[configured, unconfigured])

        assert len(aggregator.connectors) == 1
        assert aggregator.connectors[0].name == "searxng"

    @pytest.mark.unit
    def test_get_active_connectors(self):
        """get_active_connectors returns connector names."""
        connector = SearXNGConnector(host="http://localhost:8888")
        aggregator = SearchAggregator(connectors=[connector])

        active = aggregator.get_active_connectors()

        assert active == ["searxng"]

    @pytest.mark.unit
    def test_custom_top_k(self):
        """Aggregator respects custom top_k."""
        aggregator = SearchAggregator(connectors=[], top_k=20)
        assert aggregator.top_k == 20

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_search_with_searxng(self, searxng_configured, sample_query):
        """Aggregator search works with SearXNG."""
        if not searxng_configured:
            pytest.skip("SearXNG not configured")

        aggregator = SearchAggregator()
        sources, raw_results = await aggregator.search(sample_query, top_k=5)

        assert len(sources) > 0
        assert "searxng" in raw_results

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_search_with_multiple_connectors(
        self, searxng_configured, tavily_configured, sample_query
    ):
        """Aggregator combines results from multiple connectors."""
        if not searxng_configured or not tavily_configured:
            pytest.skip("Both SearXNG and Tavily required")

        aggregator = SearchAggregator()
        sources, raw_results = await aggregator.search(
            sample_query,
            top_k=5,
            connectors=["searxng", "tavily"]
        )

        assert len(raw_results) >= 1
        # Sources should be fused
        assert len(sources) > 0

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_search_connector_filter(self, searxng_configured, sample_query):
        """Aggregator respects connector filter."""
        if not searxng_configured:
            pytest.skip("SearXNG not configured")

        aggregator = SearchAggregator()
        sources, raw_results = await aggregator.search(
            sample_query,
            top_k=5,
            connectors=["searxng"]
        )

        assert list(raw_results.keys()) == ["searxng"]

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_rrf_fusion_applied(self, searxng_configured, sample_query):
        """Results have RRF scores applied."""
        if not searxng_configured:
            pytest.skip("SearXNG not configured")

        aggregator = SearchAggregator()
        sources, _ = await aggregator.search(sample_query, top_k=5)

        if sources:
            # All sources should have positive scores
            assert all(s.score > 0 for s in sources)
            # Should be sorted by score descending
            scores = [s.score for s in sources]
            assert scores == sorted(scores, reverse=True)


class TestAggregatorEdgeCases:
    """Edge case tests for SearchAggregator."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_empty_connectors(self):
        """Search with no connectors returns empty."""
        aggregator = SearchAggregator(connectors=[])
        sources, raw_results = await aggregator.search("test query")

        assert sources == []
        assert raw_results == {}

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_invalid_connector_filter(self):
        """Invalid connector filter is handled."""
        connector = SearXNGConnector(host="http://localhost:8888")
        aggregator = SearchAggregator(connectors=[connector])

        sources, raw_results = await aggregator.search(
            "test",
            connectors=["nonexistent"]
        )

        assert sources == []
        assert raw_results == {}
