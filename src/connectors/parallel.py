"""Opt-in connector for the hosted Parallel Search MCP server."""

import hashlib
import json
import logging
from collections.abc import Mapping
from typing import Any

from fastmcp import Client

from .base import Connector, SearchResult, Source
from ..config import settings

logger = logging.getLogger(__name__)


class ParallelConnector(Connector):
    """Search through Parallel's credential-free hosted MCP endpoint."""

    name = "parallel"

    def __init__(self, enabled: bool | None = None, url: str | None = None):
        self.enabled = settings.parallel_mcp_enabled if enabled is None else enabled
        self.url = url or settings.parallel_mcp_url

    def is_configured(self) -> bool:
        return self.enabled and bool(self.url)

    def _probe_url(self) -> str | None:
        return self.url

    async def search(self, query: str, top_k: int = 10) -> SearchResult:
        """Call the hosted ``web_search`` tool and normalize its result schema."""
        if not self.is_configured():
            return SearchResult(sources=[], query=query, connector_name=self.name)

        limit = max(0, top_k)
        try:
            async with Client(self.url) as client:
                response = await client.call_tool(
                    "web_search",
                    {"objective": query, "search_queries": [query]},
                )
            if getattr(response, "is_error", False):
                raise RuntimeError("Parallel MCP returned a tool error")
            payload = self._response_payload(response)
            results = payload.get("results")
            if not isinstance(results, list):
                raise ValueError("Parallel MCP response is missing a results array")
            sources = [self._to_source(item, index) for index, item in enumerate(results[:limit])]
        except Exception as exc:
            logger.error("Parallel search failed (%s)", type(exc).__name__)
            raise RuntimeError("Parallel MCP request failed") from exc

        return SearchResult(
            sources=sources,
            query=query,
            connector_name=self.name,
            total_results=len(sources),
        )

    @staticmethod
    def _response_payload(response: Any) -> Mapping[str, Any]:
        structured = getattr(response, "structured_content", None)
        if structured is not None:
            if not isinstance(structured, Mapping):
                raise ValueError("Parallel MCP structured content is malformed")
            return structured

        content = getattr(response, "content", None)
        if not isinstance(content, list) or not content:
            raise ValueError("Parallel MCP response has no usable content")
        text_blocks = [block.text for block in content if isinstance(getattr(block, "text", None), str)]
        if not text_blocks:
            raise ValueError("Parallel MCP response has no text fallback")
        parsed = json.loads("\n".join(text_blocks))
        if not isinstance(parsed, Mapping):
            raise ValueError("Parallel MCP text fallback is malformed")
        return parsed

    @classmethod
    def _to_source(cls, item: Any, index: int) -> Source:
        if not isinstance(item, Mapping):
            raise ValueError("Parallel MCP result is malformed")
        url = item.get("url")
        excerpts = item.get("excerpts")
        if not isinstance(url, str) or not url or not isinstance(excerpts, list):
            raise ValueError("Parallel MCP result is missing url or excerpts")
        if not all(isinstance(excerpt, str) for excerpt in excerpts):
            raise ValueError("Parallel MCP result excerpts must be strings")
        content = "\n\n".join(excerpt.strip() for excerpt in excerpts if excerpt.strip())
        if not content:
            raise ValueError("Parallel MCP result has no excerpt evidence")
        title = item.get("title")
        if not isinstance(title, str) or not title.strip():
            title = url
        source_id = f"pa_{hashlib.md5(url.encode()).hexdigest()[:8]}"
        return Source(
            id=source_id,
            title=title,
            url=url,
            content=content,
            score=1.0 / (index + 1),
            connector=cls.name,
            metadata={},
        )
