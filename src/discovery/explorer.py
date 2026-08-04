"""
Exploratory Discovery Module

Drives the EXPLORATORY workflow: take a cold-start query and return a knowledge
landscape (explicit, implicit, related, contrasting topics) plus a ranked source
set scored against detected knowledge gaps.

Key differentiator from basic search:
1. BREADTH EXPANSION - Surface related concepts the user didn't ask about
2. KNOWLEDGE GAP IDENTIFICATION - What nuances exist that query doesn't cover
3. URL-TO-GAP MAPPING - Score URLs by which knowledge gaps they address

This is about mapping the knowledge space around a query, not just
finding relevant documents.

Enhanced with P0 Cold-Start features:
- Query Expansion (HyDE-style variant generation)
- Adaptive Connector Routing (query type → optimal connectors)
- Iterative Gap-Filling (auto-search for detected gaps)
"""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import NamedTuple, Optional, TYPE_CHECKING

from ..connectors.base import Source
from ..config import settings
from ..degradation import (
    StageDegradation,
    degradation_from_output,
    transport_degradation,
)
from ..llm_utils import (
    ExtractionMode,
    LLMOutput,
    call_with_extraction,
    derive_effective_budget,
)

if TYPE_CHECKING:
    from .routing import ConnectorRouter
    from .expansion import QueryExpander
    from .gap_filler import GapFiller

logger = logging.getLogger(__name__)


@dataclass
class KnowledgeGap:
    """A knowledge gap identified in the query."""
    gap: str
    description: str
    importance: str  # high, medium, low
    suggested_search: Optional[str] = None  # Query to fill this gap


@dataclass
class KnowledgeLandscape:
    """The expanded knowledge space around a query."""
    explicit_topics: list[str]  # Topics directly mentioned
    implicit_topics: list[str]  # Topics implied but not stated
    related_concepts: list[str]  # Adjacent concepts worth exploring
    contrasting_views: list[str]  # Alternative perspectives


@dataclass
class ScoredSource:
    """A source scored against knowledge gaps."""
    source: Source
    relevance_score: float
    gaps_addressed: list[str]
    unique_value: str  # What this source offers that others don't
    recommended_priority: int  # 1 = fetch first, 2 = fetch if time, 3 = optional
    # Appended with a backward-compatible default (ScoredSource is publicly
    # exported): "llm_scored" = validated model record; "retrieval_fallback" =
    # raw retrieval score, no model judgement behind it.
    scoring_status: str = "llm_scored"


@dataclass
class DiscoveryResult:
    """Result of exploratory discovery."""
    query: str
    landscape: KnowledgeLandscape
    knowledge_gaps: list[KnowledgeGap]
    sources: list[ScoredSource]
    synthesis_preview: str  # Brief overview for context
    recommended_deep_dives: list[str]  # URLs worth fetching with Jina
    degradations: list["StageDegradation"] = field(default_factory=list)


# Prompts for LLM-assisted discovery
LANDSCAPE_EXPANSION_PROMPT = """Analyze this research query and map its knowledge landscape.

Query: {query}

Identify:
1. EXPLICIT TOPICS: Concepts directly mentioned in the query
2. IMPLICIT TOPICS: Concepts implied but not stated (what does the user assume?)
3. RELATED CONCEPTS: Adjacent topics that would enrich understanding
4. CONTRASTING VIEWS: Alternative perspectives or approaches

Format your response as exactly four records, one per line:
EXPLICIT: topic1, topic2, topic3
IMPLICIT: topic1, topic2, topic3
RELATED: topic1, topic2, topic3
CONTRASTING: view1, view2, view3

Output only these four records, each exactly once. EXPLICIT must list at
least one topic. For an optional category with nothing to list, write the
single word NONE as its value (e.g. "CONTRASTING: NONE")."""

