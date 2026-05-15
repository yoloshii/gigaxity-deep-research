"""Tests for synthesis engine."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

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


class TestSourceFormatting:
    """Budget-aware source formatting keeps advisory guidance OUT of evidence.

    Locks the Turn 8 codex-review fix: under budget pressure the formatter used
    to substitute a source's RCS guidance summary into its `Content:` block in
    the SOURCE EVIDENCE section, making advisory text citable as source
    evidence. Under pressure it must truncate the VERBATIM source instead; the
    guidance summary stays only in the CONTEXTUAL GUIDANCE section, and no
    source is dropped.
    """

    @staticmethod
    def _src(title, content, origin="exa", source_type="article"):
        from src.synthesis.aggregator import PreGatheredSource
        return PreGatheredSource(
            origin=origin, url=f"http://{title}.com", title=title,
            content=content, source_type=source_type,
        )

    @pytest.mark.unit
    def test_no_pressure_includes_full_verbatim_content(self):
        """When everything fits, every source appears verbatim and in full."""
        from src.synthesis.source_formatting import format_sources_for_synthesis
        sources = [self._src("A", "alpha body text"), self._src("B", "beta body text")]
        out = format_sources_for_synthesis(sources, input_budget_tokens=100_000)
        assert "alpha body text" in out
        assert "beta body text" in out

    @pytest.mark.unit
    def test_budget_pressure_truncates_verbatim_not_summary(self):
        """Under pressure, SOURCE EVIDENCE holds truncated VERBATIM text, never the summary."""
        from src.synthesis.source_formatting import format_sources_for_synthesis
        sources = [
            self._src("A", "AAAA " * 500),
            self._src("B", "BBBB " * 500),
        ]
        guidance = ["ADVISORY-SUMMARY-FOR-A", "ADVISORY-SUMMARY-FOR-B"]
        # A tiny budget guarantees budget pressure.
        out = format_sources_for_synthesis(
            sources, input_budget_tokens=200, guidance=guidance,
        )

        assert "SOURCE EVIDENCE:" in out
        advisory_part, evidence_part = out.split("SOURCE EVIDENCE:", 1)

        # Guidance summaries belong ONLY in the advisory section.
        assert "ADVISORY-SUMMARY-FOR-A" in advisory_part
        assert "ADVISORY-SUMMARY-FOR-A" not in evidence_part
        assert "ADVISORY-SUMMARY-FOR-B" not in evidence_part
        # The evidence section carries truncated VERBATIM source text.
        assert "AAAA" in evidence_part
        assert "[truncated under prompt budget pressure]" in evidence_part

    @pytest.mark.unit
    def test_budget_pressure_drops_no_source(self):
        """Even under heavy pressure, every source still appears in the output."""
        from src.synthesis.source_formatting import format_sources_for_synthesis
        sources = [self._src(f"S{i}", "x" * 4000) for i in range(6)]
        out = format_sources_for_synthesis(sources, input_budget_tokens=100)
        for i in range(6):
            assert f"S{i}" in out


class TestRCSContextualSummarize:
    """RCS parse failure must not duplicate full source content into guidance.

    Locks the Turn 9 codex-review fix: when _contextual_summarize cannot parse
    a structured summary (unparseable, empty, or truncated LLM output) it must
    return an EMPTY summary. RCS is guidance-only - the source's full verbatim
    content already reaches synthesis via the SOURCE EVIDENCE section - so
    falling back to source.content would only duplicate the whole source into
    the advisory guidance section and consume the evidence budget.
    """

    @staticmethod
    def _client(content):
        """A mock LLM client whose chat completion returns `content`."""
        client = MagicMock()
        response = SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content=content, reasoning="", reasoning_content=""),
            finish_reason="stop",
        )])
        client.chat.completions.create = AsyncMock(return_value=response)
        return client

    @staticmethod
    def _source():
        from src.synthesis.aggregator import PreGatheredSource
        return PreGatheredSource(
            origin="exa", url="http://a.com", title="A",
            content="THE FULL SOURCE BODY THAT MUST NOT BE DUPLICATED INTO GUIDANCE",
            source_type="article",
        )

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_parse_failure_returns_empty_summary_not_source_content(self):
        """Unparseable LLM output -> empty summary, never the full source content."""
        from src.synthesis.rcs import RCSPreprocessor
        source = self._source()
        rcs = RCSPreprocessor(
            llm_client=self._client("garbled output with no SUMMARY structure"),
            model="m",
        )
        result = await rcs._contextual_summarize(source, "the query")
        assert result.summary == ""
        assert source.content not in result.summary

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_parse_success_keeps_summary(self):
        """A well-formed structured response is parsed into a real summary."""
        from src.synthesis.rcs import RCSPreprocessor
        rcs = RCSPreprocessor(
            llm_client=self._client("SUMMARY: a real query-focused summary\nRELEVANCE: 0.8"),
            model="m",
        )
        result = await rcs._contextual_summarize(self._source(), "the query")
        assert result.summary == "a real query-focused summary"
