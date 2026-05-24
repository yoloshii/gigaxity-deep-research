"""Post-synthesis finalization (Phase 0).

Pure post-result normalization. Accepts the three result shapes that the five
core synthesis methods produce (`SynthesisEngine.synthesize` /
`SynthesisEngine.research` return a dict; `SynthesisAggregator.synthesize` /
`SynthesisAggregator.synthesize_with_reasoning` return `AggregatedSynthesis`;
`OutlineGuidedSynthesizer.synthesize` returns `OutlinedSynthesis`), normalizes
them into a single view, runs `verify_synthesis_output` on the normalized
content + provenance + citations, and emits a `FinalizedSynthesis` carrying the
raw + safe-annotated content, the verdict, a `cache_eligible` flag, and any
surface-specific extras (outline sections, critique, ...).

This module MUST NOT call any of the five core synthesis methods. It is a pure
post-result transform. The wrappers in `wrappers.py` are the only callers of
the core methods, and they are also the only callers of `finalize_synthesis`.
The AST audit script at `scripts/audit_synthesis_callers.py` enforces this
invariant.

The verdict schema is forward-compatible: the existing `hard_failures`,
`soft_warnings`, and `passed` shape is preserved verbatim, and new fields
(`verdict_class`, `failure_codes`, `warnings`, `diagnostics`, `retry_advice`)
default to empty / "pass" / None so a Phase 0 caller sees the same observable
behavior as before. Phases 1, 5a, 5b, and 6 populate the new fields with
their respective semantic content.
"""

from dataclasses import dataclass, field
from typing import Any, Literal, Optional

from ..llm_utils import LLMOutput
from .aggregator import AggregatedSynthesis, SynthesisStyle
from .citations import extract_numeric_citations
from .contradictions import ContradictionDetectionResult
from .output_verifier import (
    SynthesisVerdict,
    annotate_with_verdict,
    verify_synthesis_output,
)
from .outline import OutlinedSynthesis
from .quality_gate import extract_query_entities


# The 9 documented synthesis surfaces. The Literal is closed: adding a new
# surface here is a deliberate decision that needs review. Surface names map
# 1:1 to the user-facing endpoint or MCP tool name; preset/no-preset branches
# of the same surface share a name and are distinguished internally.
SurfaceName = Literal[
    "mcp_research",
    "mcp_synthesize",
    "mcp_reason",
    "rest_research_preset",
    "rest_research_no_preset",
    "rest_synthesize",
    "rest_reason",
    "rest_synthesize_enhanced",
    "rest_synthesize_p1",
]


@dataclass
class FinalizedSynthesis:
    """Normalized output of a synthesis call after post-synthesis verification.

    `raw_content` is the model's content verbatim; `safe_content` is identical
    when the verdict passed AND there are no soft warnings, otherwise it is
    the annotated form (`annotate_with_verdict` output). REST surfaces that
    embed a structured `verification` field still emit raw content alongside
    `safe_content` for the in-band fallback. `cache_eligible` mirrors
    `verdict.passed` — a hard-failed result must not be cached, period.

    `extras` carries surface-specific shape that the common normalization
    can't express directly: outline `sections` dict, outline `outline.sections`
    list, `critique` payload, `model`, `usage`, the engine's `sources_used`
    sub-list, etc. Callers reach for `extras[name]` when they need the
    surface-specific field; the common fields above are always populated.
    """
    raw_content: str
    safe_content: str
    citations: list[dict]
    source_attribution: dict[str, float]
    confidence: float
    word_count: int
    style_used: Optional[SynthesisStyle]
    llm_output: Optional[LLMOutput]
    verdict: SynthesisVerdict
    cache_eligible: bool
    surface: SurfaceName
    extras: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Internal normalizers — one per result shape.
# ---------------------------------------------------------------------------


