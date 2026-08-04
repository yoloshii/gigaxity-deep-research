"""Degradation propagation + cache policy (rev 7, steps 7-8).

Degradations must reach every channel — soft_warnings (human),
VerdictWarning (machine), diagnostics.stage_degradations, the REST
DiscoverResponse.degradations field, the MCP discover footer — and a
degraded result must never be cached: synthesis via
finalize_synthesis.cache_eligible (explicitly, without changing the
established policy that ordinary advisory warnings pass through), discovery
via the explicit skip in the /discover route. The discovery cache key
carries DISCOVER_CACHE_VERSION plus every behaviour-affecting dimension —
the pre-versioned key held only focus_mode + identify_gaps.
"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from src.cache import (
    DISCOVER_CACHE_VERSION,
    build_discover_cache_extra,
    cache,
)
from src.degradation import StageDegradation, DegradationCode
from src.discovery.explorer import (
    DiscoveryResult,
    KnowledgeLandscape,
)
from src.main import app
from src.synthesis.aggregator import PreGatheredSource
from src.synthesis.finalization import finalize_synthesis
from src.synthesis.outline import OutlinedSynthesis, SynthesisOutline
from src.synthesis.output_verifier import verify_synthesis_output


def _degradation(stage="outline", code=DegradationCode.TRUNCATED):
    return StageDegradation(
        stage=stage,
        code=code,
        parse_failed=True,
        fallback_used=True,
        message=f"{stage} output unusable; fallback substituted",
        truncated=code is DegradationCode.TRUNCATED,
        reasoning_only=False,
        finish_reason="length" if code is DegradationCode.TRUNCATED else "stop",
    )


# ---------------------------------------------------------------------------
# Verifier channels
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestVerifierChannels:
    def test_degradation_reaches_all_three_channels(self):
        verdict = verify_synthesis_output(
            content="Answer with a citation [1].",
            llm_output=None,
            cited_count=1,
            source_count=1,
            stage_degradations=[_degradation()],
        )
        assert any("outline stage degraded (truncated)" in w for w in verdict.soft_warnings)
        assert any(w.code == "stage_degradation_outline" for w in verdict.warnings)
        assert verdict.diagnostics.stage_degradations[0]["stage"] == "outline"
        # Advisory, never a hard failure — the fallback produced usable output.
        assert verdict.passed

    def test_no_degradations_no_channel_noise(self):
        verdict = verify_synthesis_output(
            content="Answer [1].",
            llm_output=None,
            cited_count=1,
            source_count=1,
        )
        assert verdict.diagnostics.stage_degradations == []
        assert not any(w.code.startswith("stage_degradation") for w in verdict.warnings)


# ---------------------------------------------------------------------------
# finalize_synthesis cache eligibility
# ---------------------------------------------------------------------------


def _outlined(degradations=None) -> OutlinedSynthesis:
    return OutlinedSynthesis(
        content="## Overview\n\nContent with a claim [1].",
        outline=SynthesisOutline(sections=["Overview"]),
        sections={"Overview": "Content with a claim [1]."},
        word_count=5,
        degradations=degradations or [],
    )


def _sources():
    return [
        PreGatheredSource(
            origin="jina",
            url="https://example.com/a",
            title="Source A",
            content="Alpha content.",
            source_type="article",
        )
    ]


@pytest.mark.unit
class TestFinalizeCacheEligibility:
    def test_degraded_stage_makes_result_non_cacheable(self):
        finalized = finalize_synthesis(
            query="q",
            result=_outlined([_degradation()]),
            sources=_sources(),
            surface="mcp_synthesize",
        )
        assert finalized.verdict.passed  # advisory, not a hard failure
        assert finalized.cache_eligible is False
        assert any(
            "outline stage degraded" in w for w in finalized.verdict.soft_warnings
        )
        # Annotated safe_content carries the caveat for REST/MCP consumers.
        assert "outline stage degraded" in finalized.safe_content

    def test_clean_result_stays_cacheable(self):
        finalized = finalize_synthesis(
            query="q",
            result=_outlined(),
            sources=_sources(),
            surface="mcp_synthesize",
        )
        assert finalized.cache_eligible is True

    def test_unrelated_soft_warnings_do_not_block_caching(self):
        """The established policy: ordinary advisory warnings pass through
        verdict.passed and stay cacheable. Two sources, one cited → the
        partial-citation soft warning fires, cache_eligible stays True."""
        sources = _sources() + [
            PreGatheredSource(
                origin="exa",
                url="https://example.com/b",
                title="Source B",
                content="Beta content.",
                source_type="article",
            )
        ]
        finalized = finalize_synthesis(
            query="q",
            result=_outlined(),
            sources=sources,
            surface="mcp_synthesize",
        )
        assert any(
            "partial citation coverage" in w
            for w in finalized.verdict.soft_warnings
        )
        assert finalized.cache_eligible is True


# ---------------------------------------------------------------------------
# Discovery cache key + REST propagation
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDiscoverCacheKey:
    def test_key_carries_version_and_all_dimensions(self):
        extra = build_discover_cache_extra(
            model="glm-5.2",
            top_k=15,
            expand_searches=True,
            fill_gaps=True,
            use_adaptive_routing=False,
            focus_mode="academic",
            identify_gaps=True,
        )
        assert f"v={DISCOVER_CACHE_VERSION}" in extra
        for fragment in (
            "model=glm-5.2",
            "top_k=15",
            "expand=True",
            "fill_gaps=True",
            "routing=False",
            "focus_mode=academic",
            "identify_gaps=True",
        ):
            assert fragment in extra

    def test_model_change_changes_key(self):
        kwargs = dict(
            top_k=15,
            expand_searches=True,
            fill_gaps=True,
            use_adaptive_routing=True,
            focus_mode=None,
            identify_gaps=True,
        )
        assert build_discover_cache_extra(
            model="a", **kwargs
        ) != build_discover_cache_extra(model="b", **kwargs)


def _discovery_result(degradations=None) -> DiscoveryResult:
    return DiscoveryResult(
        query="q",
        landscape=KnowledgeLandscape(["q"], [], [], []),
        knowledge_gaps=[],
        sources=[],
        synthesis_preview="Preview.",
        recommended_deep_dives=[],
        degradations=degradations or [],
    )


@pytest.fixture
def client():
    return TestClient(app)


def _patched_discover(result: DiscoveryResult):
    """Patch the /discover component graph to return a canned result."""
    aggregator = patch("src.api.routes.SearchAggregator")
    explorer = patch("src.api.routes.Explorer")

    class _Ctx:
        def __enter__(self):
            self.agg_cls = aggregator.start()
            agg = self.agg_cls.return_value
            agg.connectors = ["searxng"]
            agg.get_active_connectors.return_value = ["searxng"]
            self.exp_cls = explorer.start()
            self.exp_cls.return_value.discover = AsyncMock(return_value=result)
            return self

        def __exit__(self, *exc):
            aggregator.stop()
            explorer.stop()

    return _Ctx()


@pytest.mark.unit
class TestDiscoverEndpointPropagation:
    def setup_method(self):
        cache.clear()

    def teardown_method(self):
        cache.clear()

    def test_degradations_in_response_and_not_cached(self, client):
        deg = _degradation(stage="landscape", code=DegradationCode.REASONING_ONLY)
        with _patched_discover(_discovery_result([deg])) as ctx:
            r1 = client.post("/api/v1/discover", json={"query": "degraded run"})
            assert r1.status_code == 200
            body = r1.json()
            assert body["degradations"][0]["stage"] == "landscape"
            assert body["degradations"][0]["code"] == "reasoning_only"

            # Degraded → not cached → the second call recomputes.
            r2 = client.post("/api/v1/discover", json={"query": "degraded run"})
            assert r2.status_code == 200
            assert ctx.exp_cls.return_value.discover.await_count == 2

    def test_clean_run_cached(self, client):
        with _patched_discover(_discovery_result()) as ctx:
            r1 = client.post("/api/v1/discover", json={"query": "clean run"})
            assert r1.status_code == 200
            assert r1.json()["degradations"] == []

            # Clean → cached → the second call never reaches the explorer.
            r2 = client.post("/api/v1/discover", json={"query": "clean run"})
            assert r2.status_code == 200
            assert r2.json()["degradations"] == []
            assert ctx.exp_cls.return_value.discover.await_count == 1