KNOWLEDGE_GAP_PROMPT = """Given this query and the sources found, identify knowledge gaps.

Query: {query}

Source titles and snippets:
{sources}

What important aspects of this topic are NOT well covered by these sources?
What nuances might the user be missing?
What follow-up questions would a domain expert ask?

List 3-5 knowledge gaps, ranked by importance. Format each gap as a complete
block of exactly these four records, blocks separated by a line containing
only "---":
GAP: [gap name]
DESCRIPTION: [why this matters]
IMPORTANCE: [high/medium/low]
SEARCH: [suggested query to fill this gap]
---

Output only gap blocks. If the sources cover the topic with no meaningful
gaps, respond with exactly:
NO_GAPS"""

SOURCE_SCORING_PROMPT = """Score these sources against the identified knowledge gaps.

Query: {query}

Knowledge gaps to address (numbered):
{gaps}

Sources (numbered):
{sources}

For each source, identify:
1. Which numbered gaps does it address?
2. What unique value does it provide vs other sources?
3. Priority for deep-dive (1=essential, 2=valuable, 3=optional)

Output one complete block per source, blocks separated by a line containing
only "---", each block exactly:
SOURCE_INDEX: [source number]
GAPS_ADDRESSED: [comma-separated gap numbers, or NONE]
UNIQUE_VALUE: [what this offers that others don't]
PRIORITY: [1/2/3]
---

Cover every source number exactly once. Output only these blocks."""


# Stage grammars (lenient-parsed-callsites design, rev 7). Structural, not
# punctuation heuristics: every non-empty line must belong to a complete
# record or block, and unknown lines reject the whole response — a
# chain-of-thought trace must never partially "parse". Payload bounds may be
# tuned against a fixture corpus; the grammars themselves are fixed.

# The scoring prompt shows the model at most this many sources; the parser
# requires exact coverage OF THE TARGETS, keyed by injected stable index —
# "one record per input source" is unsatisfiable above this bound.
SCORING_LIMIT = 15

_LANDSCAPE_KEYS = ("EXPLICIT", "IMPLICIT", "RELATED", "CONTRASTING")


def parse_landscape_records(text: str) -> Optional[KnowledgeLandscape]:
    """Parse the four landscape records; None = grammar rejected.

    Exactly one record per category, no unknown records. EXPLICIT must carry
    at least one real topic; the optional categories may carry the exact
    token NONE for a legitimately-empty list.
    """
    raw = (text or "").strip()
    if not raw:
        return None
    values: dict[str, str] = {}
    for ln in raw.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        for key in _LANDSCAPE_KEYS:
            if ln.startswith(f"{key}:"):
                if key in values:
                    return None
                values[key] = ln[len(key) + 1:].strip()
                break
        else:
            return None
    if set(values) != set(_LANDSCAPE_KEYS):
        return None

    def topics(payload: str, optional: bool) -> Optional[list[str]]:
        if payload == "NONE":
            return [] if optional else None
        items = [t.strip() for t in payload.split(",") if t.strip()]
        return items if items else None

    explicit = topics(values["EXPLICIT"], optional=False)
    implicit = topics(values["IMPLICIT"], optional=True)
    related = topics(values["RELATED"], optional=True)
    contrasting = topics(values["CONTRASTING"], optional=True)
    if explicit is None or implicit is None or related is None or contrasting is None:
        return None
    return KnowledgeLandscape(
        explicit_topics=explicit,
        implicit_topics=implicit,
        related_concepts=related,
        contrasting_views=contrasting,
    )


_GAP_FIELDS = ("GAP", "DESCRIPTION", "IMPORTANCE", "SEARCH")
GAP_MAX_RECORDS = 5
_GAP_IMPORTANCE = {"high", "medium", "low"}


