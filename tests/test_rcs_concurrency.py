"""RCS prepare() runs per-source summaries with bounded concurrency.

The per-source contextual summaries are independent calls; running them serially
made the comprehensive pipeline scale as N x per-call latency over many sources
(the comprehensive-over-many-sources timeout). prepare() now runs them under a
bounded asyncio.Semaphore (settings.rcs_concurrency) while PRESERVING SOURCE
ORDER (RCS is guidance-only and must never reorder) and PROPAGATING a transport
failure unwrapped (type preserved) so the caller sees the original error.
"""
import asyncio
from types import SimpleNamespace

import pytest

from src.config import settings
from src.synthesis.rcs import ContextualSummary, RCSPreprocessor


def _summary(source):
    return ContextualSummary(source=source, summary=f"s:{source.title}", relevance_score=0.5)


def _sources(n):
    return [SimpleNamespace(title=f"S{i}", content="c") for i in range(n)]


def test_prepare_preserves_source_order_when_completion_order_differs(monkeypatch):
    """asyncio.gather preserves ARG order: later sources finishing first must NOT
    reorder the summaries (guidance must stay zip-aligned with the sources)."""
    monkeypatch.setattr(settings, "rcs_concurrency", 100)  # all launch together

    async def fake(source, query):
        idx = int(source.title[1:])
        # Invert: S0 sleeps longest, the LAST source returns first.
        await asyncio.sleep(0.002 * (10 - idx))
        return _summary(source)

    rcs = RCSPreprocessor(llm_client=object(), model="x")
    rcs._contextual_summarize = fake
    res = asyncio.run(rcs.prepare("q", _sources(8)))
    assert [s.source.title for s in res.summaries] == [f"S{i}" for i in range(8)]
    assert res.total_sources == 8 and res.kept_sources == 8


def test_prepare_bounds_concurrency_to_setting(monkeypatch):
    """No more than settings.rcs_concurrency summaries run at once."""
    monkeypatch.setattr(settings, "rcs_concurrency", 3)
    state = {"inflight": 0, "peak": 0}

    async def fake(source, query):
        state["inflight"] += 1
        state["peak"] = max(state["peak"], state["inflight"])
        await asyncio.sleep(0.01)
        state["inflight"] -= 1
        return _summary(source)

    rcs = RCSPreprocessor(llm_client=object(), model="x")
    rcs._contextual_summarize = fake
    res = asyncio.run(rcs.prepare("q", _sources(9)))
    assert state["peak"] <= 3
    assert state["peak"] >= 2  # actually overlapped (not accidentally serial)
    assert len(res.summaries) == 9


def test_prepare_concurrency_floor_of_one(monkeypatch):
    """rcs_concurrency < 1 is floored to 1 (serial), never a zero-permit deadlock."""
    monkeypatch.setattr(settings, "rcs_concurrency", 0)
    state = {"inflight": 0, "peak": 0}

    async def fake(source, query):
        state["inflight"] += 1
        state["peak"] = max(state["peak"], state["inflight"])
        await asyncio.sleep(0.005)
        state["inflight"] -= 1
        return _summary(source)

    rcs = RCSPreprocessor(llm_client=object(), model="x")
    rcs._contextual_summarize = fake
    res = asyncio.run(rcs.prepare("q", _sources(4)))
    assert state["peak"] == 1
    assert len(res.summaries) == 4


def test_prepare_no_client_uses_heuristic():
    """No LLM client -> heuristic path (no concurrency, summaries still in order)."""
    rcs = RCSPreprocessor(llm_client=None, model="x")
    calls = []

    def fake_heur(source, query):
        calls.append(source.title)
        return _summary(source)

    rcs._heuristic_summarize = fake_heur
    res = asyncio.run(rcs.prepare("q", _sources(3)))
    assert calls == ["S0", "S1", "S2"]
    assert [s.source.title for s in res.summaries] == ["S0", "S1", "S2"]


def test_prepare_propagates_transport_error_unwrapped():
    """A transport failure in any call propagates UNWRAPPED (not an
    ExceptionGroup) so the caller's `except` matches the original type; the
    remaining in-flight tasks are cancelled."""

    class Boom(Exception):
        pass

    async def fake(source, query):
        if source.title == "S1":
            raise Boom("transport")
        await asyncio.sleep(0.05)
        return _summary(source)

    rcs = RCSPreprocessor(llm_client=object(), model="x")
    rcs._contextual_summarize = fake
    with pytest.raises(Boom):
        asyncio.run(rcs.prepare("q", _sources(4)))
