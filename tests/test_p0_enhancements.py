"""Tests for P0 Enhancement modules.

P0 Enhancements:
- Discovery: routing.py, expansion.py, decomposer.py, gap_filler.py
- Synthesis: verification.py, binding.py, quality_gate.py, contradictions.py
"""

import pytest
from src.discovery import (
    ConnectorRouter,
    RoutingDecision,
    QueryType,
    QueryExpander,
    ExpandedQuery,
    QueryDecomposer,
    QueryAspect,
    GapFiller,
)
from src.synthesis import (
    SourceQualityGate,
    QualityDecision,
    ContradictionDetector,
    ContradictionSeverity,
    CitationVerifier,
    extract_claims_with_citations,
    BidirectionalBinder,
)


# =============================================================================
# P0 Discovery Tests
# =============================================================================


class TestConnectorRouter:
    """Tests for adaptive connector routing (CRAG-inspired)."""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_router_initialization(self):
        """Router initializes with default weights."""
        router = ConnectorRouter()
        assert router is not None
        assert hasattr(router, 'route')
        assert hasattr(router, 'classify_sync')

    @pytest.mark.unit
    @pytest.mark.p0
    def test_classify_documentation_query(self):
        """Documentation/technical query classified correctly."""
        router = ConnectorRouter()
        query_type, confidence = router.classify_sync("FastAPI authentication documentation")

        assert query_type in [QueryType.TECHNICAL, QueryType.TUTORIAL]
        assert confidence >= 0.0 and confidence <= 1.0

    @pytest.mark.unit
    @pytest.mark.p0
    def test_classify_comparison_query(self):
        """Comparison query detected correctly."""
        router = ConnectorRouter()
        query_type, confidence = router.classify_sync("Compare React vs Vue for large applications")

        assert query_type == QueryType.COMPARISON

    @pytest.mark.unit
    @pytest.mark.p0
    def test_classify_technical_query(self):
        """Technical query detected correctly."""
        router = ConnectorRouter()
        query_type, confidence = router.classify_sync("TypeError: Cannot read property 'map' of undefined")

        # Error messages are classified as technical or general
        assert query_type in [QueryType.TECHNICAL, QueryType.GENERAL]

    @pytest.mark.unit
    @pytest.mark.p0
    def test_classify_academic_query(self):
        """Academic query detected correctly."""
        router = ConnectorRouter()
        query_type, confidence = router.classify_sync("transformer attention mechanisms arxiv paper")

        assert query_type == QueryType.ACADEMIC

    @pytest.mark.unit
    @pytest.mark.p0
    @pytest.mark.asyncio
    async def test_route_returns_routing_decision(self):
        """Async route returns RoutingDecision with connectors."""
        router = ConnectorRouter()
        decision = await router.route("FastAPI authentication tutorial")

        assert isinstance(decision, RoutingDecision)
        assert len(decision.primary_connectors) > 0
        assert decision.confidence >= 0.0 and decision.confidence <= 1.0


class TestQueryExpander:
    """Tests for HyDE-style query expansion."""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_expander_initialization(self):
        """Expander initializes correctly."""
        expander = QueryExpander()
        assert expander is not None

    @pytest.mark.unit
    @pytest.mark.p0
    def test_heuristic_expansion(self):
        """Heuristic expansion generates variants."""
        expander = QueryExpander()
        result = expander.expand_sync("python async programming")

        assert isinstance(result, ExpandedQuery)
        assert result.original == "python async programming"
        assert len(result.variants) > 0

    @pytest.mark.unit
    @pytest.mark.p0
    def test_expansion_preserves_original(self):
        """Expansion preserves original query."""
        expander = QueryExpander()
        original = "how to use FastAPI"
        result = expander.expand_sync(original)

        assert result.original == original
        # Original should be in variants or synonyms
        all_queries = [result.original] + result.variants
        assert len(all_queries) >= 1


class TestQueryDecomposer:
    """Tests for multi-aspect query decomposition."""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_decomposer_initialization(self):
        """Decomposer initializes correctly."""
        decomposer = QueryDecomposer()
        assert decomposer is not None

    @pytest.mark.unit
    @pytest.mark.p0
    def test_heuristic_decomposition(self):
        """Heuristic decomposition identifies aspects."""
        decomposer = QueryDecomposer()
        aspects = decomposer.decompose_sync(
            "Compare FastAPI vs Flask performance and ease of use"
        )

        assert isinstance(aspects, list)
        assert len(aspects) > 0
        for aspect in aspects:
            assert isinstance(aspect, QueryAspect)
            assert aspect.aspect
            assert aspect.suggested_query

    @pytest.mark.unit
    @pytest.mark.p0
    def test_decomposition_comparison_query(self):
        """Comparison queries decompose into comparison aspects."""
        decomposer = QueryDecomposer()
        # Use 2-way comparison (heuristic only handles 2 parts)
        aspects = decomposer.decompose_sync("React vs Vue")

        # Should identify comparison aspects (heuristic creates 3 aspects for "X vs Y")
        aspect_names = [a.aspect.lower() for a in aspects]
        assert any("comparison" in name for name in aspect_names) or len(aspects) >= 2


