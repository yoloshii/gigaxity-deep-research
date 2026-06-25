"""Pydantic schemas for API requests and responses."""

from pydantic import BaseModel, Field
from typing import Literal


# =============================================================================
# Base Search Schemas (existing)
# =============================================================================


class SearchRequest(BaseModel):
    """Request for multi-source search."""

    query: str = Field(..., description="Search query")
    top_k: int = Field(default=10, ge=1, le=50, description="Results per source")
    connectors: list[str] | None = Field(
        default=None,
        description="Specific connectors to use (searxng, tavily, linkup)"
    )


class SourceSchema(BaseModel):
    """Source document schema."""

    id: str
    title: str
    url: str
    content: str
    score: float
    connector: str


class SearchResponse(BaseModel):
    """Response from search endpoint."""

    query: str
    sources: list[SourceSchema]
    connectors_used: list[str]
    total_results: int


class ResearchRequest(BaseModel):
    """Request for full research with synthesis."""

    query: str = Field(..., description="Research query")
    top_k: int = Field(default=10, ge=1, le=50, description="Results per source")
    connectors: list[str] | None = Field(
        default=None,
        description="Specific connectors to use"
    )
    reasoning_effort: Literal["low", "medium", "high"] = Field(
        default="medium",
        description="Depth of analysis"
    )
    # P1 Enhancement options
    preset: Literal[
        "comprehensive", "fast", "contracrow", "academic", "tutorial"
    ] | None = Field(
        default=None,
        description="Synthesis preset (enables P1 features when set)"
    )
    focus_mode: Literal[
        "general", "academic", "documentation", "comparison", "debugging", "tutorial", "news"
    ] | None = Field(
        default=None,
        description="Discovery focus mode for query optimization"
    )
    gate_focus: str | None = Field(
        default=None,
        description="Optional focus the pre-synthesis relevance gate scores sources against instead of the full query (Q2 precision lever). Distinct from focus_mode (discovery). Omitted/None/whitespace uses the full query."
    )
    api_key: str | None = Field(
        default=None,
        description="OpenRouter API key for this request. Uses server default if not provided."
    )


class CitationSchema(BaseModel):
    """Citation reference (canonical shape, codex DESIGN session 019e39f7, Q2).

    `id` stays string-typed for backward-compatible schema (no `id: str` →
    `id: int` break), but its value is the 1-based citation number as a
    string (`"1"`, `"2"`, ...) in v0.3.0. The `[xx_<hex>]` style that the
    pre-v0.3.0 `SynthesisEngine` path used is gone — clients that parse
    `citation.id` as a connector prefix (`tv_...`) must read `source_id`
    instead.
    """

    id: str
    number: int = 0
    source_id: str | None = None
    title: str
    url: str


class ResearchResponse(BaseModel):
    """Response from research endpoint."""

    query: str
    content: str
    citations: list[CitationSchema]
    sources: list[SourceSchema]
    connectors_used: list[str]
    model: str | None = None
    usage: dict | None = None
    # P1 Enhancement fields (populated when preset is used)
    preset_used: str | None = None
    focus_mode_used: str | None = None
    quality_gate: "QualityGateSchema | None" = None
    contradictions: "list[ContradictionSchema]" = []
    rcs_summaries: "list[ContextualSummarySchema] | None" = None


class HealthResponse(BaseModel):
    """Health check response."""

    status: str
    connectors: list[str]
    llm_configured: bool


# =============================================================================
# Discovery schemas (EXPLORATORY workflow)
# =============================================================================


class DiscoverRequest(BaseModel):
    """Request for exploratory discovery with breadth expansion."""

    query: str = Field(..., description="Research query")
    top_k: int = Field(default=15, ge=1, le=50, description="Number of sources")
    expand_searches: bool = Field(
        default=True,
        description="Expand to related concepts for breadth"
    )
    connectors: list[str] | None = Field(
        default=None,
        description="Specific connectors to use"
    )
    api_key: str | None = Field(
        default=None,
        description="OpenRouter API key for this request. Uses server default if not provided."
    )


