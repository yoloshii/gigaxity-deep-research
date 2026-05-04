"""
Bidirectional Evidence Binding

Research basis: Attribution Gradients (arXiv:2510.00361)
- For each claim, find supporting excerpts
- ALSO find contradicting excerpts
- Net support score = supporting / (supporting + contradicting)
- Enables nuanced confidence: "3 sources support, 1 contradicts"

Key insight: Use same NLI model but look for CONTRADICTION label too.
"""

from dataclasses import dataclass, field
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .verification import CitationVerifier


@dataclass
class EvidenceExcerpt:
    """An excerpt from a source that relates to a claim."""
    text: str
    source_number: int
    confidence: float
    source_title: str = ""


@dataclass
class BidirectionalBinding:
    """Evidence binding for a claim with support and contradiction."""
    claim: str
    supporting: list[EvidenceExcerpt] = field(default_factory=list)
    contradicting: list[EvidenceExcerpt] = field(default_factory=list)
    neutral: list[EvidenceExcerpt] = field(default_factory=list)

    @property
    def net_support(self) -> float:
        """
        Net support score: 1.0 = all support, 0.5 = balanced, 0.0 = all contradict.
        """
        total = len(self.supporting) + len(self.contradicting)
        if total == 0:
            return 0.5  # Neutral when no evidence
        return len(self.supporting) / total

    @property
    def has_disagreement(self) -> bool:
        """True if sources disagree on this claim."""
        return len(self.supporting) > 0 and len(self.contradicting) > 0

    @property
    def evidence_strength(self) -> str:
        """Qualitative assessment of evidence strength."""
        total = len(self.supporting) + len(self.contradicting) + len(self.neutral)
        if total == 0:
            return "no_evidence"
        if len(self.contradicting) > len(self.supporting):
            return "contradicted"
        if self.has_disagreement:
            return "disputed"
        if len(self.supporting) >= 2:
            return "strong"
        if len(self.supporting) == 1:
            return "moderate"
        return "weak"


class BidirectionalBinder:
    """
    Find both supporting and contradicting evidence for claims.

    Usage:
        binder = BidirectionalBinder()
        binding = await binder.bind_claim(
            "React is faster than Vue",
            sources=[source1, source2, source3]
        )
        # binding.supporting = [excerpt1, excerpt2]
        # binding.contradicting = [excerpt3]
        # binding.net_support = 0.67
        # binding.has_disagreement = True
    """

    def __init__(
        self,
        verifier: Optional["CitationVerifier"] = None,
        excerpt_size: int = 500,
        max_excerpts_per_source: int = 3,
    ):
        """
        Initialize binder.

        Args:
            verifier: CitationVerifier instance for NLI (lazy-loaded if None)
            excerpt_size: Maximum characters per excerpt
            max_excerpts_per_source: Max excerpts to extract from each source
        """
        self._verifier = verifier
        self.excerpt_size = excerpt_size
        self.max_excerpts_per_source = max_excerpts_per_source

    @property
    def verifier(self):
        """Lazy-load verifier."""
        if self._verifier is None:
            from .verification import CitationVerifier
            self._verifier = CitationVerifier()
        return self._verifier

    async def bind_claim(
        self,
        claim: str,
        sources: list,
    ) -> BidirectionalBinding:
        """
        Find all evidence (supporting and contradicting) for a claim.

        Args:
            claim: The factual claim to find evidence for
            sources: List of sources to search (with .content attribute)

        Returns:
            BidirectionalBinding with categorized evidence
        """
        binding = BidirectionalBinding(claim=claim)

        for i, source in enumerate(sources, 1):
            # Get content from source
            content = self._get_content(source)
            if not content:
                continue

            # Get source title
            title = getattr(source, 'title', '') or getattr(source, 'url', f'Source {i}')

            # Extract relevant excerpts from source
            excerpts = self._extract_relevant_excerpts(claim, content)

            for excerpt in excerpts[:self.max_excerpts_per_source]:
                # Classify stance using NLI
                result = self.verifier.verify(claim, excerpt, i)

                evidence = EvidenceExcerpt(
                    text=excerpt,
                    source_number=i,
                    confidence=result.confidence,
                    source_title=title,
                )

                if result.label == "ENTAILMENT":
                    binding.supporting.append(evidence)
                elif result.label == "CONTRADICTION":
                    binding.contradicting.append(evidence)
                else:
                    binding.neutral.append(evidence)

        return binding

    def _get_content(self, source) -> str:
        """Extract content from source object."""
        if hasattr(source, 'content'):
            return source.content or ""
        if hasattr(source, 'text'):
            return source.text or ""
        if isinstance(source, str):
            return source
        return ""

    def _extract_relevant_excerpts(
        self,
        claim: str,
        content: str,
    ) -> list[str]:
        """
        Extract passages from content that might be relevant to claim.

        Strategy:
        1. Split content into overlapping chunks
        2. Find chunks with keyword overlap to claim
        3. Return top chunks by overlap score
        """
        if not content:
            return []

        # Simple chunking with overlap
        chunks = []
        words = content.split()
        chunk_words = self.excerpt_size // 5  # Approximate words per chunk

        for i in range(0, len(words), chunk_words // 2):
            chunk = " ".join(words[i:i + chunk_words])
            if chunk:
                chunks.append(chunk)

        # Score by keyword overlap with claim
        claim_words = set(
            word.lower() for word in claim.split()
            if len(word) > 3
        )

        if not claim_words:
            return chunks[:self.max_excerpts_per_source]

        scored = []
        for chunk in chunks:
            chunk_words_set = set(
                word.lower() for word in chunk.split()
            )
            overlap = len(claim_words & chunk_words_set)
            if overlap > 0:
                scored.append((overlap, chunk))

        # Return top chunks
        scored.sort(reverse=True)
        return [chunk for _, chunk in scored[:self.max_excerpts_per_source * 2]]

    async def bind_all_claims(
        self,
        claims: list[str],
        sources: list,
    ) -> list[BidirectionalBinding]:
        """
        Bind evidence for multiple claims.

        Args:
            claims: List of claims to find evidence for
            sources: Sources to search

        Returns:
            List of BidirectionalBinding results
        """
        bindings = []
        for claim in claims:
            binding = await self.bind_claim(claim, sources)
            bindings.append(binding)
        return bindings

    def summarize_bindings(
        self,
        bindings: list[BidirectionalBinding],
    ) -> dict:
        """
        Summarize evidence bindings for all claims.

        Returns:
            Summary dict with stats and disputed claims
        """
        total_claims = len(bindings)
        if total_claims == 0:
            return {
                "total_claims": 0,
                "strong_support": 0,
                "disputed": 0,
                "contradicted": 0,
                "average_net_support": 1.0,
            }

        strong = sum(1 for b in bindings if b.evidence_strength == "strong")
        disputed = sum(1 for b in bindings if b.has_disagreement)
        contradicted = sum(1 for b in bindings if b.evidence_strength == "contradicted")
        avg_support = sum(b.net_support for b in bindings) / total_claims

        disputed_claims = [
            {
                "claim": b.claim,
                "supporting_count": len(b.supporting),
                "contradicting_count": len(b.contradicting),
            }
            for b in bindings if b.has_disagreement
        ]

        return {
            "total_claims": total_claims,
            "strong_support": strong,
            "disputed": disputed,
            "contradicted": contradicted,
            "average_net_support": avg_support,
            "disputed_claims": disputed_claims,
        }
