"""FastAPI routes for research tool."""

import hashlib
import re
from fastapi import APIRouter, HTTPException, Header
from typing import Annotated
from ..llm_client import get_llm_client, OpenRouterClient
from ..llm_utils import get_llm_content
from ..cache import cache
from .schemas import (
    # Existing
    SearchRequest,
    SearchResponse,
    ResearchRequest,
    ResearchResponse,
    HealthResponse,
    SourceSchema,
    CitationSchema,
    # Discovery
    DiscoverRequest,
    DiscoverResponse,
    KnowledgeGapSchema,
    KnowledgeLandscapeSchema,
    ScoredSourceSchema,
    # Synthesis
    SynthesizeRequest,
    SynthesizeResponse,
    PreGatheredSourceSchema,
    SynthesisAttributionSchema,
    # Reasoning
    ReasonRequest,
    ReasonResponse,
    # Conversation
    AskRequest,
    AskResponse,
    # P0 Enhancement schemas
    ContradictionSchema,
    QualityGateSchema,
    VerifiedClaimSchema,
    DiscoverRequestEnhanced,
    SynthesizeRequestEnhanced,
    SynthesizeResponseEnhanced,
    # P1 Enhancement schemas
    PresetInfoSchema,
    PresetListResponse,
    FocusModeInfoSchema,
    FocusModeListResponse,
    CritiqueSchema,
    ContextualSummarySchema,
    DiscoverRequestP1,
    SynthesizeRequestP1,
    SynthesizeResponseP1,
)
from ..synthesis import (
    SynthesisEngine,
    SynthesisAggregator,
    SynthesisStyle,
    PreGatheredSource,
    # P0 Enhancements
    SourceQualityGate,
    QualityDecision,
    ContradictionDetector,
    CitationVerifier,
    extract_claims_with_citations,
    # P1 Enhancements
    OutlineGuidedSynthesizer,
    RCSPreprocessor,
    get_preset,
    get_preset_by_enum,
    list_presets,
    PresetName,
)
from ..search import SearchAggregator
from ..discovery import (
    Explorer,
    # P0 Enhancements
    ConnectorRouter,
    QueryExpander,
    GapFiller,
    # P1 Enhancements
    FocusModeType,
    FocusModeSelector,
    FOCUS_MODES,
    get_focus_mode,
    get_gap_categories,
)
from ..config import settings

router = APIRouter()


# Type alias for optional OpenRouter API key header
OpenRouterApiKeyHeader = Annotated[str | None, Header(alias="X-OpenRouter-Api-Key")]


def _get_llm_client(api_key: str | None = None, header_api_key: str | None = None) -> OpenRouterClient:
    """Get OpenRouter client with optional per-request API key.

    Priority: request body api_key > X-OpenRouter-Api-Key header > server default
    """
    effective_key = api_key or header_api_key
    return get_llm_client(api_key=effective_key)


@router.get("/health", response_model=HealthResponse)
async def health_check():
    """Check service health and configuration."""
    aggregator = SearchAggregator()
    return HealthResponse(
        status="healthy",
        connectors=aggregator.get_active_connectors(),
        # `llm_configured` reflects whether a key is set, not just the base URL.
        # The base URL has a default of https://openrouter.ai/api/v1, which would
        # otherwise make this field always-true and useless as a readiness signal.
        llm_configured=bool(settings.llm_api_key),
    )


@router.post("/search", response_model=SearchResponse)
async def search(request: SearchRequest):
    """
    Execute multi-source search with RRF fusion.

    Returns aggregated and ranked results from configured connectors.
    """
    # Check cache
    cache_extra = f"top_k={request.top_k}"
    cached_result = cache.get(request.query, tier="search", extra=cache_extra)
    if cached_result:
        cached_result["_cached"] = True
        return SearchResponse(**cached_result)

    aggregator = SearchAggregator()

    if not aggregator.connectors:
        raise HTTPException(
            status_code=503,
            detail="No search connectors configured"
        )

    sources, raw_results = await aggregator.search(
        query=request.query,
        top_k=request.top_k,
        connectors=request.connectors,
    )

    response = SearchResponse(
        query=request.query,
        sources=[
            SourceSchema(
                id=s.id,
                title=s.title,
                url=s.url,
                content=s.content,
                score=s.score,
                connector=s.connector,
            )
            for s in sources
        ],
        connectors_used=list(raw_results.keys()),
        total_results=len(sources),
    )

    # Cache the response
    cache.set(request.query, response.model_dump(), tier="search", extra=cache_extra)
    return response


