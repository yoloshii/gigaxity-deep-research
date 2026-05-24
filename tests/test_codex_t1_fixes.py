"""Regression tests for codex Turn 1 + Turn 2 + Turn 3 findings (2026-05-18).

Covers:
- H1: MCP `synthesize` honors preset's style when caller omits explicit style
    (Turn 2 F2: now actually tested at the mcp_server.synthesize call site)
- H2: REJECT decision early-returns from MCP (mirror of REST behavior)
    (Turn 2 F2: now actually tested at the mcp_server.synthesize call site)
- H3: Verifier hard-fails on uncited entities, soft-warns ONLY when synthesis
    explicitly frames the gap (Turn 2 F1: tightened from previous soft-warn
    default; gap-framing escape hatch added)
- H4+H5: Per-preset relevance thresholds + entity-balanced filter
- H6: LLM-scoring content window > 300 chars
- Turn 2 F3: apply_overrides() preserves new quality_gate_* preset fields
- Turn 2 F4: Entity-balanced promotion uses centrality (title > body density)
- Turn 2 F5: Extractor handles vLLM / gpt-4o / llama.cpp shapes
- Turn 2 F6: MCP PARTIAL-with-zero-good early-return (gate-bypass fix)

H7 (canonical SKILL.md sync) and F7 (docs sync) are docs-only fixes verified
by file diff, not exercised by Python tests.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.synthesis import (
    SynthesisStyle,
    PresetName,
    get_preset,
    SourceQualityGate,
    QualityGateResult,
    QualityDecision,
    PreGatheredSource,
    extract_query_entities,
    verify_synthesis_output,
    PresetOverrides,
    apply_overrides,
)


# ---------- H3: verifier entity-coverage check ----------

def test_verifier_no_entities_passes():
    """No query_entities arg → entity-coverage check is a no-op (backward compat)."""
    verdict = verify_synthesis_output(
        content="Some answer about Tavily that cites [1].",
        llm_output=None,
        cited_count=1,
        source_count=1,
    )
    assert verdict.passed
    assert not verdict.soft_warnings


def test_verifier_no_entities_in_output_passes():
    """Query has entities but synthesis discusses none → no-op."""
    verdict = verify_synthesis_output(
        content="Generic answer with no specific vendor mentions, cites [1].",
        llm_output=None,
        cited_count=1,
        source_count=1,
        query_entities=["Tavily", "LinkUp", "Serper"],
        sources_text="generic content",
    )
    assert verdict.passed


def test_verifier_all_entities_covered_passes():
    """Every entity in synthesis also in sources → clean pass."""
    verdict = verify_synthesis_output(
        content="Tavily is faster than LinkUp. Cites [1].",
        llm_output=None,
        cited_count=1,
        source_count=2,
        query_entities=["Tavily", "LinkUp"],
        sources_text="tavily docs say its fast. linkup docs say its cheap.",
    )
    assert verdict.passed


def test_verifier_some_entities_uncovered_hard_fails_post_turn2():
    """Turn 2 F1 hardened H3: 'some uncovered' is no longer a soft warn.
    Now hard-fails any uncovered entity unless the synthesis explicitly
    frames the gap (see gap-framing tests below). This test asserts the
    new default behavior — replaces the obsolete Turn 1 soft-warn default."""
    verdict = verify_synthesis_output(
        content="Tavily is documented. LinkUp pricing is €5/1K. Cites [1].",
        llm_output=None,
        cited_count=1,
        source_count=1,
        query_entities=["Tavily", "LinkUp"],
        sources_text="tavily docs only without other vendor data",
    )
    assert not verdict.passed
    assert any("LinkUp" in f for f in verdict.hard_failures)


def test_verifier_all_discussed_entities_uncovered_hard_fails():
    """Synthesis discusses ONLY entities absent from sources → hard fail."""
    verdict = verify_synthesis_output(
        content="LinkUp is €5/1K. Serper is $0.30/1K. Cites [1].",
        llm_output=None,
        cited_count=1,
        source_count=1,
        query_entities=["Tavily", "LinkUp", "Serper"],
        sources_text="tavily docs only, no other vendor info",
    )
    assert not verdict.passed
    assert any("LinkUp" in f or "Serper" in f for f in verdict.hard_failures)


def test_verifier_zero_cited_still_hard_fails():
    """The original hard-fail (zero citations on sources>0) still fires."""
    verdict = verify_synthesis_output(
        content="An answer with no citation markers.",
        llm_output=None,
        cited_count=0,
        source_count=3,
    )
    assert not verdict.passed
    assert any("cites none" in f for f in verdict.hard_failures)


# ---------- entity extraction heuristic ----------

def test_extract_entities_strips_leading_stopwords():
    """'Compare Tavily' → ['Tavily'] (Compare is dropped from front)."""
    result = extract_query_entities("Compare Tavily, LinkUp, and Serper APIs")
    assert "Tavily" in result
    assert "LinkUp" in result
    assert "Serper" in result
    assert "Compare" not in result
    assert "Compare Tavily" not in result


def test_extract_entities_strips_trailing_stopwords():
    """'Serper APIs' → ['Serper'] (APIs is stopword)."""
    result = extract_query_entities("Serper APIs are cheap")
    assert "Serper" in result
    assert "APIs" not in result
    assert "Serper APIs" not in result


def test_extract_entities_keeps_multiword_product():
    """'Tavily Search' is kept because Search is not a stopword."""
    result = extract_query_entities("Tavily Search API vs Exa")
    assert "Tavily Search" in result or "Tavily" in result
    assert "Exa" in result


def test_extract_entities_empty_query():
    assert extract_query_entities("") == []
    assert extract_query_entities(None) == []


def test_extract_entities_no_capitalized():
    """All lowercase → no entities."""
    assert extract_query_entities("just lowercase words here") == []


# ---------- H6: LLM scoring content window ----------

def test_scoring_window_constant_is_1500():
    """The class-level constant should be the bumped value (was 300)."""
    assert SourceQualityGate._SCORING_CONTENT_WINDOW == 1500


def test_format_sources_uses_window():
    """_format_sources slices to the window (not 300)."""
    gate = SourceQualityGate()
    long_content = "X" * 5000
    source = MagicMock()
    source.title = "test"
    source.content = long_content
    formatted = gate._format_sources([source])
    # 1500 chars of content + title + framing
    assert formatted.count("X") == 1500


# ---------- H4+H5: per-preset thresholds + entity-balanced ----------

def test_comprehensive_preset_has_relaxed_thresholds():
    p = get_preset("comprehensive")
    assert p.quality_gate_reject_threshold == 0.2
    assert p.quality_gate_pass_threshold == 0.4
    assert p.quality_gate_entity_balanced is True


def test_contracrow_preset_has_relaxed_thresholds():
    p = get_preset("contracrow")
    assert p.quality_gate_reject_threshold == 0.2
    assert p.quality_gate_pass_threshold == 0.4
    assert p.quality_gate_entity_balanced is True


def test_fast_preset_threshold_defaults_to_none():
    """Non-comparison presets keep class defaults (None → 0.3/0.5)."""
    p = get_preset("fast")
    assert p.quality_gate_reject_threshold is None
    assert p.quality_gate_pass_threshold is None
    assert p.quality_gate_entity_balanced is False


def test_quality_gate_accepts_entity_balanced_arg():
    """Constructor accepts the new kwarg."""
    gate = SourceQualityGate(entity_balanced=True)
    assert gate.entity_balanced is True
    gate2 = SourceQualityGate(entity_balanced=False)
    assert gate2.entity_balanced is False
    gate3 = SourceQualityGate()  # default
    assert gate3.entity_balanced is False


def _make_source(title: str, content: str) -> PreGatheredSource:
    return PreGatheredSource(
        origin="test",
        url=f"http://example.com/{title}",
        title=title,
        content=content,
        source_type="article",
    )


def test_entity_balanced_promotes_uncovered_entity():
    """When entity_balanced is on and a query entity is uncovered in good_sources,
    promote the best-scoring rejected source mentioning it. Garbage source with
    no entity match stays rejected — proves promotion is selective, not blanket."""
    sources = [
        _make_source("Tavily Deep Dive", "tavily is great, all about tavily here"),
        _make_source("LinkUp Notes", "linkup brief mention, low quality"),
        _make_source("Serper Brief", "serper barely covered here"),
        _make_source("Unrelated Junk", "irrelevant filler content with no vendor"),
    ]
    # Simulate scores: only Tavily passes; 3 below pass threshold
    scores = [0.9, 0.4, 0.35, 0.3]

    gate = SourceQualityGate(entity_balanced=True)
    # Force heuristic path by not setting llm_client; inject scores directly.
    gate._score_sources_heuristic = MagicMock(return_value=scores)

    import asyncio
    result = asyncio.run(gate.evaluate(
        "Compare Tavily, LinkUp, and Serper",
        sources,
    ))
    # PARTIAL decision since Unrelated Junk stays rejected
    assert result.decision == QualityDecision.PARTIAL
    # Entity-covering sources all promoted into good_sources
    titles = [s.title for s in result.good_sources]
    assert "Tavily Deep Dive" in titles
    assert "LinkUp Notes" in titles
    assert "Serper Brief" in titles
    # Junk with no entity match stays rejected
    assert "Unrelated Junk" not in titles
    rejected_titles = [s.title for s in result.rejected_sources]
    assert "Unrelated Junk" in rejected_titles


def test_entity_balanced_off_does_not_promote():
    """With entity_balanced=False, low-scoring sources stay rejected."""
    sources = [
        _make_source("Tavily Deep Dive", "tavily is great, all about tavily here"),
        _make_source("LinkUp Notes", "linkup brief mention, low quality"),
        _make_source("Serper Brief", "serper barely covered here"),
    ]
    scores = [0.9, 0.4, 0.35]

    gate = SourceQualityGate(entity_balanced=False)
    gate._score_sources_heuristic = MagicMock(return_value=scores)

    import asyncio
    result = asyncio.run(gate.evaluate(
        "Compare Tavily, LinkUp, and Serper",
        sources,
    ))
    assert result.decision == QualityDecision.PARTIAL
    titles = [s.title for s in result.good_sources]
    assert "Tavily Deep Dive" in titles
    assert "LinkUp Notes" not in titles
    assert "Serper Brief" not in titles


def test_entity_balanced_does_not_override_reject():
    """When avg_quality is below reject threshold, REJECT still fires regardless of entity_balanced."""
    sources = [
        _make_source("Garbage 1", "irrelevant content"),
        _make_source("Garbage 2", "also irrelevant"),
    ]
    scores = [0.1, 0.1]

    gate = SourceQualityGate(entity_balanced=True, reject_threshold=0.3)
    gate._score_sources_heuristic = MagicMock(return_value=scores)

    result = asyncio.run(gate.evaluate(
        "Compare Tavily, LinkUp, and Serper",
        sources,
    ))
    assert result.decision == QualityDecision.REJECT
    assert result.good_sources == []


# ---------- Turn 2 F1: H3 hardened with gap-framing escape ----------

def test_verifier_partial_uncovered_hard_fails_without_framing():
    """Turn 2 F1: any uncovered entity → hard fail when no gap framing."""
    verdict = verify_synthesis_output(
        content="Tavily is documented. LinkUp pricing is €5/1K. Cites [1].",
        llm_output=None,
        cited_count=1,
        source_count=1,
        query_entities=["Tavily", "LinkUp"],
        sources_text="tavily docs only without other vendor data",
    )
    assert not verdict.passed
    assert any("LinkUp" in f for f in verdict.hard_failures)


def test_verifier_partial_uncovered_soft_warns_with_gap_framing():
    """Synthesis explicitly frames the gap → downgrade to soft warn."""
    verdict = verify_synthesis_output(
        content=(
            "Tavily is documented in source [1]. LinkUp pricing is €5/1K but "
            "we have no source available for LinkUp in the gathered sources, "
            "so this claim is unverified."
        ),
        llm_output=None,
        cited_count=1,
        source_count=1,
        query_entities=["Tavily", "LinkUp"],
        sources_text="tavily docs only without other vendor data",
    )
    assert verdict.passed
    assert any("LinkUp" in w for w in verdict.soft_warnings)


def test_verifier_gap_framing_must_cover_every_uncovered_entity():
    """If only SOME uncovered entities are gap-framed → still hard fail."""
    verdict = verify_synthesis_output(
        content=(
            "Tavily is documented [1]. We have no source available for LinkUp. "
            "Serper costs $0.30/1K queries."
        ),
        llm_output=None,
        cited_count=1,
        source_count=1,
        query_entities=["Tavily", "LinkUp", "Serper"],
        sources_text="tavily docs only",
    )
    # LinkUp is gap-framed, Serper is not → still hard fail
    assert not verdict.passed


# ---------- Turn 2 F3: apply_overrides preserves new fields ----------

def test_apply_overrides_preserves_quality_gate_fields():
    """Turn 2 F3: previously dropped the 3 new fields."""
    base = get_preset("comprehensive")
    assert base.quality_gate_reject_threshold == 0.2
    assert base.quality_gate_pass_threshold == 0.4
    assert base.quality_gate_entity_balanced is True

    overrides = PresetOverrides(max_tokens=4000)
    new = apply_overrides(base, overrides)
    assert new.max_tokens == 4000
    assert new.quality_gate_reject_threshold == 0.2
    assert new.quality_gate_pass_threshold == 0.4
    assert new.quality_gate_entity_balanced is True


# ---------- Turn 2 F4: entity-centrality promotion ----------

def test_promotion_prefers_title_match_over_body_mention():
    """A title-match source wins over a high-scoring body-mention source.

    Scenario: LinkUp uncovered. Source A is a Tavily-focused page that
    mentions LinkUp once in passing ('unlike LinkUp'). Source B has 'LinkUp'
    in the title even with a lower scalar score. F4 says Source B should
    promote, NOT Source A (centrality 3.0 vs ~1.0).
    """
    sources = [
        _make_source("Tavily Deep Dive", "tavily is great. unlike linkup, tavily..."),
        _make_source("LinkUp Pricing", "pricing details, scalar score is low"),
    ]
    scores = [0.45, 0.42]  # both below pass=0.5; A scores higher

    gate = SourceQualityGate(entity_balanced=True)
    gate._score_sources_heuristic = MagicMock(return_value=scores)

    result = asyncio.run(gate.evaluate("Compare Tavily and LinkUp", sources))
    titles = [s.title for s in result.good_sources]
    # LinkUp Pricing must be promoted (title centrality 3.0)
    assert "LinkUp Pricing" in titles
    # Tavily Deep Dive should NOT be promoted as LinkUp coverage
    # (its centrality for "linkup" is 1.0, body mention only)


def test_promotion_skips_source_with_zero_entity_match():
    """A source with no entity mention at all should NOT be promoted."""
    sources = [
        _make_source("Tavily Notes", "all about tavily"),
        _make_source("Unrelated", "nothing relevant to any vendor here"),
    ]
    scores = [0.55, 0.45]  # Tavily passes, Unrelated below

    gate = SourceQualityGate(entity_balanced=True)
    gate._score_sources_heuristic = MagicMock(return_value=scores)

    result = asyncio.run(gate.evaluate("Compare Tavily and LinkUp", sources))
    titles = [s.title for s in result.good_sources]
    # No LinkUp source exists → nothing to promote
    assert "Tavily Notes" in titles
    assert "Unrelated" not in titles


# ---------- Turn 2 F5: extractor expanded shapes ----------

def test_extract_entities_internal_caps():
    """vLLM, iOS, eBay — lowercase-start with internal cap."""
    result = extract_query_entities("Compare vLLM and SGLang on iOS")
    assert "vLLM" in result
    assert "SGLang" in result
    assert "iOS" in result


def test_extract_entities_hyphenated():
    """gpt-4o, claude-3-5, Llama-3 — hyphenated identifiers."""
    result = extract_query_entities("Compare gpt-4o and claude-3-5 and Llama-3")
    assert "gpt-4o" in result
    assert "claude-3-5" in result
    assert "Llama-3" in result


def test_extract_entities_dotted():
    """llama.cpp, asyncio.gather — dotted module paths."""
    result = extract_query_entities("Compare llama.cpp and ollama for inference")
    assert "llama.cpp" in result


def test_extract_entities_lowercase_tools_now_detected_post_items_6_7():
    """Single-word lowercase tools (bun, npm, deno) ARE detected as of
    BACKLOG Item 7 (post-v0.3.0 cleanup, codex DESIGN 019e3a66). The
    extractor's Shape 5 matches against `LOWERCASE_TOOL_ALLOWLIST`
    case-sensitively. This inverts the prior `..._known_limitations_
    documented` test (which asserted F5 was NOT covered)."""
    result = extract_query_entities("Compare bun and npm and deno")
    assert "bun" in result
    assert "npm" in result
    assert "deno" in result


# ---------- Turn 2 F2/F6: MCP synthesize behavior tests ----------

@pytest.mark.asyncio
async def test_mcp_synthesize_preset_style_honored_when_style_omitted():
    """H1 verified at the MCP call site (Turn 2 F2): when caller omits style
    and passes preset='contracrow', the SynthesisAggregator should be invoked
    with style=SynthesisStyle.COMPARATIVE (contracrow's preset.style)."""
    from src import mcp_server

    captured = {}

    # Phase 0: finalize_synthesis isinstance-dispatches over
    # AggregatedSynthesis — return a real instance, not a duck-typed surrogate.
    from src.synthesis import AggregatedSynthesis, SynthesisStyle as _Style

    async def fake_synthesize(query, sources, style, max_tokens, guidance=None, contradiction_notes=None):
        captured["style"] = style
        return AggregatedSynthesis(
            content="Synthesis output [1]",
            citations=[{"number": 1, "id": "1", "source_id": None, "title": "T",
                        "url": "u", "origin": None, "source_type": None}],
            source_attribution={},
            confidence=0.7,
            style_used=style,
            word_count=2,
            llm_output=None,
        )

    # OutlineGuidedSynthesizer is used when preset.use_outline=True
    # (comprehensive uses it; contracrow does NOT — uses SynthesisAggregator directly)
    fake_outline = MagicMock()
    fake_outline.synthesize = AsyncMock(side_effect=fake_synthesize)

    fake_aggregator = MagicMock()
    fake_aggregator.synthesize = AsyncMock(side_effect=fake_synthesize)

    # Gate: PROCEED with all sources (don't filter)
    fake_gate_result = QualityGateResult(
        decision=QualityDecision.PROCEED,
        avg_quality=0.9,
        good_sources=[],  # populated below
        rejected_sources=[],
        source_scores=[0.9],
    )

    fake_gate = MagicMock()
    fake_gate.evaluate = AsyncMock(return_value=fake_gate_result)

    fake_rcs = MagicMock()
    fake_rcs.prepare = AsyncMock(return_value=MagicMock(summaries=[]))
    fake_detector = MagicMock()
    fake_detector.detect = AsyncMock(return_value=MagicMock(contradictions=[], parse_failed=False))

    with patch.object(mcp_server, "_get_llm_client", return_value=MagicMock()), \
         patch("src.synthesis.wrappers.SynthesisAggregator", return_value=fake_aggregator), \
         patch("src.synthesis.wrappers.OutlineGuidedSynthesizer", return_value=fake_outline), \
         patch.object(mcp_server, "SourceQualityGate", return_value=fake_gate), \
         patch.object(mcp_server, "RCSPreprocessor", return_value=fake_rcs), \
         patch.object(mcp_server, "ContradictionDetector", return_value=fake_detector), \
         patch.object(mcp_server.cache, "get", return_value=None), \
         patch.object(mcp_server.cache, "set"):
        # Populate good_sources from the actual pre_sources the function builds
        fake_gate_result.good_sources = [
            PreGatheredSource(origin="t", url="u", title="T", content="c", source_type="article")
        ]

        # Call synthesize directly (it's an mcp.tool() wrapper; access fn attr if needed)
        result = await mcp_server.synthesize.fn(
            query="test query",
            sources=[{"title": "T", "content": "c"}],
            preset="contracrow",
            # style omitted → should fall through to preset's style
        )

    assert "style" in captured, "SynthesisAggregator.synthesize was never called"
    assert captured["style"] == SynthesisStyle.COMPARATIVE, (
        f"Expected COMPARATIVE (contracrow's preset.style), got {captured['style']}"
    )


@pytest.mark.asyncio
async def test_mcp_synthesize_reject_early_returns():
    """H2 verified at the MCP call site (Turn 2 F2): REJECT must NOT invoke
    the synthesizer and must return a 'Source quality insufficient' message."""
    from src import mcp_server

    fake_aggregator = MagicMock()
    fake_aggregator.synthesize = AsyncMock(side_effect=AssertionError(
        "SynthesisAggregator MUST NOT be called when gate REJECTs"
    ))
    fake_outline = MagicMock()
    fake_outline.synthesize = AsyncMock(side_effect=AssertionError(
        "OutlineGuidedSynthesizer MUST NOT be called when gate REJECTs"
    ))

    fake_gate_result = QualityGateResult(
        decision=QualityDecision.REJECT,
        avg_quality=0.1,
        good_sources=[],
        rejected_sources=[
            PreGatheredSource(origin="t", url="u", title="T", content="c", source_type="article")
        ],
        source_scores=[0.1],
        suggestion="Try broader searches.",
    )

    fake_gate = MagicMock()
    fake_gate.evaluate = AsyncMock(return_value=fake_gate_result)
    fake_gate.reject_threshold = 0.2

    with patch.object(mcp_server, "_get_llm_client", return_value=MagicMock()), \
         patch("src.synthesis.wrappers.SynthesisAggregator", return_value=fake_aggregator), \
         patch("src.synthesis.wrappers.OutlineGuidedSynthesizer", return_value=fake_outline), \
         patch.object(mcp_server, "SourceQualityGate", return_value=fake_gate), \
         patch.object(mcp_server.cache, "get", return_value=None), \
         patch.object(mcp_server.cache, "set") as cache_set:
        result = await mcp_server.synthesize.fn(
            query="some weak query",
            sources=[{"title": "T", "content": "c"}],
            preset="comprehensive",
        )

    assert "Source quality insufficient" in result
    assert "0 passed" in result
    assert "Try broader searches" in result
    # MUST NOT be cached
    cache_set.assert_not_called()


@pytest.mark.asyncio
async def test_mcp_synthesize_partial_with_zero_good_early_returns():
    """Turn 2 F6: PARTIAL with empty good_sources is a gate-bypass class — must early-return."""
    from src import mcp_server

    fake_aggregator = MagicMock()
    fake_aggregator.synthesize = AsyncMock(side_effect=AssertionError(
        "SynthesisAggregator MUST NOT be called on PARTIAL-with-zero-good"
    ))
    fake_outline = MagicMock()
    fake_outline.synthesize = AsyncMock(side_effect=AssertionError(
        "OutlineGuidedSynthesizer MUST NOT be called on PARTIAL-with-zero-good"
    ))

    # PARTIAL but no source cleared pass threshold
    fake_gate_result = QualityGateResult(
        decision=QualityDecision.PARTIAL,
        avg_quality=0.25,  # above reject 0.2 but below pass 0.4
        good_sources=[],
        rejected_sources=[
            PreGatheredSource(origin="t", url="u", title="T", content="c", source_type="article")
        ],
        source_scores=[0.25],
        suggestion="Sources are weak.",
    )

    fake_gate = MagicMock()
    fake_gate.evaluate = AsyncMock(return_value=fake_gate_result)
    fake_gate.reject_threshold = 0.2
    fake_gate.pass_threshold = 0.4

    with patch.object(mcp_server, "_get_llm_client", return_value=MagicMock()), \
         patch("src.synthesis.wrappers.SynthesisAggregator", return_value=fake_aggregator), \
         patch("src.synthesis.wrappers.OutlineGuidedSynthesizer", return_value=fake_outline), \
         patch.object(mcp_server, "SourceQualityGate", return_value=fake_gate), \
         patch.object(mcp_server.cache, "get", return_value=None), \
         patch.object(mcp_server.cache, "set") as cache_set:
        result = await mcp_server.synthesize.fn(
            query="query with weak sources",
            sources=[{"title": "T", "content": "c"}],
            preset="comprehensive",
        )

    assert "Source quality insufficient" in result
    assert "partial, zero passed" in result.lower() or "PARTIAL" in result
    assert "0 passed" in result
    cache_set.assert_not_called()


# ---------- Turn 3 T3F1: boundary-safe entity matching ----------

def test_entity_in_text_boundary_safety_exa_vs_example():
    """T3F1: 'Exa' must NOT match 'example' under token-boundary matching.

    Both args are expected pre-lowercased (caller contract documented in
    `_entity_match_count`); test mirrors production caller convention.
    """
    from src.synthesis.quality_gate import _entity_in_text, _entity_match_count
    assert not _entity_in_text("an example of usage", "exa")
    assert _entity_match_count("an example of usage", "exa") == 0
    assert _entity_in_text("exa is a vector search engine", "exa")
    assert _entity_match_count("exa is a vector search engine, and exa scales", "exa") == 2


def test_entity_centrality_boundary_no_false_inflation():
    """T3F1: a source full of 'example' must NOT score positive Exa centrality."""
    gate = SourceQualityGate()
    src = _make_source(
        "Generic Examples",
        "example one. example two. example three. example four. example five.",
    )
    assert gate._entity_centrality(src, "exa") == 0.0


def test_entity_centrality_boundary_real_match_still_works():
    """T3F1 negative: legitimate 'Exa' mentions still count."""
    gate = SourceQualityGate()
    src = _make_source(
        "Exa Search API",  # title token match → 3.0
        "Exa lets you search the web semantically. Exa scales well.",
    )
    assert gate._entity_centrality(src, "exa") == 3.0


def test_verifier_uses_boundary_matching():
    """T3F1: 'Exa' in synthesis is NOT 'covered' by an 'example'-only source."""
    verdict = verify_synthesis_output(
        content="Exa is a vector search engine that I'm citing here [1].",
        llm_output=None,
        cited_count=1,
        source_count=1,
        query_entities=["Exa"],
        sources_text="here is an example of how it works",
    )
    # Substring-based check would say Exa is "covered" (because "example"
    # contains "exa"). Boundary-safe check correctly flags as uncovered.
    assert not verdict.passed
    assert any("Exa" in f for f in verdict.hard_failures)


def test_verifier_gap_framing_uses_boundary_matching():
    """T3F1: gap-framing check is boundary-safe too — 'example' won't frame 'Exa'."""
    verdict = verify_synthesis_output(
        content=(
            "Exa pricing is unknown [1]. There is no source for this example. "
            "I have no source available for Exa."
        ),
        llm_output=None,
        cited_count=1,  # citation exists — bypass the zero-citation hard-fail
        source_count=1,
        query_entities=["Exa"],
        sources_text="unrelated content with no relevant vendor mentions",
    )
    # First sentence has "Exa" but no framing in that sentence.
    # Second sentence has "no source for this example" — framing word but
    # NOT the Exa token (boundary check rejects — substring would falsely
    # frame Exa via "example").
    # Third sentence has "no source available for Exa" — both, framed. ✅
    # The entity-coverage check iterates all uncovered entities and accepts
    # if any sentence frames them; "Exa" is framed in sentence 3.
    assert verdict.passed, f"hard_failures={verdict.hard_failures}"
    assert any("Exa" in w for w in verdict.soft_warnings)


# ---------- T3-extra: tightened promotion threshold (>= 2.0) ----------

def test_promotion_skips_single_body_mention_source():
    """T3-extra (codex rec #2): one-off body mention no longer promotes.

    Source has 'linkup' mentioned ONCE in body, scoring centrality 1.0.
    Previously promoted; now skipped (threshold raised to >= 2.0).
    """
    sources = [
        _make_source("Tavily Deep Dive", "tavily is great"),
        _make_source(
            "Random Notes",
            "this mentions linkup once in passing. otherwise unrelated content.",
        ),
    ]
    scores = [0.55, 0.4]  # Tavily passes, Random Notes below

    gate = SourceQualityGate(entity_balanced=True)
    gate._score_sources_heuristic = MagicMock(return_value=scores)

    result = asyncio.run(gate.evaluate("Compare Tavily and LinkUp", sources))
    titles = [s.title for s in result.good_sources]
    # Random Notes has centrality 1.0 for "linkup" (< 2.0 threshold) → NOT promoted
    assert "Tavily Deep Dive" in titles
    assert "Random Notes" not in titles


def test_promotion_accepts_title_match():
    """T3-extra: title match scores 3.0 → still promotes."""
    sources = [
        _make_source("Tavily Deep Dive", "tavily is great"),
        _make_source("LinkUp Pricing Page", "pricing details"),
    ]
    scores = [0.55, 0.4]

    gate = SourceQualityGate(entity_balanced=True)
    gate._score_sources_heuristic = MagicMock(return_value=scores)

    result = asyncio.run(gate.evaluate("Compare Tavily and LinkUp", sources))
    titles = [s.title for s in result.good_sources]
    assert "LinkUp Pricing Page" in titles


def test_promotion_accepts_dense_body_mentions():
    """T3-extra: >= 3 body mentions scores 2.0 → still promotes."""
    sources = [
        _make_source("Tavily Deep Dive", "tavily is great"),
        _make_source(
            "API Comparison",
            "linkup is one option. linkup costs €5/1K. consider linkup for B2B.",
        ),
    ]
    scores = [0.55, 0.4]

    gate = SourceQualityGate(entity_balanced=True)
    gate._score_sources_heuristic = MagicMock(return_value=scores)

    result = asyncio.run(gate.evaluate("Compare Tavily and LinkUp", sources))
    titles = [s.title for s in result.good_sources]
    # API Comparison has 3 linkup mentions → centrality 2.0 → promoted
    assert "API Comparison" in titles


# ---------- T3F2: REST PARTIAL-with-zero-good early-return ----------

def test_rest_synthesize_enhanced_partial_with_zero_good_early_returns():
    """T3F2: REST /synthesize/enhanced mirrors MCP F6 (was bypassed before)."""
    from src.api import routes
    from src.api.routes import _get_llm_client  # noqa: F401
    from fastapi.testclient import TestClient
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(routes.router, prefix="/api/v1")
    client = TestClient(app)

    fake_gate_result = QualityGateResult(
        decision=QualityDecision.PARTIAL,
        avg_quality=0.25,
        good_sources=[],
        rejected_sources=[
            PreGatheredSource(origin="t", url="u", title="T", content="c", source_type="article")
        ],
        source_scores=[0.25],
        suggestion="Try better sources.",
    )
    fake_gate = MagicMock()
    fake_gate.evaluate = AsyncMock(return_value=fake_gate_result)
    fake_gate.reject_threshold = 0.3
    fake_gate.pass_threshold = 0.5

    with patch.object(routes, "_get_llm_client", return_value=MagicMock()), \
         patch.object(routes, "SourceQualityGate", return_value=fake_gate):
        response = client.post(
            "/api/v1/synthesize/enhanced",
            json={
                "query": "test",
                "sources": [{"title": "T", "content": "c", "origin": "test", "url": "", "source_type": "article", "metadata": {}}],
                "style": "comprehensive",
                "run_quality_gate": True,
            },
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert "Source quality insufficient" in body["content"]
    assert "PARTIAL, zero passed" in body["content"]
    assert body["citations"] == []


def test_rest_synthesize_p1_partial_with_zero_good_early_returns():
    """T3F2: REST /synthesize/p1 mirrors MCP F6 (was bypassed before)."""
    from src.api import routes
    from fastapi.testclient import TestClient
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(routes.router, prefix="/api/v1")
    client = TestClient(app)

    fake_gate_result = QualityGateResult(
        decision=QualityDecision.PARTIAL,
        avg_quality=0.25,
        good_sources=[],
        rejected_sources=[
            PreGatheredSource(origin="t", url="u", title="T", content="c", source_type="article")
        ],
        source_scores=[0.25],
        suggestion="Try better sources.",
    )
    fake_gate = MagicMock()
    fake_gate.evaluate = AsyncMock(return_value=fake_gate_result)
    fake_gate.reject_threshold = 0.3
    fake_gate.pass_threshold = 0.5

    with patch.object(routes, "_get_llm_client", return_value=MagicMock()), \
         patch.object(routes, "SourceQualityGate", return_value=fake_gate):
        response = client.post(
            "/api/v1/synthesize/p1",
            json={
                "query": "test",
                "sources": [{"title": "T", "content": "c", "origin": "test", "url": "", "source_type": "article", "metadata": {}}],
                "preset": "comprehensive",
            },
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert "Source quality insufficient" in body["content"]
    assert "PARTIAL, zero passed" in body["content"]
    assert body["citations"] == []
