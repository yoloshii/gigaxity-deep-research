"""
Adaptive Connector Routing

Research basis: Query classification, domain-specific search optimization
- Classify query into type (academic, technical, news, general, etc.)
- Route to connectors optimized for that type
- Adjust search parameters per type

Key insight: Not all connectors are equal for all queries.
SearXNG with arXiv engine > Tavily for academic papers.
Tavily with recency filter > SearXNG for recent news.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from ..config import settings
from ..llm_utils import get_llm_content


class QueryType(str, Enum):
    """Types of queries for routing decisions."""
    ACADEMIC = "academic"      # Research papers, scientific topics
    TECHNICAL = "technical"    # Programming, frameworks, APIs
    NEWS = "news"              # Recent events, announcements
    COMPARISON = "comparison"  # X vs Y evaluations
    TUTORIAL = "tutorial"      # How-to, learning
    GENERAL = "general"        # Everything else


@dataclass
class RoutingDecision:
    """Result of query routing analysis."""
    query_type: QueryType
    primary_connectors: list[str]
    secondary_connectors: list[str]
    search_params: dict[str, dict] = field(default_factory=dict)
    confidence: float = 0.5


class ConnectorRouter:
    """
    Route queries to optimal connectors based on type.

    Usage:
        router = ConnectorRouter(llm_client)
        decision = await router.route("latest transformer architecture papers")

        # Returns:
        # RoutingDecision(
        #     query_type=QueryType.ACADEMIC,
        #     primary_connectors=["searxng"],
        #     secondary_connectors=["tavily"],
        #     search_params={"searxng": {"engines": "arxiv,google scholar"}},
        #     confidence=0.9
        # )
    """

    # Connector capabilities matrix - scores 0-1 for each query type
    CONNECTOR_STRENGTHS: dict[str, dict[QueryType, float]] = {
        "searxng": {
            QueryType.ACADEMIC: 0.9,    # Has arXiv, Google Scholar engines
            QueryType.TECHNICAL: 0.8,   # Has GitHub, Stack Overflow
            QueryType.NEWS: 0.7,        # Has news engines
            QueryType.COMPARISON: 0.6,
            QueryType.TUTORIAL: 0.7,
            QueryType.GENERAL: 0.8,
        },
        "tavily": {
            QueryType.ACADEMIC: 0.5,
            QueryType.TECHNICAL: 0.7,
            QueryType.NEWS: 0.9,        # Excellent recency
            QueryType.COMPARISON: 0.7,
            QueryType.TUTORIAL: 0.6,
            QueryType.GENERAL: 0.8,
        },
        "linkup": {
            QueryType.ACADEMIC: 0.4,
            QueryType.TECHNICAL: 0.7,
            QueryType.NEWS: 0.8,
            QueryType.COMPARISON: 0.6,
            QueryType.TUTORIAL: 0.7,
            QueryType.GENERAL: 0.7,
        },
    }

    # Connector-specific params per query type
    CONNECTOR_PARAMS: dict[str, dict[QueryType, dict]] = {
        "searxng": {
            QueryType.ACADEMIC: {"engines": "arxiv,google scholar,semantic scholar"},
            QueryType.TECHNICAL: {"engines": "github,stackoverflow,google"},
            QueryType.NEWS: {"engines": "google news,bing news", "time_range": "week"},
            QueryType.TUTORIAL: {"engines": "google,youtube"},
        },
        "tavily": {
            QueryType.NEWS: {"search_depth": "basic"},
            QueryType.ACADEMIC: {"search_depth": "advanced"},
        },
    }

    # Heuristic patterns for fast classification
    QUERY_PATTERNS: dict[QueryType, list[str]] = {
        QueryType.ACADEMIC: [
            "paper", "research", "study", "arxiv", "journal", "citation",
            "published", "findings", "methodology", "hypothesis", "peer-reviewed"
        ],
        QueryType.COMPARISON: [
            "vs", "versus", "compare", "better", "difference", "pros and cons",
            "which is", "advantages", "disadvantages", "tradeoffs"
        ],
        QueryType.TUTORIAL: [
            "how to", "tutorial", "guide", "learn", "step by step", "example",
            "getting started", "beginner", "walkthrough", "explain"
        ],
        QueryType.NEWS: [
            "latest", "news", "announced", "released", "2024", "2025",
            "recent", "update", "breaking", "today", "this week"
        ],
        QueryType.TECHNICAL: [
            "api", "library", "framework", "code", "implement", "function",
            "error", "bug", "documentation", "sdk", "package", "module"
        ],
    }

    CLASSIFICATION_PROMPT = """Classify this search query into one category.

Query: {query}

Categories:
- academic: Research papers, scientific studies, citations needed
- technical: Programming, APIs, frameworks, documentation
- news: Recent events, announcements, what happened
- comparison: X vs Y, choosing between options, tradeoffs
- tutorial: How to do something, step-by-step, learning
- general: None of the above