@router.post("/research", response_model=ResearchResponse)
async def research(
    request: ResearchRequest,
    x_openrouter_api_key: OpenRouterApiKeyHeader = None,  # placeholder; per-request override of env key
):
    """
    Perform full research: search + synthesis with citations.

    Multi-source search → CRAG-style quality gate → outline-guided synthesis →
    citation-aware response, all driven by the configured LLM (Tongyi DeepResearch
    by default, OpenAI-compatible endpoints supported).

    P1 Enhancements (when preset or focus_mode is specified):
    - preset: Use P1 synthesis with quality gate, RCS, contradictions
    - focus_mode: Optimize discovery for specific domains
    """
    aggregator = SearchAggregator()
    llm_client = _get_llm_client(request.api_key, x_openrouter_api_key)

    if not aggregator.connectors:
        raise HTTPException(
            status_code=503,
            detail="No search connectors configured"
        )

    # P1: Apply focus mode if specified
    focus_mode_used = None
    if request.focus_mode:
        focus_mode = get_focus_mode(FocusModeType(request.focus_mode))
        focus_mode_used = focus_mode.name

    # Step 1: Aggregate search results
    sources, raw_results = await aggregator.search(
        query=request.query,
        top_k=request.top_k,
        connectors=request.connectors,
    )

    if not sources:
        raise HTTPException(
            status_code=404,
            detail="No search results found"
        )

    # P1: Use enhanced synthesis when preset is specified
    if request.preset:
        preset = get_preset(request.preset)
        preset_used = preset.name

        # Convert search sources to PreGatheredSource format
        pre_gathered = [
            PreGatheredSource(
                origin=s.connector,
                url=s.url,
                title=s.title,
                content=s.content,
                source_type="article",
                metadata={"score": s.score},
            )
            for s in sources
        ]

        # Initialize P1 components
        quality_gate_result = None
        contradictions_list = []
        rcs_summaries_list = None
        sources_for_synthesis = pre_gathered

        # Quality Gate
        if preset.run_quality_gate:
            quality_gate = SourceQualityGate(
                llm_client=llm_client,
                model=settings.llm_model,
            )
            gate_result = await quality_gate.evaluate(request.query, pre_gathered)
            quality_gate_result = QualityGateSchema(
                decision=gate_result.decision.value,
                avg_quality=gate_result.avg_quality,
                passed_count=len(gate_result.good_sources),
                rejected_count=len(gate_result.rejected_sources),
                suggestion=gate_result.suggestion,
            )
            if gate_result.decision == QualityDecision.PARTIAL:
                sources_for_synthesis = gate_result.good_sources

        # RCS Preprocessing
        if preset.use_rcs and len(sources_for_synthesis) > 1:
            rcs = RCSPreprocessor(llm_client=llm_client, model=settings.llm_model)
            rcs_result = await rcs.prepare(
                query=request.query,
                sources=sources_for_synthesis,
                top_k=5,
            )
            rcs_summaries_list = [
                ContextualSummarySchema(
                    source_title=s.source.title,
                    source_url=s.source.url,
                    summary=s.summary,
                    relevance_score=s.relevance_score,
                    key_points=s.key_points,
                )
                for s in rcs_result.summaries
            ]
            sources_for_synthesis = [s.source for s in rcs_result.summaries]

        # Contradiction Detection
        if preset.detect_contradictions and len(sources_for_synthesis) >= 2:
            detector = ContradictionDetector(llm_client=llm_client, model=settings.llm_model)
            contradictions = await detector.detect(request.query, sources_for_synthesis)
            contradictions_list = [
                ContradictionSchema(
                    topic=c.topic,
                    position_a=c.position_a,
                    source_a=c.source_a,
                    position_b=c.position_b,
                    source_b=c.source_b,
                    severity=c.severity.value,
                    resolution_hint=c.resolution_hint,
                )
                for c in contradictions
            ]

        # Synthesis
        synth_aggregator = SynthesisAggregator(llm_client=llm_client, model=settings.llm_model)
        result = await synth_aggregator.synthesize(
            query=request.query,
            sources=sources_for_synthesis,
            style=preset.style,
            max_tokens=preset.max_tokens,
        )

        return ResearchResponse(
            query=request.query,
            content=result.content,
            citations=[
                CitationSchema(
                    id=str(c.get("number", "")),
                    title=c.get("title", ""),
                    url=c.get("url", ""),
                )
                for c in result.citations
            ],
            sources=[
                SourceSchema(
                    id=s.id, title=s.title, url=s.url,
                    content=s.content, score=s.score, connector=s.connector,
                )
                for s in sources
            ],
            connectors_used=list(raw_results.keys()),
            model=settings.llm_model,
            preset_used=preset_used,
            focus_mode_used=focus_mode_used,
            quality_gate=quality_gate_result,
            contradictions=contradictions_list,
            rcs_summaries=rcs_summaries_list,
        )

    # Standard synthesis (no preset)
    engine = SynthesisEngine(client=_get_llm_client(request.api_key, x_openrouter_api_key))
    result = await engine.research(
        query=request.query,
        sources=sources,
        reasoning_effort=request.reasoning_effort,
    )

    if "error" in result:
        raise HTTPException(
            status_code=500,
            detail=f"Synthesis error: {result['error']}"
        )

    return ResearchResponse(
        query=request.query,
        content=result["content"],
        citations=[
            CitationSchema(id=c["id"], title=c["title"], url=c["url"])
            for c in result["citations"]
        ],
        sources=[
            SourceSchema(
                id=s.id,
                title=s.title,
                url=s.url,
                content=s.content,
                score=s.score,
                connector=s.connector,
            )
            for s in sources
        ],
        connectors_used=list(raw_results.keys()),
        model=result.get("model"),
        usage=result.get("usage"),
        focus_mode_used=focus_mode_used,
    )


