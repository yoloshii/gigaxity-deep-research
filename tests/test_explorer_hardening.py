"""Explorer structured-stage hardening (lenient-parsed-callsites, rev 7).

Covers the three structured consumers (landscape, gaps, source scoring) plus
the unparsed user-facing preview:

- grammar units: valid, wrong-shape, boundary counts, duplicate/invalid
  references, the NO_GAPS sentinel;
- per-stage failure paths through discover(): mocked reasoning-only choice,
  mocked truncated-but-non-empty choice (PARSE_REQUIRED blanks content with
  reasoning_only=False — a distinct path), mocked plausible-but-wrong-shape
  content — asserting the explicit failure state and deterministic fallback,
  not merely that text was blanked;
- the locked ordering policy for both scoring outcomes, the
  no-slot-filling deep-dive rule, and the preview no-retry contract.
"""

from types import SimpleNamespace

import pytest

from src.connectors.base import Source
from src.degradation import DegradationCode
from src.discovery.explorer import (
    SCORING_LIMIT,
    Explorer,
    KnowledgeGap,
    parse_gap_records,
    parse_landscape_records,
    parse_scoring_records,
)


# ---------------------------------------------------------------------------
# Grammar units
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLandscapeGrammar:
    def test_valid_four_records(self):
        text = (
            "EXPLICIT: quantum memory, qubits\n"
            "IMPLICIT: decoherence\n"
            "RELATED: error correction\n"
            "CONTRASTING: classical RAM"
        )
        ls = parse_landscape_records(text)
        assert ls.explicit_topics == ["quantum memory", "qubits"]
        assert ls.contrasting_views == ["classical RAM"]

    def test_none_token_for_optional_category(self):
        text = (
            "EXPLICIT: quantum memory\n"
            "IMPLICIT: NONE\n"
            "RELATED: error correction\n"
            "CONTRASTING: NONE"
        )
        ls = parse_landscape_records(text)
        assert ls.implicit_topics == []
        assert ls.contrasting_views == []

    def test_none_not_allowed_for_explicit(self):
        text = (
            "EXPLICIT: NONE\n"
            "IMPLICIT: NONE\n"
            "RELATED: NONE\n"
            "CONTRASTING: NONE"
        )
        assert parse_landscape_records(text) is None

    def test_missing_category_rejected(self):
        text = "EXPLICIT: a\nIMPLICIT: b\nRELATED: c"
        assert parse_landscape_records(text) is None

    def test_duplicate_category_rejected(self):
        text = (
            "EXPLICIT: a\nEXPLICIT: b\nIMPLICIT: c\n"
            "RELATED: d\nCONTRASTING: e"
        )
        assert parse_landscape_records(text) is None

    def test_unknown_record_rejected(self):
        text = (
            "Here is the landscape:\n"
            "EXPLICIT: a\nIMPLICIT: b\nRELATED: c\nCONTRASTING: d"
        )
        assert parse_landscape_records(text) is None

    def test_blank_rejected(self):
        assert parse_landscape_records("") is None


@pytest.mark.unit
class TestGapGrammar:
    VALID_BLOCK = (
        "GAP: temporal coverage\n"
        "DESCRIPTION: recent developments missing\n"
        "IMPORTANCE: high\n"
        "SEARCH: topic 2026 updates\n"
        "---"
    )

    def test_valid_single_block(self):
        gaps = parse_gap_records(self.VALID_BLOCK)
        assert len(gaps) == 1
        assert gaps[0].gap == "temporal coverage"
        assert gaps[0].importance == "high"
        assert gaps[0].suggested_search == "topic 2026 updates"

    def test_no_gaps_sentinel(self):
        assert parse_gap_records("NO_GAPS") == []
        assert parse_gap_records('"NO_GAPS."') == []

    def test_one_block_accepted_below_prompt_aspiration(self):
        """The prompt asks for 3-5; the grammar accepts 1-5 — requiring the
        aspiration would fail closed on valid output."""
        assert parse_gap_records(self.VALID_BLOCK) is not None

    def test_six_blocks_rejected(self):
        blocks = []
        for i in range(6):
            blocks.append(
                f"GAP: gap {i}\nDESCRIPTION: d\nIMPORTANCE: low\nSEARCH: s {i}\n---"
            )
        assert parse_gap_records("\n".join(blocks)) is None

    def test_missing_search_field_rejected(self):
        text = "GAP: g\nDESCRIPTION: d\nIMPORTANCE: high\n---"
        assert parse_gap_records(text) is None

    def test_invalid_importance_rejected(self):
        text = "GAP: g\nDESCRIPTION: d\nIMPORTANCE: critical\nSEARCH: s\n---"
        assert parse_gap_records(text) is None

    def test_prose_line_rejected(self):
        text = "The main gaps are\n" + self.VALID_BLOCK
        assert parse_gap_records(text) is None

    def test_blank_rejected(self):
        assert parse_gap_records("") is None


