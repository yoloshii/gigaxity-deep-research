"""Tests for synthesis engine."""

import pytest
from src.synthesis import SynthesisEngine, RESEARCH_SYSTEM_PROMPT, build_research_prompt
from src.synthesis.prompts import format_citations
from src.connectors.base import Source


class TestPrompts:
    """Tests for synthesis prompts."""

    @pytest.mark.unit
    def test_system_prompt_exists(self):
        """System prompt is defined."""
        assert RESEARCH_SYSTEM_PROMPT
        assert "CITATION" in RESEARCH_SYSTEM_PROMPT
        assert "[source_id]" in RESEARCH_SYSTEM_PROMPT

    @pytest.mark.unit
    def test_build_research_prompt(self, sample_sources):
        """Research prompt includes query and sources."""
        query = "What is async programming?"
        prompt = build_research_prompt(query, sample_sources)

        assert query in prompt
        assert "[sx_test001]" in prompt
        assert "[tv_test002]" in prompt
        assert "Python Async IO Guide" in prompt
        assert "https://example.com/async-guide" in prompt

    @pytest.mark.unit
    def test_build_prompt_empty_sources(self):
        """Prompt handles empty sources."""
        prompt = build_research_prompt("test query", [])
        assert "test query" in prompt

    @pytest.mark.unit
    def test_format_citations(self, sample_sources):
        """Citations are formatted correctly."""
        citations = format_citations(sample_sources)

        assert "[sx_test001]" in citations
        assert "[tv_test002]" in citations
        assert "[lu_test003]" in citations
        assert "Python Async IO Guide" in citations


class TestSynthesisEngine:
    """Tests for SynthesisEngine."""

    @pytest.mark.unit
    def test_init_defaults(self):
        """Engine initializes with defaults."""
        engine = SynthesisEngine()
        assert engine.temperature == 0.85
        assert engine.top_p == 0.95

    @pytest.mark.unit
    def test_init_custom_params(self):
        """Engine accepts custom parameters."""
        engine = SynthesisEngine(
            api_base="http://custom:8080/v1",
            model="custom-model",
            temperature=0.5,
        )
        assert engine.api_base == "http://custom:8080/v1"
        assert engine.model == "custom-model"
        assert engine.temperature == 0.5

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_synthesize_empty_sources(self):
        """Synthesize handles empty sources."""
        engine = SynthesisEngine()
        result = await engine.synthesize("test query", [])

        assert "No sources available" in result["content"]
        assert result["citations"] == []
        assert result["sources_used"] == []

    @pytest.mark.integration
    @pytest.mark.slow
    @pytest.mark.asyncio
    async def test_synthesize_with_sources(self, llm_configured, sample_sources):
        """Synthesize generates response with citations."""
        if not llm_configured:
            pytest.skip("LLM not configured")

        engine = SynthesisEngine()
        result = await engine.synthesize(
            "Explain async programming in Python",
            sample_sources,
        )

        assert "content" in result
        assert len(result["content"]) > 0
        # Should have some citations
        assert "citations" in result

    @pytest.mark.integration
    @pytest.mark.slow
    @pytest.mark.asyncio
    async def test_research_low_effort(self, llm_configured, sample_sources):
        """Research with low effort returns quickly."""
        if not llm_configured:
            pytest.skip("LLM not configured")

        engine = SynthesisEngine()
        result = await engine.research(
            "What is async?",
            sample_sources,
            reasoning_effort="low",
        )

        assert "content" in result
        assert "model" in result

    @pytest.mark.integration
    @pytest.mark.slow
    @pytest.mark.asyncio
    async def test_research_high_effort(self, llm_configured, sample_sources):
        """Research with high effort performs deep analysis."""
        if not llm_configured:
            pytest.skip("LLM not configured")

        engine = SynthesisEngine()
        result = await engine.research(
            "Compare different approaches to async programming",
            sample_sources,
            reasoning_effort="high",
        )

        assert "content" in result
        # High effort should produce longer responses
        assert len(result["content"]) > 100


class TestCitationExtraction:
    """Tests for citation extraction from LLM responses."""

    @pytest.mark.unit
    def test_citation_pattern(self):
        """Citation pattern matches expected format."""
        import re
        pattern = r'\[([a-z]{2}_[a-f0-9]+)\]'

        # Should match
        assert re.findall(pattern, "Text [sx_a1b2c3d4] more text")
        assert re.findall(pattern, "[tv_12345678]")
        assert re.findall(pattern, "[lu_abcdef01]")

        # Should not match
        assert not re.findall(pattern, "[1]")
        assert not re.findall(pattern, "[source]")
        assert not re.findall(pattern, "[sx_]")
