"""Q3 observability: scorer-path + fallback-reason provenance on the quality gate.

Each test pins one scorer path so a REJECT / PARTIAL-zero-passed outcome can be
diagnosed without re-running: which scorer produced the scores, and on the
degraded fallback path, why. Asserts CORRECT behavior — a REJECT derived from
the keyword heuristic must be distinguishable from a confident LLM-scored one,
which is the gap that leaves a REJECT/PARTIAL-zero outcome un-diagnosable after the fact.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.synthesis.quality_gate import SourceQualityGate, QualityDecision
from src.api.schemas import QualityGateSchema


def _src(title, content):
    return SimpleNamespace(title=title, content=content)


def test_heuristic_only_when_no_llm_client():
    """No llm_client → heuristic is the primary scorer; tagged heuristic_only, no fallback_reason."""
    gate = SourceQualityGate()  # no llm_client
    sources = [_src("Sandbox Tool", "agentic llm runtime security sandbox guardrails")]
    result = asyncio.run(gate.evaluate("agentic llm runtime security sandbox", sources))
    assert result.scorer_path == "heuristic_only"
    assert result.fallback_reason is None
    assert result.source_scores is not None


def test_llm_path_tagged_when_scorer_returns_clean_scores():
    """LLM scorer returns exactly one score per source → path 'llm', no fallback."""
    gate = SourceQualityGate(llm_client=object())  # truthy client
    gate._call_llm = AsyncMock(return_value=SimpleNamespace(text="0.8\n0.7"))
    sources = [_src("A", "alpha"), _src("B", "beta")]
    result = asyncio.run(gate.evaluate("topic", sources))
    assert result.scorer_path == "llm"
    assert result.fallback_reason is None
    assert result.source_scores == [0.8, 0.7]


def test_llm_fallback_on_count_mismatch():
    """LLM returns fewer scores than sources → silent heuristic fallback, tagged with reason.

    This is the common small-model failure mode: the model emits a score
    block that doesn't map 1:1 to sources, the gate silently degrades, and the
    keyword heuristic then rejects on-topic sources.
    """
    gate = SourceQualityGate(llm_client=object())
    gate._call_llm = AsyncMock(return_value=SimpleNamespace(text="0.8"))  # 1 score, 2 sources
    sources = [_src("A", "alpha security sandbox"), _src("B", "beta runtime guardrails")]
    result = asyncio.run(gate.evaluate("security sandbox runtime", sources))
    assert result.scorer_path == "llm_fallback_heuristic"
    assert result.fallback_reason.startswith("score_count_mismatch")


def test_llm_fallback_on_exception():
    """LLM call raises → heuristic fallback, reason records the exception type."""
    gate = SourceQualityGate(llm_client=object())
    gate._call_llm = AsyncMock(side_effect=RuntimeError("boom"))
    sources = [_src("A", "alpha security"), _src("B", "beta runtime")]
    result = asyncio.run(gate.evaluate("security runtime", sources))
    assert result.scorer_path == "llm_fallback_heuristic"
    assert result.fallback_reason.startswith("llm_call_failed")
    assert "RuntimeError" in result.fallback_reason


def test_no_sources_marked():
    """Empty source set → REJECT tagged no_sources (no scoring ran)."""
    gate = SourceQualityGate(llm_client=object())
    result = asyncio.run(gate.evaluate("anything", []))
    assert result.decision == QualityDecision.REJECT
    assert result.scorer_path is None
    assert result.fallback_reason == "no_sources"


def test_evaluate_sync_tags_heuristic_only():
    """Sync heuristic-only evaluation tags scorer_path for parity with async."""
    gate = SourceQualityGate()
    sources = [_src("Sandbox", "agentic llm runtime security sandbox")]
    result = gate.evaluate_sync("agentic llm runtime security sandbox", sources)
    assert result.scorer_path == "heuristic_only"


def test_schema_carries_provenance_fields():
    """QualityGateSchema surfaces the provenance fields to API/MCP callers."""
    schema = QualityGateSchema(
        decision="reject",
        avg_quality=0.18,
        passed_count=0,
        rejected_count=5,
        suggestion="gather better sources",
        scorer_path="llm_fallback_heuristic",
        fallback_reason="score_count_mismatch: parsed 3 for 5 sources",
        source_scores=[0.25, 0.35, 0.06, 0.13, 0.32],
        reject_threshold=0.2,
        pass_threshold=0.4,
    )
    assert schema.scorer_path == "llm_fallback_heuristic"
    assert schema.fallback_reason.startswith("score_count_mismatch")
    assert schema.source_scores == [0.25, 0.35, 0.06, 0.13, 0.32]
    assert schema.reject_threshold == 0.2
    assert schema.pass_threshold == 0.4


def test_schema_defaults_backward_compatible():
    """Callers that omit the provenance fields still construct (additive change)."""
    schema = QualityGateSchema(
        decision="proceed", avg_quality=0.8, passed_count=3, rejected_count=0,
    )
    assert schema.scorer_path is None
    assert schema.fallback_reason is None
    assert schema.source_scores is None
    assert schema.reject_threshold is None
    assert schema.pass_threshold is None
