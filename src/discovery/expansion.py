"""
Query Expansion for Cold-Start Breadth

Research basis: HyDE (arXiv:2212.10496), Query2Doc patterns
- LLM generates semantically diverse query variants
- Each variant explores different angle of same topic
- Parallel search all variants
- RRF merge for comprehensive initial coverage

Key insight: "quantum memory" expands to:
1. "quantum memory systems" (original)
2. "qubit storage architectures" (technical synonym)
3. "quantum RAM implementation" (specific application)
4. "quantum information persistence" (conceptual framing)
5. "quantum computing memory challenges" (problem-oriented)
"""

import logging
from dataclasses import dataclass, field
from typing import Optional

from ..config import settings
from ..degradation import (
    StageDegradation,
    degradation_from_output,
    partial_degradation,
    transport_degradation,
)
from ..llm_utils import (
    ExtractionMode,
    LLMOutput,
    call_with_extraction,
    derive_effective_budget,
)
from .explorer import split_record_blocks

logger = logging.getLogger(__name__)


@dataclass
class ExpandedQuery:
    """Result of query expansion."""
    original: str
    variants: list[str] = field(default_factory=list)
    angles: list[str] = field(default_factory=list)  # What each variant explores
    # Backward-compatible carrier: partial expansion, blank-response fallback
    # and transport fallback surface here (previously invisible to callers).
    degradations: list[StageDegradation] = field(default_factory=list)


_EXPANSION_FIELDS = ("VARIANT", "ANGLE")


def parse_expansion_records(
    text: str,
    n: int,
    original: str,
) -> Optional[tuple[list[str], list[str]]]:
    """Parse VARIANT/ANGLE blocks; None = grammar rejected.

    The accepted grammar is exactly `n` complete blocks whose variants are
    distinct from each other and from the original after case-folded
    normalization. Duplicates are dropped rather than counted — structurally
    valid duplicates must never turn zero effective variants into "success"
    — so the outcome can be short: fewer than `n` distinct variants is the
    caller's usable partial success, zero is a rejection (None), and more
    than `n` blocks is a rejection.
    """
    raw = (text or "").strip()
    if not raw:
        return None
    blocks = split_record_blocks(raw)
    if not blocks or len(blocks) > n:
        return None
    variants: list[str] = []
    angles: list[str] = []
    seen = {original.strip().casefold()}
    for block in blocks:
        if set(block) != set(_EXPANSION_FIELDS):
            return None
        variant = block["VARIANT"]
        angle = block["ANGLE"]
        if not variant or not angle:
            return None
        folded = variant.strip().casefold()
        if folded in seen:
            continue
        seen.add(folded)
        variants.append(variant)
        angles.append(angle)
    if not variants:
        return None
    return variants, angles


