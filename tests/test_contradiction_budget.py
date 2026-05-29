"""Contradiction-detector reasoning-aware budget.

The detector was the lone structured LLM call in the synthesis branch still
using a flat output budget (max_tokens=2000) with PARSE_REQUIRED. On the 30B
reasoning model the budget was consumed by chain-of-thought, so `content`
arrived truncated or reasoning-only, extract_llm_output returned text="", and
detect() no-op'd with parse_failed=True (the live contracrow symptom). The fix
derives the model-aware budget at the detect() operation boundary, mirroring the
scorer + synthesis paths (codex design session 019e48fe). These tests assert the
budget is derived-and-passed and that the fallback semantics are unchanged.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from src.config import settings
from src.synthesis.contradictions import (
    ContradictionDetector,
    ContradictionDetectionResult,
)

_REASONING_MODEL = "qwen/qwen3-30b-a3b-thinking-2507"
_PLAIN_MODEL = "openai/gpt-4o-mini"


def _src(title, content):
    return SimpleNamespace(title=title, content=content)


def _two_sources():
    return [_src("A", "alpha"), _src("B", "beta")]


# --- reasoning-aware budget (the fix) -------------------------------------

def test_reasoning_model_gets_detection_headroom():
    """detect() derives base+headroom for a reasoning model and passes it down."""
    detector = ContradictionDetector(llm_client=object(), model=_REASONING_MODEL)
    detector._call_llm = AsyncMock(return_value=SimpleNamespace(text="NO_CONTRADICTIONS"))
    asyncio.run(detector.detect("q", _two_sources()))
    expected = min(2000 + settings.llm_reasoning_headroom, settings.llm_max_tokens)
    assert detector._call_llm.call_args.kwargs["max_tokens"] == expected


def test_non_reasoning_model_keeps_flat_2000():
    """A non-reasoning model gets no headroom — the prior flat 2000 base."""
    detector = ContradictionDetector(llm_client=object(), model=_PLAIN_MODEL)
    detector._call_llm = AsyncMock(return_value=SimpleNamespace(text="NO_CONTRADICTIONS"))
    asyncio.run(detector.detect("q", _two_sources()))
    assert detector._call_llm.call_args.kwargs["max_tokens"] == 2000


# --- parse failure stays an honest advisory (no heuristic fallback) -------

def test_empty_text_is_parse_failed_without_heuristic():
    """Budget-starved empty content → parse_failed=True, heuristic NOT invoked."""
    detector = ContradictionDetector(llm_client=object(), model=_REASONING_MODEL)
    detector._call_llm = AsyncMock(return_value=SimpleNamespace(text=""))
    detector._detect_heuristic = MagicMock()
    result = asyncio.run(detector.detect("q", _two_sources()))
    assert isinstance(result, ContradictionDetectionResult)
    assert result.parse_failed is True
    assert result.contradictions == []
    assert result.fallback_used is False
    detector._detect_heuristic.assert_not_called()


def test_malformed_nonempty_is_parse_failed_without_heuristic():
    """Non-empty prose with no structured blocks and no sentinel → parse_failed."""
    detector = ContradictionDetector(llm_client=object(), model=_PLAIN_MODEL)
    detector._call_llm = AsyncMock(
        return_value=SimpleNamespace(text="here is some prose with no structured blocks")
    )
    detector._detect_heuristic = MagicMock()
    result = asyncio.run(detector.detect("q", _two_sources()))
    assert result.parse_failed is True
    assert result.contradictions == []
    detector._detect_heuristic.assert_not_called()


# --- transport failure still degrades to the heuristic (unchanged) --------

def test_transport_exception_still_uses_heuristic():
    sentinel = [object()]
    detector = ContradictionDetector(llm_client=object(), model=_PLAIN_MODEL)
    detector._call_llm = AsyncMock(side_effect=RuntimeError("boom"))
    detector._detect_heuristic = MagicMock(return_value=sentinel)
    result = asyncio.run(detector.detect("q", _two_sources()))
    assert result.fallback_used is True
    assert result.parse_failed is False
    assert result.error == "boom"
    assert result.contradictions is sentinel
    detector._detect_heuristic.assert_called_once()


# --- success paths unchanged ----------------------------------------------

def test_no_contradictions_sentinel_is_clean():
    detector = ContradictionDetector(llm_client=object(), model=_PLAIN_MODEL)
    detector._call_llm = AsyncMock(return_value=SimpleNamespace(text="NO_CONTRADICTIONS"))
    result = asyncio.run(detector.detect("q", _two_sources()))
    assert result.parse_failed is False
    assert result.contradictions == []
    assert result.fallback_used is False


def test_valid_block_parses_unchanged():
    block = (
        "TOPIC: whether Redux is required\n"
        "POSITION_A: Redux is necessary for large apps\n"
        "SOURCE_A: 1\n"
        "POSITION_B: the Context API suffices\n"
        "SOURCE_B: 2\n"
        "SEVERITY: major\n"
        "RESOLUTION: depends on app size\n"
        "---"
    )
    detector = ContradictionDetector(llm_client=object(), model=_PLAIN_MODEL)
    detector._call_llm = AsyncMock(return_value=SimpleNamespace(text=block))
    result = asyncio.run(detector.detect("redux?", _two_sources()))
    assert result.parse_failed is False
    assert len(result.contradictions) == 1
    c = result.contradictions[0]
    assert c.topic == "whether Redux is required"
    assert c.severity.value == "major"
    assert c.source_a == 1
    assert c.source_b == 2


def test_fewer_than_two_sources_short_circuits_without_llm_call():
    """<2 sources can't contradict — no LLM call, no budget derivation, clean."""
    detector = ContradictionDetector(llm_client=object(), model=_REASONING_MODEL)
    detector._call_llm = AsyncMock(return_value=SimpleNamespace(text="NO_CONTRADICTIONS"))
    result = asyncio.run(detector.detect("q", [_src("A", "alpha")]))
    assert result.parse_failed is False
    assert result.contradictions == []
    detector._call_llm.assert_not_called()
