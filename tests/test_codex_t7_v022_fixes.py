"""Regression tests for codex Turn 7 findings (v0.2.2, 2026-05-18).

v0.2.1 post-ship re-test surfaced three BACKLOG items shipped as v0.2.2 slice 1:

- Item 1 (cosmetic): `Contradictions Detected` stanza rendered as
  ``- **Unknown** (moderate):  vs `` when the LLM emitted a block that
  passed the ``"TOPIC:" in block`` substring check but did not have a
  parseable ``TOPIC:`` line (e.g., prose mention "The TOPIC: heading is
  unclear"). ``fields.get("TOPIC", "Unknown")`` returned the literal
  ``"Unknown"`` and ``POSITION_A``/``POSITION_B`` defaulted to empty.
  Fix: reject blocks missing any of topic/position_a/position_b at
  ``_parse_contradictions``; defense-in-depth guard at MCP render site.

- Item 3 (prompt hardening): all synthesis prompts that emit ``[N]``
  citations now reference a shared ``CITATION_FORMAT_GUIDE`` with three
  worked examples and explicit negative examples, replacing the prior
  single-sentence "Use [1], [2], etc." instruction.

- Item 4 (REST /research parity): /research silently ignored
  ``QualityDecision.REJECT`` from the pre-synthesis quality gate, running
  synthesis over all the same sources the gate had just rejected. Now
  short-circuits on REJECT and on PARTIAL-with-zero-good, matching the
  v0.2.0 fix at /synthesize/p1 and /synthesize/enhanced.
"""

from unittest.mock import AsyncMock, MagicMock, patch

from src.synthesis import (
    PreGatheredSource,
    QualityDecision,
)
from src.synthesis.citations import CITATION_FORMAT_GUIDE
from src.synthesis.contradictions import (
    Contradiction,
    ContradictionDetector,
    ContradictionSeverity,
)
from src.synthesis.quality_gate import QualityGateResult


# ---------------------------------------------------------------------------
# Item 1 — Contradiction detector rejects malformed blocks
# ---------------------------------------------------------------------------


def _detector() -> ContradictionDetector:
    """Detector with no LLM client; we exercise `_parse_contradictions` directly."""
    return ContradictionDetector()


def test_parse_contradictions_rejects_block_with_empty_topic():
    """v0.2.2 item 1: a block with empty TOPIC but valid POSITION_A/B is dropped.
    Before fix: appended with topic='', rendering as '- **** (moderate): ...'."""
    response = (
        "TOPIC:\n"
        "POSITION_A: Source one supports X.\n"
        "SOURCE_A: 1\n"
        "POSITION_B: Source two refutes X.\n"
        "SOURCE_B: 2\n"
        "SEVERITY: moderate\n"
        "---"
    )

    result = _detector()._parse_contradictions(response)

    assert result == []


def test_parse_contradictions_rejects_block_with_empty_position_a():
    """v0.2.2 item 1: a block with empty POSITION_A is dropped."""
    response = (
        "TOPIC: Whether Redux is required\n"
        "POSITION_A:\n"
        "SOURCE_A: 1\n"
        "POSITION_B: Source two says Context API suffices.\n"
        "SOURCE_B: 2\n"
        "SEVERITY: minor\n"
        "---"
    )

    result = _detector()._parse_contradictions(response)

    assert result == []


def test_parse_contradictions_rejects_block_with_empty_position_b():
    """v0.2.2 item 1: a block with empty POSITION_B is dropped."""
    response = (
        "TOPIC: Whether Redux is required\n"
        "POSITION_A: Source one says yes.\n"
        "SOURCE_A: 1\n"
        "POSITION_B:\n"
        "SOURCE_B: 2\n"
        "SEVERITY: major\n"
        "---"
    )

    result = _detector()._parse_contradictions(response)

    assert result == []


def test_parse_contradictions_rejects_topic_substring_in_prose():
    """v0.2.2 item 1: a block where 'TOPIC:' appears only in prose (not as a
    parseable header) is dropped. Before fix: fields.get('TOPIC', 'Unknown')
    returned 'Unknown' because the 'TOPIC:' line never landed in the fields
    dict, producing the '- **Unknown** (moderate):  vs ' empirical output."""
    response = (
        "The TOPIC: heading is unclear, but here is an attempt.\n"
        "Some unrelated text without structured fields.\n"
        "---"
    )

    result = _detector()._parse_contradictions(response)

    assert result == []


