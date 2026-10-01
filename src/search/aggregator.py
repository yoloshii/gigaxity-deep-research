"""Search aggregator for parallel multi-source search."""

import asyncio
import logging

from ..connectors.base import Connector, Source, SearchResult
from ..connectors.searxng import instance_categories
from ..connectors import (
    SearXNGConnector,
    TavilyConnector,
    LinkUpConnector,
    BraveConnector,
    ParallelConnector,
    YouComConnector,
)
from .fusion import rrf_fusion
from .verticals import resolve_vertical
from ..config import settings

logger = logging.getLogger(__name__)


def _with_holes(sources: list[Source], drop_urls: set[str]) -> list[Source | None]:
    """A lane's ranked list with a hole (None) wherever a result was suppressed.

    Each source sits at its position in the instance's answer
    (`metadata["position"]`, list order when absent). Results the connector
    dropped as off-category, and URLs in `drop_urls`, leave holes, so no later
    result is promoted into a better rank.
    """
    slots: dict[int, Source] = {}
    for index, source in enumerate(sources, start=1):
        position = source.metadata.get("position")
        if not isinstance(position, int) or position < 1:
            position = index
        if source.url not in drop_urls:
            slots.setdefault(position, source)
    if not slots:
        return []
    return [slots.get(position) for position in range(1, max(slots) + 1)]


class SearchAggregator:
    """Aggregates searches across multiple connectors with RRF fusion."""

    def __init__(
        self,
        connectors: list[Connector] | None = None,
        top_k: int | None = None,
        vertical: str | None = None,
    ):
        """
        Initialize aggregator with connectors.

        Args:
            connectors: List of connectors to use. If None, uses all configured.
            top_k: Default number of results per connector.
            vertical: SearXNG vertical lane policy for every search this
                aggregator runs — None (base lane only), "auto" (keyword
                heuristic per query), a focus mode name, or a vertical name.
                See `search/verticals.py`.
        """
        self.top_k = top_k or settings.default_top_k
        self.vertical = vertical

        if connectors is not None:
            self.connectors = [c for c in connectors if c.is_configured()]
        else:
            # Default: use all configured connectors
            all_connectors = [
                SearXNGConnector(),
                TavilyConnector(),
                LinkUpConnector(),
                BraveConnector(),
                YouComConnector(),
                ParallelConnector(),
            ]
            self.connectors = [c for c in all_connectors if c.is_configured()]

    def _lane_for(
        self, query: str, active_connectors: list[Connector]
    ) -> tuple[str, str] | None:
        """(host, vertical) of the SearXNG lane this search calls for, if any."""
        if not settings.searxng_vertical_routing:
            return None
        base = next((c for c in active_connectors if c.name == "searxng"), None)
        if base is None:
            return None
        vertical = resolve_vertical(query, self.vertical)
        if vertical is None:
            return None
        return base.host, vertical

    @staticmethod
    async def _search_lane(
        query: str, host: str, vertical: str, top_k: int
    ) -> SearchResult | None:
        """Search a second SearXNG connector on one category.

        Runs beside the other searches, so a slow or failing `/config` read
        delays this lane only. The lane runs only when `/config` shows an
        enabled engine in the category: an undefined category would be
        answered from the instance's defaults under the lane's name. It never
        inherits an `engines=` pin — SearXNG would add the pinned engines to
        the category.
        """
        available = await instance_categories(host)
        if available is None or vertical not in available:
            return None
        lane = SearXNGConnector(host=host, engines="", vertical=vertical)
        result = await lane.search(query, top_k)
        # The connector re-checks the category; only a confirmed one is a lane.
        return result if lane.last_category_status == "supported" else None

    async def search(
        self,
        query: str,
        top_k: int | None = None,
        connectors: list[str] | None = None,
        connector_weights: dict[str, float] | None = None,
    ) -> tuple[list[Source], dict[str, SearchResult]]:
        """
        Execute parallel search across connectors and fuse results.

        Args:
            query: Search query
            top_k: Number of results per connector
            connectors: Optional list of connector names to use
            connector_weights: Optional weights for connector result ranking

        Returns:
            Tuple of (fused sources, raw results by connector name)
        """
        top_k = top_k or self.top_k

        # Filter connectors if specified
        active_connectors = self.connectors
        if connectors:
            active_connectors = [
                c for c in self.connectors
                if c.name in connectors
            ]

        if not active_connectors:
            return [], {}

        # Execute searches in parallel; the vertical lane is one more task.
        tasks = [c.search(query, top_k) for c in active_connectors]
        lane = self._lane_for(query, active_connectors)
        if lane is not None:
            tasks.append(self._search_lane(query, *lane, top_k))
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Collect valid results
        raw_results: dict[str, SearchResult] = {}

        for result in results:
            if isinstance(result, Exception):
                logger.warning("Search error: %s", result)
                continue
            if isinstance(result, SearchResult) and result.sources:
                raw_results[result.connector_name] = result

        ranked_lists: dict[str, list[Source | None]] = {
            name: r.sources for name, r in raw_results.items()
        }
        lane_name = f"searxng:{lane[1]}" if lane is not None else None
        if lane_name in raw_results:
            # A lane is the same instance as the base lane, so a URL in both
            # is one SearXNG answer counted twice, not two providers agreeing.
            # It counts in the base list only.
            base = raw_results.get("searxng")
            base_urls = {s.url for s in base.sources} if base else set()
            lane_result = raw_results[lane_name]
            ranked = _with_holes(lane_result.sources, drop_urls=base_urls)
            survivors = [s for s in ranked if s is not None]
            if survivors:
                lane_result.sources = survivors
                lane_result.total_results = len(survivors)
                ranked_lists[lane_name] = ranked
            else:
                del raw_results[lane_name]
                del ranked_lists[lane_name]

        source_lists = list(ranked_lists.values())

        # Apply RRF fusion
        fused = rrf_fusion(source_lists, top_k=top_k * 2) if source_lists else []

        return fused, raw_results

    def get_active_connectors(self) -> list[str]:
        """Return names of active (configured) connectors."""
        return [c.name for c in self.connectors]
