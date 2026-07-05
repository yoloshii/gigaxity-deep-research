"""Tests for the shared EVIDENCE_DISCIPLINE prompt fragment (v0.6.3).

v0.6.3 added a source-epistemic discipline — source-authority hierarchy +
fact/inference/uncertainty separation + single-source flagging — to the
synthesis prompt surface. Because `synthesize`/`reason` do NOT use
`RESEARCH_SYSTEM_PROMPT` (they route through the aggregator STYLE_PROMPTS and
the outline SECTION/CRITIQUE/REFINE pipeline), the fragment is injected across
all three families from ONE shared constant in `citations.py`, mirroring how
`CITATION_FORMAT_GUIDE` is shared.

These tests lock:
- the constant's shape (both full + brief forms, brace-free for `.format()`),
- PRESENCE of the full fragment in every final prose-generating surface,
- the compressed form in CONCISE (the `fast` preset) — full fragment absent,
- ABSENCE from OUTLINE_PROMPT (headings only — negative control),
- the two new CRITIQUE checks,
- that `.format(query=, sources=)` still renders (brace-safety regression),
- the SYNTH_CACHE_VERSION bump that stops stale pre-v6 syntheses being served.
"""

import pytest

from src.synthesis import RESEARCH_SYSTEM_PROMPT
from src.synthesis.citations import EVIDENCE_DISCIPLINE, EVIDENCE_DISCIPLINE_BRIEF
from src.synthesis.aggregator import (
    ACADEMIC_SYNTHESIS_PROMPT,
    COMPARATIVE_SYNTHESIS_PROMPT,
    COMPREHENSIVE_SYNTHESIS_PROMPT,
    CONCISE_SYNTHESIS_PROMPT,
    REASONING_SYNTHESIS_PROMPT,
)
from src.synthesis.outline import OutlineGuidedSynthesizer

# The full fragment always carries this header; the brief one-liner never does,
# so it cleanly distinguishes full-fragment injection from the compressed form.
_FULL_MARKER = "EVIDENCE DISCIPLINE:"
_BRIEF_MARKER = "Weight primary/authoritative sources over forum/social"


class TestEvidenceDisciplineConstant:
    """The shared constants are well-formed and safe to interpolate."""

    @pytest.mark.unit
    def test_full_fragment_shape(self):
        assert EVIDENCE_DISCIPLINE.startswith(_FULL_MARKER)
        # Three disciplines: authority hierarchy, fact/inference/uncertainty, consensus.
        assert EVIDENCE_DISCIPLINE.count("\n- ") >= 3
        low = EVIDENCE_DISCIPLINE.lower()
        assert "authoritative" in low
        assert "inference" in low
        # The social-authority nuance must survive (sentiment vs factual claim).
        assert "sentiment" in low

    @pytest.mark.unit
    def test_brief_fragment_shape(self):
        assert _BRIEF_MARKER in EVIDENCE_DISCIPLINE_BRIEF
        # Brief is a single line — no block header, no bullets.
        assert _FULL_MARKER not in EVIDENCE_DISCIPLINE_BRIEF
        assert "\n" not in EVIDENCE_DISCIPLINE_BRIEF

    @pytest.mark.unit
    def test_fragments_are_brace_free(self):
        """No literal braces, or `.format(query=, sources=)` on the templates raises."""
        for frag in (EVIDENCE_DISCIPLINE, EVIDENCE_DISCIPLINE_BRIEF):
            assert "{" not in frag
            assert "}" not in frag


class TestEvidenceDisciplineInjection:
    """Every final prose-generating surface carries the discipline."""

    @pytest.mark.unit
    def test_research_system_prompt_has_full_fragment(self):
        assert _FULL_MARKER in RESEARCH_SYSTEM_PROMPT
        # The subsumed 2-way "limited or uncertain" line was removed…
        assert "Note when information is limited or uncertain." not in RESEARCH_SYSTEM_PROMPT
        # …and the recency dimension (NOT owned by the fragment) was kept, reworded.
        assert "Prefer recent authoritative sources when timeliness matters." in RESEARCH_SYSTEM_PROMPT

    @pytest.mark.unit
    def test_analytical_aggregator_prompts_have_full_fragment(self):
        for prompt in (
            COMPREHENSIVE_SYNTHESIS_PROMPT,
            COMPARATIVE_SYNTHESIS_PROMPT,
            ACADEMIC_SYNTHESIS_PROMPT,
            REASONING_SYNTHESIS_PROMPT,
        ):
            assert _FULL_MARKER in prompt

    @pytest.mark.unit
    def test_concise_prompt_has_brief_not_full(self):
        """`fast` preset (CONCISE) gets the compressed line, never the full block."""
        assert _BRIEF_MARKER in CONCISE_SYNTHESIS_PROMPT
        assert _FULL_MARKER not in CONCISE_SYNTHESIS_PROMPT

    @pytest.mark.unit
    def test_outline_section_and_refine_have_full_fragment(self):
        assert _FULL_MARKER in OutlineGuidedSynthesizer.SECTION_PROMPT
        assert _FULL_MARKER in OutlineGuidedSynthesizer.REFINE_PROMPT

    @pytest.mark.unit
    def test_outline_prompt_has_no_fragment(self):
        """OUTLINE_PROMPT only picks section headings — no claims, no discipline."""
        assert _FULL_MARKER not in OutlineGuidedSynthesizer.OUTLINE_PROMPT
        assert _BRIEF_MARKER not in OutlineGuidedSynthesizer.OUTLINE_PROMPT

    @pytest.mark.unit
    def test_critique_prompt_checks_for_weak_sources_and_inference(self):
        """The critic enforces the same discipline the section writer was given."""
        critique = OutlineGuidedSynthesizer.CRITIQUE_PROMPT
        assert "weak sources" in critique
        assert "single-source assertions presented as settled fact" in critique


class TestFormatSafetyRegression:
    """Injecting the fragment must not break runtime `.format()` substitution."""

    @pytest.mark.unit
    def test_aggregator_prompts_still_format(self):
        for prompt in (
            COMPREHENSIVE_SYNTHESIS_PROMPT,
            CONCISE_SYNTHESIS_PROMPT,
            COMPARATIVE_SYNTHESIS_PROMPT,
            ACADEMIC_SYNTHESIS_PROMPT,
            REASONING_SYNTHESIS_PROMPT,
        ):
            rendered = prompt.format(query="What is X?", sources="[1] a source")
            assert "What is X?" in rendered
            assert "[1] a source" in rendered

    @pytest.mark.unit
    def test_outline_section_and_refine_still_format(self):
        section = OutlineGuidedSynthesizer.SECTION_PROMPT.format(
            section="Background", query="What is X?", sources="[1] a source"
        )
        assert "Background" in section
        assert "What is X?" in section
        refine = OutlineGuidedSynthesizer.REFINE_PROMPT.format(
            draft="d", issues="i", sources="[1] a source"
        )
        assert "[1] a source" in refine


class TestCacheVersionBumped:
    """A synthesis-output-changing prompt edit must invalidate pre-v6 cache entries."""

    @pytest.mark.unit
    def test_synth_cache_version_at_least_6(self):
        from src.cache import SYNTH_CACHE_VERSION

        assert int(SYNTH_CACHE_VERSION) >= 6
