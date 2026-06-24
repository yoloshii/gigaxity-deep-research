"""A2: hardened LLM relevance scorer.

Reasoning-aware token budget (so chain-of-thought does not starve the scores
out of `content`), a single strict-format retry, and a robust `_parse_scores`.
Tests assert behavior DIRECTLY at `_score_sources` (call count, scorer_path,
budget) and at `_parse_scores`, so A1's downstream rescue cannot mask an A2
regression (codex design session 019e4569).
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.config import settings
from src.llm_utils import derive_effective_budget
from src.synthesis.quality_gate import SourceQualityGate


def _src(title, content):
    return SimpleNamespace(title=title, content=content)


# --- A2c: _parse_scores ---------------------------------------------------

def test_parse_json_array_exact_length_accepted():
    gate = SourceQualityGate()
    assert gate._parse_scores("scores: [0.8, 0.5, 0.2]", 3) == [0.8, 0.5, 0.2]


def test_parse_json_wrong_length_falls_through_to_per_line():
    """A 1-element bracket can't be 3 scores; per-line parsing recovers them."""
    gate = SourceQualityGate()
    assert gate._parse_scores("[0.9]\n0.8\n0.2", 3) == [0.9, 0.8, 0.2]


def test_parse_json_out_of_range_not_clamped():
    """A prose list [1, 2] must NOT be accepted as clamped [1.0, 1.0] (codex T4-F1)."""
    gate = SourceQualityGate()
    assert gate._parse_scores("ranks [1, 2]", 2) != [1.0, 1.0]
    # A valid in-range array later in the text still wins.
    assert gate._parse_scores("ranks [1, 2] then [0.9, 0.1]", 2) == [0.9, 0.1]


def test_parse_single_source_skips_json_path():
    """expected_count == 1 ignores `[1]` (a source ref would misread as 1.0)."""
    gate = SourceQualityGate()
    assert gate._parse_scores("source [1] scores 0.3", 1) == [0.3]


def test_parse_per_line_source_label_not_misread():
    """'Source 1: 0.8' -> 0.8, never 1.0 (label stripped / decimal preferred)."""
    gate = SourceQualityGate()
    assert gate._parse_scores("Source 1: 0.8\nSource 2: 0.4", 2) == [0.8, 0.4]


def test_parse_per_line_enumerator_and_decimal():
    gate = SourceQualityGate()
    assert gate._parse_scores("1. 0.7\n2) 0.3", 2) == [0.7, 0.3]


def test_parse_decimal_leading_zero_not_stripped():
    """'0.85' parses as 0.85, not the enumerator '0.' + '85'."""
    gate = SourceQualityGate()
    assert gate._parse_scores("0.85\n0.15", 2) == [0.85, 0.15]


def test_parse_bare_int_score_with_enumerator():
    """'1. 0' -> 0.0 (enumerator stripped), not 1.0."""
    gate = SourceQualityGate()
    assert gate._parse_scores("1. 0\n2. 1", 2) == [0.0, 1.0]


# --- A2a: reasoning-aware budget ------------------------------------------

def test_reasoning_model_gets_reasoning_budget():
    """Gate scorer sizes its budget with derive_effective_budget(500, model).

    Reasoning models get the full reasoning headroom (Option-1 budget
    unification; codex 019e5b0f). The retired llm_scoring_headroom (1536) was a
    separate, smaller knob; the scorer now shares the same reasoning-aware
    formula synthesis, RCS, and contradiction detection already use.
    """
    gate = SourceQualityGate(llm_client=object(), model="qwen/qwen3-30b-a3b-thinking-2507")
    gate._call_llm = AsyncMock(return_value=SimpleNamespace(text="0.8\n0.7"))
    asyncio.run(gate.evaluate("topic terms here", [_src("A", "alpha"), _src("B", "beta")]))
    expected = derive_effective_budget(500, gate.model)
    assert gate._call_llm.call_args.kwargs["max_tokens"] == expected
    # Reasoning model => budget is base + reasoning headroom, capped at llm_max_tokens.
    assert expected == min(500 + settings.llm_reasoning_headroom, settings.llm_max_tokens)


