"""Regression tests for v0.3.0 citation contract unification (codex DESIGN
session 019e39f7-33ab-7691-ac6d-30c0804b6cdc, NONCE codex-design-medium4-
citation-unification-2026-05-18-9f2a7b1c).

v0.3.0 hard-cut every synthesis surface from the old `[source_id]` /
`[xx_<hex>]` contract onto the same `[N]` contract that
`SynthesisAggregator` and `OutlineGuidedSynthesizer` already used. This
file covers what the v0.2.1 + v0.2.2 regressions did not:

- `CitationSource` protocol duck-typing across `PreGatheredSource` and
  connector `Source`
- canonical citation dict shape `{number, id, source_id, title, url,
  origin, source_type}` with the correct None defaults per source type
- `SynthesisEngine` end-to-end through the new extractor (no more inline
  `[xx_<hex>]` regex)
- `CitationSchema` carrying `number` + `source_id` from `/research`
- MCP `research` footer rendering `[N]`, not `[xx_<hex>]`
- verifier soft warnings for legacy-only and mixed marker drift
- regression assertion that `enhanced.py` is gone
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


# ---------------------------------------------------------------------------
# citations.py — CitationSource duck-typing + canonical dict shape
# ---------------------------------------------------------------------------


def test_extract_numeric_citations_accepts_connector_source():
    """`SynthesisEngine` calls extract with `connectors.base.Source` (no
    .origin, no .source_type). The duck-typed CitationSource protocol
    must accept it via getattr fallbacks; provenance fields come back None.
    """
    from src.synthesis.citations import extract_numeric_citations
    from src.connectors.base import Source

    sources = [
        Source(id="tv_a1b2c3d4", title="Alpha", url="http://a", content="x"),
        Source(id="sx_deadbeef", title="Beta", url="http://b", content="y"),
    ]
    citations = extract_numeric_citations("Claim [1] and claim [2].", sources)

    assert len(citations) == 2
    # Canonical shape, all 7 keys present (codex DESIGN Q2).
    expected_keys = {"number", "id", "source_id", "title", "url", "origin", "source_type"}
    assert all(set(c.keys()) == expected_keys for c in citations)
    # number + id (= str(number)).
    assert citations[0]["number"] == 1
    assert citations[0]["id"] == "1"
    # source_id preserves connector trace (provenance metadata, not marker).
    assert citations[0]["source_id"] == "tv_a1b2c3d4"
    assert citations[1]["source_id"] == "sx_deadbeef"
    # origin/source_type None because connectors.base.Source doesn't carry them.
    assert citations[0]["origin"] is None
    assert citations[0]["source_type"] is None


def test_extract_numeric_citations_accepts_pregathered_source():
    """Aggregator path uses `PreGatheredSource` (no .id, has .origin +
    .source_type). source_id comes back None; origin/source_type populated.
    """
    from src.synthesis.aggregator import PreGatheredSource
    from src.synthesis.citations import extract_numeric_citations

    sources = [
        PreGatheredSource(
            origin="exa", url="http://1", title="One",
            content="x", source_type="documentation",
        ),
    ]
    citations = extract_numeric_citations("Claim [1].", sources)

    assert len(citations) == 1
    assert citations[0]["number"] == 1
    assert citations[0]["id"] == "1"
    # PreGatheredSource has no .id attribute → source_id is None.
    assert citations[0]["source_id"] is None
    # origin + source_type travel through.
    assert citations[0]["origin"] == "exa"
    assert citations[0]["source_type"] == "documentation"


def test_canonical_citation_shape_dict_keys_stable_across_source_types():
    """The dict shape is identical whether source is connector.Source or
    PreGatheredSource — only the values differ. Downstream consumers can
    treat citation dicts uniformly without conditional key checks.
    """
    from src.synthesis.aggregator import PreGatheredSource
    from src.synthesis.citations import extract_numeric_citations
    from src.connectors.base import Source

    conn_sources = [Source(id="tv_1", title="A", url="http://a", content="x")]
    pgs_sources = [PreGatheredSource(
        origin="exa", url="http://b", title="B", content="y", source_type="article",
    )]

    conn_cits = extract_numeric_citations("[1]", conn_sources)
    pgs_cits = extract_numeric_citations("[1]", pgs_sources)

    # Same keys, different values.
    assert set(conn_cits[0].keys()) == set(pgs_cits[0].keys())


# ---------------------------------------------------------------------------
# citations.py — drift detection helpers
# ---------------------------------------------------------------------------


def test_detect_legacy_markers_finds_all_three_connectors():
    """tv_/sx_/lu_ prefixes (Tavily/SearXNG/LinkUp) all detected."""
    from src.synthesis.citations import detect_legacy_markers

    content = "First [tv_aaaa] then [sx_bbbb] then [lu_cccc]."
    assert detect_legacy_markers(content) == ["tv_aaaa", "sx_bbbb", "lu_cccc"]


def test_detect_legacy_markers_deduplicates_first_seen_order():
    from src.synthesis.citations import detect_legacy_markers

    content = "[tv_1234] then [sx_abcd] then [tv_1234] again [sx_abcd] again."
    assert detect_legacy_markers(content) == ["tv_1234", "sx_abcd"]


def test_detect_legacy_markers_empty_on_clean_content():
    from src.synthesis.citations import detect_legacy_markers

    assert detect_legacy_markers("No markers at all.") == []
    assert detect_legacy_markers("Only [1] and [2] numeric.") == []


def test_detect_mixed_markers_true_when_both_present():
    from src.synthesis.citations import detect_mixed_markers

    assert detect_mixed_markers("First [1] then [tv_a1b2c3d4].") is True


def test_detect_mixed_markers_false_when_only_one_kind():
    from src.synthesis.citations import detect_mixed_markers

    assert detect_mixed_markers("Pure numeric [1] and [2].") is False
    assert detect_mixed_markers("Pure legacy [tv_aaaa] and [sx_bbbb].") is False
    assert detect_mixed_markers("No markers at all.") is False


# ---------------------------------------------------------------------------
# engine.py — end-to-end through new extractor
# ---------------------------------------------------------------------------


def _mock_llm_client(content: str):
    """Build a mock LLMClient whose chat.completions.create returns `content`."""
    client = MagicMock()
    response = SimpleNamespace(
        choices=[SimpleNamespace(
            message=SimpleNamespace(
                content=content,
                reasoning="",
                reasoning_content="",
            ),
            finish_reason="stop",
        )],
        usage=SimpleNamespace(prompt_tokens=100, completion_tokens=50),
    )
    client.chat.completions.create = AsyncMock(return_value=response)
    client.last_model_used = "test-model"
    return client


@pytest.mark.asyncio
async def test_engine_extracts_numeric_citations_end_to_end():
    """SynthesisEngine.research with mocked LLM emitting [1][2] → canonical dicts."""
    from src.connectors.base import Source
    from src.synthesis.engine import SynthesisEngine

    sources = [
        Source(id="tv_aaaa", title="Alpha", url="http://a", content="x"),
        Source(id="sx_bbbb", title="Beta", url="http://b", content="y"),
    ]
    client = _mock_llm_client(
        "Alpha source says X [1]. Beta source confirms Y [2]."
    )
    engine = SynthesisEngine(client=client)

    result = await engine.research("test query", sources, reasoning_effort="low")

    # Canonical dicts in citations field.
    assert len(result["citations"]) == 2
    assert result["citations"][0]["number"] == 1
    assert result["citations"][0]["id"] == "1"
    assert result["citations"][0]["source_id"] == "tv_aaaa"
    assert result["citations"][1]["source_id"] == "sx_bbbb"
    # sources_used preserves order from citation numbers.
    assert [s.id for s in result["sources_used"]] == ["tv_aaaa", "sx_bbbb"]


@pytest.mark.asyncio
async def test_engine_ignores_legacy_markers_only():
    """Engine emitting only [tv_*] (regression) → zero citations (no fallback)."""
    from src.connectors.base import Source
    from src.synthesis.engine import SynthesisEngine

    sources = [
        Source(id="tv_aaaa", title="Alpha", url="http://a", content="x"),
    ]
    # Mock LLM regresses to old contract: [tv_aaaa] instead of [1].
    client = _mock_llm_client("Alpha says X [tv_aaaa].")
    engine = SynthesisEngine(client=client)

    result = await engine.research("test query", sources, reasoning_effort="low")

    # v0.3.0 extractor only recognizes [N] — [tv_*] markers count as zero
    # citations. The verifier (separate code path) is the one that surfaces
    # the legacy-marker diagnostic as a soft warning.
    assert result["citations"] == []
    assert result["sources_used"] == []


# ---------------------------------------------------------------------------
# CitationSchema — number + source_id flow through /research
# ---------------------------------------------------------------------------


def test_citation_schema_has_number_and_source_id_fields():
    """v0.3.0 CitationSchema extended with `number: int` + `source_id: str|None`."""
    from src.api.schemas import CitationSchema

    schema = CitationSchema(
        id="1", number=1, source_id="tv_a1b2c3d4",
        title="Alpha", url="http://a",
    )
    assert schema.number == 1
    assert schema.source_id == "tv_a1b2c3d4"
    assert schema.id == "1"


def test_citation_schema_source_id_optional():
    """Aggregator path (PreGatheredSource) has no .id — source_id None is valid."""
    from src.api.schemas import CitationSchema

    schema = CitationSchema(
        id="1", number=1, source_id=None,
        title="Alpha", url="http://a",
    )
    assert schema.source_id is None


def test_citation_schema_defaults_for_back_compat():
    """Construction without number/source_id still works (defaults supplied)."""
    from src.api.schemas import CitationSchema

    # Old-style construction (id/title/url only) still accepted via defaults.
    schema = CitationSchema(id="1", title="A", url="http://a")
    assert schema.number == 0  # default
    assert schema.source_id is None


# ---------------------------------------------------------------------------
# output_verifier.py — marker drift soft warnings
# ---------------------------------------------------------------------------


def test_verifier_legacy_only_soft_warning():
    """Content with only `[tv_*]` markers → drift soft warning + hard-fail
    (cited_count==0 still triggers). The soft warning is the diagnostic
    explaining WHY the hard-fail fired (model regressed to old contract).
    """
    from src.synthesis.output_verifier import verify_synthesis_output

    verdict = verify_synthesis_output(
        content="Legacy citation [tv_a1b2c3d4] in here.",
        llm_output=None,
        cited_count=0,
        source_count=2,
    )
    # Hard-fail (cited_count==0) still fires.
    assert any("cites none" in f for f in verdict.hard_failures)
    # Diagnostic soft warning identifies the marker drift.
    assert any("legacy" in w.lower() and "tv_a1b2c3d4" in w for w in verdict.soft_warnings)


def test_verifier_mixed_marker_soft_warning():
    """Content with both `[N]` AND `[tv_*]` → drift soft warning (no hard fail
    if numeric extraction succeeded — passing cited_count > 0).
    """
    from src.synthesis.output_verifier import verify_synthesis_output

    verdict = verify_synthesis_output(
        content="Numeric [1] and legacy [tv_a1b2c3d4] together.",
        llm_output=None,
        cited_count=1,
        source_count=1,
    )
    # No hard-fail — numeric extraction succeeded.
    assert not verdict.hard_failures
    # Mixed-marker drift surfaces as soft warning.
    assert any("both" in w.lower() and "legacy" in w.lower() for w in verdict.soft_warnings)


def test_verifier_pure_numeric_no_drift_warning():
    """Clean `[N]`-only content → no marker-drift warning."""
    from src.synthesis.output_verifier import verify_synthesis_output

    verdict = verify_synthesis_output(
        content="Pure numeric [1] and [2] citations.",
        llm_output=None,
        cited_count=2,
        source_count=2,
    )
    # Neither the legacy-only nor the mixed soft warning fires.
    assert not any("marker drift" in w.lower() for w in verdict.soft_warnings)


def test_verifier_legacy_marker_preview_truncates_at_three():
    """Soft warning preview shows first 3 markers + "(+N more)" suffix."""
    from src.synthesis.output_verifier import verify_synthesis_output

    content = " ".join(f"[tv_{i:08x}]" for i in range(6))
    verdict = verify_synthesis_output(
        content=content,
        llm_output=None,
        cited_count=0,
        source_count=1,
    )
    drift_warnings = [w for w in verdict.soft_warnings if "legacy" in w.lower()]
    assert drift_warnings
    # 6 markers found → preview = 3 + "(+3 more)".
    assert "+3 more" in drift_warnings[0]


# ---------------------------------------------------------------------------
# Module-level regressions — enhanced.py deletion
# ---------------------------------------------------------------------------


def test_enhanced_module_no_longer_exists():
    """`src/synthesis/enhanced.py` deleted in v0.3.0 (codex DESIGN Q6 — no
    importers found at design lock time). Catches accidental re-creation.
    """
    import os
    enhanced_path = os.path.join(
        os.path.dirname(__file__), "..", "src", "synthesis", "enhanced.py"
    )
    assert not os.path.exists(enhanced_path), (
        "src/synthesis/enhanced.py was deleted in v0.3.0 — if it returns, "
        "verify no other code path migrated to use it and update v0.3.0 docs."
    )


def test_synthesis_init_does_not_import_enhanced():
    """The synthesis package's public API does not re-export EnhancedSynthesizer."""
    import src.synthesis as synth_pkg

    assert not hasattr(synth_pkg, "EnhancedSynthesizer")
    assert "EnhancedSynthesizer" not in getattr(synth_pkg, "__all__", [])