class KnowledgeGapSchema(BaseModel):
    """A knowledge gap identified in the query."""

    gap: str = Field(..., description="Gap name")
    description: str = Field(..., description="Why this gap matters")
    importance: str = Field(..., description="high, medium, or low")
    suggested_search: str | None = Field(
        default=None,
        description="Query to fill this gap"
    )


class KnowledgeLandscapeSchema(BaseModel):
    """Expanded knowledge space around a query."""

    explicit_topics: list[str] = Field(
        default_factory=list,
        description="Topics directly mentioned"
    )
    implicit_topics: list[str] = Field(
        default_factory=list,
        description="Topics implied but not stated"
    )
    related_concepts: list[str] = Field(
        default_factory=list,
        description="Adjacent concepts worth exploring"
    )
    contrasting_views: list[str] = Field(
        default_factory=list,
        description="Alternative perspectives"
    )


class ScoredSourceSchema(BaseModel):
    """A source scored against knowledge gaps."""

    id: str
    title: str
    url: str
    content: str
    score: float
    connector: str
    relevance_score: float = Field(..., description="Gap-adjusted score")
    gaps_addressed: list[str] = Field(default_factory=list)
    unique_value: str = Field(default="")
    recommended_priority: int = Field(
        default=2,
        description="1=fetch first, 2=if time, 3=optional"
    )


class DiscoverResponse(BaseModel):
    """Response from discovery endpoint (EXPLORATORY workflow)."""

    query: str
    landscape: KnowledgeLandscapeSchema
    knowledge_gaps: list[KnowledgeGapSchema]
    sources: list[ScoredSourceSchema]
    synthesis_preview: str = Field(
        ...,
        description="Brief overview for context"
    )
    recommended_deep_dives: list[str] = Field(
        default_factory=list,
        description="URLs worth fetching with Jina parallel_read"
    )
    connectors_used: list[str]


# =============================================================================
# Synthesis schemas (SYNTHESIS workflow)
# =============================================================================


class PreGatheredSourceSchema(BaseModel):
    """A source pre-fetched by Context7/Exa/Jina."""

    origin: str = Field(..., description="context7, exa, jina, or custom")
    url: str
    title: str
    content: str = Field(..., description="Full content already fetched")
    source_type: str = Field(
        default="article",
        description="documentation, code, article, etc."
    )
    metadata: dict = Field(default_factory=dict)


class SynthesizeRequest(BaseModel):
    """Request for pure synthesis of pre-gathered content."""

    query: str = Field(..., description="Original research query")
    sources: list[PreGatheredSourceSchema] = Field(
        ...,
        description="Pre-gathered sources from Context7/Exa/Jina"
    )
    style: Literal[
        "comprehensive", "concise", "comparative", "tutorial", "academic"
    ] = Field(
        default="comprehensive",
        description="Synthesis style"
    )
    max_tokens: int = Field(default=3000, ge=500, le=16384)
    api_key: str | None = Field(
        default=None,
        description="OpenRouter API key for this request. Uses server default if not provided."
    )


class SynthesisAttributionSchema(BaseModel):
    """Source attribution breakdown."""

    origin: str
    contribution: float = Field(..., description="Contribution percentage")


class VerdictWarningSchema(BaseModel):
    """Structured advisory warning emitted alongside the verdict.

    Phase 0 leaves this list empty on the wire. Phases 5a / 5b / 6 populate
    it with coverage-grid BM25 mismatches, structural-coverage warnings, and
    tier-insufficient signals respectively.
    """

    code: str
    message: str
    severity: Literal["info", "warning"] = "warning"


class VerdictDiagnosticsSchema(BaseModel):
    """Structured diagnostics produced by the verifier.

    Field-granular dict slots so future phases can populate them independently
    without bumping a schema version. Phase 0 leaves all slots empty.
    """

    gate_diagnostics: dict | None = None
    tier_composition: dict | None = None
    gap_declarations: list[str] = Field(default_factory=list)
    contracrow_result: dict | None = None
    coverage_grid_summary: dict | None = None
    bm25_mismatch_info: dict | None = None


