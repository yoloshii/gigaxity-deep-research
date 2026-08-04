"""Regression test: Explorer.discover() must survive a non-empty gap-fill round.

Explorer.discover() used to read `fill_result.new_sources` and
`fill_result.gaps_filled` — neither exists on GapFillingResult (its fields are
`merged_sources` / `gaps_addressed`), so any /discover call that reached
gap-filling with a non-empty gap list raised AttributeError before scoring or
preview. The crash was masked whenever the gap parse returned an empty list,
because the `if fill_gaps and self.gap_filler and gaps:` guard short-circuits
on empty gaps.

These tests drive a valid gap response through the real GapFiller so the
gap-filling branch actually executes.
"""

from types import SimpleNamespace

import pytest

from src.connectors.base import Source
from src.discovery.explorer import Explorer
from src.discovery.gap_filler import GapFiller


def _llm_response(text: str) -> SimpleNamespace:
    """OpenAI-shaped chat completion response with plain content."""
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=text),
                finish_reason="stop",
            )
        ]
    )


LANDSCAPE_TEXT = (
    "EXPLICIT: async programming\n"
    "IMPLICIT: event loops\n"
    "RELATED: concurrency\n"
    "CONTRASTING: threading"
)

# Parses into one high-importance gap WITH a suggested search, so
# GapFiller.fill() has a priority gap to act on.
GAPS_TEXT = (
    "GAP: temporal coverage\n"
    "DESCRIPTION: recent developments are missing\n"
    "IMPORTANCE: high\n"
    "SEARCH: python async 2026 updates\n"
    "---"
)

SCORING_TEXT = (
    "URL: https://example.com/original\n"
    "GAPS_ADDRESSED: temporal coverage\n"
    "UNIQUE_VALUE: baseline coverage\n"
    "PRIORITY: 1\n"
    "---"
)

PREVIEW_TEXT = "Async programming coverage now spans fundamentals and 2026 updates."


class SequencedLLMClient:
    """chat.completions.create returns canned responses in call order."""

    def __init__(self, texts: list[str]):
        self._texts = list(texts)
        self.calls: list[dict] = []
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self._create)
        )

    async def _create(self, **kwargs):
        self.calls.append(kwargs)
        if not self._texts:
            raise AssertionError("LLM mock exhausted — unexpected extra call")
        return _llm_response(self._texts.pop(0))


class RecordingAggregator:
    """search() returns the initial source first, then the gap source."""

    def __init__(self, initial: list[Source], gap: list[Source]):
        self._initial = initial
        self._gap = gap
        self.queries: list[str] = []

    async def search(self, query: str, top_k: int = 10, **kwargs):
        self.queries.append(query)
        if len(self.queries) == 1:
            return (self._initial, {})
        return (self._gap, {})


@pytest.fixture
def initial_source() -> Source:
    return Source(
        id="sx_orig",
        title="Original async guide",
        url="https://example.com/original",
        content="Covers async fundamentals.",
        score=0.8,
    )


@pytest.fixture
def gap_source() -> Source:
    return Source(
        id="sx_gap",
        title="2026 async updates",
        url="https://example.com/gap-fill",
        content="Covers the 2026 developments.",
        score=0.7,
    )


@pytest.mark.unit
async def test_discover_survives_gap_filling_with_nonempty_gaps(
    initial_source, gap_source
):
    """A /discover that reaches gap-filling with gaps must not raise.

    Regression: fill_result.new_sources / fill_result.gaps_filled do not
    exist on GapFillingResult; the merge must use merged_sources and the
    gap filter must use gaps_addressed.
    """
    llm = SequencedLLMClient(
        [LANDSCAPE_TEXT, GAPS_TEXT, SCORING_TEXT, PREVIEW_TEXT]
    )
    aggregator = RecordingAggregator([initial_source], [gap_source])
    explorer = Explorer(
        llm_client=llm,
        search_aggregator=aggregator,
        model="test-model",
        gap_filler=GapFiller(aggregator),
    )

    result = await explorer.discover(
        "python async programming",
        expand_searches=False,
        fill_gaps=True,
    )

    # The gap search actually ran (guard did not short-circuit).
    assert aggregator.queries == [
        "python async programming",
        "python async 2026 updates",
    ]

    # Gap-filling sources merged in — originals retained, gap source added.
    urls = {s.source.url for s in result.sources}
    assert "https://example.com/original" in urls
    assert "https://example.com/gap-fill" in urls

    # The addressed gap was removed from the remaining-gaps list.
    assert all(g.gap != "temporal coverage" for g in result.knowledge_gaps)

    # Pipeline completed through scoring and preview.
    assert result.synthesis_preview == PREVIEW_TEXT


@pytest.mark.unit
async def test_discover_gap_source_not_duplicated_when_already_present(
    initial_source,
):
    """A gap search returning an already-known URL must not duplicate it."""
    llm = SequencedLLMClient(
        [LANDSCAPE_TEXT, GAPS_TEXT, SCORING_TEXT, PREVIEW_TEXT]
    )
    aggregator = RecordingAggregator([initial_source], [initial_source])
    explorer = Explorer(
        llm_client=llm,
        search_aggregator=aggregator,
        model="test-model",
        gap_filler=GapFiller(aggregator),
    )

    result = await explorer.discover(
        "python async programming",
        expand_searches=False,
        fill_gaps=True,
    )

    urls = [s.source.url for s in result.sources]
    assert urls.count("https://example.com/original") == 1