# ---------------------------------------------------------------------------
# Turn 8 HIGH fix — verifier wired into mcp__research + REST /research
# ---------------------------------------------------------------------------
#
# Codex Turn 8 finding: SynthesisEngine.research() callers in
# mcp_server.py and routes.py bypassed verify_synthesis_output(), so
# legacy-only `[xx_<hex>]` output silently shipped as "successful content
# with no citations" — the Q7 drift diagnostics never fired on the very
# surface Q1 migrated. These tests lock the wiring.


def _mock_engine_research(content: str, citations: list[dict]):
    """Build an AsyncMock that emulates SynthesisEngine.research return shape.

    The real engine post-v0.3.0 returns a dict with content/citations/
    sources_used/model/usage. Tests patch on `engine.research` and replay
    a pre-built shape so we don't need the real LLM.
    """
    from unittest.mock import AsyncMock
    return AsyncMock(return_value={
        "content": content,
        "citations": citations,
        "sources_used": [],
        "model": "test-model",
        "usage": {"prompt_tokens": 100, "completion_tokens": 50},
    })


@pytest.mark.asyncio
async def test_mcp_research_surfaces_drift_warning_on_legacy_only_output():
    """MCP `research` with mocked engine returning legacy-only `[tv_*]` content
    AND empty citations (because the v0.3.0 extractor ignores legacy markers)
    must produce output containing BOTH the hard-fail header AND the drift
    soft warning, not silently return content as success.
    """
    from unittest.mock import MagicMock, patch
    from src.connectors.base import Source
    from src import mcp_server
    def _tool_fn(name):
        # FastMCP >=3 keeps the original coroutine fn bound at the module
        # name (the @mcp.tool() decorator no longer wraps it), so resolve the
        # tool by attribute rather than reaching into private internals.
        return getattr(mcp_server, name)

    sample_source = Source(
        id="s1", title="t", url="https://example.com",
        content="c", score=1.0, connector="searxng", metadata={},
    )

    with patch('src.mcp_server.SearchAggregator') as mock_agg, \
         patch('src.synthesis.wrappers.SynthesisEngine') as mock_engine:
        mock_agg_instance = MagicMock()
        mock_agg_instance.search = AsyncMock(return_value=([sample_source], {}))
        mock_agg_instance.get_active_connectors = MagicMock(return_value=["searxng"])
        mock_agg.return_value = mock_agg_instance

        mock_engine_instance = MagicMock()
        # Legacy `[tv_*]` content; citations empty because v0.3.0 extractor
        # ignores legacy markers (the model regressed to the old contract).
        mock_engine_instance.research = _mock_engine_research(
            content="The Alpha source confirms X [tv_aaaaaaaa].",
            citations=[],
        )
        mock_engine.return_value = mock_engine_instance

        result = await _tool_fn("research")(query="x", top_k=3)

    # Hard-fail header is present (cited_count==0 fires the existing gate).
    assert "Synthesis verification FAILED" in result
    assert "cites none" in result
    # Drift diagnostic identifies the regression cause.
    assert "marker drift" in result.lower()
    assert "tv_aaaaaaaa" in result