class RetryAdviceSchema(BaseModel):
    """Surface-aware retry advice emitted on hard-failure (Phase 6).

    Phase 0 always emits None on the wire. Phase 6 populates this when the
    verifier can recommend a caller action.
    """

    caller_action: Literal[
        "gather_more_sources", "resynthesize_same_sources", "abort"
    ]
    missing_entities: list[str] = Field(default_factory=list)
    missing_aspects: list[tuple[str, str]] = Field(default_factory=list)
    suggested_queries: list[str] = Field(default_factory=list)
    rationale: str = ""


class SynthesisVerdictSchema(BaseModel):
    """Post-synthesis verification verdict.

    `passed` is False when there is a blocking (hard) failure - the content
    must not be treated as a reliable synthesis. `soft_warnings` are advisory.

    `verdict_class`, `failure_codes`, `warnings`, `diagnostics`, and
    `retry_advice` are the Phase 0 envelope additions (mirror the
    `SynthesisVerdict` dataclass). Backward-compat defaults so existing
    clients that read only `passed` / `hard_failures` / `soft_warnings`
    observe no change.
    """

    passed: bool
    hard_failures: list[str] = Field(default_factory=list)
    soft_warnings: list[str] = Field(default_factory=list)
    verdict_class: Literal["pass", "calibrated_gap", "hard_fail"] = "pass"
    failure_codes: list[str] = Field(default_factory=list)
    warnings: list[VerdictWarningSchema] = Field(default_factory=list)
    diagnostics: VerdictDiagnosticsSchema = Field(default_factory=VerdictDiagnosticsSchema)
    retry_advice: RetryAdviceSchema | None = None


class SynthesizeResponse(BaseModel):
    """Response from synthesis endpoint (SYNTHESIS workflow)."""

    query: str
    content: str = Field(..., description="Synthesized narrative")
    citations: list[CitationSchema]
    source_attribution: list[SynthesisAttributionSchema] = Field(
        default_factory=list,
        description="Contribution breakdown by origin"
    )
    confidence: float = Field(..., ge=0.0, le=1.0)
    style_used: str
    word_count: int
    model: str | None = None
    usage: dict | None = None
    verification: SynthesisVerdictSchema | None = None


# =============================================================================
# Reasoning schemas (deep reasoning with optional CoT depth)
# =============================================================================


class ReasonRequest(BaseModel):
    """Request for reasoning with chain-of-thought.

    `reason` does not accept a style: the chain-of-thought prompt is fixed
    because the reasoning shape is what matters here, not the prose register.
    Use `/api/v1/synthesize` if you need style variants.
    """

    query: str = Field(..., description="Research query")
    sources: list[PreGatheredSourceSchema] = Field(
        ...,
        description="Pre-gathered sources"
    )
    api_key: str | None = Field(
        default=None,
        description="OpenRouter API key for this request. Uses server default if not provided."
    )


class ReasonResponse(BaseModel):
    """Response from reasoning endpoint (deep reasoning workflow)."""

    query: str
    content: str = Field(..., description="Final synthesis")
    reasoning: str | None = Field(
        default=None,
        description="Reserved for prompt configurations that explicitly emit a separable chain-of-thought trace. The current `reason` path consumes the chain-of-thought inside its prompt and returns only the synthesized answer in `content`; this field is `null` on that path. May become populated in future prompt configurations that surface a separate trace."
    )
    citations: list[CitationSchema]
    source_attribution: list[SynthesisAttributionSchema]
    confidence: float
    word_count: int
    model: str | None = None
    usage: dict | None = None
    verification: SynthesisVerdictSchema | None = None


# =============================================================================
# Conversation schemas (quick conversational answers, no search hop)
# =============================================================================


class AskRequest(BaseModel):
    """Request for quick conversational answer (direct LLM, no search hop)."""

    query: str = Field(..., description="Question to answer")
    context: str = Field(default="", description="Optional system-context string fed to the LLM")
    api_key: str | None = Field(
        default=None,
        description="OpenRouter API key for this request. Uses server default if not provided."
    )


class AskResponse(BaseModel):
    """Response from ask endpoint (quick conversational answer)."""

    query: str
    content: str = Field(..., description="Concise answer")
    citations: list[CitationSchema]
    sources: list[SourceSchema]
    model: str | None = None