@router.post("/ask", response_model=AskResponse)
async def ask(
    request: AskRequest,
    x_openrouter_api_key: OpenRouterApiKeyHeader = None,  # placeholder; per-request override of env key
):
    """
    Quick conversational answer.

    Optimized for fast, concise responses (direct LLM call, no search hop).
    Mirrors the stdio MCP `ask` shape: no aggregator, no synthesis, just the
    LLM speaking from its own knowledge plus any optional `context` in the
    request body.
    """
    # Check cache
    cached_result = cache.get(request.query, tier="ask")
    if cached_result:
        cached_result["_cached"] = True
        return AskResponse(**cached_result)

    llm_client = _get_llm_client(request.api_key, x_openrouter_api_key)

    messages = []
    optional_context = getattr(request, "context", "") or ""
    if optional_context:
        messages.append({"role": "system", "content": f"Context: {optional_context}"})
    messages.append({"role": "user", "content": request.query})

    completion = await llm_client.chat.completions.create(
        model=settings.llm_model,
        messages=messages,
        temperature=settings.llm_temperature,
        max_tokens=settings.llm_max_tokens,
    )
    answer = get_llm_content(completion.choices[0].message)

    response = AskResponse(
        query=request.query,
        content=answer,
        citations=[],
        sources=[],
        model=settings.llm_model,
    )

    # Cache the response
    cache.set(request.query, response.model_dump(), tier="ask")
    return response


# =============================================================================
# Deep-research endpoints — discover / synthesize / reason / ask
# =============================================================================


@router.post("/discover", response_model=DiscoverResponse)
async def discover(
    request: DiscoverRequest | DiscoverRequestEnhanced,
    x_openrouter_api_key: OpenRouterApiKeyHeader = None,  # placeholder; per-request override of env key
):
    """
    Exploratory discovery with breadth expansion.

    Optimized for the EXPLORATORY workflow:
    1. Expand knowledge landscape (explicit, implicit, related, contrasting)
    2. Identify knowledge gaps in the query
    3. Score sources by gap coverage
    4. Recommend URLs for deep dives (Jina parallel_read)

    P0 Enhancements (when using DiscoverRequestEnhanced):
    - Query expansion via HyDE-style variant generation
    - Adaptive connector routing based on query type
    - Iterative gap-filling for coverage

    This sets the table for targeted research expansion.
    """
    # Check cache - include focus_mode and identify_gaps in key
    focus_mode = getattr(request, 'focus_mode', None)
    identify_gaps = getattr(request, 'identify_gaps', True)
    cache_extra = f"focus_mode={focus_mode}:identify_gaps={identify_gaps}"
    cached_result = cache.get(request.query, tier="discover", extra=cache_extra)
    if cached_result:
        cached_result["_cached"] = True
        return DiscoverResponse(**cached_result)

    aggregator = SearchAggregator()
    llm_client = _get_llm_client(request.api_key, x_openrouter_api_key)

    if not aggregator.connectors:
        raise HTTPException(
            status_code=503,
            detail="No search connectors configured"
        )

    # Initialize P0 Enhancement components based on request type
    router_component = None
    expander_component = None
    gap_filler_component = None

    # Check if using enhanced request with P0 options
    use_routing = getattr(request, 'use_adaptive_routing', True)
    fill_gaps = getattr(request, 'fill_gaps', True)

    if use_routing:
        router_component = ConnectorRouter()

    if request.expand_searches:
        expander_component = QueryExpander(
            llm_client=llm_client,
            model=settings.llm_model,
        )

    if fill_gaps:
        gap_filler_component = GapFiller(
            search_aggregator=aggregator,
        )

    explorer = Explorer(
        llm_client=llm_client,
        search_aggregator=aggregator,
        model=settings.llm_model,
        router=router_component,
        expander=expander_component,
        gap_filler=gap_filler_component,
    )

    try:
        result = await explorer.discover(
            query=request.query,
            top_k=request.top_k,
            expand_searches=request.expand_searches,
            fill_gaps=fill_gaps,
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Discovery error: {e}"
        )

    # Convert to response schemas
    response = DiscoverResponse(
        query=result.query,
        landscape=KnowledgeLandscapeSchema(
            explicit_topics=result.landscape.explicit_topics,
            implicit_topics=result.landscape.implicit_topics,
            related_concepts=result.landscape.related_concepts,
            contrasting_views=result.landscape.contrasting_views,
        ),
        knowledge_gaps=[
            KnowledgeGapSchema(
                gap=g.gap,
                description=g.description,
                importance=g.importance,
                suggested_search=g.suggested_search,
            )
            for g in result.knowledge_gaps
        ],
        sources=[
            ScoredSourceSchema(
                id=s.source.id,
                title=s.source.title,
                url=s.source.url,
                content=s.source.content or "",
                score=s.source.score,
                connector=s.source.connector,
                relevance_score=s.relevance_score,
                gaps_addressed=s.gaps_addressed,
                unique_value=s.unique_value,
                recommended_priority=s.recommended_priority,
            )
            for s in result.sources
        ],
        synthesis_preview=result.synthesis_preview,
        recommended_deep_dives=result.recommended_deep_dives,
        connectors_used=aggregator.get_active_connectors(),
    )

    # Cache the response
    cache.set(request.query, response.model_dump(), tier="discover", extra=cache_extra)
    return response