def _normalize_engine_dict(
    result: dict,
    sources: list,
) -> tuple[
    str,                       # content
    list[dict],                # citations
    dict[str, float],          # source_attribution
    float,                     # confidence
    int,                       # word_count
    Optional[SynthesisStyle],  # style_used
    Optional[LLMOutput],       # llm_output
    dict[str, Any],            # extras
]:
    """Normalize a SynthesisEngine.{synthesize,research} dict result.

    The engine dict carries `content, citations, sources_used, model, usage`
    (and an `error` key on failure paths). The engine does NOT expose the
    `LLMOutput` provenance signal — it handles truncation-retry internally
    and consumes it. So `llm_output` is always None for engine results.

    `source_attribution` is empty for engine results: the engine is the
    legacy connector-search path; aggregator-style origin attribution is not
    computed there. `confidence` defaults to 0.0 for the same reason.
    """
    content = result.get("content", "") or ""
    citations = result.get("citations", []) or []
    word_count = len(content.split()) if content else 0
    extras: dict[str, Any] = {}
    if "model" in result:
        extras["model"] = result.get("model")
    if "usage" in result:
        extras["usage"] = result.get("usage")
    if "sources_used" in result:
        extras["sources_used"] = result.get("sources_used")
    if "error" in result:
        # `error` is the engine-layer exception text. We carry it through so
        # the verifier still sees the failure content (which it will hard-fail
        # via "synthesis cites none of N sources"). Wrappers may also raise
        # SynthesisInvocationError BEFORE finalize, in which case this path is
        # not reached — but the carry-through path is here for completeness so
        # a caller that opts to swallow the error sees a consistent shape.
        extras["error"] = result.get("error")
    return content, citations, {}, 0.0, word_count, None, None, extras


def _normalize_aggregated(
    result: AggregatedSynthesis,
    sources: list,
) -> tuple[
    str,
    list[dict],
    dict[str, float],
    float,
    int,
    Optional[SynthesisStyle],
    Optional[LLMOutput],
    dict[str, Any],
]:
    """Normalize a SynthesisAggregator.{synthesize,synthesize_with_reasoning} result."""
    return (
        result.content or "",
        list(result.citations),
        dict(result.source_attribution),
        result.confidence,
        result.word_count,
        result.style_used,
        result.llm_output,
        {},
    )


def _normalize_outlined(
    result: OutlinedSynthesis,
    sources: list,
) -> tuple[
    str,
    list[dict],
    dict[str, float],
    float,
    int,
    Optional[SynthesisStyle],
    Optional[LLMOutput],
    dict[str, Any],
]:
    """Normalize an OutlineGuidedSynthesizer.synthesize result.

    OutlinedSynthesis has no `citations` field of its own (the outline path
    composes its content from per-section LLM calls and the citations live
    inline in the content as `[N]` markers). The shared `[N]` extractor in
    citations.py parses them out using the same source-index mapping the
    aggregator uses — so MCP synthesize (preset+outline), REST /synthesize/p1
    (outline branch), and any future outline caller see citations bound to
    sources identically to the aggregator path. Without this normalization
    the outline path would hard-fail the verifier with "cites none of N
    sources" whenever the model emitted valid `[N]` markers (the parity bug
    REST /synthesize/p1 already fixed at routes.py:1413 and MCP synthesize
    already fixed at mcp_server.py:505).
    """
    content = result.content or ""
    citations = extract_numeric_citations(content, sources)
    word_count = result.word_count or (len(content.split()) if content else 0)
    extras: dict[str, Any] = {
        "outline_sections": list(result.outline.sections) if result.outline else [],
        "sections": dict(result.sections) if result.sections else {},
        "refined": result.refined,
    }
    if result.critique is not None:
        extras["critique"] = result.critique
    return content, citations, {}, 0.0, word_count, None, result.llm_output, extras


# ---------------------------------------------------------------------------
# Public API.
# ---------------------------------------------------------------------------


