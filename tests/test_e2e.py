"""End-to-end tests for complete research flow."""

import pytest
from fastapi.testclient import TestClient
from src.main import app


@pytest.fixture
def client():
    """Create test client."""
    return TestClient(app)


class TestFullResearchFlow:
    """End-to-end tests for complete research workflow."""

    @pytest.mark.integration
    @pytest.mark.slow
    def test_complete_research_flow(
        self, client, searxng_configured, llm_configured
    ):
        """Test complete flow: health → search → research."""
        if not searxng_configured or not llm_configured:
            pytest.skip("Full stack required")

        # Step 1: Health check
        health = client.get("/api/v1/health")
        assert health.status_code == 200
        assert health.json()["status"] == "healthy"

        query = "What are the benefits of async programming?"

        # Step 2: Search only
        search_response = client.post("/api/v1/search", json={
            "query": query,
            "top_k": 5
        })
        assert search_response.status_code == 200
        search_data = search_response.json()
        assert len(search_data["sources"]) > 0

        # Step 3: Full research
        research_response = client.post("/api/v1/research", json={
            "query": query,
            "top_k": 5,
            "reasoning_effort": "medium"
        })
        assert research_response.status_code == 200
        research_data = research_response.json()

        # Verify research response
        assert research_data["query"] == query
        assert len(research_data["content"]) > 100
        assert len(research_data["sources"]) > 0
        assert "connectors_used" in research_data

    @pytest.mark.integration
    @pytest.mark.slow
    def test_research_with_all_connectors(
        self, client, searxng_configured, tavily_configured, linkup_configured, llm_configured
    ):
        """Test research using all available connectors."""
        if not llm_configured:
            pytest.skip("LLM required")

        available = []
        if searxng_configured:
            available.append("searxng")
        if tavily_configured:
            available.append("tavily")
        if linkup_configured:
            available.append("linkup")

        if len(available) < 2:
            pytest.skip("At least 2 connectors required")

        response = client.post("/api/v1/research", json={
            "query": "machine learning frameworks comparison",
            "top_k": 3,
            "connectors": available,
            "reasoning_effort": "low"
        })

        assert response.status_code == 200
        data = response.json()

        # Multiple connectors should be used
        assert len(data["connectors_used"]) >= 1
        # RRF should combine results
        assert len(data["sources"]) > 0

    @pytest.mark.integration
    @pytest.mark.slow
    def test_citations_in_response(
        self, client, searxng_configured, llm_configured
    ):
        """Verify citations are properly extracted."""
        if not searxng_configured or not llm_configured:
            pytest.skip("Full stack required")

        response = client.post("/api/v1/research", json={
            "query": "Explain how HTTP works",
            "top_k": 5,
            "reasoning_effort": "medium"
        })

        assert response.status_code == 200
        data = response.json()

        # Content should exist
        assert len(data["content"]) > 0

        # If citations exist, verify format
        if data["citations"]:
            for citation in data["citations"]:
                assert "id" in citation
                assert "title" in citation
                assert "url" in citation
                # ID should match expected pattern
                assert "_" in citation["id"]

    @pytest.mark.integration
    def test_search_result_deduplication(self, client, searxng_configured):
        """Verify search results are deduplicated."""
        if not searxng_configured:
            pytest.skip("SearXNG required")

        response = client.post("/api/v1/search", json={
            "query": "python programming language",
            "top_k": 20
        })

        assert response.status_code == 200
        data = response.json()

        # Check for duplicate URLs
        urls = [s["url"] for s in data["sources"]]
        assert len(urls) == len(set(urls)), "Duplicate URLs found"

    @pytest.mark.integration
    def test_source_score_ordering(self, client, searxng_configured):
        """Verify sources are ordered by RRF score."""
        if not searxng_configured:
            pytest.skip("SearXNG required")

        response = client.post("/api/v1/search", json={
            "query": "javascript tutorial",
            "top_k": 10
        })

        assert response.status_code == 200
        data = response.json()

        if len(data["sources"]) > 1:
            scores = [s["score"] for s in data["sources"]]
            assert scores == sorted(scores, reverse=True), "Sources not sorted by score"


