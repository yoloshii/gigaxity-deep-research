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
    api_key: str | None = Field(
        default=None,
        description="OpenRouter API key for this request. Uses server default if not provided."
    )


class CitationSchema(BaseModel):
    """Citation reference."""

    id: str
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
    """A source pre-fetched by Ref/Exa/Jina."""

    origin: str = Field(..., description="ref, exa, jina, or custom")
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
        description="Pre-gathered sources from Ref/Exa/Jina"
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


class QualityGateSchema(BaseModel):
    """Result of source quality evaluation."""

    decision: str = Field(..., description="proceed, reject, or partial")
    avg_quality: float
    passed_count: int
    rejected_count: int
    suggestion: str | None = None


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
        description="Pre-gathered sources from Ref/Exa/Jina"
    )
    style: Literal[
        "comprehensive", "concise", "comparative", "tutorial", "academic"
    ] = Field(default="comprehensive")
    max_tokens: int = Field(default=3000, ge=500, le=16384)
    # P0 Enhancement options
    run_quality_gate: bool = Field(default=True, description="Evaluate source quality first")
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
        description="Pre-gathered sources from Ref/Exa/Jina"
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
