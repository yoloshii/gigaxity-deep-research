"""Parallel Search connector.

Parallel runs its own web index behind an official keyed API (`/v1/search`)
and returns ranked URLs with LLM-oriented excerpts, so like Brave it cannot be
served a CAPTCHA or a bot-block page.

Two cost traps, both handled here (pricing as of 2026-09):
- The API defaults to `advanced` mode ($5 per 1,000 requests) when `mode` is
  omitted. This connector always sends the configured mode (default `fast`,
  $1 per 1,000).
- Ten results are in the base price; each extra result bills separately, so
  `max_results` is capped by `parallel_max_results`.

A 402 (no credit) or 429 (rate limit) opens a short backoff for that API key
so a fused search does not pay a failing round trip on every call. The `max_results`
cap bounds one request, not the number of requests: every search a tool runs
(`discover` runs several) is one billed Parallel request.

There is an official SDK (`parallel-web`), but this speaks HTTP directly to
match the other connectors and avoid a dependency.
"""

import hashlib
import logging
import time

import httpx
from .base import Connector, SearchResult, Source
from ..config import settings

logger = logging.getLogger(__name__)

API_URL = "https://api.parallel.ai/v1/search"

MODES = ("turbo", "fast", "basic", "advanced")

# Seconds to skip calls after the API reports no credit (402) or a rate limit (429).
BACKOFF_SECONDS = {402: 3600.0, 429: 60.0}

# Per-account monotonic deadline before which search() makes no request, keyed
# by a digest of the API key. Module-level so every connector instance (one per
# aggregator) shares it, while a different key keeps its own state.
_backoff_until: dict[str, float] = {}


def _account(api_key: str) -> str:
    return hashlib.sha256(api_key.encode()).hexdigest()[:16]


def _reset_backoff() -> None:
    """Clear every account's backoff (tests)."""
    _backoff_until.clear()


class ParallelConnector(Connector):
    """Parallel Search API connector."""

    name = "parallel"

    def __init__(
        self,
        api_key: str | None = None,
        mode: str | None = None,
        max_results: int | None = None,
        excerpt_chars: int | None = None,
    ):
        self.api_key = api_key or settings.parallel_api_key
        self.mode = mode or settings.parallel_mode
        self.max_results = max_results or settings.parallel_max_results
        self.excerpt_chars = excerpt_chars or settings.parallel_excerpt_chars

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def _probe_url(self) -> str | None:
        # Reachability only; key validity would cost a billed call.
        return "https://api.parallel.ai"

    async def search(self, query: str, top_k: int = 10) -> SearchResult:
        """Execute a Parallel web search."""
        if not self.is_configured():
            return SearchResult(sources=[], query=query, connector_name=self.name)
        account = _account(self.api_key)
        if time.monotonic() < _backoff_until.get(account, 0.0):
            return SearchResult(sources=[], query=query, connector_name=self.name)

        mode = self.mode if self.mode in MODES else "fast"
        body = {
            "objective": query,
            "search_queries": [query],
            "mode": mode,
            "advanced_settings": {
                "max_results": max(1, min(top_k, self.max_results)),
                "excerpt_settings": {"max_chars_per_result": self.excerpt_chars},
            },
        }
        headers = {
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
        }

        sources = []
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(API_URL, json=body, headers=headers)
                response.raise_for_status()
                data = response.json()

            for result in (data.get("results") or [])[:top_k]:
                url = result.get("url") or ""
                if not url:
                    continue
                source_id = f"px_{hashlib.md5(url.encode()).hexdigest()[:8]}"
                excerpts = [e for e in (result.get("excerpts") or []) if e]

                sources.append(Source(
                    id=source_id,
                    title=result.get("title") or "",
                    url=url,
                    content="\n\n".join(excerpts),
                    score=1.0 / (len(sources) + 1),  # Rank-based score
                    connector=self.name,
                    metadata={
                        "published_date": result.get("publish_date"),
                        "mode": mode,
                    },
                ))

        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            backoff = BACKOFF_SECONDS.get(status)
            if backoff:
                _backoff_until[account] = time.monotonic() + backoff
            # Status only: error bodies can echo request details.
            logger.warning(
                "Parallel search error: HTTP %s%s",
                status,
                f" (skipping Parallel for {int(backoff)}s)" if backoff else "",
            )
        except Exception as e:
            logger.warning("Parallel search error: %s", type(e).__name__)

        return SearchResult(
            sources=sources,
            query=query,
            connector_name=self.name,
            total_results=len(sources),
        )