class QueryExpander:
    """
    Generate diverse query variants for comprehensive cold-start search.

    Usage:
        expander = QueryExpander(llm_client)
        expanded = await expander.expand("quantum memory systems")

        # Returns:
        # ExpandedQuery(
        #     original="quantum memory systems",
        #     variants=["quantum memory systems", "qubit storage...", ...],
        #     angles=["original query", "technical synonym", ...]
        # )

        # Then search all variants in parallel
        results = await asyncio.gather(*[
            aggregator.search(v) for v in expanded.variants
        ])
    """

    EXPANSION_PROMPT = """Generate exactly {n} diverse search query variants for this topic.

Original query: {query}

Each variant should explore a DIFFERENT angle, such as:
- Technical synonyms (different terminology, same concept)
- Specific applications (concrete use cases)
- Problem-oriented (challenges, limitations, issues)
- Comparative (alternatives, competing approaches)

Output one complete block per variant, blocks separated by a line containing
only "---", each block exactly:
VARIANT: [query text]
ANGLE: [what this explores]
---

Generate exactly {n} variants, each under 10 words, each different from the
original query and from each other. Output only these blocks."""

    def __init__(
        self,
        llm_client=None,
        model: str = None,
        default_num_variants: int = 4,
    ):
        """
        Initialize expander.

        Args:
            llm_client: OpenAI-compatible LLM client
            model: Model name for LLM calls
            default_num_variants: Default number of variants to generate
        """
        self.llm_client = llm_client
        self.model = model or settings.llm_model
        self.default_num_variants = default_num_variants

    async def expand(
        self,
        query: str,
        num_variants: int = None,
    ) -> ExpandedQuery:
        """
        Generate diverse query variants.

        Args:
            query: Original search query
            num_variants: Number of variants to generate (default 4)

        Returns:
            ExpandedQuery with original + variants + angles
        """
        num_variants = num_variants or self.default_num_variants

        if not self.llm_client:
            # Heuristic-only by configuration — normal operation, not a
            # degradation.
            return self._heuristic_expand(query, num_variants)

        prompt = self.EXPANSION_PROMPT.format(query=query, n=num_variants)
        try:
            output = await self._call_llm(prompt)
        except Exception:
            logger.warning("expansion LLM call failed", exc_info=True)
            fallback = self._heuristic_expand(query, num_variants)
            fallback.degradations.append(transport_degradation(
                "expansion",
                "expansion call failed; heuristic expansion substituted",
            ))
            return fallback

        parsed = parse_expansion_records(output.text, num_variants, query)
        if parsed is None:
            # Blank, malformed, over-count, or zero distinct variants: the
            # heuristic fallback runs on ALL of these — previously it sat in
            # the except block only, so a blank response yielded "no
            # variants" while the fallback was unreachable.
            fallback = self._heuristic_expand(query, num_variants)
            fallback.degradations.append(degradation_from_output(
                "expansion",
                output,
                parse_failed=True,
                fallback_used=True,
                message="expansion output unusable; heuristic expansion substituted",
            ))
            return fallback

        parsed_variants, parsed_angles = parsed
        degradations: list[StageDegradation] = []
        if len(parsed_variants) < num_variants:
            # Usable partial success — fewer distinct variants than asked
            # for, no fallback taken. Recorded so partial coverage is
            # visible; zero variants is a rejection, never "success".
            degradations.append(partial_degradation(
                "expansion",
                f"{len(parsed_variants)} of {num_variants} variants usable",
            ))

        return ExpandedQuery(
            original=query,
            # Always include original first (public contract).
            variants=[query] + parsed_variants,
            angles=["original query"] + parsed_angles,
            degradations=degradations,
        )

    def _heuristic_expand(self, query: str, num_variants: int) -> ExpandedQuery:
        """
        Simple expansion without LLM.

        Adds common query modifiers to expand coverage.
        """
        variants = [query]
        angles = ["original query"]

        # Add common expansion patterns
        expansions = [
            (f"{query} tutorial guide", "tutorial-oriented"),
            (f"{query} vs alternatives", "comparative"),
            (f"{query} problems issues", "problem-oriented"),
            (f"best {query} practices", "best practices"),
        ]

        for variant, angle in expansions[:num_variants]:
            variants.append(variant)
            angles.append(angle)

        return ExpandedQuery(
            original=query,
            variants=variants[:num_variants + 1],
            angles=angles[:num_variants + 1],
        )

    def expand_sync(self, query: str, num_variants: int = None) -> ExpandedQuery:
        """
        Synchronous expansion using heuristics only.

        Useful for quick expansion without async overhead.
        """
        return self._heuristic_expand(query, num_variants or self.default_num_variants)

    async def _call_llm(self, prompt: str) -> LLMOutput:
        """Call LLM for expansion.

        PARSE_REQUIRED — expansion parses its output — and the full
        LLMOutput is returned so a blank response stays classifiable
        (truncated vs reasoning-only vs empty). Reasoning-aware budget: the
        Q2 harness (scripts/instrument_stage_budgets.py, 2026-08-04,
        glm-5.2) showed 100% truncation at a flat 500."""
        return await call_with_extraction(
            self.llm_client,
            self.model,
            [{"role": "user", "content": prompt}],
            derive_effective_budget(500, self.model),
            ExtractionMode.PARSE_REQUIRED,
            temperature=0.7,  # Higher temp for diversity
        )