def _score_block(index: int, gaps: str = "NONE", priority: int = 2) -> str:
    return (
        f"SOURCE_INDEX: {index}\n"
        f"GAPS_ADDRESSED: {gaps}\n"
        f"UNIQUE_VALUE: value {index}\n"
        f"PRIORITY: {priority}\n"
        "---"
    )


@pytest.mark.unit
class TestScoringGrammar:
    def test_valid_complete_coverage(self):
        text = "\n".join([_score_block(1, "1", 1), _score_block(2)])
        records = parse_scoring_records(text, num_targets=2, num_gaps=1)
        assert set(records) == {1, 2}
        assert records[1].gap_indices == [1]
        assert records[1].priority == 1
        assert records[2].gap_indices == []

    def test_incomplete_coverage_rejected(self):
        text = _score_block(1)
        assert parse_scoring_records(text, num_targets=2, num_gaps=0) is None

    def test_duplicate_index_rejected(self):
        text = "\n".join([_score_block(1), _score_block(1)])
        assert parse_scoring_records(text, num_targets=2, num_gaps=0) is None

    def test_out_of_range_index_rejected(self):
        text = "\n".join([_score_block(1), _score_block(3)])
        assert parse_scoring_records(text, num_targets=2, num_gaps=0) is None

    def test_invalid_gap_reference_rejected(self):
        text = _score_block(1, gaps="2")
        assert parse_scoring_records(text, num_targets=1, num_gaps=1) is None

    def test_duplicate_gap_reference_rejected(self):
        text = _score_block(1, gaps="1, 1")
        assert parse_scoring_records(text, num_targets=1, num_gaps=1) is None

    def test_url_keyed_records_rejected(self):
        """The old URL-echo keying is not a valid record shape."""
        text = (
            "URL: https://example.com/a\n"
            "GAPS_ADDRESSED: NONE\n"
            "UNIQUE_VALUE: v\n"
            "PRIORITY: 1\n---"
        )
        assert parse_scoring_records(text, num_targets=1, num_gaps=0) is None

    def test_invalid_priority_rejected(self):
        text = _score_block(1, priority=4)
        assert parse_scoring_records(text, num_targets=1, num_gaps=0) is None

    def test_blank_rejected(self):
        assert parse_scoring_records("", num_targets=1, num_gaps=0) is None


# ---------------------------------------------------------------------------
# discover() harness
# ---------------------------------------------------------------------------


VALID_LANDSCAPE = (
    "EXPLICIT: async programming\n"
    "IMPLICIT: NONE\n"
    "RELATED: NONE\n"
    "CONTRASTING: NONE"
)

VALID_GAPS = (
    "GAP: temporal coverage\n"
    "DESCRIPTION: recent developments missing\n"
    "IMPORTANCE: low\n"
    "SEARCH: async 2026\n"
    "---"
)

PREVIEW_TEXT = "A concise overview of the topic."


def _choice(content=None, reasoning_content=None, finish_reason="stop"):
    return SimpleNamespace(
        message=SimpleNamespace(
            content=content, reasoning_content=reasoning_content
        ),
        finish_reason=finish_reason,
    )


