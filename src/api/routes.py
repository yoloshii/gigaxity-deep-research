"""FastAPI routes for research tool."""

import re
from fastapi import APIRouter, HTTPException, Header
from typing import Annotated
from ..llm_client import get_llm_client, LLMClient
from ..llm_utils import get_llm_content, derive_effective_budget
from ..cache import cache, build_synthesis_cache_extra
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
    SynthesisVerdictSchema,
    verdict_to_schema,
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
    SynthesisStyle,
    PreGatheredSource,
    # P0 Enhancements
    SourceQualityGate,
    QualityDecision,
    ContradictionDetector,
    CitationVerifier,
    extract_claims_with_citations,
    # P1 Enhancements
    RCSPreprocessor,
    get_preset,
    get_preset_by_enum,
    list_presets,
    PresetName,
    # Phase 0 wrappers — the only public path to the three core synthesis
    # classes. AST audit at scripts/audit_synthesis_callers.py enforces no
    # direct import of SynthesisEngine / SynthesisAggregator /
    # OutlineGuidedSynthesizer in this module. The wrappers internally
    # call `verify_synthesis_output` + `annotate_with_verdict` so the
    # post-synthesis verification + safe_content annotation are uniform
    # across all routes regardless of branch (preset vs no-preset, outline
    # vs aggregator).
    SynthesisInvocationError,
    run_aggregator_synthesize,
    run_aggregator_synthesize_with_reasoning,
    run_engine_research,
    run_outline_synthesize,
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


# Type alias for optional per-request LLM API key header
LLMApiKeyHeader = Annotated[str | None, Header(alias="X-LLM-Api-Key")]


