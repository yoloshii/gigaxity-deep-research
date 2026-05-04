"""
Iterative Gap-Filling Search

Research basis: Multi-hop RAG (arXiv:2507.00355), Self-RAG patterns
- After initial search, identify gaps
- Automatically search for top N gaps
- Merge gap results with initial results
- Repeat if significant gaps remain (optional)

Key insight: One search iteration is rarely enough.
Gap-filling is what separates good research from great.
"""

import asyncio
from dataclasses import dataclass, field
from typing import Optional

from ..connectors.base import Source


@dataclass
class GapFillingResult:
    """Result of iterative gap-filling."""
    original_sources: list[Source]
    gap_sources: list[Source]
    merged_sources: list[Source]
    gaps_addressed: list[str]
    gaps_remaining: list[str]
    iterations: int


class GapFiller:
    """
    Automatically search for and fill knowledge gaps.

    Usage:
        filler = GapFiller(aggregator)
        result = await filler.fill(
            query="quantum memory systems",
            initial_sources=sources,
            gaps=detected_gaps,
            max_iterations=2
        )

        # result.merged_sources contains original + gap-filling sources
        # result.gaps_addressed shows what was found
        # result.gaps_remaining shows what's still missing
    """

    def __init__(
        self,
        search_aggregator,
        max_gap_searches: int = 3,
        sources_per_gap: int = 3,
    ):
        """
        Initialize gap filler.

        Args:
            search_aggregator: SearchAggregator instance for fetching sources
            max_gap_searches: Maximum number of gaps to fill per iteration
            sources_per_gap: Number of sources to fetch per gap query
        """
        self.aggregator = search_aggregator
        self.max_gap_searches = max_gap_searches
        self.sources_per_gap = sources_per_gap

    async def fill(
        self,
        query: str,
        initial_sources: list[Source],
        gaps: list,  # KnowledgeGap objects from explorer.py
        max_iterations: int = 1,
        min_gap_importance: str = "medium",  # high, medium, low
    ) -> GapFillingResult:
        """
        Fill knowledge gaps iteratively.

        Args:
            query: Original research query
            initial_sources: Sources from initial search
            gaps: Detected knowledge gaps (KnowledgeGap objects)
            max_iterations: Max gap-filling rounds (default 1)
            min_gap_importance: Only fill gaps at or above this importance

        Returns:
            GapFillingResult with merged sources and gap status
        """
        importance_order = {"high": 3, "medium": 2, "low": 1}
        min_importance_val = importance_order.get(min_gap_importance.lower(), 2)

        all_sources = list(initial_sources)
        gap_sources: list[Source] = []
        gaps_addressed: list[str] = []

        for iteration in range(max_iterations):
            # Filter to important gaps with suggested searches
            priority_gaps = [
                g for g in gaps
                if (importance_order.get(g.importance.lower(), 2) >= min_importance_val
                    and g.suggested_search
                    and g.gap not in gaps_addressed)
            ][:self.max_gap_searches]

            if not priority_gaps:
                break

            # Search for each gap in parallel
            tasks = [
                self.aggregator.search(gap.suggested_search, top_k=self.sources_per_gap)
                for gap in priority_gaps
            ]
            gap_results = await asyncio.gather(*tasks, return_exceptions=True)

            # Merge gap sources
            for gap, result in zip(priority_gaps, gap_results):
                if isinstance(result, Exception):
                    continue
                sources, _ = result
                if sources:
                    gap_sources.extend(sources)
                    gaps_addressed.append(gap.gap)

            # Dedupe and merge
            all_sources = self._dedupe_sources(all_sources + gap_sources)

        # Identify remaining gaps
        gaps_remaining = [
            g.gap for g in gaps
            if g.gap not in gaps_addressed
        ]

        return GapFillingResult(
            original_sources=initial_sources,
            gap_sources=gap_sources,
            merged_sources=all_sources,
            gaps_addressed=gaps_addressed,
            gaps_remaining=gaps_remaining,
            iterations=min(iteration + 1 if 'iteration' in dir() else 1, max_iterations),
        )

    async def fill_single_gap(
        self,
        gap_query: str,
        existing_sources: list[Source],
    ) -> list[Source]:
        """
        Fill a single gap and merge with existing sources.

        Args:
            gap_query: Query to search for this gap
            existing_sources: Sources to merge with

        Returns:
            Merged source list with duplicates removed
        """
        try:
            new_sources, _ = await self.aggregator.search(gap_query, top_k=self.sources_per_gap)
            return self._dedupe_sources(existing_sources + new_sources)
        except Exception:
            return existing_sources

    def _dedupe_sources(self, sources: list[Source]) -> list[Source]:
        """Remove duplicate sources by URL."""
        seen_urls = set()
        unique = []
        for source in sources:
            if source.url not in seen_urls:
                seen_urls.add(source.url)
                unique.append(source)
        return unique

    def prioritize_gaps(
        self,
        gaps: list,
        max_gaps: int = None,
    ) -> list:
        """
        Prioritize gaps by importance for filling.

        Args:
            gaps: List of KnowledgeGap objects
            max_gaps: Maximum number to return

        Returns:
            Sorted list of gaps by priority
        """
        importance_order = {"high": 3, "medium": 2, "low": 1}
        max_gaps = max_gaps or self.max_gap_searches

        # Sort by importance (high > medium > low)
        sorted_gaps = sorted(
            [g for g in gaps if g.suggested_search],
            key=lambda g: importance_order.get(g.importance.lower(), 2),
            reverse=True,
        )

        return sorted_gaps[:max_gaps]