# =============================================================================
# P0 Enhancement Schemas
# =============================================================================


class ContradictionSchema(BaseModel):
    """A detected contradiction between sources."""

    topic: str = Field(..., description="What sources disagree about")
    position_a: str
    source_a: int
    position_b: str
    source_b: int
    severity: str = Field(..., description="minor, moderate, or major")
    resolution_hint: str = Field(default="")


class RejectedSourceSchema(BaseModel):
    """A source the relevance gate set aside (C5 never-vaporize provenance).

    Surfaced so the sources dropped on the happy path (PARTIAL/PASS) are
    recoverable, not just counted. `score` is null when the scorer path did
    not align per-source scores with the rejected set.
    """

    title: str | None = None
    url: str | None = None
    score: float | None = None
    reason: str | None = None


class QualityGateSchema(BaseModel):
    """Result of source quality evaluation."""

    decision: str = Field(..., description="proceed, reject, or partial")
    avg_quality: float
    passed_count: int
    rejected_count: int
    suggestion: str | None = None
    # Scorer provenance (Q3 observability) — diagnose REJECT/PARTIAL outcomes
    # without re-running. scorer_path distinguishes a confident LLM-scored
    # decision from one derived from the degraded keyword heuristic.
    scorer_path: str | None = Field(
        default=None,
        description="llm, llm_fallback_heuristic, or heuristic_only",
    )
    fallback_reason: str | None = Field(
        default=None,
        description="Why the heuristic scorer fired (only set on the fallback path)",
    )
    source_scores: list[float] | None = Field(
        default=None,
        description="Per-source relevance scores (rounded), in source order",
    )
    reject_threshold: float | None = None
    pass_threshold: float | None = None
    gate_degraded: bool = Field(
        default=False,
        description="True when the LLM relevance scorer failed and the degraded keyword heuristic produced these scores (scorer_path == llm_fallback_heuristic)",
    )
    gate_focus: str | None = Field(
        default=None,
        description="The caller-supplied focus the relevance gate scored sources against instead of the full query (Q2); null when no focus was applied",
    )
    rejected_sources: list[RejectedSourceSchema] | None = Field(
        default=None,
        description="Identity + score + reason for each source the gate set aside, so the dropped sources are recoverable rather than only counted (C5 never-vaporize)",
    )


class VerifiedClaimSchema(BaseModel):
    """A verified claim with NLI results."""

    claim: str
    source_number: int
    label: str = Field(..., description="supported, contradicted, or neutral")
    confidence: float


class DiscoverRequestEnhanced(BaseModel):
    """Enhanced discover request with P0 options."""

    query: str = Field(..., description="Research query")
    top_k: int = Field(default=15, ge=1, le=50, description="Number of sources")
    expand_searches: bool = Field(default=True, description="Expand to related concepts")
    fill_gaps: bool = Field(default=True, description="Auto-search for knowledge gaps")
    use_adaptive_routing: bool = Field(default=True, description="Route to optimal connectors")
    connectors: list[str] | None = Field(default=None)
    api_key: str | None = Field(
        default=None,
        description="OpenRouter API key for this request. Uses server default if not provided."
    )


class SynthesizeRequestEnhanced(BaseModel):
    """Enhanced synthesis request with P0 options."""

    query: str = Field(..., description="Original research query")
    sources: list[PreGatheredSourceSchema] = Field(
        ...,
        description="Pre-gathered sources from Context7/Exa/Jina"
    )
    style: Literal[
        "comprehensive", "concise", "comparative", "tutorial", "academic"
    ] = Field(default="comprehensive")
    max_tokens: int = Field(default=3000, ge=500, le=16384)
    # P0 Enhancement options
    run_quality_gate: bool = Field(default=True, description="Evaluate source quality first")
    gate_focus: str | None = Field(
        default=None,
        description="Optional focus the relevance gate scores sources against instead of the full query (Q2 precision lever). Omitted/None/whitespace uses the full query."
    )
    detect_contradictions: bool = Field(default=True, description="Surface source contradictions")
    verify_citations: bool = Field(default=False, description="NLI verify citations (slower)")
    api_key: str | None = Field(
        default=None,
        description="OpenRouter API key for this request. Uses server default if not provided."
    )


