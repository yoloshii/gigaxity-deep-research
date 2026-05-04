"""
Query Decomposition for Enhanced Gap Detection

Research basis: arXiv:2507.00355 - Multi-hop retrieval via decomposition
- LLM decomposes query into sub-questions
- Retrieve for EACH sub-question separately
- Find gaps per aspect, then aggregate

Key insight: "Best ML framework for production" decomposes to:
1. What are popular ML frameworks?
2. What defines production-readiness?
3. What are performance benchmarks?
"""

from dataclasses import dataclass
from typing import Literal, Optional

from ..config import settings
from ..llm_utils import get_llm_content


AspectType = Literal["factual", "procedural", "comparative", "evaluative"]


@dataclass
class QueryAspect:
    """An aspect of a complex query."""
    aspect: str
    type: AspectType
    focus: str
    suggested_query: str


class QueryDecomposer:
    """
    Decompose complex queries into aspects for gap analysis.

    Usage:
        decomposer = QueryDecomposer(llm_client)
        aspects = await decomposer.decompose("Best ML framework for production APIs")

        # Returns:
        # [
        #   QueryAspect(aspect="Framework Options", type="factual", ...),
        #   QueryAspect(aspect="Production Requirements", type="evaluative", ...),
        #   QueryAspect(aspect="Performance Comparison", type="comparative", ...),
        # ]
    """

    DECOMPOSITION_PROMPT = """Decompose this research query into distinct aspects.

Query: {query}

Identify 2-5 aspects that would each need separate investigation.

For each aspect:
- ASPECT: [short name]
- TYPE: [factual/procedural/comparative/evaluative]
  - factual: What exists, what is true
  - procedural: How to do something
  - comparative: X vs Y
  - evaluative: Best/worst, recommendations
- FOCUS: [what specifically to investigate]
- QUERY: [specific search query for this aspect]
---

Example for "How to deploy FastAPI to production":
ASPECT: Deployment Options
TYPE: factual
FOCUS: Available deployment methods and platforms
QUERY: FastAPI deployment options platforms
---
ASPECT: Production Configuration
TYPE: procedural
FOCUS: Settings and config for production
QUERY: FastAPI production configuration settings
---"""

    def __init__(
        self,
        llm_client=None,
        model: str = None,
    ):
        """
        Initialize decomposer.

        Args:
            llm_client: OpenAI-compatible LLM client
            model: Model name for LLM calls
        """
        self.llm_client = llm_client
        self.model = model or settings.llm_model

    async def decompose(
        self,
        query: str,
        max_aspects: int = 5,
    ) -> list[QueryAspect]:
        """
        Decompose query into aspects.

        Args:
            query: Complex research query
            max_aspects: Maximum number of aspects to return

        Returns:
            List of QueryAspect for separate investigation
        """
        if not self.llm_client:
            # Fallback: return single aspect with original query
            return [QueryAspect(
                aspect="Main Query",
                type="factual",
                focus=query,
                suggested_query=query,
            )]

        try:
            prompt = self.DECOMPOSITION_PROMPT.format(query=query)
            response = await self._call_llm(prompt)
            aspects = self._parse_aspects(response)

            # Limit to max_aspects
            return aspects[:max_aspects]

        except Exception:
            # On error, return single aspect
            return [QueryAspect(
                aspect="Main Query",
                type="factual",
                focus=query,
                suggested_query=query,
            )]

    def _parse_aspects(self, response: str) -> list[QueryAspect]:
        """Parse decomposition response into aspects."""
        aspects = []
        blocks = response.split("---")

        for block in blocks:
            block = block.strip()
            if not block or "ASPECT:" not in block:
                continue

            fields = {}
            for line in block.split("\n"):
                if ":" in line:
                    key, value = line.split(":", 1)
                    fields[key.strip().upper()] = value.strip()

            try:
                aspect_type = fields.get("TYPE", "factual").lower()
                if aspect_type not in ("factual", "procedural", "comparative", "evaluative"):
                    aspect_type = "factual"

                aspects.append(QueryAspect(
                    aspect=fields.get("ASPECT", "Unknown"),
                    type=aspect_type,
                    focus=fields.get("FOCUS", ""),
                    suggested_query=fields.get("QUERY", ""),
                ))
            except (ValueError, KeyError):
                continue

        return aspects

    def decompose_sync(self, query: str) -> list[QueryAspect]:
        """
        Synchronous decomposition using heuristics.

        Simple parsing without LLM - detects common patterns.
        """
        aspects = []
        query_lower = query.lower()

        # Detect comparison queries
        if " vs " in query_lower or " versus " in query_lower or "compare" in query_lower:
            parts = query_lower.replace(" versus ", " vs ").split(" vs ")
            if len(parts) == 2:
                aspects.append(QueryAspect(
                    aspect=parts[0].strip().title(),
                    type="factual",
                    focus=f"Details about {parts[0].strip()}",
                    suggested_query=parts[0].strip(),
                ))
                aspects.append(QueryAspect(
                    aspect=parts[1].strip().title(),
                    type="factual",
                    focus=f"Details about {parts[1].strip()}",
                    suggested_query=parts[1].strip(),
                ))
                aspects.append(QueryAspect(
                    aspect="Comparison",
                    type="comparative",
                    focus="Direct comparison of features and tradeoffs",
                    suggested_query=query,
                ))
                return aspects

        # Detect how-to queries
        if query_lower.startswith("how to ") or "tutorial" in query_lower:
            aspects.append(QueryAspect(
                aspect="Prerequisites",
                type="factual",
                focus="What you need before starting",
                suggested_query=f"{query} prerequisites requirements",
            ))
            aspects.append(QueryAspect(
                aspect="Steps",
                type="procedural",
                focus="Step-by-step process",
                suggested_query=query,
            ))
            aspects.append(QueryAspect(
                aspect="Best Practices",
                type="evaluative",
                focus="Recommended approaches and common mistakes",
                suggested_query=f"{query} best practices tips",
            ))
            return aspects

        # Detect "best" queries
        if "best" in query_lower or "recommend" in query_lower:
            aspects.append(QueryAspect(
                aspect="Options",
                type="factual",
                focus="Available choices",
                suggested_query=query.replace("best", "").replace("recommend", "").strip() + " options",
            ))
            aspects.append(QueryAspect(
                aspect="Criteria",
                type="evaluative",
                focus="What makes something 'best' for this use case",
                suggested_query=query,
            ))
            aspects.append(QueryAspect(
                aspect="Recommendations",
                type="evaluative",
                focus="Expert recommendations",
                suggested_query=query,
            ))
            return aspects

        # Default: single aspect
        return [QueryAspect(
            aspect="Main Query",
            type="factual",
            focus=query,
            suggested_query=query,
        )]

    async def _call_llm(self, prompt: str) -> str:
        """Call LLM for decomposition."""
        response = await self.llm_client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1000,
            temperature=0.3,
        )
        return get_llm_content(response.choices[0].message)
