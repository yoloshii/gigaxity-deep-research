"""
Hybrid Ranking Module

Combines multiple ranking signals for superior result ordering.
This addresses Gap #2: Position-Only Ranking.

Signals combined:
1. RRF score (position from connectors)
2. Semantic similarity (embedding-based)
3. Authority score (domain trust)
4. Freshness score (publication recency)
"""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import urlparse

import numpy as np

from ..connectors.base import Source
from .authority import AuthorityScorer


@dataclass
class RankingWeights:
    """Weights for combining ranking signals."""
    semantic: float = 0.35
    authority: float = 0.25
    freshness: float = 0.15
    rrf: float = 0.25

    def __post_init__(self):
        """Validate weights sum to 1."""
        total = self.semantic + self.authority + self.freshness + self.rrf
        if abs(total - 1.0) > 0.01:
            # Normalize
            self.semantic /= total
            self.authority /= total
            self.freshness /= total
            self.rrf /= total


class HybridRanker:
    """
    Combines multiple signals for ranking search results.

    Significantly improves over pure RRF by incorporating:
    - Semantic relevance to query
    - Source authority/trust
    - Content freshness
    """

    def __init__(
        self,
        embedding_model: Optional[str] = None,
        default_weights: Optional[RankingWeights] = None,
    ):
        """
        Initialize the hybrid ranker.

        Args:
            embedding_model: Model for semantic similarity (e.g., "BAAI/bge-large-en-v1.5")
            default_weights: Default signal weights
        """
        self.embedding_model = embedding_model
        self.default_weights = default_weights or RankingWeights()
        self.authority_scorer = AuthorityScorer()
        self._embedder = None

    @property
    def embedder(self):
        """Lazy load embedder."""
        if self._embedder is None and self.embedding_model:
            try:
                from sentence_transformers import SentenceTransformer
                self._embedder = SentenceTransformer(self.embedding_model)
            except ImportError:
                pass
        return self._embedder

    def rank(
        self,
        query: str,
        sources: list[Source],
        weights: Optional[RankingWeights] = None,
        freshness_decay_days: int = 30,
    ) -> list[Source]:
        """
        Rank sources using hybrid signals.

        Args:
            query: The search query
            sources: List of sources with RRF scores
            weights: Custom signal weights
            freshness_decay_days: Half-life for freshness decay

        Returns:
            Re-ranked list of sources
        """
        if not sources:
            return []

        weights = weights or self.default_weights

        # Compute all scores
        scored_sources = []
        for source in sources:
            scores = self._compute_scores(
                query, source, freshness_decay_days
            )
            scored_sources.append((source, scores))

        # Normalize scores across sources
        normalized = self._normalize_scores(scored_sources)

        # Compute final weighted score
        for source, scores in normalized:
            final_score = (
                weights.semantic * scores['semantic'] +
                weights.authority * scores['authority'] +
                weights.freshness * scores['freshness'] +
                weights.rrf * scores['rrf']
            )
            source.score = final_score
            source.metadata['score_breakdown'] = scores

        # Sort by final score
        sources.sort(key=lambda s: s.score, reverse=True)

        return sources

    def _compute_scores(
        self,
        query: str,
        source: Source,
        freshness_decay_days: int,
    ) -> dict[str, float]:
        """Compute individual scores for a source."""
        scores = {}

        # RRF score (already computed, just preserve)
        scores['rrf'] = source.score

        # Semantic score
        if self.embedder and source.content:
            scores['semantic'] = self._compute_semantic(query, source.content)
        else:
            # Fallback to keyword matching
            scores['semantic'] = self._compute_keyword_similarity(query, source)

        # Authority score
        authority = self.authority_scorer.score(source.url)
        scores['authority'] = authority.total

        # Freshness score
        scores['freshness'] = self._compute_freshness(source, freshness_decay_days)

        return scores

    def _compute_semantic(self, query: str, content: str) -> float:
        """Compute semantic similarity using embeddings."""
        # Use first 2000 chars to avoid memory issues
        content_truncated = content[:2000]

        query_emb = self.embedder.encode(query, convert_to_numpy=True)
        content_emb = self.embedder.encode(content_truncated, convert_to_numpy=True)

        # Cosine similarity
        similarity = np.dot(query_emb, content_emb) / (
            np.linalg.norm(query_emb) * np.linalg.norm(content_emb)
        )

        return float(similarity)

    def _compute_keyword_similarity(self, query: str, source: Source) -> float:
        """Fallback keyword-based similarity."""
        query_terms = set(re.findall(r'\w+', query.lower()))

        # Combine title and content snippet
        text = f"{source.title} {source.content[:500] if source.content else ''}"
        source_terms = set(re.findall(r'\w+', text.lower()))

        if not query_terms:
            return 0.0

        overlap = len(query_terms & source_terms)
        return overlap / len(query_terms)

    def _compute_freshness(self, source: Source, decay_days: int) -> float:
        """
        Compute freshness score with exponential decay.

        Recent content gets higher scores, decaying over time.
        """
        # Try to extract date from metadata
        date_str = source.metadata.get('date') or source.metadata.get('published')

        if not date_str:
            # Try to extract from URL
            date_str = self._extract_date_from_url(source.url)

        if not date_str:
            # Unknown date - neutral score
            return 0.5

        try:
            # Parse date (try multiple formats)
            pub_date = self._parse_date(date_str)
            if not pub_date:
                return 0.5

            # Compute age in days
            age_days = (datetime.now() - pub_date).days

            if age_days < 0:
                # Future date - probably parsing error
                return 0.5

            # Exponential decay
            # Score = 1.0 at age 0, 0.5 at decay_days, asymptotically approaches 0
            freshness = np.exp(-age_days / decay_days * np.log(2))

            return float(freshness)

        except Exception:
            return 0.5

    def _extract_date_from_url(self, url: str) -> Optional[str]:
        """Try to extract date from URL patterns like /2024/01/15/."""
        match = re.search(r'/(\d{4})/(\d{1,2})/(\d{1,2})/', url)
        if match:
            year, month, day = match.groups()
            return f"{year}-{month.zfill(2)}-{day.zfill(2)}"
        return None

    def _parse_date(self, date_str: str) -> Optional[datetime]:
        """Parse date string in various formats."""
        formats = [
            "%Y-%m-%d",
            "%Y/%m/%d",
            "%d-%m-%Y",
            "%d/%m/%Y",
            "%B %d, %Y",
            "%b %d, %Y",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%dT%H:%M:%SZ",
        ]

        for fmt in formats:
            try:
                return datetime.strptime(date_str.strip(), fmt)
            except ValueError:
                continue

        return None

    def _normalize_scores(
        self,
        scored_sources: list[tuple[Source, dict[str, float]]]
    ) -> list[tuple[Source, dict[str, float]]]:
        """
        Normalize scores to [0, 1] range across all sources.

        Uses min-max normalization per signal.
        """
        if not scored_sources:
            return scored_sources

        # Collect all scores by signal
        signals = ['semantic', 'authority', 'freshness', 'rrf']

        for signal in signals:
            values = [scores[signal] for _, scores in scored_sources]
            min_val, max_val = min(values), max(values)

            if max_val - min_val > 1e-6:
                # Normalize
                for _, scores in scored_sources:
                    scores[signal] = (scores[signal] - min_val) / (max_val - min_val)
            else:
                # All same value - set to 0.5
                for _, scores in scored_sources:
                    scores[signal] = 0.5

        return scored_sources