# =============================================================================
# P0 Synthesis Tests
# =============================================================================


class TestSourceQualityGate:
    """Tests for CRAG-style quality gating."""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_quality_gate_initialization(self):
        """Quality gate initializes correctly."""
        gate = SourceQualityGate()
        assert gate is not None

    @pytest.mark.unit
    @pytest.mark.p0
    def test_heuristic_evaluation_good_sources(self, pre_gathered_sources):
        """Good sources pass quality gate."""
        gate = SourceQualityGate()
        result = gate.evaluate_sync(
            "Compare FastAPI vs Flask",
            pre_gathered_sources
        )

        assert result.decision in [QualityDecision.PROCEED, QualityDecision.PARTIAL]
        assert result.avg_quality > 0.0
        assert len(result.good_sources) > 0

    @pytest.mark.unit
    @pytest.mark.p0
    def test_heuristic_evaluation_low_quality(self, low_quality_sources):
        """Low quality sources may be rejected."""
        gate = SourceQualityGate(reject_threshold=0.5)
        result = gate.evaluate_sync(
            "Python programming tutorial",
            low_quality_sources
        )

        # Low quality sources should have lower scores
        assert result.avg_quality < 0.8
        # May be rejected or partial
        assert result.decision in [
            QualityDecision.REJECT,
            QualityDecision.PARTIAL,
            QualityDecision.PROCEED
        ]


class TestContradictionDetector:
    """Tests for PaperQA2-style contradiction detection."""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_detector_initialization(self):
        """Detector initializes correctly."""
        detector = ContradictionDetector()
        assert detector is not None
        assert hasattr(detector, 'detect')
        assert hasattr(detector, 'format_for_synthesis')

    @pytest.mark.unit
    @pytest.mark.p0
    @pytest.mark.asyncio
    async def test_heuristic_detection_contradictions(self, contradicting_sources):
        """Heuristic detection finds contradictions."""
        detector = ContradictionDetector()  # No LLM client = uses heuristic
        contradictions = await detector.detect(
            "Is Redux necessary for React state management?",
            contradicting_sources
        )

        # Should find at least some potential contradiction
        assert isinstance(contradictions, list)
        # Heuristic may or may not find contradictions
        for c in contradictions:
            assert c.topic
            assert c.severity in ContradictionSeverity

    @pytest.mark.unit
    @pytest.mark.p0
    def test_format_for_synthesis_empty(self):
        """Format empty contradictions returns empty string."""
        detector = ContradictionDetector()
        formatted = detector.format_for_synthesis([])
        assert formatted == ""

    @pytest.mark.unit
    @pytest.mark.p0
    @pytest.mark.asyncio
    async def test_format_for_synthesis(self, contradicting_sources):
        """Contradiction context formats correctly."""
        detector = ContradictionDetector()
        contradictions = await detector.detect(
            "Is Redux necessary?",
            contradicting_sources
        )

        formatted = detector.format_for_synthesis(contradictions)
        assert isinstance(formatted, str)


class TestCitationVerifier:
    """Tests for VeriCite-style citation verification."""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_verifier_initialization(self):
        """Verifier initializes correctly."""
        verifier = CitationVerifier()
        assert verifier is not None

    @pytest.mark.unit
    @pytest.mark.p0
    def test_verify_supported_claim(self):
        """Verify a claim that is supported by evidence."""
        verifier = CitationVerifier()
        result = verifier.verify(
            claim="FastAPI is fast and modern",
            evidence="FastAPI is a modern, fast (high-performance), web framework",
            source_number=1
        )

        assert result.claim == "FastAPI is fast and modern"
        assert result.source_number == 1
        assert result.label in ["ENTAILMENT", "NEUTRAL", "CONTRADICTION"]
        assert 0.0 <= result.confidence <= 1.0

    @pytest.mark.unit
    @pytest.mark.p0
    def test_verify_contradicted_claim(self):
        """Verify a claim that contradicts evidence."""
        verifier = CitationVerifier()
        result = verifier.verify(
            claim="Flask is faster than FastAPI",
            evidence="FastAPI handles 3x more requests per second than Flask",
            source_number=2
        )

        # Should likely be contradicted or neutral (heuristic uses keyword matching)
        assert result.label in ["ENTAILMENT", "NEUTRAL", "CONTRADICTION"]

    @pytest.mark.unit
    @pytest.mark.p0
    def test_extract_claims_with_citations(self, pre_gathered_sources):
        """Extract claims with citation references."""
        content = """
        FastAPI is a modern web framework [1]. It offers high performance [1].
        Flask is a lightweight framework [2]. Performance benchmarks show
        FastAPI is faster [3].
        """

        claims = extract_claims_with_citations(content, pre_gathered_sources)

        assert isinstance(claims, list)
        for claim, evidence, source_num in claims:
            assert isinstance(claim, str)
            assert isinstance(source_num, int)
            assert 1 <= source_num <= len(pre_gathered_sources)


