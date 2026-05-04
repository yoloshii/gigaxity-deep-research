"""
Source Quality Gate

Research basis: CRAG - Corrective RAG (arXiv:2401.15884)
- Evaluate retrieved evidence quality BEFORE generation
- If quality is low, reject and suggest better queries
- Prevents hallucination from irrelevant sources

Key insight: Average relevance score < 0.3 = reject synthesis.
Suggest additional searches to fill gaps.
"""

import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from ..config import settings
from ..llm_utils import get_llm_content


class QualityDecision(str, Enum):
    """Decision outcome from quality gate."""
    PROCEED = "proceed"  # Sources adequate, continue synthesis
    REJECT = "reject"    # Sources inadequate, suggest alternatives
    PARTIAL = "partial"  # Some sources good, filter and continue


@dataclass
class QualityGateResult:
    """Result of quality gate evaluation."""
    decision: QualityDecision
    avg_quality: float
    good_sources: list  # Sources that pass threshold
    rejected_sources: list  # Sources below threshold
    source_scores: list[float] = None  # Individual scores
    suggestion: Optional[str] = None  # Suggested additional searches
    reason: Optional[str] = None


class SourceQualityGate:
    """
    Evaluate source quality and decide whether to proceed with synthesis.

    Usage:
        gate = SourceQualityGate(llm_client)
        result = await gate.evaluate(query, sources)

        if result.decision == QualityDecision.REJECT:
            return {"status": "insufficient_sources", "suggestion": result.suggestion}
        elif result.decision == QualityDecision.PARTIAL:
            sources = result.good_sources  # Use filtered sources
    """

    # Implementation defaults inspired by CRAG (arXiv:2401.15884). The paper
    # demonstrates the three-bucket schema (PASS / PARTIAL / REJECT) but does
    # not prescribe exact thresholds; these values are tuned against the
    # bundled `comprehensive` and `fast` presets and may need adjustment for
    # other domains. Override per-instance if your source distribution shifts.
    REJECT_THRESHOLD = 0.3  # Below this, reject entirely
    PASS_THRESHOLD = 0.5    # Above this, source is good

    SCORING_PROMPT = """Rate each source's relevance to the query (0.0 to 1.0).

Query: {query}

Sources:
{sources}

For each source, provide a score:
- 1.0 = Directly answers the query
- 0.7 = Highly relevant context
- 0.5 = Somewhat relevant
- 0.3 = Tangentially related
- 0.0 = Completely irrelevant

Format: One score per line, just the number (e.g., 0.8)."""

    SUGGESTION_PROMPT = """The following sources don't adequately cover this query.

Query: {query}

Current sources cover:
{coverage}

What additional searches would help answer this query?
Provide 2-3 specific search queries that would fill gaps.

Format: One query per line."""

    def __init__(
        self,
        llm_client=None,
        model: str = None,
        reject_threshold: float = None,
        pass_threshold: float = None,
    ):
        """
        Initialize quality gate.

        Args:
            llm_client: OpenAI-compatible LLM client
            model: Model name for LLM calls
            reject_threshold: Avg score below which to reject (default 0.3)
            pass_threshold: Individual source threshold (default 0.5)
        """
        self.llm_client = llm_client
        self.model = model or settings.llm_model
        self.reject_threshold = reject_threshold or self.REJECT_THRESHOLD
        self.pass_threshold = pass_threshold or self.PASS_THRESHOLD

    async def evaluate(
        self,
        query: str,
        sources: list,
    ) -> QualityGateResult:
        """
        Evaluate source quality for query.

        Args:
            query: The research query
            sources: Pre-gathered sources to evaluate

        Returns:
            QualityGateResult with decision and filtered sources
        """
        if not sources:
            return QualityGateResult(
                decision=QualityDecision.REJECT,
                avg_quality=0.0,
                good_sources=[],
                rejected_sources=[],
                source_scores=[],
                suggestion="No sources provided. Try searching with broader terms.",
                reason="No sources to evaluate",
            )

        # Score each source for query relevance
        if self.llm_client:
            scores = await self._score_sources_llm(query, sources)
        else:
            scores = self._score_sources_heuristic(query, sources)

        avg_quality = sum(scores) / len(scores)

        # Categorize sources
        good_sources = []
        rejected_sources = []
        for source, score in zip(sources, scores):
            if score >= self.pass_threshold:
                good_sources.append(source)
            else:
                rejected_sources.append(source)

        # Decide
        if avg_quality < self.reject_threshold:
            suggestion = await self._suggest_searches(query, sources) if self.llm_client else \
                f"Try more specific searches related to: {query}"
            return QualityGateResult(
                decision=QualityDecision.REJECT,
                avg_quality=avg_quality,
                good_sources=[],
                rejected_sources=sources,
                source_scores=scores,
                suggestion=suggestion,
                reason=f"Average relevance {avg_quality:.2f} below threshold {self.reject_threshold}",
            )
        elif len(good_sources) < len(sources):
            return QualityGateResult(
                decision=QualityDecision.PARTIAL,
                avg_quality=avg_quality,
                good_sources=good_sources,
                rejected_sources=rejected_sources,
                source_scores=scores,
                reason=f"Filtered {len(rejected_sources)} low-quality sources",
            )
        else:
            return QualityGateResult(
                decision=QualityDecision.PROCEED,
                avg_quality=avg_quality,
                good_sources=good_sources,
                rejected_sources=[],
                source_scores=scores,
            )

    async def _score_sources_llm(
        self,
        query: str,
        sources: list,
    ) -> list[float]:
        """Score sources using LLM."""
        prompt = self.SCORING_PROMPT.format(
            query=query,
            sources=self._format_sources(sources),
        )

        try:
            response = await self._call_llm(prompt)
            scores = self._parse_scores(response, len(sources))
        except Exception:
            # Fall back to heuristic on error
            scores = self._score_sources_heuristic(query, sources)

        return scores

    def _score_sources_heuristic(
        self,
        query: str,
        sources: list,
    ) -> list[float]:
        """Score sources using keyword overlap heuristic."""
        query_terms = set(
            word.lower() for word in re.findall(r'\b\w+\b', query)
            if len(word) > 3
        )

        if not query_terms:
            return [0.5] * len(sources)

        scores = []
        for source in sources:
            content = self._get_content(source)
            content_lower = content.lower()

            # Count term matches
            matches = sum(1 for term in query_terms if term in content_lower)
            overlap_ratio = matches / len(query_terms)

            # Scale to 0-1
            score = min(overlap_ratio * 1.2, 1.0)  # Slight boost
            scores.append(score)

        return scores

    def _parse_scores(self, response: str, expected_count: int) -> list[float]:
        """Parse scores from LLM response."""
        scores = []
        for line in response.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                # Extract number from line (handles "1. 0.8" or "0.8" formats)
                numbers = re.findall(r'\d+\.?\d*', line)
                if numbers:
                    score = float(numbers[-1])  # Take last number
                    scores.append(min(max(score, 0.0), 1.0))
            except ValueError:
                continue

        # Pad if needed
        while len(scores) < expected_count:
            scores.append(0.5)

        return scores[:expected_count]

    async def _suggest_searches(
        self,
        query: str,
        sources: list,
    ) -> str:
        """Suggest additional searches to improve coverage."""
        coverage = "\n".join(
            f"- {self._get_title(s)}"
            for s in sources[:5]
        )

        prompt = self.SUGGESTION_PROMPT.format(
            query=query,
            coverage=coverage,
        )

        try:
            response = await self._call_llm(prompt)
            return f"Consider searching for: {response.strip()}"
        except Exception:
            return f"Try more specific searches related to: {query}"

    def _format_sources(self, sources: list) -> str:
        """Format sources for scoring prompt."""
        parts = []
        for i, s in enumerate(sources, 1):
            title = self._get_title(s)
            content = self._get_content(s)[:300]
            parts.append(f"[{i}] {title}\n{content}...")
        return "\n\n".join(parts)

    def _get_title(self, source) -> str:
        """Extract title from source."""
        if hasattr(source, 'title'):
            return source.title or "Untitled"
        return "Untitled"

    def _get_content(self, source) -> str:
        """Extract content from source."""
        if hasattr(source, 'content'):
            return source.content or ""
        if hasattr(source, 'text'):
            return source.text or ""
        if isinstance(source, str):
            return source
        return ""

    async def _call_llm(self, prompt: str) -> str:
        """Call LLM."""
        response = await self.llm_client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=500,
            temperature=0.3,
        )
        return get_llm_content(response.choices[0].message)

    def evaluate_sync(
        self,
        query: str,
        sources: list,
    ) -> QualityGateResult:
        """
        Synchronous evaluation using heuristics only.

        Useful for quick evaluation without async overhead.
        """
        if not sources:
            return QualityGateResult(
                decision=QualityDecision.REJECT,
                avg_quality=0.0,
                good_sources=[],
                rejected_sources=[],
                source_scores=[],
                suggestion="No sources provided.",
                reason="No sources to evaluate",
            )

        scores = self._score_sources_heuristic(query, sources)
        avg_quality = sum(scores) / len(scores)

        good_sources = []
        rejected_sources = []
        for source, score in zip(sources, scores):
            if score >= self.pass_threshold:
                good_sources.append(source)
            else:
                rejected_sources.append(source)

        if avg_quality < self.reject_threshold:
            return QualityGateResult(
                decision=QualityDecision.REJECT,
                avg_quality=avg_quality,
                good_sources=[],
                rejected_sources=sources,
                source_scores=scores,
                suggestion=f"Try more specific searches related to: {query}",
                reason=f"Average relevance {avg_quality:.2f} below threshold",
            )
        elif len(good_sources) < len(sources):
            return QualityGateResult(
                decision=QualityDecision.PARTIAL,
                avg_quality=avg_quality,
                good_sources=good_sources,
                rejected_sources=rejected_sources,
                source_scores=scores,
            )
        else:
            return QualityGateResult(
                decision=QualityDecision.PROCEED,
                avg_quality=avg_quality,
                good_sources=good_sources,
                rejected_sources=[],
                source_scores=scores,
            )
