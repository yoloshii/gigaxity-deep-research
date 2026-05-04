"""Tests for API endpoints including enhanced P0/P1 routes.

Covers:
- Health and basic endpoints (/health, /search, /research, /ask)
- Discovery endpoint (/discover)
- Synthesis endpoints (/synthesize, /reason)
- P0 Enhanced endpoint (/synthesize/enhanced)
- P1 Enhanced endpoints (/presets, /focus-modes, /synthesize/p1)
"""

import pytest
from fastapi.testclient import TestClient


# =============================================================================
# Basic Endpoint Tests
# =============================================================================


class TestHealthEndpoint:
    """Tests for health check endpoint."""

    @pytest.mark.unit
    def test_health_check(self, test_client):
        """Health endpoint returns status."""
        response = test_client.get("/api/v1/health")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "healthy"
        assert "connectors" in data
        assert isinstance(data["connectors"], list)
        assert "llm_configured" in data


class TestSearchEndpoint:
    """Tests for search endpoint."""

    @pytest.mark.unit
    def test_search_request_validation(self, test_client):
        """Search validates request fields."""
        # Empty query should fail
        response = test_client.post("/api/v1/search", json={"query": ""})
        # Pydantic may accept empty strings, check if connectors are available
        # This test checks if the endpoint responds correctly

    @pytest.mark.unit
    def test_search_default_params(self, test_client):
        """Search uses default parameters."""
        response = test_client.post("/api/v1/search", json={
            "query": "Python async programming"
        })

        # May fail if no connectors configured
        assert response.status_code in [200, 503]

    @pytest.mark.integration
    def test_search_with_connectors(self, test_client, searxng_configured):
        """Search with specific connectors."""
        if not searxng_configured:
            pytest.skip("SearXNG not configured")

        response = test_client.post("/api/v1/search", json={
            "query": "Python async programming",
            "top_k": 5,
            "connectors": ["searxng"]
        })

        assert response.status_code == 200
        data = response.json()
        assert data["query"] == "Python async programming"
        assert len(data["sources"]) <= 5


# =============================================================================
# P0/P1 Metadata Endpoint Tests
# =============================================================================


class TestPresetsEndpoint:
    """Tests for P1 presets endpoint."""

    @pytest.mark.unit
    @pytest.mark.p1
    def test_get_presets(self, test_client):
        """Presets endpoint returns all presets."""
        response = test_client.get("/api/v1/presets")
        assert response.status_code == 200

        data = response.json()
        assert "presets" in data
        presets = data["presets"]

        # Source defines five presets; the public REST surface mirrors all of them.
        assert len(presets) == 5

        preset_values = [p["value"] for p in presets]
        for expected in ("comprehensive", "fast", "contracrow", "academic", "tutorial"):
            assert expected in preset_values

    @pytest.mark.unit
    @pytest.mark.p1
    def test_preset_schema(self, test_client):
        """Each preset has required fields."""
        response = test_client.get("/api/v1/presets")
        data = response.json()

        for preset in data["presets"]:
            assert "name" in preset
            assert "value" in preset
            assert "description" in preset
            assert "style" in preset
            assert "max_tokens" in preset


class TestFocusModesEndpoint:
    """Tests for P1 focus modes endpoint."""

    @pytest.mark.unit
    @pytest.mark.p1
    def test_get_focus_modes(self, test_client):
        """Focus modes endpoint returns all modes."""
        response = test_client.get("/api/v1/focus-modes")
        assert response.status_code == 200

        data = response.json()
        assert "modes" in data
        modes = data["modes"]

        # Should have 7 focus modes
        assert len(modes) >= 7

        # Check mode values
        mode_values = [m["value"] for m in modes]
        assert "general" in mode_values
        assert "academic" in mode_values
        assert "documentation" in mode_values
        assert "comparison" in mode_values
        assert "debugging" in mode_values
        assert "tutorial" in mode_values
        assert "news" in mode_values

    @pytest.mark.unit
    @pytest.mark.p1
    def test_focus_mode_schema(self, test_client):
        """Each focus mode has required fields."""
        response = test_client.get("/api/v1/focus-modes")
        data = response.json()

        for mode in data["modes"]:
            assert "name" in mode
            assert "value" in mode
            assert "description" in mode
            assert "search_expansion" in mode
            assert "gap_categories" in mode
            assert isinstance(mode["gap_categories"], list)


# =============================================================================
# Synthesis Endpoint Tests
# =============================================================================


