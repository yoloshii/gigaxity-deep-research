"""Tests for src/llm_utils.py extraction helpers.

Locks the Turn 8 codex-review fixes:
- extract_llm_output: PARSE_REQUIRED must not fall back to reasoning-field
  text (only LENIENT may) - a chain-of-thought trace is neither a final
  answer nor a valid parse source.
- combine_llm_outputs: a single failed contributing sub-call must surface as
  subcall_failed=True so the verifier hard-gates the assembled synthesis,
  instead of being masked because the other sub-calls succeeded.
"""

from types import SimpleNamespace

import pytest

from src.llm_utils import (
    ExtractionMode,
    LLMOutput,
    combine_llm_outputs,
    extract_llm_output,
)
from src.synthesis.output_verifier import verify_synthesis_output


def _choice(content="", reasoning="", reasoning_content="", finish_reason="stop"):
    """Build a minimal OpenAI-compatible response choice."""
    return SimpleNamespace(
        message=SimpleNamespace(
            content=content,
            reasoning=reasoning,
            reasoning_content=reasoning_content,
        ),
        finish_reason=finish_reason,
    )


class TestExtractLLMOutputModes:
    """extract_llm_output honors the ExtractionMode contract."""

    @pytest.mark.unit
    def test_parse_required_rejects_reasoning_only(self):
        """PARSE_REQUIRED must NOT return a reasoning-field trace as text.

        Regression: a reasoning-only response (content empty, reasoning field
        populated) used to be returned as text for PARSE_REQUIRED, letting a
        chain-of-thought trace masquerade as a structured answer a parser could
        accidentally accept. PARSE_REQUIRED now gets text="" and the caller
        falls back deterministically.
        """
        choice = _choice(content="", reasoning="Step 1: let me think about the scores...")
        out = extract_llm_output(choice, ExtractionMode.PARSE_REQUIRED)
        assert out.text == ""
        assert out.reasoning_only is True
        assert out.source_field == "reasoning"

    @pytest.mark.unit
    def test_final_answer_rejects_reasoning_only(self):
        """FINAL_ANSWER also refuses a reasoning-field trace as the answer."""
        choice = _choice(content="", reasoning_content="thinking out loud...")
        out = extract_llm_output(choice, ExtractionMode.FINAL_ANSWER)
        assert out.text == ""
        assert out.reasoning_only is True

    @pytest.mark.unit
    def test_lenient_keeps_reasoning_text(self):
        """LENIENT - and only LENIENT - may use the reasoning trace as text."""
        choice = _choice(content="", reasoning="the reasoning IS the output here")
        out = extract_llm_output(choice, ExtractionMode.LENIENT)
        assert out.text == "the reasoning IS the output here"
        assert out.reasoning_only is True

    @pytest.mark.unit
    def test_content_present_returned_for_all_modes(self):
        """A real `content` answer is returned verbatim regardless of mode."""
        for mode in ExtractionMode:
            out = extract_llm_output(_choice(content="real answer"), mode)
            assert out.text == "real answer"
            assert out.reasoning_only is False
            assert out.source_field == "content"

    @pytest.mark.unit
    def test_truncated_flag_set_on_length_finish(self):
        """finish_reason == 'length' surfaces as truncated=True."""
        out = extract_llm_output(
            _choice(content="partial answer", finish_reason="length"),
            ExtractionMode.FINAL_ANSWER,
        )
        assert out.truncated is True

    @pytest.mark.unit
    def test_parse_required_rejects_truncated_content(self):
        """PARSE_REQUIRED rejects truncated content even when it looks complete.

        Regression: a structured response cut short by the token limit
        (finish_reason='length') could still be parsed as complete if the
        fragment happened to contain the expected structure. PARSE_REQUIRED now
        gets text="" on truncation and the caller falls back deterministically.
        """
        choice = _choice(content="0.8\n0.9\n0.7", finish_reason="length")
        out = extract_llm_output(choice, ExtractionMode.PARSE_REQUIRED)
        assert out.text == ""
        assert out.truncated is True

    @pytest.mark.unit
    def test_final_answer_keeps_truncated_content(self):
        """FINAL_ANSWER keeps truncated content - retry + verifier hard-gate handle it."""
        choice = _choice(content="partial final answer", finish_reason="length")
        out = extract_llm_output(choice, ExtractionMode.FINAL_ANSWER)
        assert out.text == "partial final answer"
        assert out.truncated is True

    @pytest.mark.unit
    def test_lenient_keeps_truncated_content(self):
        """LENIENT keeps truncated content."""
        choice = _choice(content="partial lenient text", finish_reason="length")
        out = extract_llm_output(choice, ExtractionMode.LENIENT)
        assert out.text == "partial lenient text"
        assert out.truncated is True


