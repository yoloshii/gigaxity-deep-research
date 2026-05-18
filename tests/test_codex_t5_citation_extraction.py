"""Regression tests for codex Turn 5 findings (v0.2.1, 2026-05-18).

Codex Turn 5 surfaced that MCP `synthesize` with `preset="comprehensive"`
(use_outline=True) deterministically hard-failed the verifier with "cites none
of N sources" because:

- `OutlinedSynthesis` has no `citations` field (outline.py:37)
- MCP wrapper computed `cited_count = len(result.citations) if getattr(...)`
  which returned 0 for outline results
- Verifier therefore fired hard-fail at output_verifier.py:152-155 even when
  the model emitted valid `[N]` markers

REST `/synthesize/p1` had parity (routes.py:1313 calls
`_extract_citations_from_content` after outline synthesis); MCP did not.

Fix landed:
- HIGH 1: `src/synthesis/citations.py` — shared `extract_numeric_citations()`
- HIGH 2: MCP `synthesize` normalizes via `getattr(result, 'citations', None)
  or extract_numeric_citations(...)` before footer + verifier
- HIGH 3 (these tests): MCP-level outline regression coverage
"""

import re
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.synthesis import (
    PreGatheredSource,
    QualityDecision,
    SynthesisStyle,
)
from src.synthesis.aggregator import SynthesisAggregator
from src.synthesis.citations import extract_numeric_citations
from src.synthesis.quality_gate import QualityGateResult


# ---------------------------------------------------------------------------
# extract_numeric_citations — unit tests
# ---------------------------------------------------------------------------


def _src(n: int) -> PreGatheredSource:
    """Build a PreGatheredSource with deterministic identity for test asserts."""
    return PreGatheredSource(
        origin=f"origin-{n}",
        url=f"https://example.com/{n}",
        title=f"Source {n}",
        content=f"content {n}",
        source_type="article",
    )


def test_extract_numeric_citations_happy_path():
    sources = [_src(1), _src(2), _src(3)]
    content = "First claim [1] and second [2] and third [3]."

    citations = extract_numeric_citations(content, sources)

    assert len(citations) == 3
    assert [c["number"] for c in citations] == [1, 2, 3]
    assert citations[0]["title"] == "Source 1"
    assert citations[1]["url"] == "https://example.com/2"
    assert citations[2]["origin"] == "origin-3"


def test_extract_numeric_citations_dedup_first_appearance_order():
    sources = [_src(1), _src(2), _src(3)]
    content = "A [2] B [1] C [2] D [3] E [1]."

    citations = extract_numeric_citations(content, sources)

    # Deduplicated, in order of first appearance
    assert [c["number"] for c in citations] == [2, 1, 3]


def test_extract_numeric_citations_out_of_range_silently_skipped():
    sources = [_src(1), _src(2)]
    content = "Only [1] and [2] are valid. [5] and [99] should be ignored."

    citations = extract_numeric_citations(content, sources)

    assert [c["number"] for c in citations] == [1, 2]


def test_extract_numeric_citations_no_markers_returns_empty():
    sources = [_src(1), _src(2)]
    content = "Just prose with no bracketed citations at all."

    assert extract_numeric_citations(content, sources) == []


def test_extract_numeric_citations_empty_sources_returns_empty():
    # Even with [N] in content, no sources means no valid resolution
    assert extract_numeric_citations("Claim [1].", []) == []


def test_extract_numeric_citations_zero_index_skipped():
    # [0] is ambiguous; the formatter uses 1-based, so [0] must not resolve
    sources = [_src(1)]
    citations = extract_numeric_citations("Claim [0] not valid.", sources)
    assert citations == []