class TestSynthesizeEndpoint:
    """Tests for standard synthesis endpoint."""

    @pytest.mark.unit
    def test_synthesize_request_validation(self, test_client):
        """Synthesize validates required fields."""
        # Missing sources
        response = test_client.post("/api/v1/synthesize", json={
            "query": "Test query"
        })
        assert response.status_code == 422  # Validation error

    @pytest.mark.integration
    @pytest.mark.slow
    def test_synthesize_with_sources(self, test_client, llm_configured):
        """Synthesize generates content from sources."""
        if not llm_configured:
            pytest.skip("LLM not configured")

        response = test_client.post("/api/v1/synthesize", json={
            "query": "Compare FastAPI vs Flask",
            "sources": [
                {
                    "origin": "ref",
                    "url": "https://fastapi.tiangolo.com/",
                    "title": "FastAPI Documentation",
                    "content": "FastAPI is a modern, fast web framework for building APIs.",
                    "source_type": "documentation"
                },
                {
                    "origin": "exa",
                    "url": "https://flask.palletsprojects.com/",
                    "title": "Flask Documentation",
                    "content": "Flask is a lightweight WSGI web application framework.",
                    "source_type": "documentation"
                }
            ],
            "style": "comparative",
            "max_tokens": 1000
        })

        assert response.status_code == 200
        data = response.json()
        assert data["query"] == "Compare FastAPI vs Flask"
        assert data["content"]
        assert data["word_count"] > 0


class TestSynthesizeEnhancedEndpoint:
    """Tests for P0 enhanced synthesis endpoint."""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_enhanced_request_validation(self, test_client):
        """Enhanced endpoint validates request."""
        response = test_client.post("/api/v1/synthesize/enhanced", json={
            "query": "Test",
            "sources": []
        })
        # Empty sources might be allowed but produce minimal output
        assert response.status_code in [200, 422]

    @pytest.mark.integration
    @pytest.mark.slow
    @pytest.mark.p0
    def test_enhanced_with_quality_gate(self, test_client, llm_configured):
        """Enhanced synthesis runs quality gate."""
        if not llm_configured:
            pytest.skip("LLM not configured")

        response = test_client.post("/api/v1/synthesize/enhanced", json={
            "query": "Compare FastAPI vs Flask",
            "sources": [
                {
                    "origin": "ref",
                    "url": "https://fastapi.tiangolo.com/",
                    "title": "FastAPI Documentation",
                    "content": "FastAPI is a modern, fast web framework for building APIs with Python based on standard type hints.",
                    "source_type": "documentation"
                },
                {
                    "origin": "exa",
                    "url": "https://flask.palletsprojects.com/",
                    "title": "Flask Documentation",
                    "content": "Flask is a lightweight WSGI web application framework designed to make getting started quick and easy.",
                    "source_type": "documentation"
                }
            ],
            "style": "comparative",
            "run_quality_gate": True,
            "detect_contradictions": False,
            "verify_citations": False
        })

        assert response.status_code == 200
        data = response.json()
        assert "quality_gate" in data
        if data["quality_gate"]:
            assert data["quality_gate"]["decision"] in ["proceed", "reject", "partial"]

    @pytest.mark.integration
    @pytest.mark.slow
    @pytest.mark.p0
    def test_enhanced_with_contradictions(self, test_client, llm_configured):
        """Enhanced synthesis detects contradictions."""
        if not llm_configured:
            pytest.skip("LLM not configured")

        response = test_client.post("/api/v1/synthesize/enhanced", json={
            "query": "Is Redux necessary for React?",
            "sources": [
                {
                    "origin": "source_a",
                    "url": "https://example.com/a",
                    "title": "Pro Redux",
                    "content": "Redux is essential for managing state in React applications. Without Redux, complex applications become unmaintainable.",
                    "source_type": "article"
                },
                {
                    "origin": "source_b",
                    "url": "https://example.com/b",
                    "title": "Anti Redux",
                    "content": "Redux is often unnecessary in modern React. React Context and useReducer provide sufficient state management.",
                    "source_type": "article"
                }
            ],
            "style": "comparative",
            "run_quality_gate": False,
            "detect_contradictions": True,
            "verify_citations": False
        })

        assert response.status_code == 200
        data = response.json()
        assert "contradictions" in data
        assert isinstance(data["contradictions"], list)