@pytest.mark.asyncio
async def test_mcp_research_surfaces_drift_warning_on_mixed_markers():
    """Mixed `[N]` + `[tv_*]` content must surface the mixed-marker soft
    warning. Numeric extraction succeeded so no hard-fail fires.
    """
    from unittest.mock import MagicMock, patch
    from src.connectors.base import Source
    from src import mcp_server
    def _tool_fn(name):
        # FastMCP >=3 keeps the original coroutine fn bound at the module
        # name (the @mcp.tool() decorator no longer wraps it), so resolve the
        # tool by attribute rather than reaching into private internals.
        return getattr(mcp_server, name)

    sample_source = Source(
        id="s1", title="Alpha", url="https://example.com",
        content="c", score=1.0, connector="searxng", metadata={},
    )

    with patch('src.mcp_server.SearchAggregator') as mock_agg, \
         patch('src.synthesis.wrappers.SynthesisEngine') as mock_engine:
        mock_agg_instance = MagicMock()
        mock_agg_instance.search = AsyncMock(return_value=([sample_source], {}))
        mock_agg_instance.get_active_connectors = MagicMock(return_value=["searxng"])
        mock_agg.return_value = mock_agg_instance

        mock_engine_instance = MagicMock()
        # Mixed contract: [1] resolves cleanly, [tv_*] is drift.
        mock_engine_instance.research = _mock_engine_research(
            content="Source confirms X [1] and also Y [tv_bbbbbbbb].",
            citations=[{
                "number": 1, "id": "1", "source_id": "s1",
                "title": "Alpha", "url": "https://example.com",
                "origin": None, "source_type": None,
            }],
        )
        mock_engine.return_value = mock_engine_instance

        result = await _tool_fn("research")(query="x", top_k=3)

    # No hard-fail because numeric extraction succeeded.
    assert "Synthesis verification FAILED" not in result
    # Mixed-marker warning present in verification notes.
    assert "marker drift" in result.lower()
    assert "both" in result.lower() and "legacy" in result.lower()


