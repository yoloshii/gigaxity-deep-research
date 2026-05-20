"""Q2: optional `gate_focus` — the gate scores relevance against a caller
focus instead of the full query.

Design locked in codex session 019e4683 (3 turns, verbatim zero). Asserts the
load-bearing contract: focus drives BOTH scorers; entity-balanced promotion is
skipped under an active focus (the D3 fix — promotion ranks by full-query
centrality with no focus floor, so under a focus it could resurrect a
focus-irrelevant source); the focus is echoed on every result branch; the MCP
cache discriminator separates focused from unfocused while leaving the unfocused
key byte-identical; and omitted/None/whitespace is fully backward-compatible.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.cache import build_synthesis_cache_extra
from src.synthesis.quality_gate import (
    QualityDecision,
    SourceQualityGate,
    _ScoringOutcome,
)


def _src(title, content):
    return SimpleNamespace(title=title, content=content)


# --- focus drives the LLM scorer ------------------------------------------

def test_llm_scorer_judges_against_focus_not_full_query():
    """The SCORING_PROMPT 'Query:' line carries the focus; the full query's
    distinctive term must not leak into the prompt."""
    gate = SourceQualityGate(llm_client=object(), model="x")
    gate._call_llm = AsyncMock(return_value=SimpleNamespace(text="0.8\n0.7"))
    query = "a verbose brief about kubernetes operators and reconciliation loops"
    result = asyncio.run(
        gate.evaluate(query, [_src("A", "a"), _src("B", "b")], gate_focus="pricing tiers")
    )
    prompt = gate._call_llm.call_args.args[0]
    assert "pricing tiers" in prompt
    assert "kubernetes" not in prompt          # focus replaced the full query
    assert result.gate_focus == "pricing tiers"


def test_llm_scorer_uses_full_query_when_no_focus():
    """Backward-compat: no focus → the prompt carries the full query, echo is None."""
    gate = SourceQualityGate(llm_client=object(), model="x")
    gate._call_llm = AsyncMock(return_value=SimpleNamespace(text="0.8\n0.7"))
    result = asyncio.run(
        gate.evaluate("kubernetes operators", [_src("A", "a"), _src("B", "b")])
    )
    prompt = gate._call_llm.call_args.args[0]
    assert "kubernetes operators" in prompt
    assert result.gate_focus is None


# --- focus drives the keyword heuristic -----------------------------------

def test_heuristic_judges_against_focus():
    """heuristic_only path: a source the focus matches scores high even though
    the full query matches nothing."""
    gate = SourceQualityGate()  # no client → heuristic_only
    query = "zzz nonmatching qqq"
    src = _src("alpha", "alpha beta gamma delta")
    with_focus = asyncio.run(gate.evaluate(query, [src], gate_focus="alpha beta gamma"))
    without = asyncio.run(gate.evaluate(query, [src]))
    assert with_focus.source_scores[0] >= 0.5     # 3 focus matches → ~0.528
    assert without.source_scores[0] == 0.0        # full query matches nothing
    assert with_focus.gate_focus == "alpha beta gamma"
    assert without.gate_focus is None


# --- echo on every branch -------------------------------------------------

def test_gate_focus_echoed_on_reject_branch():
    """heuristic_only REJECT still echoes the applied focus."""
    gate = SourceQualityGate()
    # focus the source does not match → all-zero scores → avg below reject.
    result = asyncio.run(
        gate.evaluate("anything", [_src("t", "totally unrelated body")], gate_focus="quantum chromodynamics")
    )
    assert result.decision == QualityDecision.REJECT
    assert result.gate_focus == "quantum chromodynamics"


def test_gate_focus_echoed_on_no_sources_branch():
    gate = SourceQualityGate()
    result = asyncio.run(gate.evaluate("q", [], gate_focus="pricing"))
    assert result.decision == QualityDecision.REJECT
    assert result.gate_focus == "pricing"


# --- D3: entity-balanced promotion gated by focus -------------------------

def _entity_gate():
    """entity_balanced gate with comprehensive-like thresholds; scores mocked so
    the test isolates the promotion step from the scorer."""
    gate = SourceQualityGate(entity_balanced=True, reject_threshold=0.2, pass_threshold=0.4)
    # srcT passes (0.5), srcL is rejected (0.1) but is LinkUp-central → a
    # promotion candidate for the uncovered LinkUp entity.
    gate._score_sources = AsyncMock(return_value=_ScoringOutcome([0.5, 0.1], "heuristic_only"))
    srcT = _src("Tavily overview", "Tavily is a search API for agents")
    srcL = _src("LinkUp overview", "LinkUp is a search API for agents")
    return gate, srcT, srcL


def test_entity_balanced_promotes_uncovered_vendor_without_focus():
    """No focus: LinkUp (uncovered, rejected, title-central) is promoted →
    PROCEED with both sources. This is the behavior the focus must suppress."""
    gate, srcT, srcL = _entity_gate()
    result = asyncio.run(gate.evaluate("Tavily versus LinkUp", [srcT, srcL]))
    assert result.decision == QualityDecision.PROCEED
    assert len(result.good_sources) == 2
    assert srcL in result.good_sources


def test_entity_balanced_promotion_skipped_under_focus():
    """Active focus: promotion is skipped, so the focus-irrelevant LinkUp source
    is NOT resurrected — it stays rejected (PARTIAL), preserving the caller's
    narrowing (codex D3)."""
    gate, srcT, srcL = _entity_gate()
    result = asyncio.run(
        gate.evaluate("Tavily versus LinkUp", [srcT, srcL], gate_focus="pricing")
    )
    assert result.decision == QualityDecision.PARTIAL
    assert len(result.good_sources) == 1
    assert srcL not in result.good_sources
    assert result.gate_focus == "pricing"


# --- whitespace / None backward-compat ------------------------------------

def test_whitespace_focus_is_treated_as_no_focus():
    """A whitespace-only focus falls back to the full query and echoes None,
    byte-identical to omitting it."""
    gate = SourceQualityGate()
    src = _src("alpha", "alpha beta gamma")
    ws = asyncio.run(gate.evaluate("alpha beta gamma", [src], gate_focus="   "))
    none = asyncio.run(gate.evaluate("alpha beta gamma", [src]))
    assert ws.source_scores == none.source_scores
    assert ws.gate_focus is None


# --- evaluate_sync symmetry -----------------------------------------------

def test_evaluate_sync_uses_focus_and_echoes():
    gate = SourceQualityGate()
    src = _src("t", "alpha beta gamma")
    # "nonmatching" is content-bearing (len>3) but absent from the source → the
    # full query scores 0.0; the focus matches all three terms. ("zzz"/"qqq" are
    # len-3 and filtered out, which would trip the neutral-0.5 empty-terms path.)
    focused = gate.evaluate_sync("zzz nonmatching qqq", [src], gate_focus="alpha beta gamma")
    plain = gate.evaluate_sync("zzz nonmatching qqq", [src])
    assert focused.source_scores[0] >= 0.5
    assert plain.source_scores[0] == 0.0
    assert focused.gate_focus == "alpha beta gamma"
    assert plain.gate_focus is None


def test_evaluate_sync_whitespace_focus_is_none():
    gate = SourceQualityGate()
    src = _src("t", "alpha beta gamma")
    ws = gate.evaluate_sync("alpha beta gamma", [src], gate_focus="  ")
    none = gate.evaluate_sync("alpha beta gamma", [src])
    assert ws.source_scores == none.source_scores
    assert ws.gate_focus is None


# --- MCP cache discriminator ----------------------------------------------

def _cache_src():
    return [SimpleNamespace(origin="external", source_type="article", url="u", title="t", content="c")]


def test_cache_key_unfocused_matches_legacy_focused_differs():
    """The conditional append: an unfocused call uses the exact prior mode
    string (existing cache keys preserved); an active focus varies the key."""
    srcs = _cache_src()
    legacy = build_synthesis_cache_extra(srcs, model="m", max_tokens=1000, mode="preset=comprehensive:style=None")
    unfocused = build_synthesis_cache_extra(srcs, model="m", max_tokens=1000, mode="preset=comprehensive:style=None")
    focused = build_synthesis_cache_extra(srcs, model="m", max_tokens=1000, mode="preset=comprehensive:style=None:gate_focus=pricing")
    assert unfocused == legacy          # unfocused key unchanged from pre-Q2
    assert focused != unfocused          # focus separates the key


def test_cache_key_distinct_per_focus():
    srcs = _cache_src()
    a = build_synthesis_cache_extra(srcs, model="m", max_tokens=1000, mode="preset=comprehensive:style=None:gate_focus=pricing")
    a2 = build_synthesis_cache_extra(srcs, model="m", max_tokens=1000, mode="preset=comprehensive:style=None:gate_focus=pricing")
    b = build_synthesis_cache_extra(srcs, model="m", max_tokens=1000, mode="preset=comprehensive:style=None:gate_focus=rate limits")
    assert a == a2          # same focus → same key
    assert a != b           # different focus → different key