class StageRoutingClient:
    """Dispatch canned choices by discovery stage; records calls per stage."""

    def __init__(self, **overrides):
        self.overrides = overrides
        self.calls_by_stage: dict[str, list[dict]] = {}
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self._create)
        )

    @staticmethod
    def _stage_of(prompt: str) -> str:
        if "knowledge landscape" in prompt:
            return "landscape"
        if "identify knowledge gaps" in prompt:
            return "gaps"
        if "Score these sources" in prompt:
            return "scoring"
        return "preview"

    def _default_choice(self, stage: str, prompt: str):
        if stage == "landscape":
            return _choice(content=VALID_LANDSCAPE)
        if stage == "gaps":
            return _choice(content="NO_GAPS")
        if stage == "scoring":
            return _choice(content=self._scoring_text(prompt))
        return _choice(content=PREVIEW_TEXT)

    @staticmethod
    def _scoring_text(prompt: str) -> str:
        n = prompt.count("\nSOURCE ")
        blocks = [_score_block(i, priority=1) for i in range(1, n + 1)]
        return "\n".join(blocks)

    async def _create(self, **kwargs):
        prompt = kwargs["messages"][0]["content"]
        stage = self._stage_of(prompt)
        self.calls_by_stage.setdefault(stage, []).append(kwargs)
        choice = self.overrides.get(stage) or self._default_choice(stage, prompt)
        return SimpleNamespace(choices=[choice])


def _sources(n: int) -> list[Source]:
    return [
        Source(
            id=f"sx_{i}",
            title=f"Source {i}",
            url=f"https://example.com/{i}",
            content=f"Content {i}",
            score=0.9 - i * 0.01,
        )
        for i in range(n)
    ]


class StaticAggregator:
    def __init__(self, sources: list[Source]):
        self._sources = sources

    async def search(self, query: str, top_k: int = 10, **kwargs):
        return (self._sources[:top_k], {})


def _explorer(client, sources, **kwargs) -> Explorer:
    return Explorer(
        llm_client=client,
        search_aggregator=StaticAggregator(sources),
        model="test-model",
        **kwargs,
    )


async def _discover(client, n_sources=3, top_k=None):
    explorer = _explorer(client, _sources(n_sources))
    return await explorer.discover(
        "async programming",
        top_k=top_k or n_sources,
        expand_searches=False,
        fill_gaps=False,
    )


@pytest.mark.unit
class TestLandscapeStage:
    async def test_reasoning_only_falls_back_to_query_only_landscape(self):
        client = StageRoutingClient(
            landscape=_choice(reasoning_content="thinking...", finish_reason="length")
        )
        result = await _discover(client)
        assert result.landscape.explicit_topics == ["async programming"]
        assert result.landscape.implicit_topics == []
        deg = next(d for d in result.degradations if d.stage == "landscape")
        assert deg.code is DegradationCode.TRUNCATED
        assert deg.reasoning_only is True
        assert deg.parse_failed is True

    async def test_wrong_shape_is_malformed(self):
        client = StageRoutingClient(
            landscape=_choice(content="The landscape covers many topics.")
        )
        result = await _discover(client)
        deg = next(d for d in result.degradations if d.stage == "landscape")
        assert deg.code is DegradationCode.MALFORMED

    async def test_valid_landscape_no_degradation(self):
        result = await _discover(StageRoutingClient())
        assert all(d.stage != "landscape" for d in result.degradations)


@pytest.mark.unit
class TestGapsStage:
    async def test_no_gaps_sentinel_is_clean_empty(self):
        result = await _discover(StageRoutingClient())
        assert result.knowledge_gaps == []
        assert all(d.stage != "gaps" for d in result.degradations)

    async def test_truncated_nonempty_gap_content_falls_back(self):
        """Content present but finish_reason=length: PARSE_REQUIRED blanks
        it with reasoning_only=False — must classify truncated, not empty."""
        client = StageRoutingClient(
            gaps=_choice(content="GAP: partial...", finish_reason="length")
        )
        result = await _discover(client)
        assert result.knowledge_gaps == []
        deg = next(d for d in result.degradations if d.stage == "gaps")
        assert deg.code is DegradationCode.TRUNCATED
        assert deg.reasoning_only is False
        assert deg.fallback_used is True

    async def test_valid_gaps_parse(self):
        client = StageRoutingClient(gaps=_choice(content=VALID_GAPS))
        result = await _discover(client)
        assert [g.gap for g in result.knowledge_gaps] == ["temporal coverage"]
        assert all(d.stage != "gaps" for d in result.degradations)