@pytest.mark.asyncio
async def test_mcp_research_clean_numeric_no_drift_warning():
    """Pure `[N]` output produces no drift warning — the negative control
    proving the wiring doesn't spuriously fire on healthy synthesis.
    """
    from unittest.mock import MagicMock, patch
    from src.connectors.base import Source
    from src import mcp_server
    def _tool_fn(name):
        # FastMCP >=3 keeps the original coroutine fn bound at the module
        # name (the @mcp.tool() decorator no longer wraps it), so resolve the
        # tool by attribute rather than reaching into private internals.
        return getattr(mcp_server, name)

    sample_source = Source(
        id="s1", title="Alpha", url="https://example.com",
        content="c", score=1.0, connector="searxng", metadata={},
    )

    with patch('src.mcp_server.SearchAggregator') as mock_agg, \
         patch('src.synthesis.wrappers.SynthesisEngine') as mock_engine:
        mock_agg_instance = MagicMock()
        mock_agg_instance.search = AsyncMock(return_value=([sample_source], {}))
        mock_agg_instance.get_active_connectors = MagicMock(return_value=["searxng"])
        mock_agg.return_value = mock_agg_instance

        mock_engine_instance = MagicMock()
        mock_engine_instance.research = _mock_engine_research(
            content="Source confirms X [1].",
            citations=[{
                "number": 1, "id": "1", "source_id": "s1",
                "title": "Alpha", "url": "https://example.com",
                "origin": None, "source_type": None,
            }],
        )
        mock_engine.return_value = mock_engine_instance

        result = await _tool_fn("research")(query="x", top_k=3)

    # Clean — no verification failure, no drift warning.
    assert "Synthesis verification FAILED" not in result
    assert "marker drift" not in result.lower()


