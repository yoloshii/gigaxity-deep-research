"""Reciprocal Rank Fusion (RRF) for combining search results."""

from ..connectors.base import Source
from ..config import settings


def rrf_fusion(
    results_lists: list[list[Source]],
    k: int | None = None,
    top_k: int = 20,
) -> list[Source]:
    """
    Combine multiple ranked lists using Reciprocal Rank Fusion.

    RRF score = sum(1 / (k + rank)) for each list where item appears.

    Args:
        results_lists: List of ranked source lists from different connectors
        k: RRF constant (default: 60, higher = more weight to lower ranks)
        top_k: Number of results to return

    Returns:
        Fused and re-ranked list of sources
    """
    k = k or settings.rrf_k
    scores: dict[str, float] = {}
    sources_by_id: dict[str, Source] = {}

    for results in results_lists:
        for rank, source in enumerate(results, start=1):
            # Use URL as deduplication key
            source_key = source.url

            # Accumulate RRF score
            rrf_score = 1.0 / (k + rank)
            scores[source_key] = scores.get(source_key, 0.0) + rrf_score

            # Keep first occurrence (highest original rank)
            if source_key not in sources_by_id:
                sources_by_id[source_key] = source

    # Sort by RRF score descending
    sorted_keys = sorted(scores.keys(), key=lambda x: scores[x], reverse=True)

    # Update scores and return top_k
    fused_sources = []
    for key in sorted_keys[:top_k]:
        source = sources_by_id[key]
        source.score = scores[key]
        fused_sources.append(source)

    return fused_sources