class TestCombineLLMOutputs:
    """combine_llm_outputs surfaces a failed sub-call instead of masking it."""

    @staticmethod
    def _ok(text="real section text"):
        return LLMOutput(
            text=text, source_field="content", finish_reason="stop",
            truncated=False, reasoning_only=False,
        )

    @pytest.mark.unit
    def test_all_good_subcalls_no_failure(self):
        """All contributing calls usable -> subcall_failed is False."""
        combined = combine_llm_outputs("draft", [self._ok(), self._ok()])
        assert combined is not None
        assert combined.subcall_failed is False
        assert combined.reasoning_only is False
        assert combined.truncated is False

    @pytest.mark.unit
    def test_one_reasoning_only_subcall_flags_failure(self):
        """One reasoning-only section must flag subcall_failed.

        Regression: combine_llm_outputs derived reasoning_only via all(), so a
        single failed section among several successful ones reported
        reasoning_only=False and the verifier passed an incomplete synthesis.
        subcall_failed is the weakest-link signal that fixes this.
        """
        bad = LLMOutput(
            text="", source_field="reasoning", finish_reason="stop",
            truncated=False, reasoning_only=True,
        )
        combined = combine_llm_outputs("partial draft", [self._ok(), bad, self._ok()])
        assert combined.subcall_failed is True

    @pytest.mark.unit
    def test_one_empty_subcall_flags_failure(self):
        """An empty-text section (no content, no reasoning) also flags failure."""
        empty = LLMOutput(
            text="", source_field="", finish_reason="stop",
            truncated=False, reasoning_only=False,
        )
        combined = combine_llm_outputs("partial draft", [self._ok(), empty])
        assert combined.subcall_failed is True

    @pytest.mark.unit
    def test_one_truncated_subcall_flags_failure(self):
        """A truncated section flags both truncated and subcall_failed."""
        trunc = LLMOutput(
            text="cut off mid-sen", source_field="content", finish_reason="length",
            truncated=True, reasoning_only=False,
        )
        combined = combine_llm_outputs("draft", [self._ok(), trunc])
        assert combined.truncated is True
        assert combined.subcall_failed is True

    @pytest.mark.unit
    def test_failed_subcall_makes_verification_fail(self):
        """A combined output with a failed sub-call hard-fails the verifier.

        End-to-end lock for codex Turn 8: one failed outline/enhanced section
        must make verify_synthesis_output hard-gate, even though the assembled
        draft is non-empty and cites all its sources.
        """
        bad = LLMOutput(
            text="", source_field="reasoning", finish_reason="stop",
            truncated=False, reasoning_only=True,
        )
        combined = combine_llm_outputs("assembled draft [1] [2]", [self._ok(), bad])
        verdict = verify_synthesis_output(
            content="assembled draft [1] [2]",
            llm_output=combined,
            cited_count=2,
            source_count=2,
        )
        assert verdict.passed is False
        assert any("sub-call" in f for f in verdict.hard_failures)

    @pytest.mark.unit
    def test_no_contributing_outputs_returns_none(self):
        """combine_llm_outputs returns None when there are no usable outputs."""
        assert combine_llm_outputs("draft", []) is None
        assert combine_llm_outputs("draft", [None, None]) is None
