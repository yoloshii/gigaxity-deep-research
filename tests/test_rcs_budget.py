"""RCS contextual-summarizer reasoning-aware budget.

The per-source contextual summarizer was a structured PARSE_REQUIRED LLM call on
a flat max_tokens=400. On a reasoning model that budget is consumed by
chain-of-thought before the SUMMARY/KEY_POINTS/RELEVANCE lines land in `content`;
extract_llm_output then returns text="", the summary parses empty, and the
formatter drops it (lost contextual guidance, though the source's verbatim
evidence still reaches synthesis). The fix derives the model-aware budget at the
summarize boundary, mirroring the scorer + synthesis + contradiction paths
(Option 1; codex design session 019e5b0f). These tests assert the budget is
derived-and-passed and that the empty/real parse semantics are unchanged.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.config import settings
from src.llm_utils import derive_effective_budget
from src.synthesis.rcs import ContextualSummary, RCSPreprocessor

_REASONING_MODEL = "qwen/qwen3-30b-a3b-thinking-2507"
_PLAIN_MODEL = "openai/gpt-4o-mini"

_GOOD_OUTPUT = SimpleNamespace(
    text="SUMMARY: It explains useState basics.\nKEY_POINTS:\n- hook\n- state\nRELEVANCE: 0.8"
)


def _src(title, content):
    return SimpleNamespace(title=title, content=content)


# --- reasoning-aware budget (the fix) -------------------------------------

def test_reasoning_model_gets_summary_headroom():
    """_contextual_summarize derives base+headroom for a reasoning model and passes it down."""
    rcs = RCSPreprocessor(llm_client=object(), model=_REASONING_MODEL)
    rcs._call_llm = AsyncMock(return_value=_GOOD_OUTPUT)
    asyncio.run(rcs._contextual_summarize(_src("A", "alpha content"), "how does useState work"))
    expected = min(400 + settings.llm_reasoning_headroom, settings.llm_max_tokens)
    assert rcs._call_llm.call_args.kwargs["max_tokens"] == expected
    assert expected == derive_effective_budget(400, _REASONING_MODEL)


def test_non_reasoning_model_keeps_flat_400():
    """A non-reasoning model gets no headroom — the prior flat 400 base."""
    rcs = RCSPreprocessor(llm_client=object(), model=_PLAIN_MODEL)
    rcs._call_llm = AsyncMock(return_value=_GOOD_OUTPUT)
    asyncio.run(rcs._contextual_summarize(_src("A", "alpha content"), "q"))
    assert rcs._call_llm.call_args.kwargs["max_tokens"] == 400
    assert rcs._call_llm.call_args.kwargs["max_tokens"] == derive_effective_budget(400, _PLAIN_MODEL)


# --- parse semantics unchanged --------------------------------------------

def test_starved_empty_response_yields_empty_summary():
    """Budget-starved empty content → empty summary (dropped by the formatter's
    guidance filter), NOT a duplicate of source.content into the guidance section."""
    rcs = RCSPreprocessor(llm_client=object(), model=_REASONING_MODEL)
    rcs._call_llm = AsyncMock(return_value=SimpleNamespace(text=""))
    result = asyncio.run(rcs._contextual_summarize(_src("A", "alpha content"), "q"))
    assert isinstance(result, ContextualSummary)
    assert result.summary == ""


def test_real_structured_summary_parses_after_fix():
    """With the corrected budget the structured SUMMARY/KEY_POINTS/RELEVANCE lines parse."""
    rcs = RCSPreprocessor(llm_client=object(), model=_REASONING_MODEL)
    rcs._call_llm = AsyncMock(return_value=_GOOD_OUTPUT)
    result = asyncio.run(rcs._contextual_summarize(_src("A", "alpha"), "useState"))
    assert result.summary == "It explains useState basics."
    assert result.relevance_score == 0.8
    assert result.key_points == ["hook", "state"]
