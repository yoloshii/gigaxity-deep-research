"""Tests for synthesis output cleanup — the <answer> delimiter extractor.

Guards the fix for the v0.3.6-surfaced leak where a verbose reasoning model
appended a "Key Corrections Implemented" changelog to free-form synthesis. The
robust fix is delimiter extraction (the model wraps its answer in
<answer>…</answer>; anything after </answer> is dropped), chosen after five
rounds of codex review proved a content heuristic could not separate the leak
from legitimate errata/correction-notice content.

Bug-first: these assert CORRECT behavior — the changelog (after </answer>) is
dropped, and content is NEVER deleted on a content-heuristic basis (no tags ⇒
unchanged; a topical `## Corrections Made` section INSIDE <answer> survives).
"""

import pytest

from src.synthesis.output_cleanup import extract_delimited_answer
from src.synthesis.aggregator import (
    SynthesisAggregator,
    SynthesisStyle,
    PreGatheredSource,
)
from src.llm_utils import LLMOutput


_CLEAN_BODY = (
    "## Model Identity\n\n"
    "The model is Qwen3-30B-A3B-Thinking-2507 [1]. It is a Mixture-of-Experts\n"
    "model with 30.5B total parameters [2].\n\n"
    "## Context Window\n\n"
    "It supports a 131,072-token context window [2][4]."
)

# The observed artifact: a real answer wrapped in <answer>, with the self-edit
# changelog appended AFTER the closing tag (where the prompt told it to put
# nothing).
_WRAPPED_WITH_TRAILER = (
    f"<answer>\n{_CLEAN_BODY}\n</answer>\n\n"
    "---\n"
    "**Key Corrections Implemented**:\n"
    "- Removed all false claims about \"misnomer\" [1][3].\n"
    "- Cited all claims directly to sources [2][4]."
)


class TestExtractDelimitedAnswer:
    """Pure-function behavior of extract_delimited_answer."""

    @pytest.mark.unit
    def test_extracts_inner_and_drops_trailing_changelog(self):
        assert extract_delimited_answer(_WRAPPED_WITH_TRAILER) == _CLEAN_BODY

    @pytest.mark.unit
    def test_extracts_simple_block(self):
        assert extract_delimited_answer("<answer>Hello [1].</answer>") == "Hello [1]."

    @pytest.mark.unit
    def test_drops_anything_after_close_tag(self):
        body = "<answer>The answer [1].</answer>\nstray trailing note about my edits"
        assert extract_delimited_answer(body) == "The answer [1]."

    @pytest.mark.unit
    def test_preserves_topical_corrections_section_inside_answer(self):
        """The data-loss-immunity property: a legitimate `## Corrections Made`
        section (errata content) INSIDE <answer> is preserved verbatim — the
        extractor only drops what is AFTER </answer>, never inspecting content."""
        inner = (
            "Overview of the correction notice [1].\n\n"
            "## Corrections Made\n"
            "- The authors removed unsupported claims about efficacy [2]\n"
            "- The appendix re-cited the original dataset [3]"
        )
        body = f"<answer>{inner}</answer>\n\n**Editorial Notes**\n- Tightened wording"
        assert extract_delimited_answer(body) == inner

    @pytest.mark.unit
    def test_no_tags_returns_text_unchanged(self):
        """Non-destructive fallback: with no delimiter, NOTHING is stripped —
        even a topical errata changelog that a content heuristic would delete."""
        body = (
            "Overview [1].\n\n"
            "## Corrections Made\n"
            "- Removed unsupported claims about efficacy, per the notice [2]\n"
            "- Re-cited the original dataset [3]"
        )
        assert extract_delimited_answer(body) == body

    @pytest.mark.unit
    def test_truncated_open_returns_unchanged(self):
        """Truncated close (1 open, 0 close) is ambiguous → returned unchanged
        (content preserved; we never strip on ambiguity)."""
        body = "<answer>\nThe answer was cut off [1]. More text here."
        assert extract_delimited_answer(body) == body

    @pytest.mark.unit
    def test_empty_block_returns_original(self):
        body = "<answer></answer>"
        assert extract_delimited_answer(body) == body

    @pytest.mark.unit
    @pytest.mark.parametrize("value", ["", "   ", "\n\n", None])
    def test_empty_or_whitespace_is_identity(self, value):
        assert extract_delimited_answer(value) == value

    @pytest.mark.unit
    def test_case_insensitive_tags(self):
        assert extract_delimited_answer("<ANSWER>Hi [1].</Answer>") == "Hi [1]."

    @pytest.mark.unit
    def test_multiline_and_citations_preserved(self):
        inner = "Line one [1].\n\n- bullet [2]\n- bullet [3]\n\nClosing [4]."
        assert extract_delimited_answer(f"<answer>\n{inner}\n</answer>") == inner

    @pytest.mark.unit
    def test_multiple_blocks_return_unchanged(self):
        """Ambiguous (two complete blocks) → unchanged; never silently drop a
        block, and never strip (a block could be substantive)."""
        body = "<answer>real answer [1]</answer>\n<answer>second block [2]</answer>"
        assert extract_delimited_answer(body) == body

    @pytest.mark.unit
    def test_partial_wrap_returns_unchanged(self):
        """Substantive content before a (1,1) <answer> block is NOT a start-
        anchored wrapper (partial wrap, or a literal pair) → unchanged; the
        larger part is never dropped and literal tags are never stripped."""
        body = (
            "The capital of France is Paris [1]. It became the capital in 987 "
            "and has been the political center since [2]. The city grew along "
            "the Seine over centuries [3].\n<answer>Paris.</answer>"
        )
        assert extract_delimited_answer(body) == body

    @pytest.mark.unit
    def test_short_preamble_before_block_is_dropped(self):
        """A tiny allowlisted preamble ("Here is …:") is dropped."""
        body = "Here is the synthesis:\n<answer>The full detailed answer [1][2].</answer>"
        assert extract_delimited_answer(body) == "The full detailed answer [1][2]."

    @pytest.mark.unit
    def test_preserves_section_before_partial_wrap(self):
        """T6 HIGH: a real section before a wrapped section is NOT dropped — the
        length proxy is gone; substantive pre-tag content is always kept."""
        body = (
            "## Executive Summary\n"
            "Alpha is true [1]. Beta is disputed [2].\n\n"
            "<answer>\n## Details\n"
            "Gamma follows from the sources [3].\n"
            "Delta has caveats [4]. Epsilon remains unknown [5].\n</answer>"
        )
        # Not a start-anchored wrapper → unchanged; the Executive Summary section
        # (and everything else) is preserved, nothing dropped.
        assert extract_delimited_answer(body) == body

    @pytest.mark.unit
    def test_literal_answer_tags_inside_not_truncated(self):
        """T6 MEDIUM: a synthesis ABOUT <answer> delimiters (literal tags in the
        content) is not truncated at a spurious boundary; content is preserved."""
        body = (
            "<answer>\n"
            "The source recommends writing <answer>content</answer> in prompt "
            "templates [1]. It is a delimiter, not XML data [2].\n"
            "</answer>\n---\n**Key Corrections Implemented**\n- Removed unsupported claims."
        )
        out = extract_delimited_answer(body)
        assert "The source recommends writing" in out
        assert "delimiter, not XML data [2]" in out
        assert not out.startswith("The source recommends writing `<answer>content")