def test_rest_research_surfaces_drift_warning_on_legacy_only_output():
    """REST `/research` with mocked engine returning legacy-only `[tv_*]`
    must annotate the response content with the hard-fail header + drift
    soft warning. Mirror of the MCP test but exercising the FastAPI path.
    """
    from unittest.mock import AsyncMock, MagicMock, patch
    from fastapi.testclient import TestClient
    from src.connectors.base import Source
    from src.main import app

    sample_source = Source(
        id="s1", title="Alpha", url="https://example.com",
        content="c", score=1.0, connector="searxng", metadata={},
    )

    with patch('src.api.routes.SearchAggregator') as mock_agg, \
         patch('src.synthesis.wrappers.SynthesisEngine') as mock_engine:
        mock_agg_instance = MagicMock()
        mock_agg_instance.search = AsyncMock(return_value=([sample_source], {}))
        mock_agg_instance.get_active_connectors = MagicMock(return_value=["searxng"])
        mock_agg.return_value = mock_agg_instance

        mock_engine_instance = MagicMock()
        mock_engine_instance.research = _mock_engine_research(
            content="The Alpha source confirms X [tv_aaaaaaaa].",
            citations=[],
        )
        mock_engine.return_value = mock_engine_instance

        client = TestClient(app)
        response = client.post("/api/v1/research", json={
            "query": "test", "reasoning_effort": "low",
        })

    assert response.status_code == 200
    body = response.json()
    content = body["content"]
    # Verifier annotation surfaces both the hard-fail header and the
    # drift diagnostic — operator sees WHY the synthesis failed verification.
    assert "Synthesis verification FAILED" in content
    assert "marker drift" in content.lower()
    assert "tv_aaaaaaaa" in content