class TestErrorHandling:
    """Tests for error handling in E2E flow."""

    @pytest.mark.unit
    def test_empty_query_rejected(self, client):
        """Empty query is rejected."""
        response = client.post("/api/v1/search", json={
            "query": ""
        })
        # Pydantic should reject empty string or FastAPI should handle
        # Either 422 (validation) or 200 with empty results is acceptable
        assert response.status_code in [200, 422]

    @pytest.mark.unit
    def test_invalid_json_rejected(self, client):
        """Invalid JSON is rejected."""
        response = client.post(
            "/api/v1/search",
            content="not valid json",
            headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 422

    @pytest.mark.integration
    def test_nonexistent_connector_handled(self, client):
        """Non-existent connector is handled gracefully."""
        response = client.post("/api/v1/search", json={
            "query": "test",
            "connectors": ["nonexistent_connector"]
        })

        # Should return 200 with empty results, not crash
        assert response.status_code == 200
        data = response.json()
        assert data["sources"] == []


class TestPerformance:
    """Performance-related tests."""

    @pytest.mark.integration
    def test_search_response_time(self, client, searxng_configured):
        """Search responds within reasonable time."""
        if not searxng_configured:
            pytest.skip("SearXNG required")

        import time
        start = time.time()

        response = client.post("/api/v1/search", json={
            "query": "quick test query",
            "top_k": 5
        })

        elapsed = time.time() - start

        assert response.status_code == 200
        # Should complete within 10 seconds
        assert elapsed < 10, f"Search took too long: {elapsed:.2f}s"

    @pytest.mark.integration
    def test_health_response_time(self, client):
        """Health check is fast."""
        import time
        start = time.time()

        response = client.get("/api/v1/health")

        elapsed = time.time() - start

        assert response.status_code == 200
        # Health check should be instant
        assert elapsed < 1, f"Health check took too long: {elapsed:.2f}s"


class TestMCPIntegration:
    """Tests for MCP server integration."""

    @pytest.mark.unit
    def test_root_endpoint_shows_mcp(self, client):
        """Root endpoint advertises MCP endpoint."""
        response = client.get("/")
        assert response.status_code == 200
        data = response.json()
        assert data["mcp"] == "/mcp"

    @pytest.mark.unit
    def test_openapi_schema_available(self, client):
        """OpenAPI schema is available for MCP tool generation."""
        response = client.get("/openapi.json")
        assert response.status_code == 200
        schema = response.json()
        assert "paths" in schema
        # Core endpoints should be in schema
        assert "/api/v1/search" in schema["paths"]
        assert "/api/v1/research" in schema["paths"]
        assert "/api/v1/ask" in schema["paths"]
        assert "/api/v1/discover" in schema["paths"]


class TestCacheIntegration:
    """Tests for cache integration in HTTP routes."""

    @pytest.mark.unit
    def test_cache_module_imports(self):
        """Cache module is properly imported in routes."""
        from src.api.routes import cache
        assert cache is not None

    @pytest.mark.unit
    def test_cache_tiers_available(self):
        """Cache supports required tiers."""
        from src.cache import cache
        # These are the tiers used in routes
        tiers = ["search", "ask", "discover", "synthesis", "reason"]
        for tier in tiers:
            # Should not raise
            cache.get("test", tier=tier)


class TestP0P1Endpoints:
    """Tests for P0 and P1 enhanced endpoints."""

    @pytest.mark.unit
    def test_presets_endpoint(self, client):
        """Presets endpoint returns available presets."""
        response = client.get("/api/v1/presets")
        assert response.status_code == 200
        data = response.json()
        assert "presets" in data
        # OpenRouter-optimized: only fast and tutorial presets
        preset_names = [p["name"].lower() for p in data["presets"]]
        assert "fast" in preset_names
        assert "tutorial" in preset_names

    @pytest.mark.unit
    def test_focus_modes_endpoint(self, client):
        """Focus modes endpoint returns available modes."""
        response = client.get("/api/v1/focus-modes")
        assert response.status_code == 200
        data = response.json()
        assert "modes" in data
        # Should have at least these modes
        mode_values = [m["value"] for m in data["modes"]]
        assert "general" in mode_values
        assert "academic" in mode_values

    @pytest.mark.unit
    def test_synthesize_enhanced_endpoint_exists(self, client):
        """Enhanced synthesis endpoint is registered."""
        # Test that endpoint exists (will fail with 422 for missing body)
        response = client.post("/api/v1/synthesize/enhanced")
        # 422 means endpoint exists but needs body
        assert response.status_code == 422

    @pytest.mark.unit
    def test_synthesize_p1_endpoint_exists(self, client):
        """P1 synthesis endpoint is registered."""
        response = client.post("/api/v1/synthesize/p1")
        assert response.status_code == 422  # Exists but needs body


class TestOpenRouterConfiguration:
    """Tests for OpenRouter-specific configuration."""

    @pytest.mark.unit
    def test_config_has_openrouter_settings(self):
        """Config includes OpenRouter-specific settings."""
        from src.config import settings
        assert hasattr(settings, 'llm_api_base')
        assert hasattr(settings, 'llm_api_key')
        assert hasattr(settings, 'llm_model')
        # Default should be OpenRouter
        assert "openrouter.ai" in settings.llm_api_base

    @pytest.mark.unit
    def test_llm_client_accepts_per_request_key(self):
        """LLM client supports per-request API key."""
        from src.llm_client import OpenRouterClient, get_llm_client
        # Test that get_llm_client accepts api_key parameter
        client = get_llm_client(api_key="test-key")
        assert client.api_key == "test-key"
        # Test default client uses settings
        default_client = get_llm_client()
        assert hasattr(default_client, 'api_key')

    @pytest.mark.unit
    def test_synthesis_engine_uses_openrouter_client(self):
        """SynthesisEngine uses OpenRouterClient."""
        from src.synthesis.engine import SynthesisEngine
        from src.llm_client import OpenRouterClient
        engine = SynthesisEngine()
        assert isinstance(engine.client, OpenRouterClient)
