"""Search aggregator for parallel multi-source search."""

import asyncio
import logging

from ..connectors.base import Connector, Source, SearchResult
from ..connectors import SearXNGConnector, TavilyConnector, LinkUpConnector
from .fusion import rrf_fusion
from ..config import settings

logger = logging.getLogger(__name__)


class SearchAggregator:
    """Aggregates searches across multiple connectors with RRF fusion."""

    def __init__(
        self,
        connectors: list[Connector] | None = None,
        top_k: int | None = None,
    ):
        """
        Initialize aggregator with connectors.

        Args:
            connectors: List of connectors to use. If None, uses all configured.
            top_k: Default number of results per connector.
        """
        self.top_k = top_k or settings.default_top_k

        if connectors is not None:
            self.connectors = [c for c in connectors if c.is_configured()]
        else:
            # Default: use all configured connectors
            all_connectors = [
                SearXNGConnector(),
                TavilyConnector(),
                LinkUpConnector(),
            ]
            self.connectors = [c for c in all_connectors if c.is_configured()]

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

        # Execute searches in parallel
        tasks = [c.search(query, top_k) for c in active_connectors]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Collect valid results
        raw_results: dict[str, SearchResult] = {}
        source_lists: list[list[Source]] = []

        for result in results:
            if isinstance(result, Exception):
                logger.warning("Search error: %s", result)
                continue
            if isinstance(result, SearchResult) and result.sources:
                raw_results[result.connector_name] = result
                source_lists.append(result.sources)

        # Apply RRF fusion
        fused = rrf_fusion(source_lists, top_k=top_k * 2) if source_lists else []

        return fused, raw_results

    def get_active_connectors(self) -> list[str]:
        """Return names of active (configured) connectors."""
        return [c.name for c in self.connectors]
