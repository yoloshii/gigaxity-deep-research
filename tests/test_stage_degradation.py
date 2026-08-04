"""Unit tests for the shared StageDegradation record and its classifiers.

Code precedence under multiple simultaneous causes (design Q6b): `truncated`
is primary — finish_reason == "length" is the one signal that tells an
operator to raise the budget — with `reasoning_only` retained as a
structured secondary flag, never hidden by the primary code.
"""

import pytest

from src.degradation import (
    DegradationCode,
    StageDegradation,
    degradation_from_output,
    partial_degradation,
    transport_degradation,
)
from src.llm_utils import LLMOutput


def _output(
    text: str = "",
    source_field: str = "",
    finish_reason: str = "stop",
    truncated: bool = False,
    reasoning_only: bool = False,
) -> LLMOutput:
    return LLMOutput(
        text=text,
        source_field=source_field,
        finish_reason=finish_reason,
        truncated=truncated,
        reasoning_only=reasoning_only,
    )


@pytest.mark.unit
class TestDegradationFromOutput:
    def test_truncated_wins_over_reasoning_only(self):
        """Both causes true → primary code truncated, reasoning_only kept
        as the secondary flag (Q6b precedence)."""
        deg = degradation_from_output(
            "outline",
            _output(
                source_field="reasoning_content",
                finish_reason="length",
                truncated=True,
                reasoning_only=True,
            ),
            parse_failed=True,
            fallback_used=True,
            message="outline parse rejected",
        )
        assert deg.code is DegradationCode.TRUNCATED
        assert deg.truncated is True
        assert deg.reasoning_only is True
        assert deg.finish_reason == "length"

    def test_reasoning_only(self):
        deg = degradation_from_output(
            "critique",
            _output(source_field="reasoning_content", reasoning_only=True),
            parse_failed=True,
            fallback_used=True,
            message="critique unavailable",
        )
        assert deg.code is DegradationCode.REASONING_ONLY
        assert deg.truncated is False

    def test_truncated_content_blanked_by_parse_required(self):
        """PARSE_REQUIRED blanks truncated content: reasoning_only=False,
        source_field='content', text='' — a distinct path from
        reasoning-starvation that must still classify as truncated."""
        deg = degradation_from_output(
            "gaps",
            _output(
                source_field="content",
                finish_reason="length",
                truncated=True,
                reasoning_only=False,
            ),
            parse_failed=True,
            fallback_used=True,
            message="gap parse rejected",
        )
        assert deg.code is DegradationCode.TRUNCATED
        assert deg.reasoning_only is False

    def test_malformed_nonblank_text_rejected_by_grammar(self):
        deg = degradation_from_output(
            "landscape",
            _output(text="chatty prose with no records", source_field="content"),
            parse_failed=True,
            fallback_used=True,
            message="landscape grammar rejected",
        )
        assert deg.code is DegradationCode.MALFORMED

    def test_empty_response(self):
        deg = degradation_from_output(
            "source_scoring",
            _output(),
            parse_failed=True,
            fallback_used=True,
            message="scoring response empty",
        )
        assert deg.code is DegradationCode.EMPTY
        assert deg.finish_reason == "stop"


@pytest.mark.unit
class TestOtherConstructors:
    def test_partial_is_not_a_failure(self):
        deg = partial_degradation("expansion", "2 of 3 variants usable")
        assert deg.code is DegradationCode.PARTIAL
        assert deg.parse_failed is False
        assert deg.fallback_used is False

    def test_transport_records_fallback(self):
        deg = transport_degradation("preview", "preview call failed")
        assert deg.code is DegradationCode.TRANSPORT_ERROR
        assert deg.parse_failed is False
        assert deg.fallback_used is True

    def test_to_dict_is_json_safe(self):
        deg = transport_degradation("expansion", "expansion call failed")
        d = deg.to_dict()
        assert d["code"] == "transport_error"
        assert set(d) == {
            "stage",
            "code",
            "parse_failed",
            "fallback_used",
            "message",
            "truncated",
            "reasoning_only",
            "finish_reason",
        }


@pytest.mark.unit
def test_carriers_default_empty():
    """All three public carriers default to an empty degradation list
    (backward-compatible additive fields)."""
    from src.discovery.expansion import ExpandedQuery
    from src.discovery.explorer import (
        DiscoveryResult,
        KnowledgeLandscape,
    )
    from src.synthesis.outline import OutlinedSynthesis, SynthesisOutline

    eq = ExpandedQuery(original="q")
    assert eq.degradations == []

    dr = DiscoveryResult(
        query="q",
        landscape=KnowledgeLandscape([], [], [], []),
        knowledge_gaps=[],
        sources=[],
        synthesis_preview="",
        recommended_deep_dives=[],
    )
    assert dr.degradations == []

    osyn = OutlinedSynthesis(
        content="", outline=SynthesisOutline(sections=[]), sections={}
    )
    assert osyn.degradations == []