def _get_llm_client(api_key: str | None = None, header_api_key: str | None = None) -> LLMClient:
    """Get LLM client with optional per-request API key.

    Priority: request body api_key > X-LLM-Api-Key header > server default
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
        # The base URL has a default of http://localhost:8000/v1, which would
        # otherwise make this field always-true and useless as a readiness signal.
        # Local servers without auth still need the user to set a placeholder key
        # so this signal stays meaningful.
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
    x_llm_api_key: LLMApiKeyHeader = None,  # placeholder; per-request override of env key
):
    """
    Perform full research: search + synthesis with citations.

    Multi-source search → CRAG-style quality gate → outline-guided synthesis →
    citation-aware response, all driven by the configured LLM (Qwen3-30B-A3B-Thinking
    by default, any OpenAI-compatible endpoint supported).

    P1 Enhancements (when preset or focus_mode is specified):
    - preset: Use P1 synthesis with quality gate, RCS, contradictions
    - focus_mode: Optimize discovery for specific domains
    """
    aggregator = SearchAggregator()
    llm_client = _get_llm_client(request.api_key, x_llm_api_key)

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

        # Quality Gate — per-preset thresholds + entity-balanced safety net
        # match the MCP `synthesize` path (mcp_server.py).
        if preset.run_quality_gate:
            quality_gate = SourceQualityGate(
                llm_client=llm_client,
                model=settings.llm_model,
                reject_threshold=preset.quality_gate_reject_threshold,
                pass_threshold=preset.quality_gate_pass_threshold,
                entity_balanced=preset.quality_gate_entity_balanced,
            )
            gate_result = await quality_gate.evaluate(request.query, pre_gathered, gate_focus=request.gate_focus)
            quality_gate_result = QualityGateSchema(
                decision=gate_result.decision.value,
                avg_quality=gate_result.avg_quality,
                passed_count=len(gate_result.good_sources),
                rejected_count=len(gate_result.rejected_sources),
                suggestion=gate_result.suggestion,
                scorer_path=gate_result.scorer_path,
                fallback_reason=gate_result.fallback_reason,
                source_scores=[round(s, 3) for s in (gate_result.source_scores or [])] or None,
                reject_threshold=quality_gate.reject_threshold,
                pass_threshold=quality_gate.pass_threshold,
                gate_degraded=gate_result.gate_degraded,
                gate_focus=gate_result.gate_focus,
            )

            # REJECT and PARTIAL-with-zero-good must short-circuit before
            # synthesis — matches the v0.2.0 fix at /synthesize/p1
            # (routes.py:1193-1228) and the MCP `synthesize` path. Prior to
            # v0.2.2 this endpoint silently fell through, running synthesis
            # over the same sources the gate had just rejected. v0.2.2 codex
            # Turn 7 item 4.
            if gate_result.decision == QualityDecision.REJECT:
                return ResearchResponse(
                    query=request.query,
                    content=(
                        f"Source quality insufficient. "
                        f"{gate_result.suggestion or 'Try gathering more relevant sources.'}"
                    ),
                    citations=[],
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
                    contradictions=[],
                    rcs_summaries=None,
                )
            if gate_result.decision == QualityDecision.PARTIAL:
                if not gate_result.good_sources:
                    return ResearchResponse(
                        query=request.query,
                        content=(
                            f"Source quality insufficient (PARTIAL, zero passed). "
                            f"avg relevance {gate_result.avg_quality:.2f} above "
                            f"the REJECT floor but no source cleared the PASS "
                            f"threshold. "
                            f"{gate_result.suggestion or 'Try gathering more relevant sources.'}"
                        ),
                        citations=[],
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
                        contradictions=[],
                        rcs_summaries=None,
                    )
                sources_for_synthesis = gate_result.good_sources

        # RCS Preprocessing (guidance-only: summaries become advisory guidance
        # passed alongside the full sources, never a replacement for them)
        rcs_guidance = None
        if preset.use_rcs and len(sources_for_synthesis) > 1:
            rcs = RCSPreprocessor(llm_client=llm_client, model=settings.llm_model)
            rcs_result = await rcs.prepare(
                query=request.query,
                sources=sources_for_synthesis,
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
            rcs_guidance = [s.summary for s in rcs_result.summaries]

        # Contradiction Detection. `detection` is hoisted so it can be passed
        # through to the verifier via `contradiction_result=` on the wrapper
        # call below — Phase 0 surface-parity fix for the previously-missing
        # verification call on this path.
        detection = None
        if preset.detect_contradictions and len(sources_for_synthesis) >= 2:
            detector = ContradictionDetector(llm_client=llm_client, model=settings.llm_model)
            detection = await detector.detect(request.query, sources_for_synthesis)
            contradictions = detection.contradictions
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

        # Phase 0: route through the aggregator wrapper so post-synthesis
        # verification runs over this surface. Prior to Phase 0 the
        # preset-driven `/research` path skipped `verify_synthesis_output`
        # entirely — uncited hallucinations could be returned as the canonical
        # answer. The wrapper threads query_entities + sources_text +
        # contradiction_result into the verifier per the locked design;
        # `finalized.safe_content` is annotated in-band on hard-failure so a
        # client that ignores structured response fields still sees the
        # failure header.
        try:
            finalized = await run_aggregator_synthesize(
                llm_client=llm_client,
                model=settings.llm_model,
                query=request.query,
                sources=sources_for_synthesis,
                style=preset.style,
                max_tokens=preset.max_tokens,
                guidance=rcs_guidance,
                contradiction_result=detection,
                surface="rest_research_preset",
            )
        except SynthesisInvocationError as e:
            raise HTTPException(status_code=500, detail=f"Synthesis error: {e}")

        return ResearchResponse(
            query=request.query,
            content=finalized.safe_content,
            citations=[
                CitationSchema(
                    id=str(c.get("number", "")),
                    number=c.get("number", 0),
                    source_id=c.get("source_id"),
                    title=c.get("title", ""),
                    url=c.get("url", ""),
                )
                for c in finalized.citations
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

    # Standard synthesis (no preset). Phase 0: route through the engine
    # wrapper so the verifier sees query_entities + sources_text on this
    # path — prior to Phase 0 the verifier ran with both at None, which
    # silently no-op'd the entity-coverage check for the no-preset
    # `/research` surface. The wrapper raises SynthesisInvocationError on
    # an engine `{"error": ...}` invocation failure (same shape that
    # previously matched the `if "error" in result` branch); regular
    # exceptions still propagate to the route's HTTPException converter.
    try:
        finalized = await run_engine_research(
            client=_get_llm_client(request.api_key, x_llm_api_key),
            model=settings.llm_model,
            query=request.query,
            sources=sources,
            reasoning_effort=request.reasoning_effort,
            surface="rest_research_no_preset",
        )
    except SynthesisInvocationError as e:
        raise HTTPException(status_code=500, detail=f"Synthesis error: {e}")

    return ResearchResponse(
        query=request.query,
        content=finalized.safe_content,
        citations=[
            CitationSchema(
                id=c["id"],
                number=c["number"],
                source_id=c.get("source_id"),
                title=c["title"],
                url=c["url"],
            )
            for c in finalized.citations
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
        model=finalized.extras.get("model"),
        usage=finalized.extras.get("usage"),
        focus_mode_used=focus_mode_used,
    )


@router.post("/ask", response_model=AskResponse)
async def ask(
    request: AskRequest,
    x_llm_api_key: LLMApiKeyHeader = None,  # placeholder; per-request override of env key
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

    llm_client = _get_llm_client(request.api_key, x_llm_api_key)

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
    x_llm_api_key: LLMApiKeyHeader = None,  # placeholder; per-request override of env key
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
    llm_client = _get_llm_client(request.api_key, x_llm_api_key)

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
    x_llm_api_key: LLMApiKeyHeader = None,  # placeholder; per-request override of env key
):
    """
    Pure synthesis of pre-gathered content.

    Optimized for the SYNTHESIS workflow:
    - Takes content already fetched by Context7/Exa/Jina
    - Weaves into coherent narrative with attribution
    - NO additional searching - pure aggregation

    This is the final step after Triple Stack research.
    """
    # Source-aware caching: fingerprint source content in input order (citations
    # bind to input order) plus model + effective budget + version, so a reorder
    # or a content/model/budget change never returns a stale or mis-bound result.
    effective_max_tokens = derive_effective_budget(request.max_tokens, settings.llm_model)
    cache_extra = build_synthesis_cache_extra(
        request.sources,
        model=settings.llm_model,
        max_tokens=effective_max_tokens,
        mode=f"style={request.style}",
    )
    cached_result = cache.get(request.query, tier="synthesis", extra=cache_extra)
    if cached_result:
        cached_result["_cached"] = True
        return SynthesizeResponse(**cached_result)

    llm_client = _get_llm_client(request.api_key, x_llm_api_key)

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

    # Phase 0: route through the aggregator wrapper. The wrapper internally
    # runs `verify_synthesis_output` with query_entities + sources_text +
    # contradiction_result=None, returns a `FinalizedSynthesis` with
    # safe_content already annotated in-band on hard-failure (mirroring
    # the MCP synthesize path). Surface parity preserved.
    try:
        finalized = await run_aggregator_synthesize(
            llm_client=llm_client,
            model=settings.llm_model,
            query=request.query,
            sources=sources,
            style=style,
            max_tokens=request.max_tokens,
            surface="rest_synthesize",
        )
    except SynthesisInvocationError as e:
        raise HTTPException(status_code=500, detail=f"Synthesis error: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Synthesis error: {e}")

    verdict = finalized.verdict
    response = SynthesizeResponse(
        query=request.query,
        content=finalized.safe_content,
        citations=[
            CitationSchema(
                id=str(c.get("number", "")),
                number=c.get("number", 0),
                source_id=c.get("source_id"),
                title=c.get("title", ""),
                url=c.get("url", ""),
            )
            for c in finalized.citations
        ],
        source_attribution=[
            SynthesisAttributionSchema(origin=origin, contribution=contrib)
            for origin, contrib in finalized.source_attribution.items()
        ],
        confidence=finalized.confidence,
        style_used=(finalized.style_used.value if finalized.style_used else style.value),
        word_count=finalized.word_count,
        model=settings.llm_model,
        verification=verdict_to_schema(verdict),
    )

    # Cache only a verified synthesis - never cache a hard-gated failure.
    if finalized.cache_eligible:
        cache.set(request.query, response.model_dump(), tier="synthesis", extra=cache_extra)
    return response


@router.post("/reason", response_model=ReasonResponse)
async def reason(
    request: ReasonRequest,
    x_llm_api_key: LLMApiKeyHeader = None,  # placeholder; per-request override of env key
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
    # Source-aware caching: fingerprint source content in input order plus model
    # + effective budget + version. 4000 is synthesize_with_reasoning's
    # answer-budget base default; the route uses that default.
    reasoning_depth = getattr(request, 'reasoning_depth', 'moderate')
    effective_max_tokens = derive_effective_budget(4000, settings.llm_model)
    cache_extra = build_synthesis_cache_extra(
        request.sources,
        model=settings.llm_model,
        max_tokens=effective_max_tokens,
        mode=f"reasoning_depth={reasoning_depth}",
    )
    cached_result = cache.get(request.query, tier="reason", extra=cache_extra)
    if cached_result:
        cached_result["_cached"] = True
        return ReasonResponse(**cached_result)

    llm_client = _get_llm_client(request.api_key, x_llm_api_key)

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

    # Phase 0: route through the reasoning wrapper. Same wrapper machinery
    # as /synthesize but invokes `synthesize_with_reasoning` (which extracts
    # only the <synthesis> block from the chain-of-thought — a missing
    # <synthesis> tag returns "" which the verifier hard-fails). The
    # `reasoning` field is reserved for future prompt configurations that
    # emit a separable trace; current contract leaves it None.
    try:
        finalized = await run_aggregator_synthesize_with_reasoning(
            llm_client=llm_client,
            model=settings.llm_model,
            query=request.query,
            sources=sources,
            surface="rest_reason",
        )
    except SynthesisInvocationError as e:
        raise HTTPException(status_code=500, detail=f"Reasoning error: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Reasoning error: {e}")

    verdict = finalized.verdict
    reasoning = None

    response = ReasonResponse(
        query=request.query,
        content=finalized.safe_content,
        reasoning=reasoning,
        citations=[
            CitationSchema(
                id=str(c.get("number", "")),
                number=c.get("number", 0),
                source_id=c.get("source_id"),
                title=c.get("title", ""),
                url=c.get("url", ""),
            )
            for c in finalized.citations
        ],
        source_attribution=[
            SynthesisAttributionSchema(origin=origin, contribution=contrib)
            for origin, contrib in finalized.source_attribution.items()
        ],
        confidence=finalized.confidence,
        word_count=finalized.word_count,
        model=settings.llm_model,
        verification=verdict_to_schema(verdict),
    )

    # Cache only a verified reasoning result — never cache a hard-gated failure.
    if finalized.cache_eligible:
        cache.set(request.query, response.model_dump(), tier="reason", extra=cache_extra)
    return response


# =============================================================================
# P0 Enhanced Endpoint
# =============================================================================


@router.post("/synthesize/enhanced", response_model=SynthesizeResponseEnhanced)
async def synthesize_enhanced(
    request: SynthesizeRequestEnhanced,
    x_llm_api_key: LLMApiKeyHeader = None,  # placeholder; per-request override of env key
):
    """
    Enhanced synthesis with P0 reliability features.

    Adds to standard /synthesize:
    1. Source Quality Gate - Evaluate sources BEFORE synthesis (CRAG)
    2. Contradiction Detection - Surface source disagreements (PaperQA2)
    3. Citation Verification - NLI-verify claims against evidence (VeriCite)

    Use this endpoint when citation reliability is critical.
    """
    llm_client = _get_llm_client(request.api_key, x_llm_api_key)

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
        gate_result = await quality_gate.evaluate(request.query, sources, gate_focus=request.gate_focus)

        quality_gate_result = QualityGateSchema(
            decision=gate_result.decision.value,
            avg_quality=gate_result.avg_quality,
            passed_count=len(gate_result.good_sources),
            rejected_count=len(gate_result.rejected_sources),
            suggestion=gate_result.suggestion,
            scorer_path=gate_result.scorer_path,
            fallback_reason=gate_result.fallback_reason,
            source_scores=[round(s, 3) for s in (gate_result.source_scores or [])] or None,
            reject_threshold=quality_gate.reject_threshold,
            pass_threshold=quality_gate.pass_threshold,
            gate_degraded=gate_result.gate_degraded,
            gate_focus=gate_result.gate_focus,
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
            # PARTIAL-with-zero-good (Turn 3 codex T3F2): mirror MCP F6
            # early-return. Without this, assigning empty good_sources into
            # sources_for_synthesis lets synthesis run over zero sources —
            # same gate-bypass class as REJECT-doesn't-reject.
            if not gate_result.good_sources:
                return SynthesizeResponseEnhanced(
                    query=request.query,
                    content=(
                        f"Source quality insufficient (PARTIAL, zero passed). "
                        f"avg relevance {gate_result.avg_quality:.2f} above "
                        f"the REJECT floor but no source cleared the PASS "
                        f"threshold. "
                        f"{gate_result.suggestion or 'Try gathering more relevant sources.'}"
                    ),
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
            # Use only good sources
            sources_for_synthesis = gate_result.good_sources

    # Step 2: Contradiction Detection (PaperQA2-style)
    detection = None
    if request.detect_contradictions and len(sources_for_synthesis) >= 2:
        detector = ContradictionDetector(
            llm_client=llm_client,
            model=settings.llm_model,
        )
        detection = await detector.detect(request.query, sources_for_synthesis)
        contradictions = detection.contradictions

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

    style_map = {
        "comprehensive": SynthesisStyle.COMPREHENSIVE,
        "concise": SynthesisStyle.CONCISE,
        "comparative": SynthesisStyle.COMPARATIVE,
        "tutorial": SynthesisStyle.TUTORIAL,
        "academic": SynthesisStyle.ACADEMIC,
    }
    style = style_map.get(request.style, SynthesisStyle.COMPREHENSIVE)

    # Phase 0: route through the aggregator wrapper. Contradiction guidance
    # rides as a separate advisory formatter section (never merged into a
    # source's content). The wrapper threads `contradiction_result=detection`
    # into the verifier so the contracrow soft warning surfaces when
    # detection ran.
    try:
        finalized = await run_aggregator_synthesize(
            llm_client=llm_client,
            model=settings.llm_model,
            query=request.query,
            sources=sources_for_synthesis,
            style=style,
            max_tokens=request.max_tokens,
            contradiction_notes=contradiction_context or None,
            contradiction_result=detection,
            surface="rest_synthesize_enhanced",
        )
    except SynthesisInvocationError as e:
        raise HTTPException(status_code=500, detail=f"Enhanced synthesis error: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Enhanced synthesis error: {e}")

    verdict = finalized.verdict

    # Step 4: Citation Verification (VeriCite-style, optional)
    if request.verify_citations and finalized.citations:
        verifier = CitationVerifier()
        claims_with_citations = extract_claims_with_citations(
            finalized.raw_content,
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
        content=finalized.safe_content,
        citations=[
            CitationSchema(
                id=str(c.get("number", "")),
                number=c.get("number", 0),
                source_id=c.get("source_id"),
                title=c.get("title", ""),
                url=c.get("url", ""),
            )
            for c in finalized.citations
        ],
        source_attribution=[
            SynthesisAttributionSchema(origin=origin, contribution=contrib)
            for origin, contrib in finalized.source_attribution.items()
        ],
        confidence=finalized.confidence,
        style_used=(finalized.style_used.value if finalized.style_used else style.value),
        word_count=finalized.word_count,
        model=settings.llm_model,
        quality_gate=quality_gate_result,
        contradictions=contradictions_list,
        verified_claims=verified_claims_list,
        verification=verdict_to_schema(verdict),
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
    x_llm_api_key: LLMApiKeyHeader = None,  # placeholder; per-request override of env key
):
    """
    P1 enhanced synthesis with presets, outline-guided synthesis, and RCS.

    New features over /synthesize/enhanced:
    1. Preset-driven configuration (comprehensive, fast, contracrow, academic, tutorial)
    2. Outline-guided synthesis (SciRAG plan-critique-refine cycle)
    3. RCS contextual summarization (PaperQA2-style source ranking)

    Use preset=None to manually configure individual options.
    """
    llm_client = _get_llm_client(request.api_key, x_llm_api_key)

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

    # Step 1: Quality Gate (CRAG-style) — per-preset thresholds + entity-balanced
    # safety net when a preset is provided. Mirrors MCP `synthesize` path.
    if run_quality_gate:
        gate_kwargs = {}
        if request.preset:
            _gate_preset = get_preset(request.preset)
            gate_kwargs["reject_threshold"] = _gate_preset.quality_gate_reject_threshold
            gate_kwargs["pass_threshold"] = _gate_preset.quality_gate_pass_threshold
            gate_kwargs["entity_balanced"] = _gate_preset.quality_gate_entity_balanced
        quality_gate = SourceQualityGate(
            llm_client=llm_client,
            model=settings.llm_model,
            **gate_kwargs,
        )
        gate_result = await quality_gate.evaluate(request.query, sources, gate_focus=request.gate_focus)

        quality_gate_result = QualityGateSchema(
            decision=gate_result.decision.value,
            avg_quality=gate_result.avg_quality,
            passed_count=len(gate_result.good_sources),
            rejected_count=len(gate_result.rejected_sources),
            suggestion=gate_result.suggestion,
            scorer_path=gate_result.scorer_path,
            fallback_reason=gate_result.fallback_reason,
            source_scores=[round(s, 3) for s in (gate_result.source_scores or [])] or None,
            reject_threshold=quality_gate.reject_threshold,
            pass_threshold=quality_gate.pass_threshold,
            gate_degraded=gate_result.gate_degraded,
            gate_focus=gate_result.gate_focus,
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
            # PARTIAL-with-zero-good (Turn 3 codex T3F2): mirror MCP F6
            # early-return. Same gate-bypass class as REJECT-doesn't-reject
            # — synthesis must not run over zero sources.
            if not gate_result.good_sources:
                return SynthesizeResponseP1(
                    query=request.query,
                    content=(
                        f"Source quality insufficient (PARTIAL, zero passed). "
                        f"avg relevance {gate_result.avg_quality:.2f} above "
                        f"the REJECT floor but no source cleared the PASS "
                        f"threshold. "
                        f"{gate_result.suggestion or 'Try gathering more relevant sources.'}"
                    ),
                    citations=[],
                    source_attribution=[],
                    confidence=0.0,
                    style_used=style_str,
                    word_count=0,
                    model=settings.llm_model,
                    quality_gate=quality_gate_result,
                    preset_used=preset_used,
                )
            sources_for_synthesis = gate_result.good_sources

    # Step 2: RCS Contextual Summarization (PaperQA2-style)
    # Guidance-only: the contextual summaries become advisory guidance passed
    # alongside the full sources - they never replace or drop sources.
    rcs_guidance = None
    if use_rcs and len(sources_for_synthesis) > 1:
        rcs = RCSPreprocessor(
            llm_client=llm_client,
            model=settings.llm_model,
        )
        rcs_result = await rcs.prepare(
            query=request.query,
            sources=sources_for_synthesis,
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

        rcs_guidance = [s.summary for s in rcs_result.summaries]

    # Step 3: Contradiction Detection (PaperQA2-style)
    contradiction_context = ""
    detection = None
    if detect_contradictions and len(sources_for_synthesis) >= 2:
        detector = ContradictionDetector(
            llm_client=llm_client,
            model=settings.llm_model,
        )
        detection = await detector.detect(request.query, sources_for_synthesis)
        contradictions = detection.contradictions

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

    # Step 4: Synthesis (standard or outline-guided). Both branches route
    # through Phase 0 wrappers; the wrappers internally finalize_synthesis,
    # so the same verdict + safe_content semantics apply regardless of which
    # core synthesizer ran. Contradiction guidance + RCS guidance ride along
    # as separate advisory formatter sections — never merged into source content.
    try:
        if use_outline:
            finalized = await run_outline_synthesize(
                llm_client=llm_client,
                model=settings.llm_model,
                query=request.query,
                sources=sources_for_synthesis,
                style=style,
                max_tokens=max_tokens,
                guidance=rcs_guidance,
                contradiction_notes=contradiction_context or None,
                contradiction_result=detection,
                surface="rest_synthesize_p1",
            )
            outline_sections = finalized.extras.get("outline_sections", []) or None
            sections_dict = finalized.extras.get("sections", {}) or None
            _critique_obj = finalized.extras.get("critique")
            if _critique_obj is not None:
                critique_result = CritiqueSchema(
                    issues=_critique_obj.issues,
                    has_critical=_critique_obj.has_critical,
                )
            # Outline confidence heuristic preserved verbatim from the prior
            # /synthesize/p1 path: 0.8 baseline, 0.6 if critique surfaced
            # critical issues. The aggregator path's `finalized.confidence`
            # carries the aggregator's own composite score; only the outline
            # branch substitutes the heuristic.
            confidence = 0.8 if (_critique_obj is None or not _critique_obj.has_critical) else 0.6
        else:
            finalized = await run_aggregator_synthesize(
                llm_client=llm_client,
                model=settings.llm_model,
                query=request.query,
                sources=sources_for_synthesis,
                style=style,
                max_tokens=max_tokens,
                guidance=rcs_guidance,
                contradiction_notes=contradiction_context or None,
                contradiction_result=detection,
                surface="rest_synthesize_p1",
            )
            confidence = finalized.confidence
    except SynthesisInvocationError as e:
        raise HTTPException(status_code=500, detail=f"P1 synthesis error: {e}")

    verdict = finalized.verdict
    citations = [
        CitationSchema(
            id=str(c.get("number", "")),
            number=c.get("number", 0),
            source_id=c.get("source_id"),
            title=c.get("title", ""),
            url=c.get("url", ""),
        )
        for c in finalized.citations
    ]
    if use_outline:
        # The outline normalizer leaves source_attribution empty (no per-origin
        # counts at the outline layer). Preserve prior behavior of computing
        # attribution from raw content + sources for /synthesize/p1 outline.
        source_attribution = _compute_attribution(finalized.raw_content, sources_for_synthesis)
    else:
        source_attribution = [
            SynthesisAttributionSchema(origin=origin, contribution=contrib)
            for origin, contrib in finalized.source_attribution.items()
        ]
    word_count = finalized.word_count

    # Step 5: Citation Verification (VeriCite-style, optional). Uses
    # `finalized.raw_content` so claim-extraction sees the LLM's verbatim
    # output, not the (potentially) annotated `safe_content`.
    if verify_citations and citations:
        verifier = CitationVerifier()
        claims_with_citations = extract_claims_with_citations(
            finalized.raw_content,
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
        content=finalized.safe_content,
        citations=citations,
        source_attribution=source_attribution,
        confidence=confidence,
        style_used=style_str,
        word_count=word_count,
        model=settings.llm_model,
        quality_gate=quality_gate_result,
        contradictions=contradictions_list,
        verified_claims=verified_claims_list,
        verification=verdict_to_schema(verdict),
        preset_used=preset_used,
        outline=outline_sections,
        sections=sections_dict,
        critique=critique_result,
        rcs_summaries=rcs_summaries_list,
        sources_filtered=sources_filtered,
    )


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