@router.post("/synthesize", response_model=SynthesizeResponse)
async def synthesize(
    request: SynthesizeRequest,
    x_openrouter_api_key: OpenRouterApiKeyHeader = None,  # placeholder; per-request override of env key
):
    """
    Pure synthesis of pre-gathered content.

    Optimized for the SYNTHESIS workflow:
    - Takes content already fetched by Ref/Exa/Jina
    - Weaves into coherent narrative with attribution
    - NO additional searching - pure aggregation

    This is the final step after Triple Stack research.
    """
    # Source-aware caching: hash source URLs to prevent false cache hits
    source_urls = sorted([s.url for s in request.sources])
    source_hash = hashlib.sha256(":".join(source_urls).encode()).hexdigest()[:8]
    cache_extra = f"style={request.style}:sources={source_hash}"
    cached_result = cache.get(request.query, tier="synthesis", extra=cache_extra)
    if cached_result:
        cached_result["_cached"] = True
        return SynthesizeResponse(**cached_result)

    llm_client = _get_llm_client(request.api_key, x_openrouter_api_key)
    aggregator = SynthesisAggregator(
        llm_client=llm_client,
        model=settings.llm_model,
    )

    # Convert request sources to internal format
    sources = [
        PreGatheredSource(
            origin=s.origin,
            url=s.url,
            title=s.title,
            content=s.content,
            source_type=s.source_type,
            metadata=s.metadata,
        )
        for s in request.sources
    ]

    # Map style string to enum
    style_map = {
        "comprehensive": SynthesisStyle.COMPREHENSIVE,
        "concise": SynthesisStyle.CONCISE,
        "comparative": SynthesisStyle.COMPARATIVE,
        "tutorial": SynthesisStyle.TUTORIAL,
        "academic": SynthesisStyle.ACADEMIC,
    }
    style = style_map.get(request.style, SynthesisStyle.COMPREHENSIVE)

    try:
        result = await aggregator.synthesize(
            query=request.query,
            sources=sources,
            style=style,
            max_tokens=request.max_tokens,
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Synthesis error: {e}"
        )

    response = SynthesizeResponse(
        query=request.query,
        content=result.content,
        citations=[
            CitationSchema(
                id=str(c.get("number", "")),
                title=c.get("title", ""),
                url=c.get("url", ""),
            )
            for c in result.citations
        ],
        source_attribution=[
            SynthesisAttributionSchema(origin=origin, contribution=contrib)
            for origin, contrib in result.source_attribution.items()
        ],
        confidence=result.confidence,
        style_used=result.style_used.value,
        word_count=result.word_count,
        model=settings.llm_model,
    )

    # Cache the response
    cache.set(request.query, response.model_dump(), tier="synthesis", extra=cache_extra)
    return response