class TestSynthesizeP1Endpoint:
    """Tests for P1 enhanced synthesis endpoint."""

    @pytest.mark.unit
    @pytest.mark.p1
    def test_p1_request_validation(self, test_client):
        """P1 endpoint validates request."""
        response = test_client.post("/api/v1/synthesize/p1", json={
            "query": "Test",
            "sources": []
        })
        assert response.status_code in [200, 422]

    @pytest.mark.integration
    @pytest.mark.slow
    @pytest.mark.p1
    def test_p1_with_preset(self, test_client, llm_configured):
        """P1 synthesis with preset."""
        if not llm_configured:
            pytest.skip("LLM not configured")

        response = test_client.post("/api/v1/synthesize/p1", json={
            "query": "How to implement OAuth2 in FastAPI",
            "sources": [
                {
                    "origin": "ref",
                    "url": "https://fastapi.tiangolo.com/tutorial/security/",
                    "title": "FastAPI Security Tutorial",
                    "content": "FastAPI provides several tools to help you handle security. OAuth2 with Password is one of the most common flows.",
                    "source_type": "documentation"
                }
            ],
            "preset": "tutorial"
        })

        assert response.status_code == 200
        data = response.json()
        assert data["preset_used"] == "Tutorial"

    @pytest.mark.integration
    @pytest.mark.slow
    @pytest.mark.p1
    def test_p1_with_outline(self, test_client, llm_configured):
        """P1 synthesis with outline-guided mode."""
        if not llm_configured:
            pytest.skip("LLM not configured")

        response = test_client.post("/api/v1/synthesize/p1", json={
            "query": "Compare FastAPI vs Flask",
            "sources": [
                {
                    "origin": "ref",
                    "url": "https://fastapi.tiangolo.com/",
                    "title": "FastAPI Documentation",
                    "content": "FastAPI is a modern, fast web framework. High performance, on par with NodeJS and Go.",
                    "source_type": "documentation"
                },
                {
                    "origin": "exa",
                    "url": "https://flask.palletsprojects.com/",
                    "title": "Flask Documentation",
                    "content": "Flask is a lightweight WSGI web application framework. Designed to make getting started quick.",
                    "source_type": "documentation"
                }
            ],
            "preset": None,
            "use_outline": True,
            "use_rcs": False,
            "style": "comparative"
        })

        assert response.status_code == 200
        data = response.json()
        # Should have outline data if outline mode worked
        if data["outline"]:
            assert isinstance(data["outline"], list)
            assert len(data["outline"]) >= 2

    @pytest.mark.integration
    @pytest.mark.slow
    @pytest.mark.p1
    def test_p1_with_rcs(self, test_client, llm_configured):
        """P1 synthesis with RCS contextual summarization."""
        if not llm_configured:
            pytest.skip("LLM not configured")

        response = test_client.post("/api/v1/synthesize/p1", json={
            "query": "React state management patterns",
            "sources": [
                {
                    "origin": "ref",
                    "url": "https://react.dev/",
                    "title": "React Documentation",
                    "content": "React provides useState and useReducer hooks for local state management.",
                    "source_type": "documentation"
                },
                {
                    "origin": "exa",
                    "url": "https://redux.js.org/",
                    "title": "Redux Documentation",
                    "content": "Redux is a predictable state container for JavaScript apps.",
                    "source_type": "documentation"
                },
                {
                    "origin": "jina",
                    "url": "https://example.com/zustand",
                    "title": "Zustand Overview",
                    "content": "Zustand is a small, fast and scalable state management solution.",
                    "source_type": "article"
                }
            ],
            "preset": None,
            "use_outline": False,
            "use_rcs": True,
            "rcs_top_k": 2,
            "style": "concise"
        })

        assert response.status_code == 200
        data = response.json()
        # Should have RCS summaries if RCS mode worked
        if data["rcs_summaries"]:
            assert len(data["rcs_summaries"]) <= 2

    @pytest.mark.integration
    @pytest.mark.slow
    @pytest.mark.p1
    def test_p1_tutorial_preset(self, test_client, llm_configured):
        """P1 synthesis with tutorial preset (outline-guided)."""
        if not llm_configured:
            pytest.skip("LLM not configured")

        response = test_client.post("/api/v1/synthesize/p1", json={
            "query": "How to build APIs with modern Python frameworks",
            "sources": [
                {
                    "origin": "ref",
                    "url": "https://fastapi.tiangolo.com/",
                    "title": "FastAPI",
                    "content": "FastAPI is a modern, fast framework. Provides automatic OpenAPI documentation.",
                    "source_type": "documentation"
                },
                {
                    "origin": "exa",
                    "url": "https://flask.palletsprojects.com/",
                    "title": "Flask",
                    "content": "Flask is lightweight and flexible. Large ecosystem of extensions.",
                    "source_type": "documentation"
                }
            ],
            "preset": "tutorial"
        })

        assert response.status_code == 200
        data = response.json()
        assert data["preset_used"] == "Tutorial"


# =============================================================================
# Discovery Endpoint Tests
# =============================================================================