def test_non_reasoning_model_keeps_flat_500():
    gate = SourceQualityGate(llm_client=object(), model="openai/gpt-4o-mini")
    gate._call_llm = AsyncMock(return_value=SimpleNamespace(text="0.8\n0.7"))
    asyncio.run(gate.evaluate("topic terms here", [_src("A", "alpha"), _src("B", "beta")]))
    assert gate._call_llm.call_args.kwargs["max_tokens"] == 500
    assert gate._call_llm.call_args.kwargs["max_tokens"] == derive_effective_budget(500, gate.model)


# --- A2b: single strict-format retry --------------------------------------

def test_clean_first_attempt_no_retry():
    gate = SourceQualityGate(llm_client=object(), model="x")
    gate._call_llm = AsyncMock(return_value=SimpleNamespace(text="0.8\n0.7"))
    result = asyncio.run(gate.evaluate("t", [_src("A", "a"), _src("B", "b")]))
    assert gate._call_llm.call_count == 1
    assert result.scorer_path == "llm"
    assert result.source_scores == [0.8, 0.7]


def test_empty_first_then_json_retry_recovers():
    """Reasoning-only first attempt (content empty) → strict-format retry lands JSON scores.

    Both attempts must use the same reasoning-aware budget so the retry does not
    re-starve at an under-sized cap (Option-1: the A2 retry shares
    derive_effective_budget(500, model)). A recovered scorer leaves the gate NOT
    degraded.
    """
    gate = SourceQualityGate(llm_client=object(), model="qwen/qwen3-30b-a3b-thinking-2507")
    gate._call_llm = AsyncMock(side_effect=[
        SimpleNamespace(text=""),            # reasoning-only first attempt
        SimpleNamespace(text="[0.9, 0.6]"),  # strict-format retry
    ])
    result = asyncio.run(gate.evaluate("t", [_src("A", "a"), _src("B", "b")]))
    assert gate._call_llm.call_count == 2
    assert result.scorer_path == "llm"
    assert result.gate_degraded is False
    assert result.source_scores == [0.9, 0.6]
    retry_prompt = gate._call_llm.call_args_list[1].args[0]
    assert "JSON array of exactly 2" in retry_prompt
    # Both the first attempt and the strict retry use the corrected budget.
    expected = derive_effective_budget(500, gate.model)
    assert gate._call_llm.call_args_list[0].kwargs["max_tokens"] == expected
    assert gate._call_llm.call_args_list[1].kwargs["max_tokens"] == expected


def test_count_mismatch_after_retry_falls_back():
    gate = SourceQualityGate(llm_client=object(), model="x")
    gate._call_llm = AsyncMock(return_value=SimpleNamespace(text="0.8"))  # 1 score for 2 sources
    result = asyncio.run(gate.evaluate("security sandbox runtime", [
        _src("A", "alpha security sandbox"), _src("B", "beta runtime"),
    ]))
    assert gate._call_llm.call_count == 2
    assert result.scorer_path == "llm_fallback_heuristic"
    assert result.fallback_reason.startswith("score_count_mismatch")


def test_both_empty_falls_back_with_empty_reason():
    gate = SourceQualityGate(llm_client=object(), model="x")
    gate._call_llm = AsyncMock(return_value=SimpleNamespace(text=""))
    # Sources match the query so the heuristic fallback does NOT reject — a
    # REJECT would fire a 3rd _call_llm for _suggest_searches; matching sources
    # isolate the two scoring attempts.
    result = asyncio.run(gate.evaluate("alpha beta gamma", [
        _src("A", "alpha beta gamma"), _src("B", "alpha beta gamma"),
    ]))
    assert gate._call_llm.call_count == 2
    assert result.scorer_path == "llm_fallback_heuristic"
    assert result.fallback_reason == "empty_response"