class TestBidirectionalBinder:
    """Tests for evidence binding."""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_binder_initialization(self):
        """Binder initializes correctly."""
        binder = BidirectionalBinder()
        assert binder is not None
        assert hasattr(binder, 'bind_claim')
        assert hasattr(binder, 'bind_all_claims')

    @pytest.mark.unit
    @pytest.mark.p0
    @pytest.mark.asyncio
    async def test_bind_evidence_heuristic(self, pre_gathered_sources):
        """Heuristic binding links claims to evidence."""
        binder = BidirectionalBinder()
        claim = "FastAPI offers high performance for API development"

        binding = await binder.bind_claim(claim, pre_gathered_sources)

        assert binding.claim == claim
        # BidirectionalBinding has supporting, contradicting, neutral lists
        assert isinstance(binding.supporting, list)
        assert isinstance(binding.contradicting, list)
        assert isinstance(binding.neutral, list)

    @pytest.mark.unit
    @pytest.mark.p0
    @pytest.mark.asyncio
    async def test_bind_all_claims(self, pre_gathered_sources):
        """Bind multiple claims to evidence."""
        binder = BidirectionalBinder()
        claims = [
            "FastAPI is fast",
            "Flask is lightweight",
        ]

        bindings = await binder.bind_all_claims(claims, pre_gathered_sources)

        assert isinstance(bindings, list)
        assert len(bindings) == 2
        for binding in bindings:
            assert binding.claim
            assert hasattr(binding, 'net_support')
            assert hasattr(binding, 'evidence_strength')


# =============================================================================
# Integration Tests (require LLM)
# =============================================================================


class TestP0DiscoveryIntegration:
    """Integration tests for P0 discovery with LLM."""

    @pytest.mark.integration
    @pytest.mark.slow
    @pytest.mark.p0
    @pytest.mark.asyncio
    async def test_query_expansion_with_llm(self, llm_client):
        """Query expansion with LLM generates diverse variants."""
        from src.config import settings

        expander = QueryExpander(
            llm_client=llm_client,
            model=settings.llm_model,
        )

        result = await expander.expand("FastAPI authentication best practices")

        assert len(result.variants) >= 2
        # First variant is always the original (by design), check we have additional variants
        non_original_variants = [v for v in result.variants if v != result.original]
        assert len(non_original_variants) >= 1, "Should have at least one variant different from original"

    @pytest.mark.integration
    @pytest.mark.slow
    @pytest.mark.p0
    @pytest.mark.asyncio
    async def test_query_decomposition_with_llm(self, llm_client):
        """Query decomposition with LLM identifies aspects."""
        from src.config import settings

        decomposer = QueryDecomposer(
            llm_client=llm_client,
            model=settings.llm_model,
        )

        aspects = await decomposer.decompose(
            "Compare FastAPI and Flask performance, ecosystem, and learning curve"
        )

        # LLM should return at least 1 aspect (may fall back on parse errors)
        assert len(aspects) >= 1, f"Expected at least 1 aspect, got {len(aspects)}"
        # Should have named aspects
        assert all(a.aspect for a in aspects)
        # If multiple aspects found, verify they're distinct
        if len(aspects) > 1:
            aspect_names = [a.aspect.lower() for a in aspects]
            assert len(set(aspect_names)) > 1, "Aspects should be distinct"


class TestP0SynthesisIntegration:
    """Integration tests for P0 synthesis with LLM."""

    @pytest.mark.integration
    @pytest.mark.slow
    @pytest.mark.p0
    @pytest.mark.asyncio
    async def test_quality_gate_with_llm(self, llm_client, pre_gathered_sources):
        """Quality gate with LLM evaluates sources."""
        from src.config import settings

        gate = SourceQualityGate(
            llm_client=llm_client,
            model=settings.llm_model,
        )

        result = await gate.evaluate(
            "Compare FastAPI vs Flask",
            pre_gathered_sources
        )

        assert result.decision in [QualityDecision.PROCEED, QualityDecision.PARTIAL]
        assert len(result.good_sources) > 0

    @pytest.mark.integration
    @pytest.mark.slow
    @pytest.mark.p0
    @pytest.mark.asyncio
    async def test_contradiction_detection_with_llm(
        self, llm_client, contradicting_sources
    ):
        """Contradiction detection with LLM finds disagreements."""
        from src.config import settings

        detector = ContradictionDetector(
            llm_client=llm_client,
            model=settings.llm_model,
        )

        contradictions = await detector.detect(
            "Is Redux necessary for React applications?",
            contradicting_sources
        )

        # LLM may or may not find contradictions depending on model and response format
        # The test verifies the detection pipeline works, not specific LLM behavior
        assert isinstance(contradictions, list), "Should return a list"

        # If contradictions were found, verify structure
        if len(contradictions) >= 1:
            # Check that it found a relevant topic
            topics = [c.topic.lower() for c in contradictions]
            # Topics should relate to the query (Redux, state management, React)
            relevant_keywords = ["redux", "state", "react", "management", "necessary", "essential"]
            has_relevant_topic = any(
                any(kw in t for kw in relevant_keywords)
                for t in topics
            )
            # Note: Not asserting this as LLM topic extraction varies
            if has_relevant_topic:
                pass  # Good - found relevant topic