def _src(i: int) -> PreGatheredSource:
    return PreGatheredSource(
        origin="searxng",
        url=f"https://example.com/{i}",
        title=f"Source {i}",
        content=f"Content for source {i}.",
        source_type="article",
    )


class TestAggregatorExtractsAnswer:
    """The free-form and reasoning aggregator paths return the delimited answer."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_freeform_synthesize_extracts_and_drops_trailer(self, monkeypatch):
        async def _fake_call(*args, **kwargs):
            return LLMOutput(
                text=_WRAPPED_WITH_TRAILER,
                source_field="content",
                finish_reason="stop",
                truncated=False,
                reasoning_only=False,
            )

        monkeypatch.setattr("src.synthesis.aggregator.call_with_extraction", _fake_call)
        agg = SynthesisAggregator(llm_client=object(), model="test-model")
        result = await agg.synthesize(
            query="What is the model?",
            sources=[_src(1), _src(2)],
            style=SynthesisStyle.COMPREHENSIVE,
        )
        assert "Key Corrections Implemented" not in result.content
        assert result.content == _CLEAN_BODY

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_freeform_synthesize_falls_back_without_tags(self, monkeypatch):
        """If the model omits the tags, the full synthesis is returned unchanged
        (no data loss) — citations still extracted from it."""
        raw = "The capital is Paris [1]. It has a long history [2]."

        async def _fake_call(*args, **kwargs):
            return LLMOutput(
                text=raw, source_field="content", finish_reason="stop",
                truncated=False, reasoning_only=False,
            )

        monkeypatch.setattr("src.synthesis.aggregator.call_with_extraction", _fake_call)
        agg = SynthesisAggregator(llm_client=object(), model="test-model")
        result = await agg.synthesize(query="q", sources=[_src(1), _src(2)])
        assert result.content == raw

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_reasoning_synthesize_extracts_synthesis_tag(self, monkeypatch):
        """The reasoning path extracts <synthesis> and is immune to a trailer
        after </synthesis> by the same delimiter mechanism."""
        wrapped = (
            "<reasoning>\nthinking...\n</reasoning>\n"
            "<synthesis>\nThe answer body [1].\n</synthesis>\n\n"
            "---\n**Corrections Made:**\n- adjusted phrasing"
        )

        async def _fake_call(*args, **kwargs):
            return LLMOutput(
                text=wrapped, source_field="content", finish_reason="stop",
                truncated=False, reasoning_only=False,
            )

        monkeypatch.setattr("src.synthesis.aggregator.call_with_extraction", _fake_call)
        agg = SynthesisAggregator(llm_client=object(), model="test-model")
        result = await agg.synthesize_with_reasoning(query="q", sources=[_src(1)])
        assert "Corrections Made" not in result.content
        assert result.content == "The answer body [1]."

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_reasoning_synthesize_accepts_style_and_labels_it(self, monkeypatch):
        """Regression (P0): `synthesize_with_reasoning` must accept `style` and
        echo it as `style_used`. The wrapper (wrappers.py) passes `style=`, so a
        missing parameter is a live TypeError on the `reason` MCP tool and the
        `/reason` REST route. This calls the real method (only the LLM call is
        stubbed), so it fails against the pre-fix signature."""
        wrapped = "<synthesis>\nThe answer body [1].\n</synthesis>"

        async def _fake_call(*args, **kwargs):
            return LLMOutput(
                text=wrapped, source_field="content", finish_reason="stop",
                truncated=False, reasoning_only=False,
            )

        monkeypatch.setattr("src.synthesis.aggregator.call_with_extraction", _fake_call)
        agg = SynthesisAggregator(llm_client=object(), model="test-model")
        result = await agg.synthesize_with_reasoning(
            query="q", sources=[_src(1)], style=SynthesisStyle.ACADEMIC,
        )
        assert result.style_used == SynthesisStyle.ACADEMIC
        assert result.content == "The answer body [1]."
