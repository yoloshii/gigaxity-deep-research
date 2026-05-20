"""A1: evidence-gated rescue on the degraded scorer path + gate_degraded.

The synthesis-skip (REJECT / PARTIAL-zero) is only justified when the scorer is
reliable. On scorer_path == "llm_fallback_heuristic" the LLM scorer failed, so a
low average is weak evidence of irrelevance: retain any source the de-diluted
keyword heuristic still scores >= pass_threshold, else fail CLOSED. Blind
pass-all was rejected by codex (the post-synthesis verifier does not prove broad
relevance). Scope is strictly the fallback path — heuristic_only and llm REJECTs
stand. gate_degraded flags every fallback-path result for the caller (codex
design 019e4569).
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.synthesis.quality_gate import (
    SourceQualityGate,
    QualityDecision,
    _ScoringOutcome,
)
from src.api.schemas import QualityGateSchema


def _src(i):
    return SimpleNamespace(title=f"S{i}", content=f"content {i}")


def _run(scores, scorer_path, *, reject=0.2, pass_=0.4, fallback_reason=None):
    """Drive evaluate() with a pinned scorer outcome (decouples A1 from the scorer)."""
    gate = SourceQualityGate(reject_threshold=reject, pass_threshold=pass_)
    gate._score_sources = AsyncMock(
        return_value=_ScoringOutcome(scores, scorer_path, fallback_reason)
    )
    sources = [_src(i) for i in range(len(scores))]
    return asyncio.run(gate.evaluate("query", sources))


def test_degraded_reject_rescues_passing_source():
    """Degraded + avg<reject + one source >= pass -> PARTIAL retaining it, flagged."""
    r = _run([0.5, 0.0, 0.0], "llm_fallback_heuristic")  # avg 0.167 < 0.2
    assert r.decision == QualityDecision.PARTIAL
    assert len(r.good_sources) == 1
    assert len(r.rejected_sources) == 2
    assert r.gate_degraded is True


def test_degraded_reject_no_passing_source_fails_closed():
    """Degraded + avg<reject + no source >= pass -> REJECT stands (fail closed), flagged."""
    r = _run([0.1, 0.1, 0.1], "llm_fallback_heuristic")
    assert r.decision == QualityDecision.REJECT
    assert r.good_sources == []
    assert r.gate_degraded is True


def test_heuristic_only_reject_not_rescued():
    """heuristic_only is a legitimate primary scorer — a passing source does NOT trigger rescue."""
    r = _run([0.5, 0.0, 0.0], "heuristic_only")  # same scores as the rescue case
    assert r.decision == QualityDecision.REJECT
    assert r.good_sources == []
    assert r.gate_degraded is False


def test_llm_reject_not_rescued():
    """A confident LLM REJECT stands and is not flagged degraded."""
    r = _run([0.5, 0.0, 0.0], "llm")
    assert r.decision == QualityDecision.REJECT
    assert r.good_sources == []
    assert r.gate_degraded is False


def test_degraded_proceed_sets_flag():
    """A degraded PROCEED (avg ok, all pass) still carries gate_degraded for the caveat."""
    r = _run([0.8, 0.7], "llm_fallback_heuristic")
    assert r.decision == QualityDecision.PROCEED
    assert r.gate_degraded is True


def test_degraded_partial_zero_fails_closed_with_flag():
    """Degraded + avg>=reject but no source >= pass -> PARTIAL-zero stands, flagged."""
    r = _run([0.3, 0.35], "llm_fallback_heuristic")  # avg 0.325 >= 0.2, none >= 0.4
    assert r.decision == QualityDecision.PARTIAL
    assert r.good_sources == []
    assert r.gate_degraded is True


def test_non_degraded_proceed_flag_false():
    r = _run([0.8, 0.7], "llm")
    assert r.decision == QualityDecision.PROCEED
    assert r.gate_degraded is False


def test_schema_carries_gate_degraded():
    s = QualityGateSchema(
        decision="proceed", avg_quality=0.8, passed_count=2, rejected_count=0,
        gate_degraded=True,
    )
    assert s.gate_degraded is True


def test_schema_gate_degraded_defaults_false():
    s = QualityGateSchema(
        decision="proceed", avg_quality=0.8, passed_count=2, rejected_count=0,
    )
    assert s.gate_degraded is False