@pytest.mark.unit
class TestScoringStage:
    async def test_complete_parse_scores_targets_no_degradation(self):
        result = await _discover(StageRoutingClient())
        assert all(s.scoring_status == "llm_scored" for s in result.sources)
        assert all(d.stage != "source_scoring" for d in result.degradations)

    async def test_rejected_parse_retrieval_fallback_ordering(self):
        """Rejected outcome: retrieval order preserved, retrieval relevance,
        priority 2 for the first seven and 3 after, one degradation."""
        client = StageRoutingClient(scoring=_choice(content="garbage output"))
        result = await _discover(client, n_sources=9)
        assert [s.source.id for s in result.sources] == [
            f"sx_{i}" for i in range(9)
        ]
        assert all(s.scoring_status == "retrieval_fallback" for s in result.sources)
        assert [s.recommended_priority for s in result.sources] == [2] * 7 + [3] * 2
        assert [s.relevance_score for s in result.sources] == [
            s.source.score for s in result.sources
        ]
        degs = [d for d in result.degradations if d.stage == "source_scoring"]
        assert len(degs) == 1
        assert degs[0].code is DegradationCode.MALFORMED
        # Rejected parse: deep dives are the first seven retrieval-ranked.
        assert result.recommended_deep_dives == [
            f"https://example.com/{i}" for i in range(7)
        ]

    async def test_reasoning_only_scoring_classified(self):
        client = StageRoutingClient(
            scoring=_choice(reasoning_content="let me think about each source...")
        )
        result = await _discover(client)
        deg = next(d for d in result.degradations if d.stage == "source_scoring")
        assert deg.code is DegradationCode.REASONING_ONLY

    async def test_beyond_limit_sources_are_fallback_without_degradation(self):
        """Complete target parse with sources beyond SCORING_LIMIT: bounded
        scoring is intentional policy — non-targets are retrieval_fallback
        priority 3, appended in retrieval order, and NO degradation is
        recorded (the result stays cache-eligible)."""
        n = SCORING_LIMIT + 3
        result = await _discover(StageRoutingClient(), n_sources=n, top_k=n)
        targets = [s for s in result.sources if s.scoring_status == "llm_scored"]
        fallbacks = [
            s for s in result.sources if s.scoring_status == "retrieval_fallback"
        ]
        assert len(targets) == SCORING_LIMIT
        assert len(fallbacks) == 3
        assert all(s.recommended_priority == 3 for s in fallbacks)
        # Non-targets append AFTER the sorted targets, in retrieval order.
        assert [s.source.id for s in result.sources[-3:]] == [
            f"sx_{i}" for i in range(SCORING_LIMIT, n)
        ]
        assert all(d.stage != "source_scoring" for d in result.degradations)

    async def test_deep_dives_never_fill_from_fallback(self):
        """Complete parse: only llm_scored priority 1-2 sources qualify —
        fallback-scored sources must not top up the remaining slots."""
        n = SCORING_LIMIT + 3
        result = await _discover(StageRoutingClient(), n_sources=n, top_k=n)
        target_urls = {
            s.source.url
            for s in result.sources
            if s.scoring_status == "llm_scored" and s.recommended_priority <= 2
        }
        assert result.recommended_deep_dives
        assert set(result.recommended_deep_dives) <= target_urls

    async def test_targets_sorted_by_priority_then_relevance(self):
        """Locked ordering: (priority, -computed_relevance, original rank)."""

        class PriorityClient(StageRoutingClient):
            @staticmethod
            def _scoring_text(prompt: str) -> str:
                n = prompt.count("\nSOURCE ")
                priorities = {1: 3, 2: 1, 3: 2}
                return "\n".join(
                    _score_block(i, priority=priorities.get(i, 2))
                    for i in range(1, n + 1)
                )

        result = await _discover(PriorityClient(), n_sources=3)
        assert [s.recommended_priority for s in result.sources] == [1, 2, 3]
        assert [s.source.id for s in result.sources] == ["sx_1", "sx_2", "sx_0"]


