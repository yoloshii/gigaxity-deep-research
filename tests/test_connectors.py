"""Tests for search connectors."""

import pytest
from src.connectors import SearXNGConnector, TavilyConnector, LinkUpConnector
from src.connectors.base import Source, SearchResult


class TestSearXNGConnector:
    """Tests for SearXNG connector."""

    @pytest.mark.unit
    def test_connector_name(self):
        """Verify connector name."""
        connector = SearXNGConnector()
        assert connector.name == "searxng"

    @pytest.mark.unit
    def test_is_configured_with_host(self):
        """Connector is configured when host is set."""
        connector = SearXNGConnector(host="http://localhost:8888")
        assert connector.is_configured() is True

    @pytest.mark.unit
    def test_is_not_configured_without_host(self, monkeypatch):
        """Connector is not configured when host is empty."""
        monkeypatch.setattr("src.config.settings.searxng_host", "")
        connector = SearXNGConnector(host="")
        assert connector.is_configured() is False

    @pytest.mark.unit
    def test_custom_engines(self):
        """Custom engines are stored correctly."""
        connector = SearXNGConnector(
            host="http://localhost:8888",
            engines="google,bing"
        )
        assert connector.engines == "google,bing"

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_search_returns_results(self, searxng_configured, sample_query):
        """SearXNG search returns results."""
        if not searxng_configured:
            pytest.skip("SearXNG not configured")

        connector = SearXNGConnector()
        result = await connector.search(sample_query, top_k=5)

        assert isinstance(result, SearchResult)
        assert result.connector_name == "searxng"
        assert result.query == sample_query
        assert len(result.sources) > 0

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_search_source_format(self, searxng_configured, sample_query):
        """SearXNG sources have correct format."""
        if not searxng_configured:
            pytest.skip("SearXNG not configured")

        connector = SearXNGConnector()
        result = await connector.search(sample_query, top_k=3)

        if result.sources:
            source = result.sources[0]
            assert source.id.startswith("sx_")
            assert source.title
            assert source.url.startswith("http")
            assert source.connector == "searxng"


class TestTavilyConnector:
    """Tests for Tavily connector."""

    @pytest.mark.unit
    def test_connector_name(self):
        """Verify connector name."""
        connector = TavilyConnector()
        assert connector.name == "tavily"

    @pytest.mark.unit
    def test_is_configured_with_key(self):
        """Connector is configured when API key is set."""
        connector = TavilyConnector(api_key="test-key")
        assert connector.is_configured() is True

    @pytest.mark.unit
    def test_is_not_configured_without_key(self, monkeypatch):
        """Connector is not configured when API key is empty."""
        monkeypatch.setattr("src.config.settings.tavily_api_key", "")
        connector = TavilyConnector(api_key="")
        assert connector.is_configured() is False

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_search_returns_results(self, tavily_configured, sample_query):
        """Tavily search returns results."""
        if not tavily_configured:
            pytest.skip("Tavily not configured")

        connector = TavilyConnector()
        result = await connector.search(sample_query, top_k=5)

        assert isinstance(result, SearchResult)
        assert result.connector_name == "tavily"
        assert len(result.sources) > 0

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_search_source_format(self, tavily_configured, sample_query):
        """Tavily sources have correct format."""
        if not tavily_configured:
            pytest.skip("Tavily not configured")

        connector = TavilyConnector()
        result = await connector.search(sample_query, top_k=3)

        if result.sources:
            source = result.sources[0]
            assert source.id.startswith("tv_")
            assert source.connector == "tavily"


class TestLinkUpConnector:
    """Tests for LinkUp connector."""

    @pytest.mark.unit
    def test_connector_name(self):
        """Verify connector name."""
        connector = LinkUpConnector()
        assert connector.name == "linkup"

    @pytest.mark.unit
    def test_is_configured_with_key(self):
        """Connector is configured when API key is set."""
        connector = LinkUpConnector(api_key="test-key")
        assert connector.is_configured() is True

    @pytest.mark.unit
    def test_is_not_configured_without_key(self, monkeypatch):
        """Connector is not configured when API key is empty."""
        monkeypatch.setattr("src.config.settings.linkup_api_key", "")
        connector = LinkUpConnector(api_key="")
        assert connector.is_configured() is False

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_search_returns_results(self, linkup_configured, sample_query):
        """LinkUp search returns results."""
        if not linkup_configured:
            pytest.skip("LinkUp not configured")

        connector = LinkUpConnector()
        result = await connector.search(sample_query, top_k=5)

        assert isinstance(result, SearchResult)
        assert result.connector_name == "linkup"
        # LinkUp may return fewer results

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_search_source_format(self, linkup_configured, sample_query):
        """LinkUp sources have correct format."""
        if not linkup_configured:
            pytest.skip("LinkUp not configured")

        connector = LinkUpConnector()
        result = await connector.search(sample_query, top_k=3)

        if result.sources:
            source = result.sources[0]
            assert source.id.startswith("lu_")
            assert source.connector == "linkup"


class TestSourceModel:
    """Tests for Source data model."""

    @pytest.mark.unit
    def test_source_creation(self):
        """Source can be created with required fields."""
        source = Source(
            id="test_001",
            title="Test Title",
            url="https://example.com",
            content="Test content",
        )
        assert source.id == "test_001"
        assert source.score == 0.0
        assert source.metadata == {}

    @pytest.mark.unit
    def test_source_to_citation(self):
        """Source formats as citation correctly."""
        source = Source(
            id="sx_abc123",
            title="My Article",
            url="https://example.com/article",
            content="Content here",
        )
        citation = source.to_citation()
        assert "[sx_abc123]" in citation
        assert "My Article" in citation
        assert "https://example.com/article" in citation
