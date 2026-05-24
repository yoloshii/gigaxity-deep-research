"""Tests for src/synthesis/wrappers.py (Phase 0).

The wrappers are the ONLY public callers of the five core synthesis methods.
These tests verify:
- Each wrapper instantiates the right core class with the right kwargs.
- Each wrapper invokes the right method on the instance.
- Each wrapper threads finalize_synthesis args correctly (surface +
  contradiction_result + query_entities).
- Engine wrappers raise SynthesisInvocationError on `{"error": ...}` dicts
  BEFORE finalize_synthesis runs.
- Aggregator and outline wrappers do NOT raise SynthesisInvocationError
  (degraded results go through the verifier's hard-fail channel instead).
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.connectors.base import Source
from src.synthesis import (
    AggregatedSynthesis,
    OutlinedSynthesis,
    PreGatheredSource,
    SynthesisInvocationError,
    SynthesisStyle,
    run_aggregator_synthesize,
    run_aggregator_synthesize_with_reasoning,
    run_engine_research,
    run_engine_synthesize,
    run_outline_synthesize,
)
from src.synthesis.outline import SynthesisOutline


# ---------------------------------------------------------------------------
# Fixtures.
# ---------------------------------------------------------------------------


def _pre_source(title: str = "T", content: str = "Body [1]") -> PreGatheredSource:
    return PreGatheredSource(
        origin="exa", url="https://example.com", title=title, content=content,
        source_type="article",
    )


def _connector_source(title: str = "T", content: str = "Body [1]") -> Source:
    return Source(
        id="tv_a1", title=title, url="https://example.com",
        content=content, score=0.5, connector="tavily",
    )


def _aggregated(content: str = "Result [1].") -> AggregatedSynthesis:
    return AggregatedSynthesis(
        content=content,
        citations=[{"number": 1, "id": "1", "source_id": None, "title": "T", "url": "u",
                    "origin": "exa", "source_type": "article"}],
        source_attribution={"exa": 1.0},
        confidence=0.7,
        style_used=SynthesisStyle.COMPREHENSIVE,
        word_count=2,
        llm_output=None,
    )


def _outlined() -> OutlinedSynthesis:
    return OutlinedSynthesis(
        content="## Overview\n[1] body",
        outline=SynthesisOutline(sections=["Overview"]),
        sections={"Overview": "[1] body"},
        critique=None,
        refined=False,
        word_count=2,
        llm_output=None,
    )


# ---------------------------------------------------------------------------
# Engine wrappers — error-dict raises BEFORE finalize.
# ---------------------------------------------------------------------------


class TestEngineWrappers:
    """Wrappers for SynthesisEngine.synthesize and .research."""

    @pytest.mark.unit
    async def test_run_engine_synthesize_threads_args(self):
        """run_engine_synthesize constructs SynthesisEngine and calls .synthesize."""
        client = MagicMock()
        sources = [_connector_source()]
        with patch("src.synthesis.wrappers.SynthesisEngine") as engine_cls:
            instance = engine_cls.return_value
            instance.synthesize = AsyncMock(return_value={
                "content": "ok [1]",
                "citations": [{"number": 1, "id": "1", "source_id": "tv_a1",
                               "title": "T", "url": "u",
                               "origin": None, "source_type": None}],
                "sources_used": [sources[0]],
                "model": "test-model",
            })
            finalized = await run_engine_synthesize(
                client=client,
                model="test-model",
                query="q",
                sources=sources,
                surface="mcp_research",
            )
        engine_cls.assert_called_once_with(client=client, model="test-model")
        instance.synthesize.assert_called_once_with("q", sources, None)
        assert finalized.surface == "mcp_research"
        assert finalized.verdict.passed

    @pytest.mark.unit
    async def test_run_engine_synthesize_error_dict_raises_invocation(self):
        """Engine `{"error": ...}` → SynthesisInvocationError raised BEFORE finalize."""
        client = MagicMock()
        sources = [_connector_source()]
        with patch("src.synthesis.wrappers.SynthesisEngine") as engine_cls, \
                patch("src.synthesis.wrappers.finalize_synthesis") as fin_mock:
            engine_cls.return_value.synthesize = AsyncMock(return_value={
                "content": "Synthesis error: timeout",
                "citations": [],
                "sources_used": [],
                "error": "timeout",
            })
            with pytest.raises(SynthesisInvocationError, match="timeout"):
                await run_engine_synthesize(
                    client=client,
                    query="q",
                    sources=sources,
                    surface="rest_research_no_preset",
                )
            # finalize_synthesis MUST NOT run when the engine errored.
            fin_mock.assert_not_called()

    @pytest.mark.unit
    async def test_run_engine_research_invokes_research_method(self):
        """run_engine_research calls engine.research, not engine.synthesize."""
        client = MagicMock()
        sources = [_connector_source()]
        with patch("src.synthesis.wrappers.SynthesisEngine") as engine_cls:
            instance = engine_cls.return_value
            instance.research = AsyncMock(return_value={
                "content": "ok [1]",
                "citations": [{"number": 1, "id": "1", "source_id": "tv_a1",
                               "title": "T", "url": "u",
                               "origin": None, "source_type": None}],
                "sources_used": [sources[0]],
            })
            instance.synthesize = AsyncMock()  # to assert NOT called
            await run_engine_research(
                client=client,
                model="test-model",
                query="q",
                sources=sources,
                reasoning_effort="high",
                surface="mcp_research",
            )
        instance.research.assert_called_once_with("q", sources, "high")
        instance.synthesize.assert_not_called()


# ---------------------------------------------------------------------------
# Aggregator wrappers — no SynthesisInvocationError path; degraded results
# flow through the verifier's hard-fail channel.
# ---------------------------------------------------------------------------


class TestAggregatorWrappers:
    """Wrappers for SynthesisAggregator.{synthesize,synthesize_with_reasoning}."""

    @pytest.mark.unit
    async def test_run_aggregator_synthesize_threads_args(self):
        """run_aggregator_synthesize constructs SynthesisAggregator and calls .synthesize."""
        client = MagicMock()
        sources = [_pre_source()]
        with patch("src.synthesis.wrappers.SynthesisAggregator") as agg_cls:
            instance = agg_cls.return_value
            instance.synthesize = AsyncMock(return_value=_aggregated())
            finalized = await run_aggregator_synthesize(
                llm_client=client,
                model="m",
                query="q",
                sources=sources,
                style=SynthesisStyle.ACADEMIC,
                max_tokens=5000,
                guidance=["g1"],
                contradiction_notes="cn",
                surface="rest_synthesize",
            )
        agg_cls.assert_called_once_with(llm_client=client, model="m")
        instance.synthesize.assert_called_once_with(
            query="q",
            sources=sources,
            style=SynthesisStyle.ACADEMIC,
            max_tokens=5000,
            guidance=["g1"],
            contradiction_notes="cn",
        )
        assert finalized.surface == "rest_synthesize"
        assert finalized.verdict.passed

    @pytest.mark.unit
    async def test_run_aggregator_with_reasoning_invokes_with_reasoning_method(self):
        """The reasoning wrapper hits synthesize_with_reasoning, not synthesize."""
        client = MagicMock()
        sources = [_pre_source()]
        with patch("src.synthesis.wrappers.SynthesisAggregator") as agg_cls:
            instance = agg_cls.return_value
            instance.synthesize_with_reasoning = AsyncMock(return_value=_aggregated())
            instance.synthesize = AsyncMock()  # should NOT be called
            await run_aggregator_synthesize_with_reasoning(
                llm_client=client,
                query="q",
                sources=sources,
                surface="rest_reason",
            )
        instance.synthesize_with_reasoning.assert_called_once()
        instance.synthesize.assert_not_called()

    @pytest.mark.unit
    async def test_aggregator_wrappers_do_not_raise_on_degraded_result(self):
        """Empty content + no citations → verdict hard-fail, no exception."""
        client = MagicMock()
        sources = [_pre_source()]
        degraded = AggregatedSynthesis(
            content="",  # empty content (verifier hard-fail)
            citations=[],
            source_attribution={},
            confidence=0.0,
            style_used=SynthesisStyle.COMPREHENSIVE,
            word_count=0,
            llm_output=None,
        )
        with patch("src.synthesis.wrappers.SynthesisAggregator") as agg_cls:
            agg_cls.return_value.synthesize = AsyncMock(return_value=degraded)
            # Aggregator wrappers don't raise SynthesisInvocationError —
            # the verifier hard-fails through the normal channel.
            finalized = await run_aggregator_synthesize(
                llm_client=client,
                query="q",
                sources=sources,
                surface="rest_synthesize",
            )
        assert not finalized.verdict.passed
        assert not finalized.cache_eligible


class TestOutlineWrapper:
    """Wrapper for OutlineGuidedSynthesizer.synthesize."""

    @pytest.mark.unit
    async def test_run_outline_synthesize_threads_args_with_refinement(self):
        """run_outline_synthesize constructs OutlineGuidedSynthesizer with max_refinement_rounds."""
        client = MagicMock()
        sources = [_pre_source()]
        with patch("src.synthesis.wrappers.OutlineGuidedSynthesizer") as ogs_cls:
            instance = ogs_cls.return_value
            instance.synthesize = AsyncMock(return_value=_outlined())
            finalized = await run_outline_synthesize(
                llm_client=client,
                model="m",
                max_refinement_rounds=2,
                query="q",
                sources=sources,
                style=SynthesisStyle.COMPARATIVE,
                max_tokens=8000,
                surface="rest_synthesize_p1",
            )
        ogs_cls.assert_called_once_with(
            llm_client=client, model="m", max_refinement_rounds=2,
        )
        instance.synthesize.assert_called_once()
        assert finalized.surface == "rest_synthesize_p1"
        # Outline-specific extras present.
        assert finalized.extras["outline_sections"] == ["Overview"]
        assert finalized.extras["sections"] == {"Overview": "[1] body"}


class TestWrapperFinalizeContract:
    """Every wrapper must call finalize_synthesis with the same arg shape."""

    @pytest.mark.unit
    async def test_surface_threaded_into_finalize(self):
        """The `surface=` arg reaches finalize_synthesis verbatim."""
        client = MagicMock()
        sources = [_pre_source()]
        with patch("src.synthesis.wrappers.SynthesisAggregator") as agg_cls, \
                patch("src.synthesis.wrappers.finalize_synthesis") as fin_mock:
            agg_cls.return_value.synthesize = AsyncMock(return_value=_aggregated())
            fin_mock.return_value = MagicMock()
            await run_aggregator_synthesize(
                llm_client=client,
                query="q",
                sources=sources,
                surface="rest_synthesize_enhanced",
            )
        fin_mock.assert_called_once()
        kwargs = fin_mock.call_args.kwargs
        assert kwargs["surface"] == "rest_synthesize_enhanced"
        assert kwargs["query"] == "q"
        assert kwargs["sources"] is sources

    @pytest.mark.unit
    async def test_contradiction_result_threaded_into_finalize(self):
        """`contradiction_result=` reaches finalize_synthesis verbatim."""
        client = MagicMock()
        sources = [_pre_source()]
        marker = MagicMock(name="ContradictionMarker")
        with patch("src.synthesis.wrappers.SynthesisAggregator") as agg_cls, \
                patch("src.synthesis.wrappers.finalize_synthesis") as fin_mock:
            agg_cls.return_value.synthesize = AsyncMock(return_value=_aggregated())
            fin_mock.return_value = MagicMock()
            await run_aggregator_synthesize(
                llm_client=client,
                query="q",
                sources=sources,
                contradiction_result=marker,
                surface="rest_synthesize_enhanced",
            )
        assert fin_mock.call_args.kwargs["contradiction_result"] is marker

    @pytest.mark.unit
    async def test_query_entities_threaded_into_finalize(self):
        """`query_entities=` reaches finalize_synthesis verbatim."""
        client = MagicMock()
        sources = [_pre_source()]
        with patch("src.synthesis.wrappers.SynthesisAggregator") as agg_cls, \
                patch("src.synthesis.wrappers.finalize_synthesis") as fin_mock:
            agg_cls.return_value.synthesize = AsyncMock(return_value=_aggregated())
            fin_mock.return_value = MagicMock()
            await run_aggregator_synthesize(
                llm_client=client,
                query="q",
                sources=sources,
                query_entities=["FastAPI", "Flask"],
                surface="rest_synthesize",
            )
        assert fin_mock.call_args.kwargs["query_entities"] == ["FastAPI", "Flask"]