@router.post("/reason", response_model=ReasonResponse)
async def reason(
    request: ReasonRequest,
    x_openrouter_api_key: OpenRouterApiKeyHeader = None,  # placeholder; per-request override of env key
):
    """
    Advanced reasoning with chain-of-thought.

    Same as /synthesize but with explicit reasoning process:
    1. Analyze key aspects of the query
    2. Map source relevance to each aspect
    3. Identify contradictions between sources
    4. Determine confident vs uncertain claims
    5. Synthesize with reasoning trace
    """
    # Source-aware caching with reasoning_depth
    source_urls = sorted([s.url for s in request.sources])
    source_hash = hashlib.sha256(":".join(source_urls).encode()).hexdigest()[:8]
    reasoning_depth = getattr(request, 'reasoning_depth', 'moderate')
    cache_extra = f"reasoning_depth={reasoning_depth}:sources={source_hash}"
    cached_result = cache.get(request.query, tier="reason", extra=cache_extra)
    if cached_result:
        cached_result["_cached"] = True
        return ReasonResponse(**cached_result)

    llm_client = _get_llm_client(request.api_key, x_openrouter_api_key)
    aggregator = SynthesisAggregator(
        llm_client=llm_client,
        model=settings.llm_model,
    )

    # Convert request sources to internal format
    sources = [
        PreGatheredSource(
            origin=s.origin,
            url=s.url,
            title=s.title,
            content=s.content,
            source_type=s.source_type,
            metadata=s.metadata,
        )
        for s in request.sources
    ]

    try:
        result = await aggregator.synthesize_with_reasoning(
            query=request.query,
            sources=sources,
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Reasoning error: {e}"
        )

    # `synthesize_with_reasoning` consumes the chain-of-thought inside its
    # prompt and extracts only the <synthesis> block into `result.content`,
    # so the reasoning trace is not echoed back. If the model fails to emit
    # the expected tags, the full raw response is returned in `result.content`
    # as a fallback. The `reasoning` field on the response stays None on this
    # path — it is reserved for future prompt configurations that explicitly
    # emit a separable trace. See ReasonResponse.reasoning docstring.
    reasoning = None

    response = ReasonResponse(
        query=request.query,
        content=result.content,
        reasoning=reasoning,
        citations=[
            CitationSchema(
                id=str(c.get("number", "")),
                title=c.get("title", ""),
                url=c.get("url", ""),
            )
            for c in result.citations
        ],
        source_attribution=[
            SynthesisAttributionSchema(origin=origin, contribution=contrib)
            for origin, contrib in result.source_attribution.items()
        ],
        confidence=result.confidence,
        word_count=result.word_count,
        model=settings.llm_model,
    )

    # Cache the response
    cache.set(request.query, response.model_dump(), tier="reason", extra=cache_extra)
    return response


# =============================================================================
# P0 Enhanced Endpoint
# =============================================================================


