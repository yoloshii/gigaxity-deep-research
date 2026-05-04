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

from dataclasses import dataclass, field
from typing import Optional

from ..config import settings
from ..llm_utils import get_llm_content


@dataclass
class ExpandedQuery:
    """Result of query expansion."""
    original: str
    variants: list[str] = field(default_factory=list)
    angles: list[str] = field(default_factory=list)  # What each variant explores


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

    EXPANSION_PROMPT = """Generate 4 diverse search query variants for this topic.

Original query: {query}

Each variant should explore a DIFFERENT angle:
1. Technical synonyms (different terminology, same concept)
2. Specific applications (concrete use cases)
3. Problem-oriented (challenges, limitations, issues)
4. Comparative (alternatives, competing approaches)

Format:
VARIANT: [query text]
ANGLE: [what this explores]
---

Generate exactly 4 variants. Keep each under 10 words."""

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

        # Always include original
        variants = [query]
        angles = ["original query"]

        if not self.llm_client:
            # Fallback: simple expansion without LLM
            return self._heuristic_expand(query, num_variants)

        try:
            prompt = self.EXPANSION_PROMPT.format(query=query)
            response = await self._call_llm(prompt)

            # Parse variants from response
            parsed_variants, parsed_angles = self._parse_expansion(response, query)

            # Add parsed variants (excluding duplicates of original)
            for variant, angle in zip(parsed_variants, parsed_angles):
                if variant.lower() != query.lower() and variant not in variants:
                    variants.append(variant)
                    angles.append(angle)

            # Limit to requested number
            variants = variants[:num_variants + 1]
            angles = angles[:num_variants + 1]

        except Exception as e:
            # On error, fall back to heuristic expansion
            return self._heuristic_expand(query, num_variants)

        return ExpandedQuery(
            original=query,
            variants=variants,
            angles=angles,
        )

    def _parse_expansion(
        self,
        response: str,
        original_query: str,
    ) -> tuple[list[str], list[str]]:
        """Parse expansion response into variants and angles."""
        variants = []
        angles = []

        blocks = response.split("---")
        for block in blocks:
            block = block.strip()
            if not block or "VARIANT:" not in block:
                continue

            variant = ""
            angle = ""
            for line in block.split("\n"):
                line = line.strip()
                if line.startswith("VARIANT:"):
                    variant = line.replace("VARIANT:", "").strip()
                elif line.startswith("ANGLE:"):
                    angle = line.replace("ANGLE:", "").strip()

            if variant:
                variants.append(variant)
                angles.append(angle or "unspecified angle")

        return variants, angles

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

    async def _call_llm(self, prompt: str) -> str:
        """Call LLM for expansion."""
        response = await self.llm_client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=500,
            temperature=0.7,  # Higher temp for diversity
        )
        return get_llm_content(response.choices[0].message)