class SynthesizeResponseEnhanced(BaseModel):
    """Enhanced synthesis response with P0 fields."""

    query: str
    content: str = Field(..., description="Synthesized narrative")
    citations: list[CitationSchema]
    source_attribution: list[SynthesisAttributionSchema] = Field(default_factory=list)
    confidence: float = Field(..., ge=0.0, le=1.0)
    style_used: str
    word_count: int
    model: str | None = None
    usage: dict | None = None
    # P0 Enhancement fields
    quality_gate: QualityGateSchema | None = None
    contradictions: list[ContradictionSchema] = Field(default_factory=list)
    verified_claims: list[VerifiedClaimSchema] = Field(default_factory=list)
    verification: SynthesisVerdictSchema | None = None


# =============================================================================
# P1 Enhancement Schemas
# =============================================================================


class PresetInfoSchema(BaseModel):
    """Summary info for a synthesis preset."""

    name: str = Field(..., description="Preset display name")
    value: str = Field(..., description="Preset value to use in requests")
    description: str
    style: str
    max_tokens: int


class PresetListResponse(BaseModel):
    """Response listing available synthesis presets."""

    presets: list[PresetInfoSchema]


class FocusModeInfoSchema(BaseModel):
    """Summary info for a focus mode."""

    name: str
    value: str
    description: str
    search_expansion: bool
    gap_categories: list[str]


class FocusModeListResponse(BaseModel):
    """Response listing available focus modes."""

    modes: list[FocusModeInfoSchema]


class OutlineSectionSchema(BaseModel):
    """A section in an outlined synthesis."""

    title: str
    content: str


class CritiqueSchema(BaseModel):
    """Critique of a synthesis draft."""

    issues: list[str]
    has_critical: bool


class ContextualSummarySchema(BaseModel):
    """A source summarized in context of the query."""

    source_title: str
    source_url: str
    summary: str
    relevance_score: float
    key_points: list[str]


class DiscoverRequestP1(BaseModel):
    """P1 enhanced discover request with focus mode."""

    query: str = Field(..., description="Research query")
    top_k: int = Field(default=15, ge=1, le=50, description="Number of sources")
    expand_searches: bool = Field(default=True, description="Expand to related concepts")
    fill_gaps: bool = Field(default=True, description="Auto-search for knowledge gaps")
    use_adaptive_routing: bool = Field(default=True, description="Route to optimal connectors")
    connectors: list[str] | None = Field(default=None)
    # P1: Focus Mode
    focus_mode: Literal[
        "general", "academic", "documentation", "comparison", "debugging", "tutorial", "news"
    ] | None = Field(
        default=None,
        description="Domain-specific mode (auto-detected if not provided)"
    )
    api_key: str | None = Field(
        default=None,
        description="OpenRouter API key for this request. Uses server default if not provided."
    )


class SynthesizeRequestP1(BaseModel):
    """P1 enhanced synthesis request with presets and outline."""

    query: str = Field(..., description="Original research query")
    sources: list[PreGatheredSourceSchema] = Field(
        ...,
        description="Pre-gathered sources from Context7/Exa/Jina"
    )
    # P1: Preset-driven configuration
    preset: Literal[
        "comprehensive", "fast", "contracrow", "academic", "tutorial"
    ] | None = Field(
        default=None,
        description="Use preset configuration (overrides individual options)"
    )
    # Individual options (used when preset is None)
    style: Literal[
        "comprehensive", "concise", "comparative", "tutorial", "academic"
    ] = Field(default="comprehensive")
    max_tokens: int = Field(default=3000, ge=500, le=16384)
    # P1: Outline-guided synthesis
    use_outline: bool = Field(
        default=False,
        description="Use SciRAG outline-guided synthesis"
    )
    # P1: Contextual summarization
    use_rcs: bool = Field(
        default=False,
        description="Use PaperQA2-style contextual summarization"
    )
    rcs_top_k: int = Field(
        default=5,
        ge=1,
        le=20,
        description="Top sources to keep after RCS ranking"
    )
    # P0 options (inherited)
    run_quality_gate: bool = Field(default=True, description="Evaluate source quality first")
    gate_focus: str | None = Field(
        default=None,
        description="Optional focus the relevance gate scores sources against instead of the full query (Q2 precision lever). Omitted/None/whitespace uses the full query."
    )
    detect_contradictions: bool = Field(default=True, description="Surface source contradictions")
    verify_citations: bool = Field(default=False, description="NLI verify citations (slower)")
    api_key: str | None = Field(
        default=None,
        description="OpenRouter API key for this request. Uses server default if not provided."
    )


