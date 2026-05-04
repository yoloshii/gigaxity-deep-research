"""
Ranking & Contextual Summarization (RCS).

Research basis: PaperQA2 library
- Summarize each source specifically for the query context
- LLM re-rank summaries by relevance
- Synthesize from ranked contextual summaries

Key insight: Context-aware summarization > raw content.
"""

from dataclasses import dataclass, field
from typing import Optional

from ..llm_utils import get_llm_content
from .aggregator import PreGatheredSource


@dataclass
class ContextualSummary:
    """A source summarized in context of the query."""
    source: PreGatheredSource
    summary: str
    relevance_score: float
    key_points: list[str] = field(default_factory=list)


@dataclass
class RCSResult:
    """Result of RCS preprocessing."""
    summaries: list[ContextualSummary]
    total_sources: int
    kept_sources: int


class RCSPreprocessor:
    """
    Prepare sources with contextual summarization before synthesis.

    Usage:
        rcs = RCSPreprocessor(llm_client)
        result = await rcs.prepare(
            "How does React useState work?",
            sources,
            top_k=5
        )
        # Use result.summaries for synthesis
    """

    SUMMARIZE_PROMPT = """Summarize this source specifically for answering the query.

Query: {query}

Source: {title}
Content:
{content}

Provide:
1. A 2-3 sentence summary of what this source contributes to answering the query
2. 3-5 key points relevant to the query
3. A relevance score (0.0-1.0) indicating how useful this source is

Format:
SUMMARY: [your summary]
KEY_POINTS:
- [point 1]
- [point 2]
- [point 3]
RELEVANCE: [score]"""

    RERANK_PROMPT = """Re-rank these source summaries by relevance to the query.

Query: {query}

Summaries:
{summaries}

Return the source numbers in order of relevance (most relevant first).
Format: comma-separated numbers, e.g., "3, 1, 4, 2, 5"

Ranking:"""

    def __init__(
        self,
        llm_client=None,
        model: str = None,
        min_relevance: float = 0.3,
    ):
        """
        Initialize RCS preprocessor.

        Args:
            llm_client: Optional LLM client for contextual summarization
            model: Model name
            min_relevance: Minimum relevance to keep source
        """
        self.llm_client = llm_client
        self.model = model
        self.min_relevance = min_relevance

    async def prepare(
        self,
        query: str,
        sources: list[PreGatheredSource],
        top_k: int = 5,
        use_llm_rerank: bool = True,
    ) -> RCSResult:
        """
        Create contextual summaries and rank by relevance.

        Args:
            query: Research query
            sources: Pre-gathered sources
            top_k: Number of top sources to keep
            use_llm_rerank: Whether to use LLM for re-ranking

        Returns:
            RCSResult with ranked contextual summaries
        """
        if not sources:
            return RCSResult(summaries=[], total_sources=0, kept_sources=0)

        # Create contextual summaries (sequential - single GPU can only process one at a time)
        summaries = []
        for source in sources:
            if self.llm_client:
                summary = await self._contextual_summarize(source, query)
            else:
                summary = self._heuristic_summarize(source, query)
            summaries.append(summary)

        # Filter by minimum relevance
        summaries = [s for s in summaries if s.relevance_score >= self.min_relevance]

        # Re-rank if requested
        if use_llm_rerank and self.llm_client and len(summaries) > 1:
            summaries = await self._llm_rerank(summaries, query)
        else:
            # Sort by relevance score
            summaries.sort(key=lambda x: x.relevance_score, reverse=True)

        # Take top_k
        kept = summaries[:top_k]

        return RCSResult(
            summaries=kept,
            total_sources=len(sources),
            kept_sources=len(kept),
        )

    async def _contextual_summarize(
        self,
        source: PreGatheredSource,
        query: str,
    ) -> ContextualSummary:
        """Summarize source in context of query using LLM."""
        prompt = self.SUMMARIZE_PROMPT.format(
            query=query,
            title=source.title,
            content=source.content[:2000],
        )

        response = await self._call_llm(prompt, max_tokens=400)

        # Parse response
        summary = ""
        key_points = []
        relevance = 0.5

        lines = response.split("\n")
        in_key_points = False

        for line in lines:
            line = line.strip()
            if line.startswith("SUMMARY:"):
                summary = line.replace("SUMMARY:", "").strip()
                in_key_points = False
            elif line.startswith("KEY_POINTS:"):
                in_key_points = True
            elif line.startswith("- ") and in_key_points:
                key_points.append(line[2:].strip())
            elif line.startswith("RELEVANCE:"):
                try:
                    score_str = line.replace("RELEVANCE:", "").strip()
                    relevance = float(score_str)
                    relevance = max(0.0, min(1.0, relevance))
                except ValueError:
                    relevance = 0.5
                in_key_points = False

        return ContextualSummary(
            source=source,
            summary=summary or f"Summary of {source.title}",
            relevance_score=relevance,
            key_points=key_points[:5],
        )

    def _heuristic_summarize(
        self,
        source: PreGatheredSource,
        query: str,
    ) -> ContextualSummary:
        """Heuristic summarization without LLM."""
        # Extract first meaningful paragraph
        content = source.content.strip()
        paragraphs = [p.strip() for p in content.split("\n\n") if p.strip()]
        summary = paragraphs[0][:300] if paragraphs else content[:300]

        # Calculate relevance by keyword overlap
        query_words = set(query.lower().split())
        content_words = set(content.lower().split())
        overlap = len(query_words & content_words)
        relevance = min(overlap / max(len(query_words), 1), 1.0)

        # Boost for title match
        title_words = set(source.title.lower().split())
        if query_words & title_words:
            relevance = min(relevance + 0.2, 1.0)

        # Boost for documentation sources
        if source.source_type in ("documentation", "official"):
            relevance = min(relevance + 0.1, 1.0)

        # Extract key points (first sentences from paragraphs)
        key_points = []
        for p in paragraphs[:5]:
            sentences = p.split(". ")
            if sentences:
                point = sentences[0].strip()
                if len(point) > 10 and len(point) < 200:
                    key_points.append(point)

        return ContextualSummary(
            source=source,
            summary=summary,
            relevance_score=relevance,
            key_points=key_points[:5],
        )

    async def _llm_rerank(
        self,
        summaries: list[ContextualSummary],
        query: str,
    ) -> list[ContextualSummary]:
        """Re-rank summaries using LLM."""
        # Format summaries for ranking
        summary_text = "\n".join(
            f"[{i+1}] {s.source.title}: {s.summary}"
            for i, s in enumerate(summaries)
        )

        prompt = self.RERANK_PROMPT.format(
            query=query,
            summaries=summary_text,
        )

        response = await self._call_llm(prompt, max_tokens=50)

        # Parse ranking
        try:
            # Extract numbers from response
            numbers = []
            for part in response.replace(",", " ").split():
                try:
                    num = int(part.strip())
                    if 1 <= num <= len(summaries):
                        numbers.append(num - 1)  # Convert to 0-indexed
                except ValueError:
                    continue

            # Reorder summaries
            if numbers:
                reranked = []
                seen = set()
                for idx in numbers:
                    if idx not in seen and idx < len(summaries):
                        reranked.append(summaries[idx])
                        seen.add(idx)

                # Add any missed summaries at the end
                for i, s in enumerate(summaries):
                    if i not in seen:
                        reranked.append(s)

                return reranked

        except Exception:
            pass

        # Fallback to original order
        return summaries

    async def _call_llm(self, prompt: str, max_tokens: int = 400) -> str:
        """Call LLM with prompt."""
        response = await self.llm_client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            temperature=0.3,
        )
        return get_llm_content(response.choices[0].message)

    def prepare_sync(
        self,
        query: str,
        sources: list[PreGatheredSource],
        top_k: int = 5,
    ) -> RCSResult:
        """Synchronous heuristic-only preparation."""
        if not sources:
            return RCSResult(summaries=[], total_sources=0, kept_sources=0)

        summaries = [
            self._heuristic_summarize(source, query)
            for source in sources
        ]

        # Filter and sort
        summaries = [s for s in summaries if s.relevance_score >= self.min_relevance]
        summaries.sort(key=lambda x: x.relevance_score, reverse=True)

        return RCSResult(
            summaries=summaries[:top_k],
            total_sources=len(sources),
            kept_sources=min(len(summaries), top_k),
        )