def test_extract_numeric_citations_returns_full_dict_shape():
    """Canonical citation dict shape (v0.3.0, codex DESIGN Q2).

    v0.2.1 emitted 5 keys (number, title, url, origin, source_type). v0.3.0
    added `id` (`= str(number)`, public compatibility alias from the merged
    SynthesisEngine path) and `source_id` (connector trace provenance), so
    the contract is stable across aggregator AND engine paths.
    """
    sources = [_src(1)]
    citations = extract_numeric_citations("[1]", sources)
    assert set(citations[0].keys()) == {
        "number", "id", "source_id", "title", "url", "origin", "source_type"
    }


# ---------------------------------------------------------------------------
# Delegation tests — aggregator + REST use the shared extractor
# ---------------------------------------------------------------------------


def test_aggregator_extract_citations_delegates_to_shared():
    """SynthesisAggregator._extract_citations now returns the shared extractor
    output verbatim. Aggregator's prior implementation diverged subtly from
    REST's (0-indexed conversion vs 1-based bounds check); the shared resolver
    fixes both."""
    sources = [_src(1), _src(2)]
    content = "alpha [1] beta [2] gamma [99]"

    expected = extract_numeric_citations(content, sources)
    # Construct an aggregator with a no-op client (we don't await anything)
    agg = SynthesisAggregator(llm_client=MagicMock(), model="test")
    actual = agg._extract_citations(content, sources)

    assert actual == expected
    # And: dict shape matches the shared resolver
    assert [c["number"] for c in actual] == [1, 2]


def test_rest_extract_citations_from_content_delegates_to_shared():
    """REST `_extract_citations_from_content` wraps the shared extractor's
    dict output into CitationSchema."""
    from src.api.routes import _extract_citations_from_content
    from src.api.schemas import CitationSchema

    sources = [_src(1), _src(2), _src(3)]
    content = "claim [1] and [3]"

    citations = _extract_citations_from_content(content, sources)

    assert len(citations) == 2
    assert all(isinstance(c, CitationSchema) for c in citations)
    assert citations[0].id == "1"
    assert citations[0].title == "Source 1"
    assert citations[1].id == "3"
    assert citations[1].url == "https://example.com/3"


# ---------------------------------------------------------------------------
# MCP outline regression (codex HIGH 3 headline)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mcp_synthesize_outline_extracts_numeric_citations_from_content():
    """codex Turn 5 HIGH 3: preset='comprehensive' (use_outline=True) with
    outline content containing [1][2] MUST pass verifier and emit a citations
    footer.

    Before fix: OutlinedSynthesis has no `citations` field, MCP wrapper used
    `getattr(result, "citations", None)` → returned 0 → verifier hard-failed
    with 'cites none of N provided sources' regardless of [N] markers in
    content."""
    from src import mcp_server

    # Mock outline result that contains valid [N] markers — exactly the
    # failure mode codex flagged: aggregator-style markers, no `citations` attr
    class FakeOutlineResult:
        content = (
            "Anthropic released Claude Opus 4.7 on April 16 [1]. "
            "The conference was held May 6 in San Francisco [2]. "
            "Agent SDK billing changes effective June 15 [3]."
        )
        llm_output = MagicMock(reasoning_only=False, truncated=False, subcall_failed=False)
        # NOTE: explicitly NO `citations` attribute — mirrors the real
        # OutlinedSynthesis dataclass (outline.py:37)

    fake_outline = MagicMock()
    fake_outline.synthesize = AsyncMock(return_value=FakeOutlineResult())

    # Aggregator must NOT be called for comprehensive (use_outline=True)
    fake_aggregator = MagicMock()
    fake_aggregator.synthesize = AsyncMock(side_effect=AssertionError(
        "Comprehensive preset has use_outline=True; aggregator path must not run"
    ))

    test_sources = [
        PreGatheredSource(origin="t", url="https://a.com", title="A", content="x", source_type="article"),
        PreGatheredSource(origin="t", url="https://b.com", title="B", content="y", source_type="article"),
        PreGatheredSource(origin="t", url="https://c.com", title="C", content="z", source_type="article"),
    ]
    fake_gate_result = QualityGateResult(
        decision=QualityDecision.PROCEED,
        avg_quality=0.9,
        good_sources=test_sources,
        rejected_sources=[],
        source_scores=[0.9, 0.9, 0.9],
    )
    fake_gate = MagicMock()
    fake_gate.evaluate = AsyncMock(return_value=fake_gate_result)

    fake_rcs = MagicMock()
    fake_rcs.prepare = AsyncMock(return_value=MagicMock(summaries=[]))
    fake_detector = MagicMock()
    fake_detector.detect = AsyncMock(return_value=MagicMock(contradictions=[], parse_failed=False))

    with patch.object(mcp_server, "_get_llm_client", return_value=MagicMock()), \
         patch.object(mcp_server, "SynthesisAggregator", return_value=fake_aggregator), \
         patch.object(mcp_server, "OutlineGuidedSynthesizer", return_value=fake_outline), \
         patch.object(mcp_server, "SourceQualityGate", return_value=fake_gate), \
         patch.object(mcp_server, "RCSPreprocessor", return_value=fake_rcs), \
         patch.object(mcp_server, "ContradictionDetector", return_value=fake_detector), \
         patch.object(mcp_server.cache, "get", return_value=None), \
         patch.object(mcp_server.cache, "set"):
        result = await mcp_server.synthesize.fn(
            query="anthropic may 2026 announcements",
            sources=[
                {"title": "A", "content": "x"},
                {"title": "B", "content": "y"},
                {"title": "C", "content": "z"},
            ],
            preset="comprehensive",
        )

    # The verifier MUST NOT hard-fail with cites-none
    assert "verification FAILED" not in result, (
        "Outline content with valid [N] citations should pass the verifier. "
        "Without the normalization, MCP would have hard-failed."
    )
    assert "cites none" not in result.lower()

    # The Citations footer should be rendered with resolved sources
    assert "## Citations" in result
    # Each [N] should resolve to its 1-indexed source
    assert "[1]" in result and "[2]" in result and "[3]" in result


