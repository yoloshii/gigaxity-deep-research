"""Tests for FastAPI endpoints."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.cache import cache
from src.llm_utils import LLMOutput
from src.main import app


@pytest.fixture
def client():
    """Create test client."""
    return TestClient(app)


class TestHealthEndpoint:
    """Tests for health check endpoint."""

    @pytest.mark.unit
    def test_health_returns_200(self, client):
        """Health endpoint returns 200."""
        response = client.get("/api/v1/health")
        assert response.status_code == 200

    @pytest.mark.unit
    def test_health_response_format(self, client):
        """Health response has correct format."""
        response = client.get("/api/v1/health")
        data = response.json()

        assert "status" in data
        assert "connectors" in data
        assert "llm_configured" in data
        assert data["status"] == "healthy"


class TestRootEndpoint:
    """Tests for root endpoint."""

    @pytest.mark.unit
    def test_root_returns_200(self, client):
        """Root endpoint returns 200."""
        response = client.get("/")
        assert response.status_code == 200

    @pytest.mark.unit
    def test_root_response_format(self, client):
        """Root response has API info."""
        response = client.get("/")
        data = response.json()

        assert "name" in data
        assert "version" in data
        assert "endpoints" in data


class TestSearchEndpoint:
    """Tests for search endpoint."""

    @pytest.mark.unit
    def test_search_requires_query(self, client):
        """Search requires query field."""
        response = client.post("/api/v1/search", json={})
        assert response.status_code == 422

    @pytest.mark.unit
    def test_search_validates_top_k(self, client):
        """Search validates top_k range."""
        response = client.post("/api/v1/search", json={
            "query": "test",
            "top_k": 100  # Exceeds max of 50
        })
        assert response.status_code == 422

    @pytest.mark.integration
    def test_search_returns_results(self, client, searxng_configured):
        """Search returns results with valid query."""
        if not searxng_configured:
            pytest.skip("SearXNG not configured")

        response = client.post("/api/v1/search", json={
            "query": "python tutorial",
            "top_k": 3
        })

        assert response.status_code == 200
        data = response.json()
        assert "query" in data
        assert "sources" in data
        assert "connectors_used" in data

    @pytest.mark.integration
    def test_search_respects_connector_filter(self, client, searxng_configured):
        """Search respects connector filter."""
        if not searxng_configured:
            pytest.skip("SearXNG not configured")

        response = client.post("/api/v1/search", json={
            "query": "test query",
            "connectors": ["searxng"]
        })

        assert response.status_code == 200
        data = response.json()
        assert data["connectors_used"] == ["searxng"]


class TestResearchEndpoint:
    """Tests for research endpoint."""

    @pytest.mark.unit
    def test_research_requires_query(self, client):
        """Research requires query field."""
        response = client.post("/api/v1/research", json={})
        assert response.status_code == 422

    @pytest.mark.unit
    def test_research_validates_reasoning_effort(self, client):
        """Research validates reasoning_effort values."""
        response = client.post("/api/v1/research", json={
            "query": "test",
            "reasoning_effort": "invalid"
        })
        assert response.status_code == 422

    @pytest.mark.integration
    @pytest.mark.slow
    def test_research_returns_synthesis(
        self, client, searxng_configured, llm_configured
    ):
        """Research returns synthesized response."""
        if not searxng_configured or not llm_configured:
            pytest.skip("SearXNG and LLM required")

        response = client.post("/api/v1/research", json={
            "query": "What is Python?",
            "top_k": 3,
            "reasoning_effort": "low"
        })

        assert response.status_code == 200
        data = response.json()
        assert "content" in data
        assert "citations" in data
        assert "sources" in data


class TestAskEndpoint:
    """Tests for ask endpoint."""

    @pytest.mark.unit
    def test_ask_requires_query(self, client):
        """Ask requires query field."""
        response = client.post("/api/v1/ask", json={})
        assert response.status_code == 422

    @pytest.mark.integration
    @pytest.mark.slow
    def test_ask_returns_quick_response(
        self, client, searxng_configured, llm_configured
    ):
        """Ask returns quick response."""
        if not searxng_configured or not llm_configured:
            pytest.skip("SearXNG and LLM required")

        response = client.post("/api/v1/ask", json={
            "query": "What is HTTP?",
            "top_k": 3
        })

        assert response.status_code == 200
        data = response.json()
        assert "content" in data


class TestOpenAPISchema:
    """Tests for OpenAPI schema."""

    @pytest.mark.unit
    def test_openapi_available(self, client):
        """OpenAPI schema is available."""
        response = client.get("/openapi.json")
        assert response.status_code == 200

    @pytest.mark.unit
    def test_docs_available(self, client):
        """Swagger docs are available."""
        response = client.get("/docs")
        assert response.status_code == 200


class TestCORS:
    """Tests for CORS configuration."""

    @pytest.mark.unit
    def test_cors_headers(self, client):
        """CORS headers are present."""
        response = client.options(
            "/api/v1/health",
            headers={"Origin": "http://localhost:3000"}
        )
        # CORS preflight should work
        assert response.status_code in [200, 405]


class TestReasonVerification:
    """The /reason route verifies output and caches only verified results.

    Locks the Turn 8 codex-review fix: /reason previously bypassed
    post-synthesis verification entirely and cached every result
    unconditionally - a degraded synthesize_with_reasoning result (empty
    content from a missing <synthesis> tag) was relayed as a clean answer and
    served from cache.
    """

    def setup_method(self):
        cache.clear()

    def teardown_method(self):
        cache.clear()

    @staticmethod
    def _payload():
        return {
            "query": "reason verification test query",
            "sources": [
                {"origin": "exa", "url": "http://a.com", "title": "A",
                 "content": "alpha content", "source_type": "article"},
            ],
        }

    @staticmethod
    def _mock_result(content, citations, llm_output):
        result = MagicMock()
        result.content = content
        result.citations = citations
        result.source_attribution = {}
        result.confidence = 0.7 if content else 0.0
        result.word_count = len(content.split())
        result.llm_output = llm_output
        return result

    @pytest.mark.unit
    def test_degraded_reason_result_not_cached(self, client):
        """A degraded (empty-content) reasoning result hard-fails and is not cached."""
        degraded = self._mock_result(
            content="",  # missing <synthesis> tag -> degraded result
            citations=[],
            llm_output=LLMOutput(
                text="<reasoning>partial trace</reasoning>", source_field="content",
                finish_reason="stop", truncated=False, reasoning_only=False,
            ),
        )
        with patch("src.api.routes.SynthesisAggregator") as mock_agg:
            inst = MagicMock()
            inst.synthesize_with_reasoning = AsyncMock(return_value=degraded)
            mock_agg.return_value = inst

            r1 = client.post("/api/v1/reason", json=self._payload())
            assert r1.status_code == 200
            body = r1.json()
            assert body["verification"]["passed"] is False
            assert body["content"].startswith("# Synthesis verification FAILED")

            # A second identical call must re-run synthesis - the failed result
            # was not cached.
            r2 = client.post("/api/v1/reason", json=self._payload())
            assert r2.status_code == 200
            assert inst.synthesize_with_reasoning.call_count == 2

    @pytest.mark.unit
    def test_verified_reason_result_is_cached(self, client):
        """A verified reasoning result passes and IS served from cache."""
        good = self._mock_result(
            content="real synthesis answer [1]",
            citations=[{"number": 1, "title": "A", "url": "http://a.com"}],
            llm_output=LLMOutput(
                text="real synthesis answer [1]", source_field="content",
                finish_reason="stop", truncated=False, reasoning_only=False,
            ),
        )
        with patch("src.api.routes.SynthesisAggregator") as mock_agg:
            inst = MagicMock()
            inst.synthesize_with_reasoning = AsyncMock(return_value=good)
            mock_agg.return_value = inst

            r1 = client.post("/api/v1/reason", json=self._payload())
            assert r1.status_code == 200
            assert r1.json()["verification"]["passed"] is True

            # A second identical call is served from cache - synthesis not re-run.
            r2 = client.post("/api/v1/reason", json=self._payload())
            assert r2.status_code == 200
            assert inst.synthesize_with_reasoning.call_count == 1