def test_parse_contradictions_accepts_fully_populated_block():
    """v0.2.2 item 1 positive control: a well-formed block still parses."""
    response = (
        "TOPIC: Whether Redux is required for React state\n"
        "POSITION_A: Redux is essential for non-trivial apps\n"
        "SOURCE_A: 1\n"
        "POSITION_B: Modern React with Context API replaces most Redux uses\n"
        "SOURCE_B: 2\n"
        "SEVERITY: moderate\n"
        "RESOLUTION: Depends on app scale and team familiarity\n"
        "---"
    )

    result = _detector()._parse_contradictions(response)

    assert len(result) == 1
    c = result[0]
    assert c.topic == "Whether Redux is required for React state"
    assert c.position_a == "Redux is essential for non-trivial apps"
    assert c.position_b == "Modern React with Context API replaces most Redux uses"
    assert c.severity == ContradictionSeverity.MODERATE
    assert c.resolution_hint == "Depends on app scale and team familiarity"


def test_parse_contradictions_mixed_keeps_valid_drops_malformed():
    """v0.2.2 item 1: mixed-validity response keeps only the well-formed blocks."""
    response = (
        # Valid block
        "TOPIC: Performance impact of caching\n"
        "POSITION_A: Cache always improves performance\n"
        "SOURCE_A: 1\n"
        "POSITION_B: Cache invalidation overhead can hurt small-data workloads\n"
        "SOURCE_B: 2\n"
        "SEVERITY: minor\n"
        "---\n"
        # Malformed block (empty POSITION_B)
        "TOPIC: Whether GraphQL replaces REST\n"
        "POSITION_A: GraphQL is the future\n"
        "SOURCE_A: 3\n"
        "POSITION_B:\n"
        "SOURCE_B: 4\n"
        "SEVERITY: major\n"
        "---"
    )

    result = _detector()._parse_contradictions(response)

    assert len(result) == 1
    assert result[0].topic == "Performance impact of caching"


# ---------------------------------------------------------------------------
# Item 1 — MCP renderer guard (defense in depth)
# ---------------------------------------------------------------------------


def test_mcp_render_skips_contradictions_with_empty_fields():
    """v0.2.2 item 1: even if a malformed Contradiction slips past the detector
    (heuristic detector / future code path), MCP render must not emit empty
    stanzas. Belt-and-braces with the source-side `_parse_contradictions` guard."""
    bad_c = Contradiction(
        topic="",
        position_a="",
        source_a=1,
        position_b="",
        source_b=2,
        severity=ContradictionSeverity.MODERATE,
    )
    good_c = Contradiction(
        topic="Real disagreement",
        position_a="Side A view",
        source_a=1,
        position_b="Side B view",
        source_b=2,
        severity=ContradictionSeverity.MAJOR,
    )

    renderable = [
        c for c in [bad_c, good_c]
        if c.topic and c.position_a and c.position_b
    ]

    assert len(renderable) == 1
    assert renderable[0] is good_c


# ---------------------------------------------------------------------------
# Item 3 — Prompt hardening: every [N]-citation prompt embeds the shared guide
# ---------------------------------------------------------------------------


def test_citation_format_guide_has_required_examples():
    """v0.2.2 item 3: the shared guide must carry the three worked examples
    codex Turn 5 specified (single, multi, co-citation) plus negative examples.
    The model's compliance hinges on these — if the guide regresses, every
    downstream prompt regresses."""
    assert "[1]" in CITATION_FORMAT_GUIDE
    assert "[1][3]" in CITATION_FORMAT_GUIDE
    assert "[xx_hex]" in CITATION_FORMAT_GUIDE
    # Three bullets minimum
    assert CITATION_FORMAT_GUIDE.count("\n- ") >= 3