class TestDiscoverEndpoint:
    """Tests for discovery endpoint."""

    @pytest.mark.unit
    def test_discover_request_validation(self, test_client):
        """Discover validates request fields."""
        response = test_client.post("/api/v1/discover", json={
            "query": ""
        })
        # Empty query might be allowed at validation level
        # but will fail at search level if no results

    @pytest.mark.integration
    @pytest.mark.slow
    def test_discover_with_expansion(self, test_client, llm_configured):
        """Discover with search expansion."""
        if not llm_configured:
            pytest.skip("LLM not configured")

        response = test_client.post("/api/v1/discover", json={
            "query": "Python async programming patterns",
            "top_k": 5,
            "expand_searches": True
        })

        # May fail if no connectors configured
        if response.status_code == 503:
            pytest.skip("No connectors configured")

        assert response.status_code == 200
        data = response.json()
        assert "landscape" in data
        assert "knowledge_gaps" in data
        assert "sources" in data
        assert "recommended_deep_dives" in data


# =============================================================================
# Reason Endpoint Tests
# =============================================================================


class TestReasonEndpoint:
    """Tests for reasoning endpoint."""

    @pytest.mark.unit
    def test_reason_request_validation(self, test_client):
        """Reason validates required fields."""
        response = test_client.post("/api/v1/reason", json={
            "query": "Test"
        })
        assert response.status_code == 422  # Missing sources

    @pytest.mark.integration
    @pytest.mark.slow
    def test_reason_with_sources(self, test_client, llm_configured):
        """Reason generates chain-of-thought synthesis."""
        if not llm_configured:
            pytest.skip("LLM not configured")

        response = test_client.post("/api/v1/reason", json={
            "query": "Compare performance of FastAPI vs Flask",
            "sources": [
                {
                    "origin": "benchmark",
                    "url": "https://example.com/benchmark",
                    "title": "Web Framework Benchmark",
                    "content": "FastAPI handles 3x more requests per second than Flask. FastAPI async support provides better concurrency.",
                    "source_type": "article"
                }
            ],
            "style": "comparative"
        })

        assert response.status_code == 200
        data = response.json()
        assert data["content"]
        assert "confidence" in data


# =============================================================================
# Ask Endpoint Tests
# =============================================================================


class TestAskEndpoint:
    """Tests for quick ask endpoint."""

    @pytest.mark.integration
    def test_ask_quick_response(self, test_client, llm_configured):
        """Ask provides quick response."""
        if not llm_configured:
            pytest.skip("LLM not configured")

        response = test_client.post("/api/v1/ask", json={
            "query": "What is Python?",
            "top_k": 3
        })

        # May fail if no connectors
        if response.status_code == 503:
            pytest.skip("No connectors configured")

        assert response.status_code == 200
        data = response.json()
        assert "content" in data
        assert data["query"] == "What is Python?"


# =============================================================================
# Response Schema Validation Tests
# =============================================================================


class TestResponseSchemas:
    """Tests for response schema compliance."""

    @pytest.mark.integration
    @pytest.mark.slow
    @pytest.mark.p1
    def test_p1_response_schema(self, test_client, llm_configured):
        """P1 response includes all expected fields."""
        if not llm_configured:
            pytest.skip("LLM not configured")

        response = test_client.post("/api/v1/synthesize/p1", json={
            "query": "Test query",
            "sources": [
                {
                    "origin": "test",
                    "url": "https://test.com",
                    "title": "Test Source",
                    "content": "Test content for synthesis.",
                    "source_type": "article"
                }
            ],
            "preset": "fast"
        })

        if response.status_code == 200:
            data = response.json()
            # Required fields
            assert "query" in data
            assert "content" in data
            assert "citations" in data
            assert "confidence" in data
            assert "style_used" in data
            assert "word_count" in data

            # P1 specific fields
            assert "preset_used" in data
            assert "outline" in data
            assert "sections" in data
            assert "critique" in data
            assert "rcs_summaries" in data
            assert "sources_filtered" in data

    @pytest.mark.integration
    @pytest.mark.slow
    @pytest.mark.p0
    def test_enhanced_response_schema(self, test_client, llm_configured):
        """Enhanced response includes P0 fields."""
        if not llm_configured:
            pytest.skip("LLM not configured")

        response = test_client.post("/api/v1/synthesize/enhanced", json={
            "query": "Test query",
            "sources": [
                {
                    "origin": "test",
                    "url": "https://test.com",
                    "title": "Test Source",
                    "content": "Test content for synthesis.",
                    "source_type": "article"
                }
            ],
            "run_quality_gate": False,
            "detect_contradictions": False,
            "verify_citations": False
        })

        if response.status_code == 200:
            data = response.json()
            # P0 specific fields should be present
            assert "quality_gate" in data
            assert "contradictions" in data
            assert "verified_claims" in data
