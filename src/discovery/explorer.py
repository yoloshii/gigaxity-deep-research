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
import re
from dataclasses import dataclass, field
from typing import Optional, TYPE_CHECKING

from ..connectors.base import Source
from ..config import settings
from ..llm_utils import ExtractionMode, call_with_extraction

if TYPE_CHECKING:
    from .routing import ConnectorRouter
    from .expansion import QueryExpander
    from .gap_filler import GapFiller


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


@dataclass
class DiscoveryResult:
    """Result of exploratory discovery."""
    query: str
    landscape: KnowledgeLandscape
    knowledge_gaps: list[KnowledgeGap]
    sources: list[ScoredSource]
    synthesis_preview: str  # Brief overview for context
    recommended_deep_dives: list[str]  # URLs worth fetching with Jina


# Prompts for LLM-assisted discovery
LANDSCAPE_EXPANSION_PROMPT = """Analyze this research query and map its knowledge landscape.

Query: {query}

Identify:
1. EXPLICIT TOPICS: Concepts directly mentioned in the query
2. IMPLICIT TOPICS: Concepts implied but not stated (what does the user assume?)
3. RELATED CONCEPTS: Adjacent topics that would enrich understanding
4. CONTRASTING VIEWS: Alternative perspectives or approaches

Format your response as:
EXPLICIT: topic1, topic2, topic3
IMPLICIT: topic1, topic2, topic3
RELATED: topic1, topic2, topic3
CONTRASTING: view1, view2, view3"""

KNOWLEDGE_GAP_PROMPT = """Given this query and the sources found, identify knowledge gaps.

Query: {query}

Source titles and snippets:
{sources}

What important aspects of this topic are NOT well covered by these sources?
What nuances might the user be missing?
What follow-up questions would a domain expert ask?

List 3-5 knowledge gaps, ranked by importance:

Format:
GAP: [gap name]
DESCRIPTION: [why this matters]
IMPORTANCE: [high/medium/low]
SEARCH: [suggested query to fill this gap]
---"""

SOURCE_SCORING_PROMPT = """Score these sources against the identified knowledge gaps.

Query: {query}

Knowledge gaps to address:
{gaps}

Sources:
{sources}

For each source, identify:
1. Which gaps does it address?
2. What unique value does it provide vs other sources?
3. Priority for deep-dive (1=essential, 2=valuable, 3=optional)

Format per source:
URL: [url]
GAPS_ADDRESSED: gap1, gap2
UNIQUE_VALUE: [what this offers that others don't]
PRIORITY: [1/2/3]
---"""


