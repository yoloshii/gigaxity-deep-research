"""Gate-demotion fail-open behavior (v0.6.0).

Generalizes the v0.5.0 entity-coverage demotion to the relevance gate: a content
gate SCORES + LABELS, it never silently DELETES/REFUSES. When the pre-synthesis
relevance gate returns REJECT (or PARTIAL with zero good sources) but at least one
source cleared the fail-open floor (``settings.fail_open_min_source_score``,
default 0.3 = REJECT_THRESHOLD), the pipeline now FAILS OPEN — it synthesizes over
the weak (set-aside) sources with a low-relevance caveat instead of refusing — and
marks the result non-cacheable. Below the floor there is no positive evidence to
ground a synthesis, so the gate still hard-refuses.

Coverage map (unit-level contract locks; backend-agnostic):
- R2-C1 fail-open floor predicate + caveat            → fail_open_eligible / caveat tests
- R2-C2 cache carrier (fail-open non-cacheable;        → apply_fail_open unit test
  entity-coverage soft-pass STAYS cacheable)
- C5 never-vaporize rejected-source provenance         → rejected_provenance unit test
- D1 MINOR contradictions stay internal                → surfaced_contradictions unit test

The REST /research fail-open path is covered in test_codex_t7_v022_fixes.py
(test_rest_research_partial_with_zero_good_fails_open); the below-floor refuse path
is covered there too (scores below the floor still short-circuit). The MCP + the
other REST surfaces apply the same gate-result predicate (fail_open_eligible) and
the same apply_fail_open carrier verified here.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.synthesis import (
    AggregatedSynthesis,
    FinalizedSynthesis,
    PreGatheredSource,
    QualityDecision,
    SynthesisStyle,
    SynthesisVerdict,
    apply_fail_open,
)
from src.synthesis.quality_gate import QualityGateResult
from src.synthesis.contradictions import (
    Contradiction,
    ContradictionSeverity,
    surfaced_contradictions,
)

FLOOR = 0.3


def _src(n: int) -> PreGatheredSource:
    return PreGatheredSource(
        origin="t", url=f"http://example.com/{n}", title=f"S{n}",
        content=f"content {n}", source_type="article",
    )


def _gate_result(decision, avg, scores, good=None, rejected=None, rejected_scores=None):
    return QualityGateResult(
        decision=decision,
        avg_quality=avg,
        good_sources=good or [],
        rejected_sources=rejected or [],
        source_scores=scores,
        suggestion="weak",
        rejected_scores=rejected_scores,
    )


def _aggregated(content="Weak synthesis [1]."):
    return AggregatedSynthesis(
        content=content,
        citations=[{"number": 1, "id": "1", "source_id": None, "title": "S1",
                    "url": "http://example.com/1", "origin": None, "source_type": None}],
        source_attribution={},
        confidence=0.5,
        style_used=SynthesisStyle.COMPREHENSIVE,
        word_count=2,
        llm_output=None,
    )


# ---------------------------------------------------------------------------
# R2-C1 — fail-open floor predicate + caveat
# ---------------------------------------------------------------------------


def test_fail_open_eligible_boundary():
    """fail-open iff max(source_scores) >= floor. 0.29 hard-refuses, 0.30 fails open."""
    refuse = _gate_result(QualityDecision.REJECT, 0.20, [0.10, 0.29])
    fails_open = _gate_result(QualityDecision.REJECT, 0.20, [0.10, 0.30])
    assert refuse.fail_open_eligible(FLOOR) is False
    assert fails_open.fail_open_eligible(FLOOR) is True


def test_fail_open_eligible_empty_scores_refuses():
    """No scores → no positive evidence → not eligible (hard-refuse even if degraded)."""
    assert _gate_result(QualityDecision.REJECT, 0.0, []).fail_open_eligible(FLOOR) is False


def test_partial_always_fails_open_at_default_floor():
    """A PARTIAL's avg is >= the REJECT floor by definition, and max >= avg, so a
    PARTIAL-zero-good can never be below the default fail-open floor."""
    partial = _gate_result(QualityDecision.PARTIAL, 0.30, [0.30])
    assert partial.fail_open_eligible(FLOOR) is True


def test_fail_open_caveat_text():
    r = _gate_result(QualityDecision.PARTIAL, 0.42, [0.42])
    caveat = r.fail_open_caveat(FLOOR)
    assert "fail-open" in caveat
    assert "partial" in caveat.lower()          # decision.value
    assert "0.42" in caveat                      # best source score
    assert "weakly grounded" in caveat.lower()   # the actionable instruction


# ---------------------------------------------------------------------------
# C5 — never-vaporize: rejected-source provenance
# ---------------------------------------------------------------------------


def test_rejected_provenance_surfaces_identity_score_reason():
    s1, s2 = _src(1), _src(2)
    r = _gate_result(
        QualityDecision.PARTIAL, 0.42, [0.70, 0.42],
        good=[], rejected=[s1, s2], rejected_scores=[0.25, 0.42],
    )
    prov = r.rejected_provenance()
    assert len(prov) == 2
    assert prov[0]["title"] == "S1" and prov[0]["url"] == "http://example.com/1"
    assert prov[0]["score"] == 0.25
    assert "below" in prov[0]["reason"].lower()


def test_rejected_provenance_score_none_when_unaligned():
    """The sync heuristic gate path leaves rejected_scores=None; provenance still
    surfaces identity, score just reads None (never a crash, never vaporized)."""
    r = _gate_result(QualityDecision.REJECT, 0.1, [0.1], rejected=[_src(1)], rejected_scores=None)
    prov = r.rejected_provenance()
    assert len(prov) == 1
    assert prov[0]["score"] is None
    assert prov[0]["title"] == "S1"


# ---------------------------------------------------------------------------
# R2-C2 — cache carrier: fail-open non-cacheable; soft-pass STAYS cacheable
# ---------------------------------------------------------------------------


def _finalized(verdict, cache_eligible):
    return FinalizedSynthesis(
        raw_content="answer [1]",
        safe_content="answer [1]",
        citations=[],
        source_attribution={},
        confidence=0.7,
        word_count=2,
        style_used=SynthesisStyle.COMPREHENSIVE,
        llm_output=None,
        verdict=verdict,
        cache_eligible=cache_eligible,
        surface="unit",
        extras=None,
    )


def test_entity_coverage_soft_pass_stays_cacheable():
    """v0.5.0 precedent: a passing verdict that merely carries a soft warning
    (e.g. entity-coverage) is STILL cacheable — cache_eligible mirrors passed."""
    verdict = SynthesisVerdict()
    verdict.soft_warnings.append("synthesis discusses entities absent from every retained source")
    assert verdict.passed is True
    finalized = _finalized(verdict, cache_eligible=verdict.passed)
    assert finalized.cache_eligible is True


def test_apply_fail_open_marks_noncacheable_and_carries_caveat():
    """R2-C2: apply_fail_open EXPLICITLY flips cache_eligible False (never derived
    from soft-warning presence, so the soft-pass precedent above is untouched) and
    rides the caveat as a soft warning re-annotated into safe_content."""
    verdict = SynthesisVerdict()
    finalized = _finalized(verdict, cache_eligible=verdict.passed)  # starts cacheable
    assert finalized.cache_eligible is True

    caveat = "low source relevance (fail-open): synthesized over weak sources"
    result = apply_fail_open(finalized, caveat)

    assert result.cache_eligible is False
    assert caveat in result.verdict.soft_warnings
    assert "fail-open" in result.safe_content.lower()


# ---------------------------------------------------------------------------
# D1 — MINOR contradictions stay internal diagnostics
# ---------------------------------------------------------------------------


def _contradiction(topic, severity):
    return Contradiction(
        topic=topic, position_a="a", source_a=1, position_b="b", source_b=2,
        severity=severity,
    )


def test_surfaced_contradictions_excludes_minor():
    cs = [
        _contradiction("t-minor", ContradictionSeverity.MINOR),
        _contradiction("t-moderate", ContradictionSeverity.MODERATE),
        _contradiction("t-major", ContradictionSeverity.MAJOR),
    ]
    surfaced = surfaced_contradictions(cs)
    severities = {c.severity for c in surfaced}
    assert ContradictionSeverity.MINOR not in severities
    assert ContradictionSeverity.MODERATE in severities
    assert ContradictionSeverity.MAJOR in severities
    assert len(surfaced) == 2


def test_surfaced_contradictions_all_minor_yields_none_surfaced():
    cs = [_contradiction("a", ContradictionSeverity.MINOR),
          _contradiction("b", ContradictionSeverity.MINOR)]
    assert surfaced_contradictions(cs) == []
