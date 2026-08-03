"""Brave Search connector.

Brave runs its own web index rather than syndicating Google or Bing, and it is
reached through an official keyed API. That matters operationally: unlike the
scraped engines behind a self-hosted SearXNG, an API lane cannot be served a
CAPTCHA or a bot-block page, so it stays available under sustained automated
load. See `companions/searxng/settings.yml.example` ("Durable lanes").

There is no official Python SDK, so this speaks HTTP directly.
"""

import hashlib
import logging
import httpx
from .base import Connector, SearchResult, Source
from ..config import settings

logger = logging.getLogger(__name__)

API_URL = "https://api.search.brave.com/res/v1/web/search"

# Brave's `count` parameter is rejected above 20.
MAX_COUNT = 20


class BraveConnector(Connector):
    """Brave Search API connector."""

    name = "brave"

    def __init__(
        self,
        api_key: str | None = None,
        country: str | None = None,
        safesearch: str | None = None,
    ):
        self.api_key = api_key or settings.brave_api_key
        self.country = country or settings.brave_country
        self.safesearch = safesearch or settings.brave_safesearch

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def _probe_url(self) -> str | None:
        # Reachability only; key validity would cost a billed call.
        return "https://api.search.brave.com"

    async def search(self, query: str, top_k: int = 10) -> SearchResult:
        """Execute a Brave web search."""
        if not self.is_configured():
            return SearchResult(sources=[], query=query, connector_name=self.name)

        params: dict[str, str] = {
            "q": query,
            "count": str(max(1, min(top_k, MAX_COUNT))),
        }
        if self.country:
            params["country"] = self.country
        if self.safesearch:
            params["safesearch"] = self.safesearch

        headers = {
            "Accept": "application/json",
            "X-Subscription-Token": self.api_key,
        }

        sources = []
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(API_URL, params=params, headers=headers)
                response.raise_for_status()
                data = response.json()

            # A query Brave answers only with videos/news carries no `web` key at
            # all, rather than an empty list — treat that as zero results, not an
            # error, so it fuses as a quiet non-contributor.
            results = (data.get("web") or {}).get("results", [])[:top_k]

            for idx, result in enumerate(results):
                url = result.get("url", "")
                source_id = f"br_{hashlib.md5(url.encode()).hexdigest()[:8]}"

                sources.append(Source(
                    id=source_id,
                    title=result.get("title", ""),
                    url=url,
                    content=result.get("description", ""),
                    score=1.0 / (idx + 1),  # Rank-based score
                    connector=self.name,
                    metadata={
                        "published_date": result.get("page_age"),
                        "age": result.get("age"),
                        "language": result.get("language"),
                    },
                ))

        except Exception as e:
            logger.warning("Brave search error: %s", e)

        return SearchResult(
            sources=sources,
            query=query,
            connector_name=self.name,
            total_results=len(sources),
        )