def test_rest_research_clean_numeric_no_annotation():
    """REST `/research` with clean `[N]` output returns raw content (no
    verifier annotation appended). Verifies the `verdict.passed and not
    verdict.soft_warnings` short-circuit avoids spurious annotation noise.
    """
    from unittest.mock import AsyncMock, MagicMock, patch
    from fastapi.testclient import TestClient
    from src.connectors.base import Source
    from src.main import app

    sample_source = Source(
        id="s1", title="Alpha", url="https://example.com",
        content="c", score=1.0, connector="searxng", metadata={},
    )

    with patch('src.api.routes.SearchAggregator') as mock_agg, \
         patch('src.synthesis.wrappers.SynthesisEngine') as mock_engine:
        mock_agg_instance = MagicMock()
        mock_agg_instance.search = AsyncMock(return_value=([sample_source], {}))
        mock_agg_instance.get_active_connectors = MagicMock(return_value=["searxng"])
        mock_agg.return_value = mock_agg_instance

        mock_engine_instance = MagicMock()
        mock_engine_instance.research = _mock_engine_research(
            content="Source confirms X [1].",
            citations=[{
                "number": 1, "id": "1", "source_id": "s1",
                "title": "Alpha", "url": "https://example.com",
                "origin": None, "source_type": None,
            }],
        )
        mock_engine.return_value = mock_engine_instance

        client = TestClient(app)
        response = client.post("/api/v1/research", json={
            "query": "test", "reasoning_effort": "low",
        })

    assert response.status_code == 200
    body = response.json()
    content = body["content"]
    assert "Synthesis verification FAILED" not in content
    assert "marker drift" not in content.lower()
