"""
NLI-based Citation Verification

Research basis: VeriCite 3-stage pipeline (arXiv:2510.11394)
- Stage 1: Generate initial response from all contexts
- Stage 2: NLI model verifies each claim against cited evidence
- Stage 3: Refine response with verified claims only

Key insight: Use microsoft/deberta-v3-base-mnli for NLI classification.
Labels: ENTAILMENT (supports), NEUTRAL (unrelated), CONTRADICTION (opposes)

Note: NLI model requires transformers and torch packages.
Gracefully degrades to heuristic verification if unavailable.
"""

import re
from dataclasses import dataclass
from typing import Literal, Optional

from ..config import settings


NLI_MODEL = "microsoft/deberta-v3-base-mnli"
VERIFICATION_THRESHOLD = 0.7


@dataclass
class VerifiedClaim:
    """A claim with NLI verification result."""
    claim: str
    evidence: str
    source_number: int
    label: Literal["ENTAILMENT", "NEUTRAL", "CONTRADICTION"]
    confidence: float
    verified: bool  # True if ENTAILMENT with confidence > threshold


@dataclass
class VerificationResult:
    """Overall verification result for a synthesis."""
    claims: list[VerifiedClaim]
    verification_rate: float  # Fraction of verified claims
    unverified_claims: list[str]
    contradicted_claims: list[str]


class CitationVerifier:
    """
    Verify claims against source evidence using NLI.

    Usage:
        verifier = CitationVerifier()
        result = verifier.verify("Python is interpreted", "Python is an interpreted language...")
        # result.verified = True, result.label = "ENTAILMENT", result.confidence = 0.95
    """

    def __init__(
        self,
        model: str = NLI_MODEL,
        device: int = -1,
        threshold: float = VERIFICATION_THRESHOLD,
    ):
        """
        Initialize verifier.

        Args:
            model: HuggingFace NLI model name
            device: -1 for CPU, 0+ for GPU
            threshold: Confidence threshold for verification
        """
        self.model_name = model
        self.device = device
        self.threshold = threshold
        self._nli = None
        self._use_heuristic = False

    def _load_nli(self):
        """Lazy-load NLI model."""
        if self._nli is not None or self._use_heuristic:
            return

        try:
            from transformers import pipeline
            self._nli = pipeline(
                "text-classification",
                model=self.model_name,
                device=self.device,
            )
        except ImportError:
            # transformers not available - fall back to heuristic
            self._use_heuristic = True
        except Exception:
            # Model loading failed - fall back to heuristic
            self._use_heuristic = True

    def verify(
        self,
        claim: str,
        evidence: str,
        source_number: int = 0,
    ) -> VerifiedClaim:
        """
        Verify a single claim against evidence.

        Args:
            claim: The factual claim to verify
            evidence: Source text that should support the claim
            source_number: Source number for attribution

        Returns:
            VerifiedClaim with NLI classification and confidence
        """
        self._load_nli()

        if self._use_heuristic:
            return self._verify_heuristic(claim, evidence, source_number)

        try:
            # NLI format: premise [SEP] hypothesis
            # Evidence is premise, claim is hypothesis
            result = self._nli(f"{evidence} [SEP] {claim}")

            label = result[0]["label"]
            confidence = result[0]["score"]

            # Normalize label (different models use different formats)
            label = label.upper()
            if "ENTAIL" in label:
                label = "ENTAILMENT"
            elif "CONTRA" in label:
                label = "CONTRADICTION"
            else:
                label = "NEUTRAL"

            return VerifiedClaim(
                claim=claim,
                evidence=evidence[:500],  # Truncate for storage
                source_number=source_number,
                label=label,
                confidence=confidence,
                verified=(label == "ENTAILMENT" and confidence > self.threshold),
            )
        except Exception:
            return self._verify_heuristic(claim, evidence, source_number)

    def _verify_heuristic(
        self,
        claim: str,
        evidence: str,
        source_number: int,
    ) -> VerifiedClaim:
        """
        Heuristic verification when NLI is unavailable.

        Uses keyword overlap and simple pattern matching.
        """
        claim_lower = claim.lower()
        evidence_lower = evidence.lower()

        # Extract key terms from claim (words > 3 chars)
        claim_terms = set(
            word for word in re.findall(r'\b\w+\b', claim_lower)
            if len(word) > 3
        )

        # Count matches in evidence
        matches = sum(1 for term in claim_terms if term in evidence_lower)
        total_terms = len(claim_terms) if claim_terms else 1

        # Calculate confidence based on overlap
        overlap_ratio = matches / total_terms

        if overlap_ratio >= 0.6:
            label = "ENTAILMENT"
            confidence = min(0.5 + overlap_ratio * 0.4, 0.95)
        elif overlap_ratio >= 0.3:
            label = "NEUTRAL"
            confidence = 0.5
        else:
            label = "NEUTRAL"
            confidence = 0.3

        return VerifiedClaim(
            claim=claim,
            evidence=evidence[:500],
            source_number=source_number,
            label=label,
            confidence=confidence,
            verified=(label == "ENTAILMENT" and confidence > self.threshold),
        )

    async def verify_all(
        self,
        claims: list[tuple[str, str, int]],  # (claim, evidence, source_num)
    ) -> list[VerifiedClaim]:
        """
        Verify multiple claims in batch.

        Args:
            claims: List of (claim_text, evidence_text, source_number) tuples

        Returns:
            List of VerifiedClaim results
        """
        results = []
        for claim, evidence, source_num in claims:
            verified = self.verify(claim, evidence, source_num)
            results.append(verified)
        return results

    def compute_verification_result(
        self,
        verified_claims: list[VerifiedClaim],
    ) -> VerificationResult:
        """
        Compute overall verification statistics.

        Args:
            verified_claims: List of verified claims

        Returns:
            VerificationResult with stats
        """
        if not verified_claims:
            return VerificationResult(
                claims=[],
                verification_rate=1.0,  # No claims = nothing to verify
                unverified_claims=[],
                contradicted_claims=[],
            )

        verified_count = sum(1 for c in verified_claims if c.verified)
        verification_rate = verified_count / len(verified_claims)

        unverified = [c.claim for c in verified_claims if not c.verified and c.label != "CONTRADICTION"]
        contradicted = [c.claim for c in verified_claims if c.label == "CONTRADICTION"]

        return VerificationResult(
            claims=verified_claims,
            verification_rate=verification_rate,
            unverified_claims=unverified,
            contradicted_claims=contradicted,
        )


def extract_claims_with_citations(
    text: str,
    sources: list,
) -> list[tuple[str, str, int]]:
    """
    Extract claims with their cited evidence from synthesis text.

    Args:
        text: Synthesis text with [N] citations
        sources: List of source objects with content

    Returns:
        List of (claim, evidence, source_number) tuples
    """
    claims = []

    # Pattern: sentence ending with citation [N]
    pattern = r'([^.!?]+[.!?])\s*\[(\d+)\]'

    for match in re.finditer(pattern, text):
        claim = match.group(1).strip()
        source_num = int(match.group(2))

        # Get evidence from source
        if 0 < source_num <= len(sources):
            source = sources[source_num - 1]
            evidence = source.content if hasattr(source, 'content') else str(source)
            claims.append((claim, evidence, source_num))

    return claims