def test_aggregator_prompts_include_citation_guide():
    """v0.2.2 item 3: all five aggregator prompt templates carry the shared
    CITATION_FORMAT_GUIDE post-interpolation. A regression that drops the
    guide from any one template would let the corresponding style fall back
    to the prior thin "Use [1], [2], etc." instruction."""
    from src.synthesis.aggregator import (
        ACADEMIC_SYNTHESIS_PROMPT,
        COMPARATIVE_SYNTHESIS_PROMPT,
        COMPREHENSIVE_SYNTHESIS_PROMPT,
        CONCISE_SYNTHESIS_PROMPT,
        REASONING_SYNTHESIS_PROMPT,
    )

    needle = '"Anthropic released Claude Opus 4.7 on April 16 [1]."'
    for prompt in (
        COMPREHENSIVE_SYNTHESIS_PROMPT,
        CONCISE_SYNTHESIS_PROMPT,
        COMPARATIVE_SYNTHESIS_PROMPT,
        ACADEMIC_SYNTHESIS_PROMPT,
        REASONING_SYNTHESIS_PROMPT,
    ):
        assert needle in prompt


def test_outline_section_and_refine_prompts_include_citation_guide():
    """v0.2.2 item 3: SECTION_PROMPT and REFINE_PROMPT (the two outline
    templates that ask for [N] citations) carry the guide. OUTLINE_PROMPT
    is intentionally NOT touched — it produces section headings, not citations."""
    from src.synthesis.outline import OutlineGuidedSynthesizer

    needle = '"Anthropic released Claude Opus 4.7 on April 16 [1]."'
    assert needle in OutlineGuidedSynthesizer.SECTION_PROMPT
    assert needle in OutlineGuidedSynthesizer.REFINE_PROMPT
    # And NOT in OUTLINE_PROMPT (negative control — that prompt only picks headings)
    assert needle not in OutlineGuidedSynthesizer.OUTLINE_PROMPT


def test_prompts_still_format_with_query_and_sources():
    """v0.2.2 item 3 sanity: the f-string conversion preserves runtime
    `.format(query=..., sources=...)` substitution. A bug here would break
    every synthesis call."""
    from src.synthesis.aggregator import COMPREHENSIVE_SYNTHESIS_PROMPT

    rendered = COMPREHENSIVE_SYNTHESIS_PROMPT.format(
        query="What is FastAPI?",
        sources="[1] FastAPI docs ...",
    )
    assert "What is FastAPI?" in rendered
    assert "[1] FastAPI docs ..." in rendered
    # And the guide came along at module load
    assert "Anthropic released Claude Opus 4.7" in rendered


# ---------------------------------------------------------------------------
# Item 4 — REST /research REJECT and PARTIAL-zero-good early returns
# ---------------------------------------------------------------------------


class _FakeSearchResult:
    """Minimal SearchResult shape consumed by routes.py /research path."""

    def __init__(self, n: int):
        self.id = f"id-{n}"
        self.title = f"Source {n}"
        self.url = f"https://example.com/{n}"
        self.content = f"content {n}"
        self.score = 0.5
        self.connector = "test"


def _build_research_test_app():
    """Build a FastAPI app with the routes module attached, matching the
    pattern used by the existing /synthesize/p1 REJECT tests."""
    from fastapi import FastAPI
    from src.api import routes

    app = FastAPI()
    app.include_router(routes.router, prefix="/api/v1")
    return app, routes


def _build_mock_aggregator(sources):
    """Mock SearchAggregator: non-empty connectors, returns the provided sources."""
    fake_agg = MagicMock()
    fake_agg.connectors = ["searxng"]  # truthy — bypasses 503 short-circuit
    fake_agg.search = AsyncMock(return_value=(sources, {"searxng": []}))
    return fake_agg