Respond with just the category name."""

    def __init__(
        self,
        llm_client=None,
        model: str = None,
        available_connectors: list[str] = None,
    ):
        """
        Initialize router.

        Args:
            llm_client: Optional LLM client for ambiguous queries
            model: Model name for LLM calls
            available_connectors: List of available connector names
        """
        self.llm_client = llm_client
        self.model = model or settings.llm_model
        self.available_connectors = available_connectors or ["searxng", "tavily", "linkup"]

    async def route(self, query: str) -> RoutingDecision:
        """
        Determine optimal connectors for this query.

        Args:
            query: Search query to route

        Returns:
            RoutingDecision with prioritized connectors and params
        """
        # Classify query (fast heuristics first, LLM fallback)
        query_type, confidence = await self._classify(query)

        # Score connectors for this query type
        scored = []
        for connector in self.available_connectors:
            if connector in self.CONNECTOR_STRENGTHS:
                score = self.CONNECTOR_STRENGTHS[connector].get(query_type, 0.5)
                scored.append((connector, score))

        # Sort by score descending
        scored.sort(key=lambda x: x[1], reverse=True)

        # Primary = top scorer(s), secondary = rest above threshold
        primary = [scored[0][0]] if scored else []
        secondary = [c for c, s in scored[1:] if s >= 0.6]

        # Build connector-specific params
        search_params = {}
        for connector in primary + secondary:
            if connector in self.CONNECTOR_PARAMS:
                type_params = self.CONNECTOR_PARAMS[connector].get(query_type, {})
                if type_params:
                    search_params[connector] = type_params

        return RoutingDecision(
            query_type=query_type,
            primary_connectors=primary,
            secondary_connectors=secondary,
            search_params=search_params,
            confidence=confidence,
        )

    async def _classify(self, query: str) -> tuple[QueryType, float]:
        """
        Classify query into type.

        Returns tuple of (QueryType, confidence).
        Uses fast heuristics first, falls back to LLM for ambiguous cases.
        """
        query_lower = query.lower()

        # Fast heuristic classification
        scores = {}
        for query_type, patterns in self.QUERY_PATTERNS.items():
            score = sum(1 for p in patterns if p in query_lower)
            if score > 0:
                scores[query_type] = score

        if scores:
            # Return highest scoring type
            best_type = max(scores, key=scores.get)
            # Confidence based on how many patterns matched
            confidence = min(0.5 + scores[best_type] * 0.15, 0.95)
            return best_type, confidence

        # No clear pattern match - use LLM if available
        if self.llm_client:
            try:
                prompt = self.CLASSIFICATION_PROMPT.format(query=query)
                response = await self._call_llm(prompt)
                type_str = response.strip().lower()
                return QueryType(type_str), 0.7
            except (ValueError, Exception):
                pass

        # Default to general
        return QueryType.GENERAL, 0.5

    async def _call_llm(self, prompt: str) -> str:
        """Call LLM for classification."""
        response = await self.llm_client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=50,
            temperature=0.1,
        )
        return get_llm_content(response.choices[0].message)

    def classify_sync(self, query: str) -> tuple[QueryType, float]:
        """
        Synchronous classification using heuristics only.

        Useful for quick routing without async overhead.
        """
        query_lower = query.lower()

        scores = {}
        for query_type, patterns in self.QUERY_PATTERNS.items():
            score = sum(1 for p in patterns if p in query_lower)
            if score > 0:
                scores[query_type] = score

        if scores:
            best_type = max(scores, key=scores.get)
            confidence = min(0.5 + scores[best_type] * 0.15, 0.95)
            return best_type, confidence

        return QueryType.GENERAL, 0.5

    def route_sync(self, query: str) -> RoutingDecision:
        """
        Synchronous routing using heuristics only.

        For benchmarking and cases where async overhead is not desired.
        """
        query_type, confidence = self.classify_sync(query)

        # Score connectors for this query type
        scored = []
        for connector in self.available_connectors:
            if connector in self.CONNECTOR_STRENGTHS:
                score = self.CONNECTOR_STRENGTHS[connector].get(query_type, 0.5)
                scored.append((connector, score))

        scored.sort(key=lambda x: x[1], reverse=True)

        primary = [scored[0][0]] if scored else []
        secondary = [c for c, s in scored[1:] if s >= 0.6]

        search_params = {}
        for connector in primary + secondary:
            if connector in self.CONNECTOR_PARAMS:
                type_params = self.CONNECTOR_PARAMS[connector].get(query_type, {})
                if type_params:
                    search_params[connector] = type_params

        return RoutingDecision(
            query_type=query_type,
            primary_connectors=primary,
            secondary_connectors=secondary,
            search_params=search_params,
            confidence=confidence,
        )
