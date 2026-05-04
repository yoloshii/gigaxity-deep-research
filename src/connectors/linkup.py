"""LinkUp connector for premium search."""

import hashlib
import logging

from .base import Connector, SearchResult, Source
from ..config import settings

logger = logging.getLogger(__name__)


class LinkUpConnector(Connector):
    """LinkUp premium search connector."""

    name = "linkup"

    def __init__(
        self,
        api_key: str | None = None,
        depth: str | None = None,
    ):
        self.api_key = api_key or settings.linkup_api_key
        self.depth = depth or settings.linkup_depth

    def is_configured(self) -> bool:
        return bool(self.api_key)

    async def search(self, query: str, top_k: int = 10) -> SearchResult:
        """Execute LinkUp search."""
        if not self.is_configured():
            return SearchResult(sources=[], query=query, connector_name=self.name)

        sources = []
        try:
            from linkup import LinkupClient

            client = LinkupClient(api_key=self.api_key)
            response = client.search(
                query=query,
                depth=self.depth,
                output_type="searchResults",
            )

            results = getattr(response, "results", [])[:top_k]

            for idx, result in enumerate(results):
                url = getattr(result, "url", "")
                source_id = f"lu_{hashlib.md5(url.encode()).hexdigest()[:8]}"

                sources.append(Source(
                    id=source_id,
                    title=getattr(result, "name", getattr(result, "title", "")),
                    url=url,
                    content=getattr(result, "content", ""),
                    score=1.0 / (idx + 1),
                    connector=self.name,
                    metadata={},
                ))

        except Exception as e:
            logger.warning("LinkUp search error: %s", e)

        return SearchResult(
            sources=sources,
            query=query,
            connector_name=self.name,
            total_results=len(sources),
        )