@pytest.mark.unit
class TestPreviewStage:
    async def test_truncated_preview_falls_back_not_escalates(self):
        """A truncated preview reports unavailable; the FINAL_ANSWER ceiling
        retry is disabled for this call (exactly one preview call)."""
        client = StageRoutingClient(
            preview=_choice(content="Partial overv", finish_reason="length")
        )
        result = await _discover(client)
        assert result.synthesis_preview == "Synthesis preview unavailable."
        assert len(client.calls_by_stage["preview"]) == 1
        deg = next(d for d in result.degradations if d.stage == "preview")
        assert deg.code is DegradationCode.TRUNCATED
        assert deg.parse_failed is False  # the preview is never parsed
        assert deg.fallback_used is True

    async def test_reasoning_only_preview_falls_back(self):
        client = StageRoutingClient(
            preview=_choice(reasoning_content="drafting an overview...")
        )
        result = await _discover(client)
        assert result.synthesis_preview == "Synthesis preview unavailable."
        deg = next(d for d in result.degradations if d.stage == "preview")
        assert deg.code is DegradationCode.REASONING_ONLY

    async def test_preview_transport_error_caught_and_reported(self):
        """Deliberate deviation from the structured stages' fail-hard
        boundary: the optional preview catches transport errors."""

        class ExplodingPreviewClient(StageRoutingClient):
            async def _create(self, **kwargs):
                prompt = kwargs["messages"][0]["content"]
                if self._stage_of(prompt) == "preview":
                    raise RuntimeError("boom")
                return await super()._create(**kwargs)

        result = await _discover(ExplodingPreviewClient())
        assert result.synthesis_preview == "Synthesis preview unavailable."
        deg = next(d for d in result.degradations if d.stage == "preview")
        assert deg.code is DegradationCode.TRANSPORT_ERROR
        assert "boom" not in deg.message  # sanitized, never the raw exception

    async def test_structured_stage_transport_errors_still_fail_hard(self):
        """Explorer's structured stages keep their fail-hard boundary — no
        blanket catch was introduced."""

        class ExplodingScoringClient(StageRoutingClient):
            async def _create(self, **kwargs):
                prompt = kwargs["messages"][0]["content"]
                if self._stage_of(prompt) == "scoring":
                    raise RuntimeError("scoring transport down")
                return await super()._create(**kwargs)

        with pytest.raises(RuntimeError, match="scoring transport down"):
            await _discover(ExplodingScoringClient())

    async def test_clean_preview_no_degradation(self):
        result = await _discover(StageRoutingClient())
        assert result.synthesis_preview == PREVIEW_TEXT
        assert result.degradations == []


# ---------------------------------------------------------------------------
# Step-9 sweep: every discovery stage × every failure shape. The pipeline
# must complete on its deterministic fallback and classify the cause — the
# assertion is the explicit failure state, not merely that text was blanked.
# ---------------------------------------------------------------------------


_FAILURE_SHAPES = {
    "reasoning_only": (
        _choice(reasoning_content="chain of thought...", finish_reason="stop"),
        DegradationCode.REASONING_ONLY,
    ),
    "truncated_nonempty": (
        _choice(content="plausible but cut off mid-rec", finish_reason="length"),
        DegradationCode.TRUNCATED,
    ),
    "wrong_shape": (
        _choice(content="Prose that matches no record grammar at all."),
        DegradationCode.MALFORMED,
    ),
}


@pytest.mark.unit
@pytest.mark.parametrize("stage", ["landscape", "gaps", "scoring", "preview"])
@pytest.mark.parametrize("shape", list(_FAILURE_SHAPES))
async def test_stage_failure_shapes_classified_and_survived(stage, shape):
    choice, expected_code = _FAILURE_SHAPES[shape]
    if stage == "preview" and shape == "wrong_shape":
        # The preview is never parsed — prose is a VALID preview, not a
        # failure shape. Covered by test_clean_preview_no_degradation.
        pytest.skip("preview has no grammar; wrong-shape does not apply")
    result = await _discover(StageRoutingClient(**{stage: choice}))
    deg_stage = "source_scoring" if stage == "scoring" else stage
    deg = next(d for d in result.degradations if d.stage == deg_stage)
    assert deg.code is expected_code
    assert deg.fallback_used is True
    # Exactly one degradation — the other stages ran clean.
    assert len(result.degradations) == 1
