"""SearXNG connector for meta-search."""

import hashlib
import logging
import httpx
from .base import Connector, SearchResult, Source
from ..config import settings

logger = logging.getLogger(__name__)


class SearXNGConnector(Connector):
    """SearXNG meta-search connector."""

    name = "searxng"

    def __init__(
        self,
        host: str | None = None,
        engines: str | None = None,
        categories: str | None = None,
        language: str | None = None,
        safesearch: int | None = None,
    ):
        self.host = host or settings.searxng_host
        self.engines = engines or settings.searxng_engines
        self.categories = categories or settings.searxng_categories
        self.language = language or settings.searxng_language
        self.safesearch = safesearch if safesearch is not None else settings.searxng_safesearch

    def is_configured(self) -> bool:
        return bool(self.host)

    async def search(self, query: str, top_k: int = 10) -> SearchResult:
        """Execute SearXNG search."""
        if not self.is_configured():
            return SearchResult(sources=[], query=query, connector_name=self.name)

        params = {
            "q": query,
            "format": "json",
            "language": self.language,
        }

        if self.engines:
            params["engines"] = self.engines
        if self.categories:
            params["categories"] = self.categories
        if self.safesearch is not None:
            params["safesearch"] = str(self.safesearch)

        sources = []
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(f"{self.host}/search", params=params)
                response.raise_for_status()
                data = response.json()

            results = data.get("results", [])[:top_k]

            for idx, result in enumerate(results):
                url = result.get("url", "")
                source_id = f"sx_{hashlib.md5(url.encode()).hexdigest()[:8]}"

                sources.append(Source(
                    id=source_id,
                    title=result.get("title", ""),
                    url=url,
                    content=result.get("content", ""),
                    score=1.0 / (idx + 1),  # Rank-based score
                    connector=self.name,
                    metadata={
                        "engine": result.get("engine", ""),
                        "category": result.get("category", ""),
                        "parsed_url": result.get("parsed_url", []),
                    },
                ))

        except Exception as e:
            logger.warning("SearXNG search error: %s", e)

        return SearchResult(
            sources=sources,
            query=query,
            connector_name=self.name,
            total_results=len(sources),
        )
