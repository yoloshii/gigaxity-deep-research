"""OpenAI Responses API retriever with domain filtering for social platforms.

Uses OpenAI's web_search tool with allowed_domains filter to guarantee
results from specific social platforms (Reddit, X, YouTube, etc.).
Surfaces community discussions and lived-experience content that general web
search and documentation lookup miss.
"""

import os
import logging
from typing import List, Dict, Any, Optional

from .reddit_resolver import get_default_resolver

logger = logging.getLogger(__name__)


class SocialOpenAIRetriever:
    """Social platform retriever using OpenAI Responses API with domain filtering.

    Guarantees results from configured social platforms (default: reddit.com)
    by using the web_search tool's allowed_domains filter.

    Configuration (env vars):
        OPENAI_API_KEY: Required. OpenAI API key.
        SOCIAL_OPENAI_DOMAINS: Comma-separated domains. Default: "reddit.com"
        SOCIAL_OPENAI_MODEL: Model for search. Default: "gpt-4o"
            NOTE: Must be gpt-4o or higher. gpt-4o-mini does NOT support
            the filters parameter in web_search tool.
    """

    def __init__(self, query: str, query_domains: Optional[List[str]] = None):
        self.query = query
        self.api_key = os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError(
                "OPENAI_API_KEY environment variable not set. "
                "Required for SocialOpenAIRetriever."
            )

        # Domain priority: parameter > env var > default
        if query_domains:
            self.allowed_domains = query_domains
        else:
            domains_env = os.getenv("SOCIAL_OPENAI_DOMAINS", "reddit.com")
            self.allowed_domains = [d.strip() for d in domains_env.split(",")]

        # Must be gpt-4o+; gpt-4o-mini doesn't support filters in web_search
        self.model = os.getenv("SOCIAL_OPENAI_MODEL", "gpt-4o")

    def _enrich_and_select(
        self, candidates: List[Dict[str, Any]], max_results: int
    ) -> List[Dict[str, Any]]:
        """Select up to ``max_results`` results, routing Reddit URLs off-IP.

        Reddit URLs are resolved to comment text via the Arctic Shift archive and
        carried as ``raw_content`` so the conductor uses them directly and never
        scrapes reddit.com. A Reddit URL that cannot produce >100 chars of content
        is DROPPED — never returned as a bare scrapeable href (this safety holds
        even on resolver error or when Arctic Shift is disabled). Non-Reddit
        results pass through unchanged. To keep the result count stable when
        Reddit results are dropped, scan further candidates up to a hard cap.
        """
        try:
            resolver = get_default_resolver()
        except Exception as e:  # never let resolver setup break the retriever
            logger.error("reddit resolver unavailable: %s", e)
            resolver = None

        def _looks_reddit(u: str) -> bool:
            low = (u or "").lower()
            return (
                "//reddit.com" in low or ".reddit.com" in low
                or "//redd.it" in low or ".redd.it" in low
            )

        selected: List[Dict[str, Any]] = []
        cap = max(max_results * 3, max_results + 12)  # bound archive calls
        for idx, item in enumerate(candidates):
            if len(selected) >= max_results or idx >= cap:
                break
            href = item.get("href") or item.get("url") or ""
            if resolver is not None:
                try:
                    is_reddit = resolver.is_reddit_url(href)
                except Exception:
                    is_reddit = _looks_reddit(href)
            else:
                is_reddit = _looks_reddit(href)

            if not is_reddit:
                selected.append(item)
                continue

            # Reddit URL: resolve off-IP or drop — never hand it to the scraper.
            rc = None
            if resolver is not None:
                try:
                    rc = resolver.resolve(
                        href, item.get("title", ""), item.get("body", "")
                    )
                except Exception as e:
                    logger.warning("reddit enrichment error for %s: %s", href, e)
                    rc = None
            if rc and len(rc) > 100:
                enriched = dict(item)
                enriched["raw_content"] = rc
                selected.append(enriched)
            # else: drop the reddit result (safety invariant: never scrape reddit)
        return selected

    def search(self, max_results: int = 10) -> List[Dict[str, Any]]:
        """Search social platforms via OpenAI Responses API.

        Returns list of dicts with keys: href, body, title
        (matching GPT-Researcher retriever contract).
        On error, returns empty list for graceful degradation.
        """
        try:
            from openai import OpenAI

            client = OpenAI(api_key=self.api_key)

            response = client.responses.create(
                model=self.model,
                tools=[
                    {
                        "type": "web_search",
                        "search_context_size": "medium",
                        "filters": {"allowed_domains": self.allowed_domains},
                    }
                ],
                tool_choice="required",
                include=["web_search_call.action.sources"],
                input=(
                    f"Find discussions, questions, opinions, and threads about: "
                    f"{self.query}. Focus on community questions that lack "
                    f"authoritative answers elsewhere on the web."
                ),
            )

            search_response = []
            seen_urls = set()

            for item in response.output:
                # Extract from web_search_call action sources
                if hasattr(item, "action") and hasattr(item.action, "sources"):
                    for source in item.action.sources:
                        url = getattr(source, "url", "")
                        if url and url not in seen_urls:
                            seen_urls.add(url)
                            search_response.append(
                                {
                                    "href": url,
                                    "body": getattr(
                                        source,
                                        "snippet",
                                        getattr(source, "text", ""),
                                    ),
                                    "title": getattr(source, "title", ""),
                                }
                            )

                # Extract from message content annotations (URL citations)
                if hasattr(item, "content") and isinstance(item.content, list):
                    for block in item.content:
                        if hasattr(block, "annotations"):
                            for ann in block.annotations:
                                url = getattr(ann, "url", "")
                                if url and url not in seen_urls:
                                    seen_urls.add(url)
                                    search_response.append(
                                        {
                                            "href": url,
                                            "body": getattr(ann, "title", ""),
                                            "title": getattr(ann, "title", ""),
                                        }
                                    )

            if not search_response:
                logger.warning(
                    "SocialOpenAIRetriever: No results for '%s' "
                    "with domains %s",
                    self.query,
                    self.allowed_domains,
                )

            return self._enrich_and_select(search_response, max_results)

        except ImportError:
            logger.error(
                "SocialOpenAIRetriever: openai package not installed. "
                "Install with: pip install openai"
            )
            return []
        except Exception as e:
            logger.error("SocialOpenAIRetriever error: %s", e)
            return []