@router.post("/synthesize/enhanced", response_model=SynthesizeResponseEnhanced)
async def synthesize_enhanced(
    request: SynthesizeRequestEnhanced,
    x_openrouter_api_key: OpenRouterApiKeyHeader = None,  # placeholder; per-request override of env key
):
    """
    Enhanced synthesis with P0 reliability features.

    Adds to standard /synthesize:
    1. Source Quality Gate - Evaluate sources BEFORE synthesis (CRAG)
    2. Contradiction Detection - Surface source disagreements (PaperQA2)
    3. Citation Verification - NLI-verify claims against evidence (VeriCite)

    Use this endpoint when citation reliability is critical.
    """
    llm_client = _get_llm_client(request.api_key, x_openrouter_api_key)

    # Convert request sources to internal format
    sources = [
        PreGatheredSource(
            origin=s.origin,
            url=s.url,
            title=s.title,
            content=s.content,
            source_type=s.source_type,
            metadata=s.metadata,
        )
        for s in request.sources
    ]

    quality_gate_result = None
    contradictions_list = []
    verified_claims_list = []
    sources_for_synthesis = sources

    # Step 1: Quality Gate (CRAG-style)
    if request.run_quality_gate:
        quality_gate = SourceQualityGate(
            llm_client=llm_client,
            model=settings.llm_model,
        )
        gate_result = await quality_gate.evaluate(request.query, sources)

        quality_gate_result = QualityGateSchema(
            decision=gate_result.decision.value,
            avg_quality=gate_result.avg_quality,
            passed_count=len(gate_result.good_sources),
            rejected_count=len(gate_result.rejected_sources),
            suggestion=gate_result.suggestion,
        )

        if gate_result.decision == QualityDecision.REJECT:
            # Return early with rejection
            return SynthesizeResponseEnhanced(
                query=request.query,
                content=f"Source quality insufficient. {gate_result.suggestion or 'Try gathering more relevant sources.'}",
                citations=[],
                source_attribution=[],
                confidence=0.0,
                style_used=request.style,
                word_count=0,
                model=settings.llm_model,
                quality_gate=quality_gate_result,
                contradictions=[],
                verified_claims=[],
            )
        elif gate_result.decision == QualityDecision.PARTIAL:
            # Use only good sources
            sources_for_synthesis = gate_result.good_sources

    # Step 2: Contradiction Detection (PaperQA2-style)
    if request.detect_contradictions and len(sources_for_synthesis) >= 2:
        detector = ContradictionDetector(
            llm_client=llm_client,
            model=settings.llm_model,
        )
        contradictions = await detector.detect(request.query, sources_for_synthesis)

        contradictions_list = [
            ContradictionSchema(
                topic=c.topic,
                position_a=c.position_a,
                source_a=c.source_a,
                position_b=c.position_b,
                source_b=c.source_b,
                severity=c.severity.value,
                resolution_hint=c.resolution_hint,
            )
            for c in contradictions
        ]

        # Inject contradiction awareness into synthesis
        contradiction_context = detector.format_for_synthesis(contradictions)
    else:
        contradiction_context = ""

    # Step 3: Synthesize with contradiction awareness
    aggregator = SynthesisAggregator(
        llm_client=llm_client,
        model=settings.llm_model,
    )

    style_map = {
        "comprehensive": SynthesisStyle.COMPREHENSIVE,
        "concise": SynthesisStyle.CONCISE,
        "comparative": SynthesisStyle.COMPARATIVE,
        "tutorial": SynthesisStyle.TUTORIAL,
        "academic": SynthesisStyle.ACADEMIC,
    }
    style = style_map.get(request.style, SynthesisStyle.COMPREHENSIVE)

    try:
        # If contradictions found, append to synthesis context
        if contradiction_context:
            # Inject contradiction awareness by temporarily modifying sources
            enhanced_sources = sources_for_synthesis.copy()
            if enhanced_sources:
                # Add contradiction context to first source for LLM awareness
                first = enhanced_sources[0]
                enhanced_sources[0] = PreGatheredSource(
                    origin=first.origin,
                    url=first.url,
                    title=first.title,
                    content=f"{first.content}\n\n{contradiction_context}",
                    source_type=first.source_type,
                    metadata=first.metadata,
                )
            result = await aggregator.synthesize(
                query=request.query,
                sources=enhanced_sources,
                style=style,
                max_tokens=request.max_tokens,
            )
        else:
            result = await aggregator.synthesize(
                query=request.query,
                sources=sources_for_synthesis,
                style=style,
                max_tokens=request.max_tokens,
            )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Enhanced synthesis error: {e}"
        )

    # Step 4: Citation Verification (VeriCite-style, optional)
    if request.verify_citations and result.citations:
        verifier = CitationVerifier()
        claims_with_citations = extract_claims_with_citations(
            result.content,
            sources_for_synthesis,
        )

        for claim, evidence, source_num in claims_with_citations[:10]:  # Limit to first 10
            verified = verifier.verify(claim, evidence, source_num)
            verified_claims_list.append(VerifiedClaimSchema(
                claim=verified.claim,
                source_number=verified.source_number,
                label=verified.label,
                confidence=verified.confidence,
            ))

    return SynthesizeResponseEnhanced(
        query=request.query,
        content=result.content,
        citations=[
            CitationSchema(
                id=str(c.get("number", "")),
                title=c.get("title", ""),
                url=c.get("url", ""),
            )
            for c in result.citations
        ],
        source_attribution=[
            SynthesisAttributionSchema(origin=origin, contribution=contrib)
            for origin, contrib in result.source_attribution.items()
        ],
        confidence=result.confidence,
        style_used=result.style_used.value,
        word_count=result.word_count,
        model=settings.llm_model,
        quality_gate=quality_gate_result,
        contradictions=contradictions_list,
        verified_claims=verified_claims_list,
    )


# =============================================================================
# P1 Enhancement Endpoints
# =============================================================================


@router.get("/presets", response_model=PresetListResponse)
async def get_presets():
    """
    List available synthesis presets.

    Presets are pre-configured settings bundles (PaperQA2-inspired):
    - comprehensive: Full analysis with all verification steps
    - fast: Quick synthesis, skip verification for speed
    - contracrow: Optimized for finding contradictions
    - academic: Scholarly synthesis with rigorous citations
    - tutorial: Step-by-step guide format
    """
    presets = list_presets()
    return PresetListResponse(
        presets=[
            PresetInfoSchema(
                name=p["name"],
                value=p["value"],
                description=p["description"],
                style=p["style"],
                max_tokens=p["max_tokens"],
            )
            for p in presets
        ]
    )


@router.get("/focus-modes", response_model=FocusModeListResponse)
async def get_focus_modes():
    """
    List available focus modes for discovery.

    Focus modes are domain-specific configurations:
    - general: Broad technical questions
    - academic: Research papers, scientific studies
    - documentation: Library/framework docs, API references
    - comparison: X vs Y evaluations
    - debugging: Error messages, bug investigation
    - tutorial: How-to guides, step-by-step learning
    - news: Recent events, announcements
    """
    modes = []
    for mode_type, mode in FOCUS_MODES.items():
        modes.append(FocusModeInfoSchema(
            name=mode.name,
            value=mode_type.value,
            description=mode.description,
            search_expansion=mode.search_expansion,
            gap_categories=mode.gap_categories,
        ))
    return FocusModeListResponse(modes=modes)


