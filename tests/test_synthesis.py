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
        """System prompt is defined and uses the v0.3.0 [N] contract."""
        assert RESEARCH_SYSTEM_PROMPT
        # v0.3.0 unified everything on [N] (codex DESIGN session 019e39f7).
        # The system prompt now embeds CITATION_FORMAT_GUIDE instead of the
        # old "[source_id]" instruction. The negative example inside the
        # guide ("never `[xx_hex]`") is allowed; "[source_id]" as a directive
        # to the model is not.
        assert "CITATION" in RESEARCH_SYSTEM_PROMPT
        assert "[N]" in RESEARCH_SYSTEM_PROMPT or "`[1]`" in RESEARCH_SYSTEM_PROMPT
        # Hard regression — the format directive must NOT teach the LLM the
        # old [source_id] contract anymore. The substring "source_id" inside
        # CITATION_DISCIPLINE/RESPONSE_FORMAT prose is forbidden.
        assert "[source_id]" not in RESEARCH_SYSTEM_PROMPT

    @pytest.mark.unit
    def test_build_research_prompt(self, sample_sources):
        """Research prompt renders sources as `[1]`, `[2]`, ... not `[sx_*]`."""
        query = "What is async programming?"
        prompt = build_research_prompt(query, sample_sources)

        assert query in prompt
        # v0.3.0 — numeric source blocks (1-based index) replace the old
        # [tv_*]/[sx_*]/[lu_*] connector-ID blocks.
        assert "[1]" in prompt
        assert "[2]" in prompt
        # Hard regression — connector-ID blocks must not appear in the
        # rendered prompt under any source ordering.
        assert "[sx_test001]" not in prompt
        assert "[tv_test002]" not in prompt
        # Source titles and URLs still travel through.
        assert "Python Async IO Guide" in prompt
        assert "https://example.com/async-guide" in prompt

    @pytest.mark.unit
    def test_build_prompt_empty_sources(self):
        """Prompt handles empty sources."""
        prompt = build_research_prompt("test query", [])
        assert "test query" in prompt

    @pytest.mark.unit
    def test_format_citations(self, sample_sources):
        """Citations are formatted as `[1] title - url`, not `[sx_*]`."""
        citations = format_citations(sample_sources)

        # v0.3.0 — numeric list.
        assert "[1]" in citations
        assert "[2]" in citations
        assert "[3]" in citations
        # Hard regression — connector IDs must not appear.
        assert "[sx_test001]" not in citations
        assert "[tv_test002]" not in citations
        assert "[lu_test003]" not in citations
        # Titles still travel through.
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
    """Tests for citation extraction from LLM responses.

    v0.3.0 unified every synthesis surface onto `[N]` (codex DESIGN session
    019e39f7). The legacy `[xx_<hex>]` pattern is no longer in production
    code paths; it survives only as a drift-detection helper in
    `synthesis.citations.detect_legacy_markers`. These tests lock the new
    primary `[N]` resolver AND the drift-detection helper.
    """

    @pytest.mark.unit
    def test_numeric_citation_pattern(self):
        """`[N]` is the canonical v0.3.0 citation format — extractor resolves."""
        from src.synthesis.citations import extract_numeric_citations
        from src.connectors.base import Source

        sources = [
            Source(id="sx_a1b2c3d4", title="One", url="http://1", content="x"),
            Source(id="tv_12345678", title="Two", url="http://2", content="y"),
        ]
        citations = extract_numeric_citations("First [1] then [2].", sources)
        assert len(citations) == 2
        assert [c["number"] for c in citations] == [1, 2]
        assert [c["id"] for c in citations] == ["1", "2"]
        # source_id (connector trace) preserved as separate field.
        assert citations[0]["source_id"] == "sx_a1b2c3d4"
        assert citations[1]["source_id"] == "tv_12345678"

    @pytest.mark.unit
    def test_legacy_marker_detection_diagnostic(self):
        """Legacy `[xx_<hex>]` markers are diagnostically detected (drift signal)."""
        from src.synthesis.citations import detect_legacy_markers

        # Legacy markers in synthesis content are a regression signal in v0.3.0+
        # (the prompt asks for [N]; legacy emission means the model regressed).
        # detect_legacy_markers returns the unique markers in first-seen order.
        assert detect_legacy_markers("Text [sx_a1b2c3d4] more [tv_12345678] text") == [
            "sx_a1b2c3d4",
            "tv_12345678",
        ]
        assert detect_legacy_markers("Numeric [1] only") == []
        # Malformed legacy-shape brackets are not false positives.
        assert detect_legacy_markers("[sx_]") == []
        assert detect_legacy_markers("[source]") == []


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