@pytest.mark.asyncio
async def test_mcp_synthesize_outline_zero_citations_still_hard_fails():
    """Inverse of the above: when outline content has NO [N] markers, the
    verifier MUST still hard-fail. The normalization should NOT mask the
    genuine missing-citation case."""
    from src import mcp_server

    class FakeOutlineResultNoCitations:
        content = (
            "Anthropic released Claude Opus 4.7 in April. "
            "The conference was held in San Francisco. "
            "Agent SDK billing changes are coming."
        )
        llm_output = MagicMock(reasoning_only=False, truncated=False, subcall_failed=False)
        # NO `citations` attribute, AND content has no [N] markers

    fake_outline = MagicMock()
    fake_outline.synthesize = AsyncMock(return_value=FakeOutlineResultNoCitations())

    test_sources = [
        PreGatheredSource(origin="t", url="https://a.com", title="A", content="x", source_type="article"),
        PreGatheredSource(origin="t", url="https://b.com", title="B", content="y", source_type="article"),
    ]
    fake_gate_result = QualityGateResult(
        decision=QualityDecision.PROCEED,
        avg_quality=0.9,
        good_sources=test_sources,
        rejected_sources=[],
        source_scores=[0.9, 0.9],
    )
    fake_gate = MagicMock()
    fake_gate.evaluate = AsyncMock(return_value=fake_gate_result)

    fake_rcs = MagicMock()
    fake_rcs.prepare = AsyncMock(return_value=MagicMock(summaries=[]))
    fake_detector = MagicMock()
    fake_detector.detect = AsyncMock(return_value=MagicMock(contradictions=[], parse_failed=False))

    with patch.object(mcp_server, "_get_llm_client", return_value=MagicMock()), \
         patch.object(mcp_server, "OutlineGuidedSynthesizer", return_value=fake_outline), \
         patch.object(mcp_server, "SourceQualityGate", return_value=fake_gate), \
         patch.object(mcp_server, "RCSPreprocessor", return_value=fake_rcs), \
         patch.object(mcp_server, "ContradictionDetector", return_value=fake_detector), \
         patch.object(mcp_server.cache, "get", return_value=None), \
         patch.object(mcp_server.cache, "set") as cache_set:
        result = await mcp_server.synthesize.fn(
            query="test query",
            sources=[
                {"title": "A", "content": "x"},
                {"title": "B", "content": "y"},
            ],
            preset="comprehensive",
        )

    # Verifier MUST hard-fail with cites-none (no [N] in content → 0 citations)
    assert "verification FAILED" in result
    assert "cites none of the 2 provided sources" in result
    # Failed verdicts MUST NOT be cached
    cache_set.assert_not_called()