@router.post("/synthesize/p1", response_model=SynthesizeResponseP1)
async def synthesize_p1(
    request: SynthesizeRequestP1,
    x_openrouter_api_key: OpenRouterApiKeyHeader = None,  # placeholder; per-request override of env key
):
    """
    P1 enhanced synthesis with presets, outline-guided synthesis, and RCS.

    New features over /synthesize/enhanced:
    1. Preset-driven configuration (comprehensive, fast, contracrow, academic, tutorial)
    2. Outline-guided synthesis (SciRAG plan-critique-refine cycle)
    3. RCS contextual summarization (PaperQA2-style source ranking)

    Use preset=None to manually configure individual options.
    """
    llm_client = _get_llm_client(request.api_key, x_openrouter_api_key)

    # Convert request sources to internal format
    sources = [
        PreGatheredSource(
            origin=s.origin,
            url=s.url,
            title=s.title,
            content=s.content,
            source_type=s.source_type,
            metadata=s.metadata,
        )
        for s in request.sources
    ]

    # Determine configuration from preset or individual options
    preset_used = None
    if request.preset:
        preset = get_preset(request.preset)
        preset_used = preset.name
        use_outline = preset.use_outline
        use_rcs = preset.use_rcs
        run_quality_gate = preset.run_quality_gate
        detect_contradictions = preset.detect_contradictions
        verify_citations = request.verify_citations  # Always from request
        max_tokens = preset.max_tokens
        style_str = preset.style.value
    else:
        use_outline = request.use_outline
        use_rcs = request.use_rcs
        run_quality_gate = request.run_quality_gate
        detect_contradictions = request.detect_contradictions
        verify_citations = request.verify_citations
        max_tokens = request.max_tokens
        style_str = request.style

    # Map style string to enum
    style_map = {
        "comprehensive": SynthesisStyle.COMPREHENSIVE,
        "concise": SynthesisStyle.CONCISE,
        "comparative": SynthesisStyle.COMPARATIVE,
        "tutorial": SynthesisStyle.TUTORIAL,
        "academic": SynthesisStyle.ACADEMIC,
    }
    style = style_map.get(style_str, SynthesisStyle.COMPREHENSIVE)

    quality_gate_result = None
    contradictions_list = []
    verified_claims_list = []
    rcs_summaries_list = None
    sources_filtered = None
    outline_sections = None
    sections_dict = None
    critique_result = None
    sources_for_synthesis = sources

    # Step 1: Quality Gate (CRAG-style)
    if run_quality_gate:
        quality_gate = SourceQualityGate(
            llm_client=llm_client,
            model=settings.llm_model,
        )
        gate_result = await quality_gate.evaluate(request.query, sources)

        quality_gate_result = QualityGateSchema(
            decision=gate_result.decision.value,
            avg_quality=gate_result.avg_quality,
            passed_count=len(gate_result.good_sources),
            rejected_count=len(gate_result.rejected_sources),
            suggestion=gate_result.suggestion,
        )

        if gate_result.decision == QualityDecision.REJECT:
            return SynthesizeResponseP1(
                query=request.query,
                content=f"Source quality insufficient. {gate_result.suggestion or 'Try gathering more relevant sources.'}",
                citations=[],
                source_attribution=[],
                confidence=0.0,
                style_used=style_str,
                word_count=0,
                model=settings.llm_model,
                quality_gate=quality_gate_result,
                preset_used=preset_used,
            )
        elif gate_result.decision == QualityDecision.PARTIAL:
            sources_for_synthesis = gate_result.good_sources

    # Step 2: RCS Contextual Summarization (PaperQA2-style)
    if use_rcs and len(sources_for_synthesis) > 1:
        rcs = RCSPreprocessor(
            llm_client=llm_client,
            model=settings.llm_model,
        )
        rcs_result = await rcs.prepare(
            query=request.query,
            sources=sources_for_synthesis,
            top_k=request.rcs_top_k,
        )

        sources_filtered = rcs_result.total_sources - rcs_result.kept_sources
        rcs_summaries_list = [
            ContextualSummarySchema(
                source_title=s.source.title,
                source_url=s.source.url,
                summary=s.summary,
                relevance_score=s.relevance_score,
                key_points=s.key_points,
            )
            for s in rcs_result.summaries
        ]

        # Use top-ranked sources for synthesis
        sources_for_synthesis = [s.source for s in rcs_result.summaries]

    # Step 3: Contradiction Detection (PaperQA2-style)
    contradiction_context = ""
    if detect_contradictions and len(sources_for_synthesis) >= 2:
        detector = ContradictionDetector(
            llm_client=llm_client,
            model=settings.llm_model,
        )
        contradictions = await detector.detect(request.query, sources_for_synthesis)

        contradictions_list = [
            ContradictionSchema(
                topic=c.topic,
                position_a=c.position_a,
                source_a=c.source_a,
                position_b=c.position_b,
                source_b=c.source_b,
                severity=c.severity.value,
                resolution_hint=c.resolution_hint,
            )
            for c in contradictions
        ]

        contradiction_context = detector.format_for_synthesis(contradictions)

    # Step 4: Synthesis (standard or outline-guided)
    if use_outline:
        # Outline-guided synthesis (SciRAG)
        synthesizer = OutlineGuidedSynthesizer(
            llm_client=llm_client,
            model=settings.llm_model,
        )
        outlined_result = await synthesizer.synthesize(
            query=request.query,
            sources=sources_for_synthesis,
            style=style,
            max_tokens_per_section=max_tokens // 4,
        )

        content = outlined_result.content
        outline_sections = outlined_result.outline.sections
        sections_dict = outlined_result.sections
        word_count = outlined_result.word_count

        if outlined_result.critique:
            critique_result = CritiqueSchema(
                issues=outlined_result.critique.issues,
                has_critical=outlined_result.critique.has_critical,
            )

        # Generate citations from content
        citations = _extract_citations_from_content(content, sources_for_synthesis)
        source_attribution = _compute_attribution(content, sources_for_synthesis)
        confidence = 0.8 if not outlined_result.critique or not outlined_result.critique.has_critical else 0.6

    else:
        # Standard synthesis
        aggregator = SynthesisAggregator(
            llm_client=llm_client,
            model=settings.llm_model,
        )

        # Inject contradiction awareness if present
        if contradiction_context:
            enhanced_sources = sources_for_synthesis.copy()
            if enhanced_sources:
                first = enhanced_sources[0]
                enhanced_sources[0] = PreGatheredSource(
                    origin=first.origin,
                    url=first.url,
                    title=first.title,
                    content=f"{first.content}\n\n{contradiction_context}",
                    source_type=first.source_type,
                    metadata=first.metadata,
                )
            result = await aggregator.synthesize(
                query=request.query,
                sources=enhanced_sources,
                style=style,
                max_tokens=max_tokens,
            )
        else:
            result = await aggregator.synthesize(
                query=request.query,
                sources=sources_for_synthesis,
                style=style,
                max_tokens=max_tokens,
            )

        content = result.content
        word_count = result.word_count
        citations = [
            CitationSchema(
                id=str(c.get("number", "")),
                title=c.get("title", ""),
                url=c.get("url", ""),
            )
            for c in result.citations
        ]
        source_attribution = [
            SynthesisAttributionSchema(origin=origin, contribution=contrib)
            for origin, contrib in result.source_attribution.items()
        ]
        confidence = result.confidence

    # Step 5: Citation Verification (VeriCite-style, optional)
    if verify_citations and citations:
        verifier = CitationVerifier()
        claims_with_citations = extract_claims_with_citations(
            content,
            sources_for_synthesis,
        )

        for claim, evidence, source_num in claims_with_citations[:10]:
            verified = verifier.verify(claim, evidence, source_num)
            verified_claims_list.append(VerifiedClaimSchema(
                claim=verified.claim,
                source_number=verified.source_number,
                label=verified.label,
                confidence=verified.confidence,
            ))

    return SynthesizeResponseP1(
        query=request.query,
        content=content,
        citations=citations,
        source_attribution=source_attribution,
        confidence=confidence,
        style_used=style_str,
        word_count=word_count,
        model=settings.llm_model,
        quality_gate=quality_gate_result,
        contradictions=contradictions_list,
        verified_claims=verified_claims_list,
        preset_used=preset_used,
        outline=outline_sections,
        sections=sections_dict,
        critique=critique_result,
        rcs_summaries=rcs_summaries_list,
        sources_filtered=sources_filtered,
    )


def _extract_citations_from_content(
    content: str,
    sources: list[PreGatheredSource],
) -> list[CitationSchema]:
    """Extract citation references from content."""
    citations = []
    seen = set()

    # Find all [N] patterns
    pattern = r"\[(\d+)\]"
    for match in re.finditer(pattern, content):
        num = int(match.group(1))
        if num not in seen and 1 <= num <= len(sources):
            seen.add(num)
            source = sources[num - 1]
            citations.append(CitationSchema(
                id=str(num),
                title=source.title,
                url=source.url,
            ))

    return citations


def _compute_attribution(
    content: str,
    sources: list[PreGatheredSource],
) -> list[SynthesisAttributionSchema]:
    """Compute source attribution breakdown."""
    # Count citation mentions per origin
    origin_counts: dict[str, int] = {}
    pattern = r"\[(\d+)\]"

    for match in re.finditer(pattern, content):
        num = int(match.group(1))
        if 1 <= num <= len(sources):
            origin = sources[num - 1].origin
            origin_counts[origin] = origin_counts.get(origin, 0) + 1

    total = sum(origin_counts.values()) or 1
    return [
        SynthesisAttributionSchema(
            origin=origin,
            contribution=count / total,
        )
        for origin, count in origin_counts.items()
    ]