class Explorer:
    """
    Exploratory discovery engine.

    Optimized for the specific role in exploratory workflows:
    - Set the table for Jina/Exa/Ref deep dives
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
        # Step 0: Query expansion (P0 Enhancement)
        expanded_queries = []
        if self.expander and expand_searches:
            expanded = await self.expander.expand(query, num_variants=3)
            expanded_queries = expanded.variants

        # Step 1: Expand the knowledge landscape
        landscape = await self._expand_landscape(query)

        # Step 2: Run searches (original + expanded + variants)
        sources = await self._gather_sources(
            query, landscape, top_k, expand_searches, expanded_queries
        )

        # Step 3: Identify knowledge gaps
        gaps = await self._identify_gaps(query, sources)

        # Step 4: Iterative gap-filling (P0 Enhancement)
        if fill_gaps and self.gap_filler and gaps:
            fill_result = await self.gap_filler.fill(
                query=query,
                initial_sources=sources,
                gaps=gaps,
                max_iterations=1,  # Single iteration for speed
            )
            # Merge gap-filling sources
            seen_urls = {s.url for s in sources}
            for source in fill_result.new_sources:
                if source.url not in seen_urls:
                    sources.append(source)
                    seen_urls.add(source.url)
            # Update gaps with remaining unfilled ones
            gaps = [g for g in gaps if g.gap not in fill_result.gaps_filled]

        # Step 5: Score sources against gaps
        scored_sources = await self._score_sources(query, sources, gaps)

        # Step 6: Generate synthesis preview
        preview = await self._generate_preview(query, scored_sources[:5])

        # Step 7: Recommend deep dives
        deep_dives = [
            s.source.url for s in scored_sources
            if s.recommended_priority <= 2
        ][:7]  # Top 7 for Jina parallel_read

        return DiscoveryResult(
            query=query,
            landscape=landscape,
            knowledge_gaps=gaps,
            sources=scored_sources,
            synthesis_preview=preview,
            recommended_deep_dives=deep_dives,
        )

    async def _expand_landscape(self, query: str) -> KnowledgeLandscape:
        """Expand the knowledge landscape around the query."""
        prompt = LANDSCAPE_EXPANSION_PROMPT.format(query=query)

        response = await self._call_llm(prompt, max_tokens=500)

        return self._parse_landscape(response)

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
    ) -> list[KnowledgeGap]:
        """Identify knowledge gaps not covered by sources."""
        source_text = "\n".join([
            f"- {s.title}: {s.content[:200] if s.content else 'No snippet'}..."
            for s in sources[:10]
        ])

        prompt = KNOWLEDGE_GAP_PROMPT.format(
            query=query,
            sources=source_text,
        )

        response = await self._call_llm(prompt, max_tokens=800)

        return self._parse_gaps(response)

    async def _score_sources(
        self,
        query: str,
        sources: list[Source],
        gaps: list[KnowledgeGap],
    ) -> list[ScoredSource]:
        """Score sources against knowledge gaps."""
        if not sources:
            return []

        gaps_text = "\n".join([
            f"- {g.gap}: {g.description} (importance: {g.importance})"
            for g in gaps
        ])

        sources_text = "\n".join([
            f"URL: {s.url}\nTitle: {s.title}\nSnippet: {s.content[:200] if s.content else 'N/A'}...\n---"
            for s in sources[:15]
        ])

        prompt = SOURCE_SCORING_PROMPT.format(
            query=query,
            gaps=gaps_text,
            sources=sources_text,
        )

        response = await self._call_llm(prompt, max_tokens=1500)

        scored = self._parse_scored_sources(response, sources, gaps)

        # Sort by priority then relevance
        scored.sort(key=lambda x: (x.recommended_priority, -x.relevance_score))

        return scored

    async def _generate_preview(
        self,
        query: str,
        top_sources: list[ScoredSource],
    ) -> str:
        """Generate a brief synthesis preview."""
        if not top_sources:
            return "No sources found for synthesis preview."

        source_context = "\n".join([
            f"[{i+1}] {s.source.title}: {s.source.content[:300] if s.source.content else 'N/A'}"
            for i, s in enumerate(top_sources)
        ])

        prompt = f"""Based on these top sources, provide a 2-3 sentence overview that answers or frames the query. This is a preview, not a full synthesis.

Query: {query}

Sources:
{source_context}