def split_record_blocks(raw: str) -> Optional[list[dict[str, str]]]:
    """Split ---separated field blocks; None on any structural violation.

    Every non-empty line must be a `FIELD: payload` record; a field may
    appear at most once per block; a trailing separator is tolerated.
    Which fields are known/required is the caller's check.
    """
    blocks: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for ln in raw.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        if ln == "---":
            if current:
                blocks.append(current)
                current = {}
            continue
        if ":" not in ln:
            return None
        key, _, payload = ln.partition(":")
        key = key.strip()
        if key in current:
            return None
        current[key] = payload.strip()
    if current:
        blocks.append(current)
    return blocks


def parse_gap_records(text: str) -> Optional[list[KnowledgeGap]]:
    """Parse gap blocks; [] = clean NO_GAPS verdict, None = rejected.

    NO_GAPS is matched against the whole normalized response — before this
    sentinel existed there was no valid "no gaps" representation at all, so
    an empty parse was indistinguishable from a failed one. Otherwise 1 to
    GAP_MAX_RECORDS complete blocks (the prompt's 3-5 is aspirational;
    requiring it would fail closed on valid output), IMPORTANCE constrained,
    SEARCH required.
    """
    raw = (text or "").strip()
    if not raw:
        return None
    if raw.strip('"\'').rstrip('.').strip().upper() == "NO_GAPS":
        return []
    blocks = split_record_blocks(raw)
    if not blocks or len(blocks) > GAP_MAX_RECORDS:
        return None
    gaps: list[KnowledgeGap] = []
    for block in blocks:
        if set(block) != set(_GAP_FIELDS):
            return None
        importance = block["IMPORTANCE"].lower()
        if (
            not block["GAP"]
            or not block["DESCRIPTION"]
            or not block["SEARCH"]
            or importance not in _GAP_IMPORTANCE
        ):
            return None
        gaps.append(
            KnowledgeGap(
                gap=block["GAP"],
                description=block["DESCRIPTION"],
                importance=importance,
                suggested_search=block["SEARCH"],
            )
        )
    return gaps


class ParsedSourceScore(NamedTuple):
    """One validated scoring record, keyed by injected source index."""
    gap_indices: list[int]
    unique_value: str
    priority: int


_SCORING_FIELDS = ("SOURCE_INDEX", "GAPS_ADDRESSED", "UNIQUE_VALUE", "PRIORITY")


def parse_scoring_records(
    text: str,
    num_targets: int,
    num_gaps: int,
) -> Optional[dict[int, ParsedSourceScore]]:
    """Parse scoring blocks; None = grammar rejected.

    Requires an exact duplicate-free permutation of target indices 1..N —
    keyed by the injected stable index, never by the model echoing a URL
    byte-for-byte. GAPS_ADDRESSED is NONE or valid duplicate-free gap
    indices; UNIQUE_VALUE non-empty; PRIORITY in {1, 2, 3}.
    """
    raw = (text or "").strip()
    if not raw:
        return None
    blocks = split_record_blocks(raw)
    if not blocks:
        return None
    records: dict[int, ParsedSourceScore] = {}
    for block in blocks:
        if set(block) != set(_SCORING_FIELDS):
            return None
        try:
            index = int(block["SOURCE_INDEX"])
        except ValueError:
            return None
        if not (1 <= index <= num_targets) or index in records:
            return None
        gaps_payload = block["GAPS_ADDRESSED"]
        if gaps_payload == "NONE":
            gap_indices: list[int] = []
        else:
            try:
                gap_indices = [int(tok.strip()) for tok in gaps_payload.split(",") if tok.strip()]
            except ValueError:
                return None
            if not gap_indices:
                return None
            if len(set(gap_indices)) != len(gap_indices):
                return None
            if any(not (1 <= g <= num_gaps) for g in gap_indices):
                return None
        if not block["UNIQUE_VALUE"]:
            return None
        if block["PRIORITY"] not in {"1", "2", "3"}:
            return None
        records[index] = ParsedSourceScore(
            gap_indices=gap_indices,
            unique_value=block["UNIQUE_VALUE"],
            priority=int(block["PRIORITY"]),
        )
    if len(records) != num_targets:
        return None
    return records