def test_rest_research_reject_short_circuits_before_synthesis():
    """v0.2.2 item 4: /research with REJECT gate verdict must return a
    `Source quality insufficient` response without running synthesis. Before
    v0.2.2 this endpoint silently fell through, running synthesis over the
    same sources the gate had just rejected — the bug item 4 fixes."""
    from fastapi.testclient import TestClient

    app, routes = _build_research_test_app()
    client = TestClient(app)

    sources = [_FakeSearchResult(1), _FakeSearchResult(2)]
    fake_agg = _build_mock_aggregator(sources)

    rejected = PreGatheredSource(
        origin="searxng", url="https://example.com/1",
        title="Source 1", content="content 1", source_type="article",
    )
    fake_gate_result = QualityGateResult(
        decision=QualityDecision.REJECT,
        avg_quality=0.15,
        good_sources=[],
        rejected_sources=[rejected],
        source_scores=[0.15],
        suggestion="Sources are not relevant to the query.",
    )
    fake_gate = MagicMock()
    fake_gate.evaluate = AsyncMock(return_value=fake_gate_result)
    fake_gate.reject_threshold = 0.3
    fake_gate.pass_threshold = 0.5

    # Sentinel to verify synthesis was NOT invoked
    synth_called = MagicMock()

    with patch.object(routes, "_get_llm_client", return_value=MagicMock()), \
         patch.object(routes, "SearchAggregator", return_value=fake_agg), \
         patch.object(routes, "SourceQualityGate", return_value=fake_gate), \
         patch("src.synthesis.wrappers.SynthesisAggregator", side_effect=synth_called):
        response = client.post(
            "/api/v1/research",
            json={"query": "test", "top_k": 2, "preset": "comprehensive"},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert "Source quality insufficient" in body["content"]
    assert "Sources are not relevant to the query." in body["content"]
    assert body["citations"] == []
    assert body["contradictions"] == []
    # Quality gate reported in response payload for caller observability
    assert body["quality_gate"]["decision"] == "reject"
    # CRITICAL: synthesis MUST NOT have been invoked — the whole point of the
    # short-circuit is to avoid wasting tokens on gate-rejected sources.
    synth_called.assert_not_called()


def test_rest_research_partial_with_zero_good_fails_open():
    """Gate-demotion R2-C1 (supersedes the v0.2.2 short-circuit for this input):
    PARTIAL with empty good_sources but at least one source at/above the fail-open
    floor (default 0.3 = REJECT_THRESHOLD) no longer refuses. It FAILS OPEN —
    synthesizing over the weak (rejected) sources with a low-relevance caveat — and
    the result is marked non-cacheable.

    avg=0.42 ⇒ max(source_scores)=0.42 ≥ 0.3 ⇒ fail-open eligible. A PARTIAL's avg
    is by definition ≥ the REJECT floor, and max ≥ avg, so PARTIAL-zero-good ALWAYS
    fails open at the default floor; the old short-circuit branch is dead for this
    class. The below-floor refuse path is still covered by the REJECT test above
    (scores=[0.15]) and the boundary unit test in test_gate_fail_open.py."""
    from fastapi.testclient import TestClient

    app, routes = _build_research_test_app()
    client = TestClient(app)

    sources = [_FakeSearchResult(1)]
    fake_agg = _build_mock_aggregator(sources)

    rejected = PreGatheredSource(
        origin="searxng", url="https://example.com/1",
        title="Source 1", content="content 1", source_type="article",
    )
    fake_gate_result = QualityGateResult(
        decision=QualityDecision.PARTIAL,
        avg_quality=0.42,  # ≥ REJECT floor (0.3) by definition of PARTIAL
        good_sources=[],
        rejected_sources=[rejected],
        source_scores=[0.42],  # max ≥ 0.3 → fail-open eligible
        suggestion="Try more targeted queries.",
    )
    fake_gate = MagicMock()
    fake_gate.evaluate = AsyncMock(return_value=fake_gate_result)
    fake_gate.reject_threshold = 0.3
    fake_gate.pass_threshold = 0.5

    # Fail-open proceeds to synthesis: return a real AggregatedSynthesis so the
    # verifier/finalization pipeline runs (the MagicMock surrogate would trip the
    # unsupported-result-type guard / fail the await).
    from src.synthesis import AggregatedSynthesis, SynthesisStyle as _Style

    fake_synth_result = AggregatedSynthesis(
        content="Weakly-grounded answer [1].",
        citations=[
            {"number": 1, "id": "1", "source_id": None, "title": "Source 1",
             "url": "https://example.com/1", "origin": None, "source_type": None},
        ],
        source_attribution={},
        confidence=0.5,
        style_used=_Style.COMPREHENSIVE,
        word_count=3,
        llm_output=None,
    )
    fake_synth = MagicMock()
    fake_synth.synthesize = AsyncMock(return_value=fake_synth_result)

    with patch.object(routes, "_get_llm_client", return_value=MagicMock()), \
         patch.object(routes, "SearchAggregator", return_value=fake_agg), \
         patch.object(routes, "SourceQualityGate", return_value=fake_gate), \
         patch("src.synthesis.wrappers.SynthesisAggregator", return_value=fake_synth):
        response = client.post(
            "/api/v1/research",
            json={"query": "test", "top_k": 1, "preset": "comprehensive"},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    # Failed open — NOT a refusal.
    assert "Source quality insufficient" not in body["content"]
    assert "Weakly-grounded answer [1]." in body["content"]
    # Low-relevance caveat surfaced (R2-C1) in the REST-visible safe_content.
    assert "fail-open" in body["content"].lower()
    # Synthesis ran over the set-aside (rejected) sources — never-vaporize.
    fake_synth.synthesize.assert_awaited_once()
    assert fake_synth.synthesize.call_args.kwargs["sources"] == [rejected]
    # Gate decision preserved for caller observability.
    assert body["quality_gate"]["decision"] == "partial"


def test_rest_research_partial_with_good_sources_proceeds_to_synthesis():
    """v0.2.2 item 4 negative control: PARTIAL with at least one good source
    must still proceed to synthesis using only the good sources. The fix
    must not over-correct and short-circuit on every PARTIAL."""
    from fastapi.testclient import TestClient

    app, routes = _build_research_test_app()
    client = TestClient(app)

    sources = [_FakeSearchResult(1), _FakeSearchResult(2)]
    fake_agg = _build_mock_aggregator(sources)

    good = PreGatheredSource(
        origin="searxng", url="https://example.com/1",
        title="Good Source", content="cont", source_type="article",
    )
    rejected = PreGatheredSource(
        origin="searxng", url="https://example.com/2",
        title="Bad Source", content="cont", source_type="article",
    )
    fake_gate_result = QualityGateResult(
        decision=QualityDecision.PARTIAL,
        avg_quality=0.45,
        good_sources=[good],
        rejected_sources=[rejected],
        source_scores=[0.7, 0.2],
        suggestion="",
    )
    fake_gate = MagicMock()
    fake_gate.evaluate = AsyncMock(return_value=fake_gate_result)
    fake_gate.reject_threshold = 0.3
    fake_gate.pass_threshold = 0.5

    # Phase 0: finalize_synthesis isinstance-dispatches over
    # AggregatedSynthesis — return a real instance. The MagicMock surrogate
    # the pre-Phase-0 test used trips the unsupported-result-type guard.
    from src.synthesis import AggregatedSynthesis, SynthesisStyle as _Style

    fake_synth_result = AggregatedSynthesis(
        content="Synthesized answer [1].",
        citations=[
            {"number": 1, "id": "1", "source_id": None, "title": "Good Source",
             "url": "https://example.com/1", "origin": None, "source_type": None},
        ],
        source_attribution={},
        confidence=0.7,
        style_used=_Style.COMPREHENSIVE,
        word_count=2,
        llm_output=None,
    )
    fake_synth = MagicMock()
    fake_synth.synthesize = AsyncMock(return_value=fake_synth_result)

    with patch.object(routes, "_get_llm_client", return_value=MagicMock()), \
         patch.object(routes, "SearchAggregator", return_value=fake_agg), \
         patch.object(routes, "SourceQualityGate", return_value=fake_gate), \
         patch("src.synthesis.wrappers.SynthesisAggregator", return_value=fake_synth):
        # `comprehensive` (not `fast`) — `fast` has run_quality_gate=False
        # which would bypass the gate entirely and make the assertion below
        # impossible to validate.
        response = client.post(
            "/api/v1/research",
            json={"query": "test", "top_k": 2, "preset": "comprehensive"},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    # Synthesis ran (NOT the short-circuit response)
    assert "Source quality insufficient" not in body["content"]
    assert body["content"] == "Synthesized answer [1]."
    # And synthesis was called with only the good source from the gate
    fake_synth.synthesize.assert_called_once()
    call_sources = fake_synth.synthesize.call_args.kwargs["sources"]
    assert call_sources == [good]