@pytest.mark.asyncio
async def test_mcp_synthesize_aggregator_path_still_uses_result_citations():
    """For aggregator-path presets (use_outline=False, e.g. contracrow), the
    normalization should fall through to result.citations and not re-extract.
    This protects the path that already works — Subagent 1's contracrow case
    in real-world testing."""
    from src import mcp_server

    aggregator_citations = [
        {"number": 1, "title": "T1", "url": "u1", "origin": "o1", "source_type": "article"},
        {"number": 2, "title": "T2", "url": "u2", "origin": "o2", "source_type": "article"},
    ]

    class FakeAggregatorResult:
        content = "Aggregator content with no [N] in it (citations come from result.citations)"
        citations = aggregator_citations  # aggregator already extracted internally
        llm_output = MagicMock(reasoning_only=False, truncated=False, subcall_failed=False)
        source_attribution = {}

    fake_aggregator = MagicMock()
    fake_aggregator.synthesize = AsyncMock(return_value=FakeAggregatorResult())

    fake_outline = MagicMock()
    fake_outline.synthesize = AsyncMock(side_effect=AssertionError(
        "Contracrow has use_outline=False; outline path must not run"
    ))

    test_sources = [
        PreGatheredSource(origin="t", url="u1", title="T1", content="x", source_type="article"),
        PreGatheredSource(origin="t", url="u2", title="T2", content="y", source_type="article"),
    ]
    fake_gate_result = QualityGateResult(
        decision=QualityDecision.PROCEED,
        avg_quality=0.9,
        good_sources=test_sources,
        rejected_sources=[],
        source_scores=[0.9, 0.9],
    )
    fake_gate = MagicMock()
    fake_gate.evaluate = AsyncMock(return_value=fake_gate_result)

    fake_rcs = MagicMock()
    fake_rcs.prepare = AsyncMock(return_value=MagicMock(summaries=[]))
    fake_detector = MagicMock()
    fake_detector.detect = AsyncMock(return_value=MagicMock(contradictions=[], parse_failed=False))

    # Spy on the shared extractor — it MUST NOT be called when result.citations
    # is already populated. Otherwise we'd be doing double work on the happy path.
    with patch.object(mcp_server, "_get_llm_client", return_value=MagicMock()), \
         patch.object(mcp_server, "SynthesisAggregator", return_value=fake_aggregator), \
         patch.object(mcp_server, "OutlineGuidedSynthesizer", return_value=fake_outline), \
         patch.object(mcp_server, "SourceQualityGate", return_value=fake_gate), \
         patch.object(mcp_server, "RCSPreprocessor", return_value=fake_rcs), \
         patch.object(mcp_server, "ContradictionDetector", return_value=fake_detector), \
         patch.object(mcp_server, "extract_numeric_citations", wraps=mcp_server.extract_numeric_citations) as spy, \
         patch.object(mcp_server.cache, "get", return_value=None), \
         patch.object(mcp_server.cache, "set"):
        result = await mcp_server.synthesize.fn(
            query="compare X vs Y",
            sources=[
                {"title": "T1", "content": "x"},
                {"title": "T2", "content": "y"},
            ],
            preset="contracrow",
        )

    # Shared extractor should NOT have been invoked for the aggregator path
    spy.assert_not_called()
    # Citations footer should render from result.citations
    assert "## Citations" in result
    assert "T1" in result and "T2" in result
