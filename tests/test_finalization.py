"""Tests for src/synthesis/finalization.py (Phase 0)."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.connectors.base import Source
from src.llm_utils import LLMOutput
from src.synthesis import (
    AggregatedSynthesis,
    FinalizedSynthesis,
    OutlinedSynthesis,
    PreGatheredSource,
    SynthesisStyle,
    finalize_synthesis,
)
from src.synthesis.contradictions import (
    Contradiction,
    ContradictionDetectionResult,
    ContradictionSeverity,
)
from src.synthesis.outline import SynthesisOutline


# ---------------------------------------------------------------------------
# Fixtures.
# ---------------------------------------------------------------------------


def _pre_source(title: str, content: str, *, origin: str = "exa", url: str = "https://example.com") -> PreGatheredSource:
    return PreGatheredSource(
        origin=origin,
        url=url,
        title=title,
        content=content,
        source_type="article",
    )


def _connector_source(title: str, content: str, *, id_: str = "tv_a1b2c3", url: str = "https://example.com") -> Source:
    return Source(
        id=id_,
        title=title,
        url=url,
        content=content,
        score=0.5,
        connector="tavily",
    )


def _good_aggregated(content: str = "Result with [1] cite.", *, llm_output: LLMOutput | None = None) -> AggregatedSynthesis:
    return AggregatedSynthesis(
        content=content,
        citations=[{"number": 1, "id": "1", "source_id": None, "title": "T", "url": "u",
                    "origin": "exa", "source_type": "article"}],
        source_attribution={"exa": 1.0},
        confidence=0.7,
        style_used=SynthesisStyle.COMPREHENSIVE,
        word_count=4,
        llm_output=llm_output,
    )


# ---------------------------------------------------------------------------
# Normalization tests — one per result shape.
# ---------------------------------------------------------------------------


class TestFinalizeAggregatedResult:
    """`finalize_synthesis` over an AggregatedSynthesis (Aggregator path)."""

    @pytest.mark.unit
    def test_passthrough_fields(self):
        """All common fields are copied from AggregatedSynthesis verbatim."""
        sources = [_pre_source("FastAPI", "FastAPI is fast and modern [1]")]
        result = _good_aggregated()
        finalized = finalize_synthesis(
            query="What is FastAPI?",
            result=result,
            sources=sources,
            surface="rest_synthesize",
        )
        assert finalized.raw_content == "Result with [1] cite."
        assert len(finalized.citations) == 1
        assert finalized.source_attribution == {"exa": 1.0}
        assert finalized.confidence == 0.7
        assert finalized.word_count == 4
        assert finalized.style_used == SynthesisStyle.COMPREHENSIVE
        assert finalized.surface == "rest_synthesize"

    @pytest.mark.unit
    def test_pass_verdict_clean(self):
        """Clean result with citations → passed verdict, cache_eligible=True."""
        sources = [_pre_source("FastAPI", "FastAPI doc body [1]")]
        result = _good_aggregated()
        finalized = finalize_synthesis(
            query="What is FastAPI?",
            result=result,
            sources=sources,
            surface="rest_synthesize",
        )
        assert finalized.verdict.passed
        assert finalized.cache_eligible
        assert finalized.verdict.verdict_class == "pass"
        # Clean pass — no annotation.
        assert finalized.safe_content == finalized.raw_content

    @pytest.mark.unit
    def test_hard_fail_no_citations(self):
        """No citations against N≥1 sources → hard fail, cache_eligible=False."""
        sources = [_pre_source("FastAPI", "FastAPI body")]
        result = AggregatedSynthesis(
            content="A paragraph with no [N] markers anywhere.",
            citations=[],
            source_attribution={},
            confidence=0.5,
            style_used=SynthesisStyle.COMPREHENSIVE,
            word_count=8,
        )
        finalized = finalize_synthesis(
            query="What is FastAPI?",
            result=result,
            sources=sources,
            surface="rest_synthesize",
        )
        assert not finalized.verdict.passed
        assert not finalized.cache_eligible
        assert finalized.verdict.verdict_class == "hard_fail"
        # safe_content carries the in-band failure header.
        assert "Synthesis verification FAILED" in finalized.safe_content
        assert "cites none of the 1 provided sources" in finalized.safe_content

    @pytest.mark.unit
    def test_soft_warning_partial_citations(self):
        """Partial citation coverage → soft warning, passed=True but annotated."""
        sources = [_pre_source("A", "Body of A"), _pre_source("B", "Body of B")]
        result = AggregatedSynthesis(
            content="Mention only [1].",
            citations=[{"number": 1, "id": "1", "source_id": None, "title": "A", "url": "u",
                        "origin": "exa", "source_type": "article"}],
            source_attribution={"exa": 1.0},
            confidence=0.6,
            style_used=SynthesisStyle.COMPREHENSIVE,
            word_count=3,
        )
        finalized = finalize_synthesis(
            query="What is A?",
            result=result,
            sources=sources,
            surface="rest_synthesize",
        )
        assert finalized.verdict.passed
        assert finalized.cache_eligible
        assert finalized.verdict.soft_warnings  # partial coverage advisory
        # Soft-warning path annotates with verification notes (not failure header).
        assert "Verification notes" in finalized.safe_content
        assert "Synthesis verification FAILED" not in finalized.safe_content

    @pytest.mark.unit
    def test_truncated_llm_output_hard_fails(self):
        """`llm_output.truncated=True` → hard fail."""
        sources = [_pre_source("A", "Body")]
        result = _good_aggregated(
            llm_output=LLMOutput(
                text="trunc", source_field="content", finish_reason="length",
                truncated=True, reasoning_only=False,
            ),
        )
        finalized = finalize_synthesis(
            query="q",
            result=result,
            sources=sources,
            surface="rest_synthesize",
        )
        assert not finalized.verdict.passed
        assert any("truncat" in f.lower() for f in finalized.verdict.hard_failures)


class TestFinalizeOutlinedResult:
    """`finalize_synthesis` over an OutlinedSynthesis (outline path)."""

    @pytest.mark.unit
    def test_extras_carry_outline_fields(self):
        """outline_sections + sections + critique surface in extras."""
        sources = [_pre_source("A", "Body A [1]")]
        result = OutlinedSynthesis(
            content="## Overview\nContent with cite [1].",
            outline=SynthesisOutline(sections=["Overview", "Details"]),
            sections={"Overview": "Content [1]", "Details": ""},
            critique=None,
            refined=False,
            word_count=4,
            llm_output=None,
        )
        finalized = finalize_synthesis(
            query="q",
            result=result,
            sources=sources,
            surface="mcp_synthesize",
        )
        assert finalized.extras["outline_sections"] == ["Overview", "Details"]
        assert finalized.extras["sections"] == {"Overview": "Content [1]", "Details": ""}
        assert finalized.extras["refined"] is False
        assert "critique" not in finalized.extras  # None critique omitted

    @pytest.mark.unit
    def test_citations_extracted_via_shared_resolver(self):
        """Outline has no .citations field — extractor runs against content."""
        sources = [_pre_source("A", "Body"), _pre_source("B", "Body")]
        result = OutlinedSynthesis(
            content="First [1] and second [2].",
            outline=SynthesisOutline(sections=["S"]),
            sections={"S": "First [1] and second [2]."},
            critique=None,
            refined=False,
            word_count=4,
            llm_output=None,
        )
        finalized = finalize_synthesis(
            query="q",
            result=result,
            sources=sources,
            surface="mcp_synthesize",
        )
        # Both [1] and [2] extracted by extract_numeric_citations.
        assert {c["number"] for c in finalized.citations} == {1, 2}
        assert finalized.verdict.passed


class TestFinalizeEngineDictResult:
    """`finalize_synthesis` over a dict (SynthesisEngine path)."""

    @pytest.mark.unit
    def test_engine_dict_normalization(self):
        """Engine dict → content/citations/extras carry through; llm_output=None."""
        sources = [_connector_source("A", "Body [1]")]
        result = {
            "content": "Answer with cite [1].",
            "citations": [{"number": 1, "id": "1", "source_id": "tv_a1b2c3", "title": "A",
                           "url": "https://example.com", "origin": None, "source_type": None}],
            "sources_used": [sources[0]],
            "model": "test-model",
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }
        finalized = finalize_synthesis(
            query="q",
            result=result,
            sources=sources,
            surface="mcp_research",
        )
        assert finalized.raw_content == "Answer with cite [1]."
        assert len(finalized.citations) == 1
        assert finalized.extras["model"] == "test-model"
        assert finalized.extras["usage"]["prompt_tokens"] == 10
        assert finalized.extras["sources_used"] == [sources[0]]
        assert finalized.llm_output is None  # engine never exposes LLMOutput
        assert finalized.source_attribution == {}  # engine path skips attribution
        assert finalized.confidence == 0.0  # engine path defaults

    @pytest.mark.unit
    def test_engine_error_carries_through_extras(self):
        """An `error` key in the engine dict is preserved in extras (caller may swallow)."""
        sources = [_connector_source("A", "Body")]
        result = {
            "content": "Synthesis error: timeout",
            "citations": [],
            "sources_used": [],
            "error": "timeout",
        }
        finalized = finalize_synthesis(
            query="q",
            result=result,
            sources=sources,
            surface="mcp_research",
        )
        assert finalized.extras.get("error") == "timeout"
        # The verifier should still hard-fail (no citations + N≥1 sources).
        assert not finalized.verdict.passed
        assert not finalized.cache_eligible


class TestFinalizeBoundaryCases:
    """Edge cases of the public `finalize_synthesis` API."""

    @pytest.mark.unit
    def test_unsupported_result_type_raises(self):
        """A non-dict/Aggregated/Outlined value raises TypeError."""
        with pytest.raises(TypeError, match="unsupported result type"):
            finalize_synthesis(
                query="q",
                result="just a string",
                sources=[_pre_source("A", "B")],
                surface="rest_synthesize",
            )

    @pytest.mark.unit
    def test_pre_extracted_query_entities_short_circuits(self):
        """Pre-extracted query_entities bypasses extract_query_entities()."""
        sources = [_pre_source("FastAPI", "FastAPI body [1]")]
        result = _good_aggregated()
        with patch("src.synthesis.finalization.extract_query_entities") as ex_mock:
            finalize_synthesis(
                query="What about FastAPI?",
                result=result,
                sources=sources,
                query_entities=["FastAPI"],
                surface="rest_synthesize",
            )
            ex_mock.assert_not_called()

    @pytest.mark.unit
    def test_query_entities_auto_extracted_when_none(self):
        """When query_entities=None, extract_query_entities() runs."""
        sources = [_pre_source("FastAPI", "FastAPI body [1]")]
        result = _good_aggregated()
        with patch(
            "src.synthesis.finalization.extract_query_entities",
            return_value=["FastAPI"],
        ) as ex_mock:
            finalize_synthesis(
                query="What about FastAPI?",
                result=result,
                sources=sources,
                surface="rest_synthesize",
            )
            ex_mock.assert_called_once()

    @pytest.mark.unit
    def test_contradiction_result_threaded_to_verifier(self):
        """`contradiction_result=` surfaces as a verifier soft warning."""
        sources = [_pre_source("A", "Body A [1]"), _pre_source("B", "Body B")]
        result = _good_aggregated(content="Result [1].")
        detection = ContradictionDetectionResult(
            contradictions=[
                Contradiction(
                    topic="X",
                    position_a="claim a",
                    source_a=1,
                    position_b="claim b",
                    source_b=2,
                    severity=ContradictionSeverity.MAJOR,
                ),
            ],
        )
        finalized = finalize_synthesis(
            query="q",
            result=result,
            sources=sources,
            contradiction_result=detection,
            surface="rest_synthesize",
        )
        # Soft warning surfaces the contradiction count.
        assert any("contradiction" in w.lower() for w in finalized.verdict.soft_warnings)

    @pytest.mark.unit
    def test_cache_eligible_mirrors_passed(self):
        """`cache_eligible` strictly follows verdict.passed."""
        sources = [_pre_source("A", "Body A [1]")]
        # Pass case
        passed = finalize_synthesis(
            query="q",
            result=_good_aggregated(),
            sources=sources,
            surface="rest_synthesize",
        )
        assert passed.cache_eligible == passed.verdict.passed is True
        # Fail case (no citations)
        failed = finalize_synthesis(
            query="q",
            result=AggregatedSynthesis(
                content="No cites here.", citations=[], source_attribution={},
                confidence=0.0, style_used=SynthesisStyle.COMPREHENSIVE,
                word_count=3, llm_output=None,
            ),
            sources=sources,
            surface="rest_synthesize",
        )
        assert failed.cache_eligible is False
        assert failed.verdict.passed is False

    @pytest.mark.unit
    def test_sources_with_duck_typed_attrs_handled(self):
        """Connector Source AND PreGatheredSource both work as `sources`."""
        for ctor in (_pre_source, _connector_source):
            src = ctor("T", "Body [1]")
            result = _good_aggregated()
            finalized = finalize_synthesis(
                query="q",
                result=result,
                sources=[src],
                surface="rest_synthesize",
            )
            assert finalized.verdict.passed


class TestVerdictEnvelope:
    """Phase 0 backward-compat envelope on SynthesisVerdict."""

    @pytest.mark.unit
    def test_verdict_class_pass_default(self):
        """A clean verdict has verdict_class='pass'."""
        sources = [_pre_source("A", "Body A [1]")]
        finalized = finalize_synthesis(
            query="q",
            result=_good_aggregated(),
            sources=sources,
            surface="rest_synthesize",
        )
        assert finalized.verdict.verdict_class == "pass"

    @pytest.mark.unit
    def test_verdict_class_hard_fail_when_failures_present(self):
        """A hard-failed verdict has verdict_class='hard_fail'."""
        sources = [_pre_source("A", "Body")]
        result = AggregatedSynthesis(
            content="No citations.",
            citations=[],
            source_attribution={},
            confidence=0.0,
            style_used=SynthesisStyle.COMPREHENSIVE,
            word_count=2,
            llm_output=None,
        )
        finalized = finalize_synthesis(
            query="q",
            result=result,
            sources=sources,
            surface="rest_synthesize",
        )
        assert finalized.verdict.verdict_class == "hard_fail"

    @pytest.mark.unit
    def test_new_envelope_fields_default_empty(self):
        """failure_codes / warnings / diagnostics / retry_advice default to empty."""
        sources = [_pre_source("A", "Body A [1]")]
        finalized = finalize_synthesis(
            query="q",
            result=_good_aggregated(),
            sources=sources,
            surface="rest_synthesize",
        )
        v = finalized.verdict
        assert v.failure_codes == []
        assert v.warnings == []
        assert v.diagnostics.gap_declarations == []
        assert v.diagnostics.gate_diagnostics is None
        assert v.retry_advice is None

    @pytest.mark.unit
    def test_existing_passed_property_unchanged(self):
        """The existing `.passed` property semantics are unchanged."""
        sources = [_pre_source("A", "Body A [1]")]
        finalized = finalize_synthesis(
            query="q",
            result=_good_aggregated(),
            sources=sources,
            surface="rest_synthesize",
        )
        # `.passed` still computed from hard_failures; preserved verbatim.
        assert finalized.verdict.passed == (not finalized.verdict.hard_failures)


class TestVerdictPostInit:
    """Codex Turn 1 F5: __post_init__ reconciles verdict_class with hard_failures."""

    @pytest.mark.unit
    def test_direct_construction_with_hard_failures_sets_hard_fail(self):
        """SynthesisVerdict(hard_failures=["x"]) → verdict_class='hard_fail'.

        Before F5 fix this returned verdict_class='pass' (the default), a
        contradictory state. __post_init__ reconciles on direct construction.
        """
        from src.synthesis import SynthesisVerdict
        v = SynthesisVerdict(hard_failures=["boom"])
        assert v.verdict_class == "hard_fail"
        assert v.passed is False

    @pytest.mark.unit
    def test_direct_construction_with_no_failures_keeps_pass(self):
        """Default SynthesisVerdict() → verdict_class='pass'."""
        from src.synthesis import SynthesisVerdict
        v = SynthesisVerdict()
        assert v.verdict_class == "pass"
        assert v.passed is True

    @pytest.mark.unit
    def test_direct_construction_calibrated_gap_preserved(self):
        """Phase 1 can explicitly mark a clean-but-acknowledged-gap verdict.

        `SynthesisVerdict(verdict_class="calibrated_gap")` with empty
        hard_failures must NOT be reset to 'pass' — calibrated_gap is the
        Phase 1 structural-gap acknowledgement signal that survives
        post-init.
        """
        from src.synthesis import SynthesisVerdict
        v = SynthesisVerdict(verdict_class="calibrated_gap")
        assert v.verdict_class == "calibrated_gap"
        assert v.passed is True

    @pytest.mark.unit
    def test_direct_construction_hard_fail_with_empty_failures_demoted(self):
        """A caller passing verdict_class='hard_fail' with empty hard_failures
        is in a contradictory state. __post_init__ demotes to 'pass' since the
        empty list is authoritative."""
        from src.synthesis import SynthesisVerdict
        v = SynthesisVerdict(verdict_class="hard_fail")
        assert v.verdict_class == "pass"
        assert v.passed is True

    @pytest.mark.unit
    def test_direct_construction_failures_override_calibrated_gap(self):
        """A caller passing verdict_class='calibrated_gap' WITH hard_failures
        is contradictory. hard_failures wins."""
        from src.synthesis import SynthesisVerdict
        v = SynthesisVerdict(verdict_class="calibrated_gap", hard_failures=["err"])
        assert v.verdict_class == "hard_fail"


class TestVerdictToSchema:
    """Codex Turn 1 F4: SynthesisVerdictSchema extended with envelope fields,
    and `verdict_to_schema` populates them from `SynthesisVerdict`."""

    @pytest.mark.unit
    def test_pass_verdict_serializes_with_defaults(self):
        from src.synthesis import SynthesisVerdict
        from src.api.schemas import verdict_to_schema

        v = SynthesisVerdict()
        s = verdict_to_schema(v)
        assert s.passed is True
        assert s.verdict_class == "pass"
        assert s.failure_codes == []
        assert s.warnings == []
        assert s.diagnostics.gap_declarations == []
        assert s.retry_advice is None

    @pytest.mark.unit
    def test_hard_fail_verdict_serializes_class_and_failures(self):
        from src.synthesis import SynthesisVerdict
        from src.api.schemas import verdict_to_schema

        v = SynthesisVerdict(hard_failures=["boom"])
        s = verdict_to_schema(v)
        assert s.passed is False
        assert s.verdict_class == "hard_fail"
        assert s.hard_failures == ["boom"]

    @pytest.mark.unit
    def test_warnings_and_diagnostics_round_trip(self):
        from src.synthesis import SynthesisVerdict
        from src.synthesis.output_verifier import VerdictWarning, VerdictDiagnostics
        from src.api.schemas import verdict_to_schema

        v = SynthesisVerdict()
        v.warnings.append(VerdictWarning(code="phase5a_test", message="hi", severity="warning"))
        v.diagnostics = VerdictDiagnostics(
            gap_declarations=["E1", "E2"],
            tier_composition={"T1": 1, "T2": 2},
        )
        s = verdict_to_schema(v)
        assert len(s.warnings) == 1
        assert s.warnings[0].code == "phase5a_test"
        assert s.warnings[0].message == "hi"
        assert s.diagnostics.gap_declarations == ["E1", "E2"]
        assert s.diagnostics.tier_composition == {"T1": 1, "T2": 2}

    @pytest.mark.unit
    def test_retry_advice_round_trip(self):
        from src.synthesis import SynthesisVerdict
        from src.synthesis.output_verifier import RetryAdvice
        from src.api.schemas import verdict_to_schema

        v = SynthesisVerdict(hard_failures=["needs more sources"])
        v.retry_advice = RetryAdvice(
            caller_action="gather_more_sources",
            missing_entities=["Foo", "Bar"],
            suggested_queries=["foo bar comparison"],
            rationale="entity coverage gap",
        )
        s = verdict_to_schema(v)
        assert s.retry_advice is not None
        assert s.retry_advice.caller_action == "gather_more_sources"
        assert s.retry_advice.missing_entities == ["Foo", "Bar"]
        assert s.retry_advice.suggested_queries == ["foo bar comparison"]
        assert s.retry_advice.rationale == "entity coverage gap"

    @pytest.mark.unit
    def test_schema_default_envelope_backward_compat(self):
        """Pydantic schema's new fields all default — existing clients that
        only read passed/hard_failures/soft_warnings see no observable
        change."""
        from src.api.schemas import SynthesisVerdictSchema
        s = SynthesisVerdictSchema(passed=True)
        assert s.verdict_class == "pass"
        assert s.failure_codes == []
        assert s.warnings == []
        assert s.retry_advice is None
