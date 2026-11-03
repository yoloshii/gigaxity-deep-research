"""Keenable Search connector.

Keenable is reached through an official keyed API (POST /v1/search with an
``X-API-Key`` header). Like Brave, an API lane cannot be served a CAPTCHA or a
bot-block page, so it stays available under sustained automated load.

There is no official Python SDK, so this speaks HTTP directly.
"""

import hashlib
import logging
import httpx
from .base import Connector, SearchResult, Source
from ..config import settings

logger = logging.getLogger(__name__)

API_URL = "https://api.keenable.ai/v1/search"

MAX_RESULTS = 20
# Fallback page excerpts can be long; the short description is preferred.
SNIPPET_MAX_CHARS = 500


class KeenableConnector(Connector):
    """Keenable Search API connector."""

    name = "keenable"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or settings.keenable_api_key

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def _probe_url(self) -> str | None:
        # Reachability only; key validity would cost a billed call.
        return "https://api.keenable.ai"

    async def search(self, query: str, top_k: int = 10) -> SearchResult:
        """Execute a Keenable web search."""
        if not self.is_configured():
            return SearchResult(sources=[], query=query, connector_name=self.name)

        payload = {"query": query, "max_results": max(1, min(top_k, MAX_RESULTS))}
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-API-Key": self.api_key,
            "X-Keenable-Title": "Gigaxity Deep Research",
        }

        sources = []
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(API_URL, json=payload, headers=headers)
                response.raise_for_status()
                data = response.json()

            results = [r for r in (data.get("results") or []) if r.get("url")][:top_k]

            for idx, result in enumerate(results):
                url = result["url"]
                source_id = f"kn_{hashlib.md5(url.encode()).hexdigest()[:8]}"
                content = result.get("description") or (result.get("snippet") or "")[
                    :SNIPPET_MAX_CHARS
                ]

                sources.append(Source(
                    id=source_id,
                    title=result.get("title", ""),
                    url=url,
                    content=content,
                    score=1.0 / (idx + 1),  # Rank-based score
                    connector=self.name,
                    metadata={},
                ))

        except Exception as e:
            logger.warning("Keenable search error: %s", e)

        return SearchResult(
            sources=sources,
            query=query,
            connector_name=self.name,
            total_results=len(sources),
        )