class SynthesizeResponseP1(BaseModel):
    """P1 enhanced synthesis response with outline and RCS info."""

    query: str
    content: str = Field(..., description="Synthesized narrative")
    citations: list[CitationSchema]
    source_attribution: list[SynthesisAttributionSchema] = Field(default_factory=list)
    confidence: float = Field(..., ge=0.0, le=1.0)
    style_used: str
    word_count: int
    model: str | None = None
    usage: dict | None = None
    # P0 Enhancement fields
    quality_gate: QualityGateSchema | None = None
    contradictions: list[ContradictionSchema] = Field(default_factory=list)
    verified_claims: list[VerifiedClaimSchema] = Field(default_factory=list)
    verification: SynthesisVerdictSchema | None = None
    # P1 Enhancement fields
    preset_used: str | None = None
    outline: list[str] | None = Field(
        default=None,
        description="Section headings if outline-guided"
    )
    sections: dict[str, str] | None = Field(
        default=None,
        description="Section contents if outline-guided"
    )
    critique: CritiqueSchema | None = None
    rcs_summaries: list[ContextualSummarySchema] | None = Field(
        default=None,
        description="Contextual summaries if RCS was used"
    )
    sources_filtered: int | None = Field(
        default=None,
        description="Sources removed by RCS filtering"
    )


# Rebuild models with forward references
ResearchResponse.model_rebuild()


def verdict_to_schema(verdict) -> SynthesisVerdictSchema:
    """Convert a `SynthesisVerdict` dataclass into its REST schema form.

    Phase 0 envelope: pass through `passed`, `hard_failures`, `soft_warnings`
    (existing), plus `verdict_class`, `failure_codes`, `warnings`,
    `diagnostics`, `retry_advice` (new). All 4 REST routes that emit a
    structured `verification=` block route through this helper so the
    Pydantic shape stays in lockstep with the dataclass shape.

    Imported lazily to avoid coupling schemas.py to the synthesis package
    at module-load time (schemas.py is also imported by mcp_server.py and
    we want the API-side dependency graph to stay one-way).
    """
    return SynthesisVerdictSchema(
        passed=verdict.passed,
        hard_failures=list(verdict.hard_failures),
        soft_warnings=list(verdict.soft_warnings),
        verdict_class=verdict.verdict_class,
        failure_codes=list(verdict.failure_codes),
        warnings=[
            VerdictWarningSchema(code=w.code, message=w.message, severity=w.severity)
            for w in verdict.warnings
        ],
        diagnostics=VerdictDiagnosticsSchema(
            gate_diagnostics=verdict.diagnostics.gate_diagnostics,
            tier_composition=verdict.diagnostics.tier_composition,
            gap_declarations=list(verdict.diagnostics.gap_declarations),
            contracrow_result=verdict.diagnostics.contracrow_result,
            coverage_grid_summary=verdict.diagnostics.coverage_grid_summary,
            bm25_mismatch_info=verdict.diagnostics.bm25_mismatch_info,
        ),
        retry_advice=(
            RetryAdviceSchema(
                caller_action=verdict.retry_advice.caller_action,
                missing_entities=list(verdict.retry_advice.missing_entities),
                missing_aspects=[tuple(a) for a in verdict.retry_advice.missing_aspects],
                suggested_queries=list(verdict.retry_advice.suggested_queries),
                rationale=verdict.retry_advice.rationale,
            )
            if verdict.retry_advice is not None
            else None
        ),
    )