class Explorer:
    """
    Exploratory discovery engine.

    Optimized for the specific role in exploratory workflows:
    - Set the table for Jina/Exa/Context7 deep dives
    - Expand breadth beyond the literal query
    - Identify what the user doesn't know to ask
    - Score URLs by gap coverage, not just relevance

    Enhanced with P0 Cold-Start features when components provided:
    - Query expansion for semantic breadth
    - Adaptive routing to optimal connectors
    - Iterative gap-filling for coverage
    """

    def __init__(
        self,
        llm_client,
        search_aggregator,
        model: str = None,
        router: Optional["ConnectorRouter"] = None,
        expander: Optional["QueryExpander"] = None,
        gap_filler: Optional["GapFiller"] = None,
    ):
        """
        Initialize the explorer.

        Args:
            llm_client: OpenAI-compatible LLM client
            search_aggregator: SearchAggregator instance for fetching sources
            model: Model name for LLM calls
            router: Optional ConnectorRouter for adaptive routing
            expander: Optional QueryExpander for query expansion
            gap_filler: Optional GapFiller for iterative gap-filling
        """
        self.llm_client = llm_client
        self.search_aggregator = search_aggregator
        self.model = model or settings.llm_model

        # P0 Enhancement components (optional)
        self.router = router
        self.expander = expander
        self.gap_filler = gap_filler

    async def discover(
        self,
        query: str,
        top_k: int = 15,
        expand_searches: bool = True,
        fill_gaps: bool = True,
    ) -> DiscoveryResult:
        """
        Perform exploratory discovery.

        Args:
            query: The research query
            top_k: Number of sources to return
            expand_searches: Whether to run expanded searches for breadth
            fill_gaps: Whether to auto-search for high-priority gaps

        Returns:
            DiscoveryResult with landscape, gaps, and scored sources
        """
        degradations: list[StageDegradation] = []

        # Step 0: Query expansion (P0 Enhancement)
        expanded_queries = []
        if self.expander and expand_searches:
            expanded = await self.expander.expand(query, num_variants=3)
            # ExpandedQuery.variants includes the original first (public
            # contract, test-asserted). _gather_sources seeds the original
            # itself, so select only case-fold-distinct NON-original
            # variants here — copying the full list ran the original twice
            # and discarded a generated variant.
            seen = {query.strip().casefold()}
            for variant in expanded.variants:
                folded = variant.strip().casefold()
                if folded in seen:
                    continue
                seen.add(folded)
                expanded_queries.append(variant)
            degradations.extend(expanded.degradations)

        # Step 1: Expand the knowledge landscape
        landscape, landscape_deg = await self._expand_landscape(query)
        if landscape_deg:
            degradations.append(landscape_deg)

        # Step 2: Run searches (original + expanded + variants)
        sources = await self._gather_sources(
            query, landscape, top_k, expand_searches, expanded_queries
        )

        # Step 3: Identify knowledge gaps
        gaps, gaps_deg = await self._identify_gaps(query, sources)
        if gaps_deg:
            degradations.append(gaps_deg)

        # Step 4: Iterative gap-filling (P0 Enhancement)
        if fill_gaps and self.gap_filler and gaps:
            fill_result = await self.gap_filler.fill(
                query=query,
                initial_sources=sources,
                gaps=gaps,
                max_iterations=1,  # Single iteration for speed
            )
            # Merge gap-filling sources (merged_sources is already URL-deduped,
            # originals first — GapFillingResult has no new_sources/gaps_filled)
            sources = fill_result.merged_sources
            # Update gaps with remaining unfilled ones
            gaps = [g for g in gaps if g.gap not in fill_result.gaps_addressed]

        # Step 5: Score sources against gaps
        scored_sources, scoring_deg = await self._score_sources(query, sources, gaps)
        if scoring_deg:
            degradations.append(scoring_deg)

        # Step 6: Generate synthesis preview
        preview, preview_deg = await self._generate_preview(query, scored_sources[:5])
        if preview_deg:
            degradations.append(preview_deg)

        # Step 7: Recommend deep dives. On a complete target parse, only
        # llm_scored priority-1/2 sources qualify — a fallback-scored source
        # has no validated gap coverage, so promoting it would assert a
        # judgement the model never made (slots are NOT topped up). On a
        # rejected parse, the first seven retrieval-ranked sources.
        if scoring_deg is None:
            deep_dives = [
                s.source.url for s in scored_sources
                if s.scoring_status == "llm_scored" and s.recommended_priority <= 2
            ][:7]  # Top 7 for Jina parallel_read
        else:
            deep_dives = [s.source.url for s in scored_sources[:7]]

        return DiscoveryResult(
            query=query,
            landscape=landscape,
            knowledge_gaps=gaps,
            sources=scored_sources,
            synthesis_preview=preview,
            recommended_deep_dives=deep_dives,
            degradations=degradations,
        )

    async def _expand_landscape(
        self, query: str
    ) -> tuple[KnowledgeLandscape, Optional[StageDegradation]]:
        """Expand the knowledge landscape around the query.

        On grammar rejection: preserve only the original query as explicit
        context and leave the expansion categories empty — downstream search
        expansion simply doesn't widen, instead of widening on garbage.
        """
        prompt = LANDSCAPE_EXPANSION_PROMPT.format(query=query)

        # Reasoning-aware budget: the Q2 instrumentation harness
        # (scripts/instrument_stage_budgets.py, 2026-08-04, glm-5.2) showed
        # 100% truncation at a flat 500 — the whole budget went to
        # chain-of-thought. Base unchanged; non-reasoning models unaffected.
        output = await self._call_llm(
            prompt,
            max_tokens=derive_effective_budget(500, self.model),
            mode=ExtractionMode.PARSE_REQUIRED,
        )
        landscape = parse_landscape_records(output.text)
        if landscape is None:
            return (
                KnowledgeLandscape(
                    explicit_topics=[query],
                    implicit_topics=[],
                    related_concepts=[],
                    contrasting_views=[],
                ),
                degradation_from_output(
                    "landscape",
                    output,
                    parse_failed=True,
                    fallback_used=True,
                    message="landscape output unusable; original query kept as explicit context",
                ),
            )
        return landscape, None

    async def _gather_sources(
        self,
        query: str,
        landscape: KnowledgeLandscape,
        top_k: int,
        expand_searches: bool,
        expanded_queries: list[str] = None,
    ) -> list[Source]:
        """Gather sources from multiple search angles."""
        searches = [query]  # Always include original

        # Add HyDE-style expanded queries (P0 Enhancement)
        if expanded_queries:
            searches.extend(expanded_queries[:3])

        if expand_searches:
            # Add searches for implicit topics
            for topic in landscape.implicit_topics[:2]:
                searches.append(f"{query} {topic}")

            # Add searches for related concepts
            for concept in landscape.related_concepts[:2]:
                searches.append(f"{concept} {landscape.explicit_topics[0] if landscape.explicit_topics else query}")

        # Adaptive connector routing (P0 Enhancement)
        connector_weights = None
        if self.router:
            routing = await self.router.route(query)
            # Build weights from primary/secondary connectors
            connector_weights = {c: 1.0 for c in routing.primary_connectors}
            connector_weights.update({c: 0.5 for c in routing.secondary_connectors})

        # Run searches in parallel
        all_sources = []
        per_search_k = max(5, top_k // len(searches) + 3)

        tasks = [
            self.search_aggregator.search(
                q,
                top_k=per_search_k,
                connector_weights=connector_weights,
            )
            for q in searches
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, tuple):
                sources, _ = result
                all_sources.extend(sources)

        # Deduplicate by URL
        seen_urls = set()
        unique_sources = []
        for source in all_sources:
            if source.url not in seen_urls:
                seen_urls.add(source.url)
                unique_sources.append(source)

        return unique_sources[:top_k]

    async def _identify_gaps(
        self,
        query: str,
        sources: list[Source],
    ) -> tuple[list[KnowledgeGap], Optional[StageDegradation]]:
        """Identify knowledge gaps not covered by sources.

        A clean NO_GAPS verdict is [] with no degradation; a rejected parse
        is [] WITH a degradation — the two must stay distinguishable."""
        source_text = "\n".join([
            f"- {s.title}: {s.content[:200] if s.content else 'No snippet'}..."
            for s in sources[:10]
        ])

        prompt = KNOWLEDGE_GAP_PROMPT.format(
            query=query,
            sources=source_text,
        )

        # Reasoning-aware budget (Q2 harness 2026-08-04: 100% truncation at
        # a flat 800 on glm-5.2). Base unchanged.
        output = await self._call_llm(
            prompt,
            max_tokens=derive_effective_budget(800, self.model),
            mode=ExtractionMode.PARSE_REQUIRED,
        )
        gaps = parse_gap_records(output.text)
        if gaps is None:
            return [], degradation_from_output(
                "gaps",
                output,
                parse_failed=True,
                fallback_used=True,
                message="gap output unusable; continuing without gap analysis",
            )
        return gaps, None

    async def _score_sources(
        self,
        query: str,
        sources: list[Source],
        gaps: list[KnowledgeGap],
    ) -> tuple[list[ScoredSource], Optional[StageDegradation]]:
        """Score sources against knowledge gaps.

        Only the first SCORING_LIMIT sources are scoring targets — the model
        never sees the rest, so "one record per input source" is
        unsatisfiable above the limit. Two outcomes:

        - complete target parse: targets are llm_scored and sorted by
          (priority, -relevance, original rank); non-targets append in
          retrieval order as retrieval_fallback/priority 3. NO degradation —
          bounded scoring is intentional policy operating normally.
        - rejected target parse: every source keeps retrieval order and its
          retrieval relevance, priority 2 for the first seven and 3 after;
          one degradation.

        Relevance stays computed deterministically from retrieval score,
        validated gap coverage and priority — never parsed from the model.
        """
        if not sources:
            return [], None

        targets = sources[:SCORING_LIMIT]

        gaps_text = "\n".join([
            f"{i}. {g.gap}: {g.description} (importance: {g.importance})"
            for i, g in enumerate(gaps, start=1)
        ]) or "(no gaps identified)"

        sources_text = "\n".join([
            f"SOURCE {i}:\nTitle: {s.title}\nURL: {s.url}\n"
            f"Snippet: {s.content[:200] if s.content else 'N/A'}...\n---"
            for i, s in enumerate(targets, start=1)
        ])

        prompt = SOURCE_SCORING_PROMPT.format(
            query=query,
            gaps=gaps_text,
            sources=sources_text,
        )

        # Reasoning-aware budget (Q2 harness 2026-08-04: 33% truncation at a
        # flat 1500 on glm-5.2). Base unchanged.
        output = await self._call_llm(
            prompt,
            max_tokens=derive_effective_budget(1500, self.model),
            mode=ExtractionMode.PARSE_REQUIRED,
        )
        records = parse_scoring_records(
            output.text, num_targets=len(targets), num_gaps=len(gaps)
        )

        if records is None:
            scored = [
                ScoredSource(
                    source=s,
                    relevance_score=s.score,
                    gaps_addressed=[],
                    unique_value="Not analyzed",
                    recommended_priority=2 if i < 7 else 3,
                    scoring_status="retrieval_fallback",
                )
                for i, s in enumerate(sources)
            ]
            return scored, degradation_from_output(
                "source_scoring",
                output,
                parse_failed=True,
                fallback_used=True,
                message="source scoring output unusable; retrieval-order fallback",
            )

        scored_targets = []
        for i, s in enumerate(targets, start=1):
            record = records[i]
            gaps_addressed = [gaps[g - 1].gap for g in record.gap_indices]
            # Deterministic relevance — same formula as before this repair:
            # retrieval score x validated gap coverage x priority weight.
            relevance = (
                s.score
                * (1 + 0.1 * len(gaps_addressed))
                * (4 - record.priority) / 3
            )
            scored_targets.append(ScoredSource(
                source=s,
                relevance_score=min(relevance, 1.0),
                gaps_addressed=gaps_addressed,
                unique_value=record.unique_value,
                recommended_priority=record.priority,
                scoring_status="llm_scored",
            ))

        # Stable sort: original rank is the implicit tiebreak.
        scored_targets.sort(
            key=lambda x: (x.recommended_priority, -x.relevance_score)
        )

        non_targets = [
            ScoredSource(
                source=s,
                relevance_score=s.score,
                gaps_addressed=[],
                unique_value="Not analyzed",
                recommended_priority=3,
                scoring_status="retrieval_fallback",
            )
            for s in sources[SCORING_LIMIT:]
        ]

        return scored_targets + non_targets, None

    async def _generate_preview(
        self,
        query: str,
        top_sources: list[ScoredSource],
    ) -> tuple[str, Optional[StageDegradation]]:
        """Generate a brief synthesis preview.

        The preview is user-facing text, never parsed: FINAL_ANSWER with the
        ceiling retry disabled — a truncated 200-token overview must fall
        back to "preview unavailable", not escalate to a full-ceiling
        completion. Deliberate deviation from the fail-hard boundary of the
        structured stages: the preview is optional, so a transport error is
        caught and reported unavailable instead of failing the request.
        """
        if not top_sources:
            return "No sources found for synthesis preview.", None

        source_context = "\n".join([
            f"[{i+1}] {s.source.title}: {s.source.content[:300] if s.source.content else 'N/A'}"
            for i, s in enumerate(top_sources)
        ])

        prompt = f"""Based on these top sources, provide a 2-3 sentence overview that answers or frames the query. This is a preview, not a full synthesis.

Query: {query}

Sources:
{source_context}

Brief overview:"""

        try:
            # Reasoning-aware INITIAL budget (Q2 harness 2026-08-04: 100%
            # truncation at a flat 200 on glm-5.2 — which would also make
            # every discovery non-cacheable via the preview degradation).
            # The ceiling retry stays disabled: a truncated preview reports
            # unavailable rather than escalating.
            output = await self._call_llm(
                prompt,
                max_tokens=derive_effective_budget(200, self.model),
                mode=ExtractionMode.FINAL_ANSWER,
                retry_on_truncation=False,
            )
        except Exception:
            logger.warning("preview LLM call failed", exc_info=True)
            return "Synthesis preview unavailable.", transport_degradation(
                "preview", "preview call failed; preview unavailable"
            )

        if output.truncated or not output.text.strip():
            return "Synthesis preview unavailable.", degradation_from_output(
                "preview",
                output,
                parse_failed=False,
                fallback_used=True,
                message="preview output unusable; preview unavailable",
            )
        return output.text.strip(), None

    async def _call_llm(
        self,
        prompt: str,
        *,
        max_tokens: int,
        mode: ExtractionMode,
        retry_on_truncation: bool = True,
    ) -> LLMOutput:
        """Raw forwarder: every operation boundary states its own extraction
        mode and budget explicitly, and gets the full LLMOutput back — blank
        text alone cannot distinguish truncated from reasoning-only from
        empty, so returning str here would make degradations unclassifiable.
        """
        return await call_with_extraction(
            self.llm_client,
            self.model,
            [{"role": "user", "content": prompt}],
            max_tokens,
            mode,
            temperature=0.7,
            retry_on_truncation=retry_on_truncation,
        )
