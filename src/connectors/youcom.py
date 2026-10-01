"""You.com connector for AI-optimized web search.

You.com runs its own web index behind a keyed REST API (`ydc-index.io/v1/search`)
and returns clean, structured results (title, description, keyword snippets,
page_age) — a good RAG-side lane for the fusion pool. It is another keyed-API
lane like Tavily/LinkUp/Brave, so it cannot be served a bot-block page the way
SearXNG's scraped engines can. Optional like the others; absent key drops it
from fusion.

There is no required Python SDK for the search endpoint, so this speaks HTTP
directly with httpx (same shape as the Brave connector).
"""

import hashlib
import logging

import httpx

from .base import Connector, SearchResult, Source
from ..config import settings

logger = logging.getLogger(__name__)

API_URL = "https://ydc-index.io/v1/search"

# You.com's `count` parameter is documented as max 100; keep requests modest
# so a wide top_k doesn't pull 100-result pages into every fusion round.
MAX_COUNT = 20


class YouComConnector(Connector):
    """You.com Web Search API connector."""

    name = "youcom"

    def __init__(
        self,
        api_key: str | None = None,
        safesearch: str | None = None,
    ):
        self.api_key = api_key or settings.youcom_api_key
        self.safesearch = safesearch or settings.youcom_safesearch

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def _probe_url(self) -> str | None:
        # Reachability only; key validity would cost a billed call.
        return "https://ydc-index.io"

    async def search(self, query: str, top_k: int = 10) -> SearchResult:
        """Execute a You.com web search."""
        if not self.is_configured():
            return SearchResult(sources=[], query=query, connector_name=self.name)

        payload: dict[str, object] = {
            "query": query,
            "count": max(1, min(top_k, MAX_COUNT)),
            "safesearch": self.safesearch,
        }

        headers = {
            "Accept": "application/json",
            "X-API-Key": self.api_key,
        }

        sources = []
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(API_URL, json=payload, headers=headers)
                response.raise_for_status()
                data = response.json()

            # `results.web` carries the organic hits; `results.news` rides
            # alongside on news-intent queries. A response without either key
            # is zero results, not an error, so it fuses as a quiet
            # non-contributor.
            results = (data.get("results") or {}).get("web") or []
            results = results[:top_k]

            for idx, result in enumerate(results):
                url = result.get("url", "")
                source_id = f"yc_{hashlib.md5(url.encode()).hexdigest()[:8]}"

                # Prefer the clean `description` summary; fall back to the
                # first keyword snippet when You.com omits it.
                content = result.get("description", "") or ""
                if not content:
                    snippets = result.get("snippets") or []
                    content = snippets[0] if snippets else ""

                sources.append(Source(
                    id=source_id,
                    title=result.get("title", ""),
                    url=url,
                    content=content,
                    score=1.0 / (idx + 1),  # Rank-based score
                    connector=self.name,
                    metadata={
                        "published_date": result.get("page_age"),
                    },
                ))

        except Exception as e:
            logger.warning("You.com search error: %s", e)

        return SearchResult(
            sources=sources,
            query=query,
            connector_name=self.name,
            total_results=len(sources),
        )