Brief overview:"""

        response = await self._call_llm(prompt, max_tokens=200)

        return response.strip()

    def _parse_landscape(self, response: str) -> KnowledgeLandscape:
        """Parse landscape expansion response."""
        explicit = []
        implicit = []
        related = []
        contrasting = []

        for line in response.split('\n'):
            line = line.strip()
            if line.startswith('EXPLICIT:'):
                explicit = [t.strip() for t in line[9:].split(',') if t.strip()]
            elif line.startswith('IMPLICIT:'):
                implicit = [t.strip() for t in line[9:].split(',') if t.strip()]
            elif line.startswith('RELATED:'):
                related = [t.strip() for t in line[8:].split(',') if t.strip()]
            elif line.startswith('CONTRASTING:'):
                contrasting = [t.strip() for t in line[12:].split(',') if t.strip()]

        return KnowledgeLandscape(
            explicit_topics=explicit or ["topic extraction failed"],
            implicit_topics=implicit,
            related_concepts=related,
            contrasting_views=contrasting,
        )

    def _parse_gaps(self, response: str) -> list[KnowledgeGap]:
        """Parse knowledge gaps response."""
        gaps = []
        current_gap = {}

        for line in response.split('\n'):
            line = line.strip()
            if line.startswith('GAP:'):
                if current_gap.get('gap'):
                    gaps.append(KnowledgeGap(**current_gap))
                current_gap = {'gap': line[4:].strip()}
            elif line.startswith('DESCRIPTION:'):
                current_gap['description'] = line[12:].strip()
            elif line.startswith('IMPORTANCE:'):
                current_gap['importance'] = line[11:].strip().lower()
            elif line.startswith('SEARCH:'):
                current_gap['suggested_search'] = line[7:].strip()
            elif line == '---':
                if current_gap.get('gap'):
                    gaps.append(KnowledgeGap(
                        gap=current_gap.get('gap', ''),
                        description=current_gap.get('description', ''),
                        importance=current_gap.get('importance', 'medium'),
                        suggested_search=current_gap.get('suggested_search'),
                    ))
                current_gap = {}

        # Don't forget last gap
        if current_gap.get('gap'):
            gaps.append(KnowledgeGap(
                gap=current_gap.get('gap', ''),
                description=current_gap.get('description', ''),
                importance=current_gap.get('importance', 'medium'),
                suggested_search=current_gap.get('suggested_search'),
            ))

        return gaps

    def _parse_scored_sources(
        self,
        response: str,
        sources: list[Source],
        gaps: list[KnowledgeGap],
    ) -> list[ScoredSource]:
        """Parse source scoring response."""
        source_map = {s.url: s for s in sources}
        gap_names = [g.gap.lower() for g in gaps]
        scored = []

        current = {}
        for line in response.split('\n'):
            line = line.strip()
            if line.startswith('URL:'):
                if current.get('url') and current['url'] in source_map:
                    scored.append(self._build_scored_source(current, source_map, gap_names))
                current = {'url': line[4:].strip()}
            elif line.startswith('GAPS_ADDRESSED:'):
                current['gaps'] = [g.strip() for g in line[15:].split(',') if g.strip()]
            elif line.startswith('UNIQUE_VALUE:'):
                current['unique'] = line[13:].strip()
            elif line.startswith('PRIORITY:'):
                try:
                    current['priority'] = int(line[9:].strip()[0])
                except (ValueError, IndexError):
                    current['priority'] = 2
            elif line == '---':
                if current.get('url') and current['url'] in source_map:
                    scored.append(self._build_scored_source(current, source_map, gap_names))
                current = {}

        # Last entry
        if current.get('url') and current['url'] in source_map:
            scored.append(self._build_scored_source(current, source_map, gap_names))

        # Add any sources not scored by LLM with default scores
        scored_urls = {s.source.url for s in scored}
        for source in sources:
            if source.url not in scored_urls:
                scored.append(ScoredSource(
                    source=source,
                    relevance_score=source.score,
                    gaps_addressed=[],
                    unique_value="Not analyzed",
                    recommended_priority=3,
                ))

        return scored

    def _build_scored_source(
        self,
        parsed: dict,
        source_map: dict,
        gap_names: list[str],
    ) -> ScoredSource:
        """Build a ScoredSource from parsed data."""
        source = source_map[parsed['url']]
        gaps_addressed = parsed.get('gaps', [])
        priority = parsed.get('priority', 2)

        # Compute relevance score based on gaps addressed and priority
        gap_coverage = len([g for g in gaps_addressed if g.lower() in gap_names])
        relevance = source.score * (1 + 0.1 * gap_coverage) * (4 - priority) / 3

        return ScoredSource(
            source=source,
            relevance_score=min(relevance, 1.0),
            gaps_addressed=gaps_addressed,
            unique_value=parsed.get('unique', 'No unique value identified'),
            recommended_priority=priority,
        )

    async def _call_llm(self, prompt: str, max_tokens: int = 500) -> str:
        """Call LLM with prompt (LENIENT extraction - discovery uses raw text)."""
        output = await call_with_extraction(
            self.llm_client,
            self.model,
            [{"role": "user", "content": prompt}],
            max_tokens,
            ExtractionMode.LENIENT,
            temperature=0.7,
        )
        return output.text
