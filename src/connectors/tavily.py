"""Tavily connector for AI-optimized search."""

import hashlib
import logging

from .base import Connector, SearchResult, Source
from ..config import settings

logger = logging.getLogger(__name__)


class TavilyConnector(Connector):
    """Tavily AI search connector."""

    name = "tavily"

    def __init__(
        self,
        api_key: str | None = None,
        search_depth: str | None = None,
    ):
        self.api_key = api_key or settings.tavily_api_key
        self.search_depth = search_depth or settings.tavily_search_depth

    def is_configured(self) -> bool:
        return bool(self.api_key)

    async def search(self, query: str, top_k: int = 10) -> SearchResult:
        """Execute Tavily search."""
        if not self.is_configured():
            return SearchResult(sources=[], query=query, connector_name=self.name)

        sources = []
        try:
            from tavily import TavilyClient

            client = TavilyClient(api_key=self.api_key)
            response = client.search(
                query=query,
                search_depth=self.search_depth,
                max_results=top_k,
                include_raw_content=False,
            )

            results = response.get("results", [])

            for idx, result in enumerate(results):
                url = result.get("url", "")
                source_id = f"tv_{hashlib.md5(url.encode()).hexdigest()[:8]}"

                sources.append(Source(
                    id=source_id,
                    title=result.get("title", ""),
                    url=url,
                    content=result.get("content", ""),
                    score=result.get("score", 1.0 / (idx + 1)),
                    connector=self.name,
                    metadata={
                        "published_date": result.get("published_date"),
                    },
                ))

        except Exception as e:
            logger.warning("Tavily search error: %s", e)

        return SearchResult(
            sources=sources,
            query=query,
            connector_name=self.name,
            total_results=len(sources),
        )