def _sources_text_lower(sources: list) -> str:
    """Compose the lowercased `title + content` corpus for entity coverage."""
    parts: list[str] = []
    for s in sources:
        title = getattr(s, "title", "") or ""
        content = getattr(s, "content", "") or ""
        parts.append((content + " " + title).lower())
    return " ".join(parts)


def finalize_synthesis(
    *,
    query: str,
    result: Any,
    sources: list,
    contradiction_result: Optional[ContradictionDetectionResult] = None,
    query_entities: Optional[list[str]] = None,
    surface: SurfaceName,
) -> FinalizedSynthesis:
    """Normalize a synthesis result and apply post-synthesis verification.

    Pure post-result. MUST NOT call any of the five core synthesis methods.

    Args:
        query: the original synthesis query. Used to (re)compute query_entities
            for the entity-coverage check when the caller did not pre-extract
            them.
        result: the value returned by ONE of the five core synthesis methods:
            * `dict` from `SynthesisEngine.synthesize` / `.research`
            * `AggregatedSynthesis` from `SynthesisAggregator.synthesize` /
              `.synthesize_with_reasoning`
            * `OutlinedSynthesis` from `OutlineGuidedSynthesizer.synthesize`
        sources: the source set the synthesis ran over. For the engine path
            these are `connectors.base.Source`; for the aggregator/outline
            paths they are `PreGatheredSource`. The verifier consumes them
            only via duck-typed attribute access (`.content`, `.title`) so the
            two shapes share one code path. POST-gate set when a gate filtered
            sources upstream (matches existing routes.py:1080 + routes.py:1474
            + mcp_server.py:561 conventions).
        contradiction_result: pass-through to the verifier for the
            contracrow signal. None when contradiction detection did not run
            on the path. The verifier surfaces this as a soft warning, never
            a hard failure (Phase 0 — Phase 4 unifies how this is rendered).
        query_entities: pre-extracted entity list. When None, finalize_synthesis
            extracts via `extract_query_entities(query)` itself. Pre-extraction
            is the recommended pattern for surfaces that need the entity list
            elsewhere (e.g. cache key, future Phase 5a coverage-grid keying)
            so the cost is paid once per request.
        surface: a closed enum identifying the caller. Tags the
            `FinalizedSynthesis` for downstream telemetry/logging.

    Returns:
        FinalizedSynthesis carrying raw + safe-annotated content, the
        verdict, and surface-specific extras.
    """
    if isinstance(result, AggregatedSynthesis):
        content, citations, attribution, confidence, word_count, style_used, llm_output, extras = (
            _normalize_aggregated(result, sources)
        )
    elif isinstance(result, OutlinedSynthesis):
        content, citations, attribution, confidence, word_count, style_used, llm_output, extras = (
            _normalize_outlined(result, sources)
        )
    elif isinstance(result, dict):
        content, citations, attribution, confidence, word_count, style_used, llm_output, extras = (
            _normalize_engine_dict(result, sources)
        )
    else:
        raise TypeError(
            f"finalize_synthesis: unsupported result type {type(result).__name__}. "
            "Expected AggregatedSynthesis, OutlinedSynthesis, or a SynthesisEngine "
            "dict result."
        )

    if query_entities is None:
        query_entities = extract_query_entities(query)
    sources_text = _sources_text_lower(sources)

    verdict = verify_synthesis_output(
        content=content,
        llm_output=llm_output,
        cited_count=len(citations),
        source_count=len(sources),
        contradiction_result=contradiction_result,
        query_entities=query_entities,
        sources_text=sources_text,
    )

    if verdict.passed and not verdict.soft_warnings:
        safe_content = content
    else:
        safe_content = annotate_with_verdict(content, verdict)

    return FinalizedSynthesis(
        raw_content=content,
        safe_content=safe_content,
        citations=citations,
        source_attribution=attribution,
        confidence=confidence,
        word_count=word_count,
        style_used=style_used,
        llm_output=llm_output,
        verdict=verdict,
        cache_eligible=verdict.passed,
        surface=surface,
        extras=extras,
    )
