"""Synthesis wrappers — the ONLY callers of the five core synthesis methods.

Phase 0 architectural invariant: every code path that needs a synthesis result
goes through one of the five wrappers in this module. The wrappers construct
the core class instance themselves (so callers do not import the class names),
invoke the single method this wrapper is responsible for, and pass the result
to `finalize_synthesis` for verification + annotation. No caller is allowed
to import `SynthesisEngine`, `SynthesisAggregator`, or
`OutlineGuidedSynthesizer` outside this module — that invariant is enforced
by `scripts/audit_synthesis_callers.py` (AST audit) and a grep-secondary CI
gate.

Why this matters: prior to Phase 0 the post-synthesis verification call was
hand-rolled at every caller site. Three of the ten call sites had real
deficiencies:
- REST `/research` preset path skipped verification entirely.
- REST `/research` no-preset and MCP `research` ran the verifier without
  threading `query_entities` / `sources_text`, so the entity-coverage check
  silently no-op'd on those paths.

Centralizing through these wrappers fixes the three real-debt sites by
construction (every wrapper always runs `finalize_synthesis` with the
full verifier arguments) and gives later phases (1, 2a, 3, 4, 5a, 5b, 6) a
single chokepoint where new envelope state (entity-section parser, advisory
skill layer, provenance tiering, contracrow normalization, coverage grid,
structural hard-fail, retry advice) can be introduced without touching every
endpoint.

The wrappers raise `SynthesisInvocationError` when the engine layer returned
an `{"error": ...}` dict — that's a true invocation failure, not a synthesis
quality issue, and the caller (REST handler vs MCP tool) decides how to
surface it. Aggregator and outline paths return their own degraded shapes
(empty content, `llm_output.truncated`, etc.) which the verifier hard-fails
through the normal channel.
"""

from typing import Optional

from ..config import settings
from ..connectors.base import Source
from .aggregator import AggregatedSynthesis, PreGatheredSource, SynthesisAggregator, SynthesisStyle
from .contradictions import ContradictionDetectionResult
from .engine import SynthesisEngine
from .finalization import FinalizedSynthesis, SurfaceName, finalize_synthesis
from .outline import OutlinedSynthesis, OutlineGuidedSynthesizer


class SynthesisInvocationError(RuntimeError):
    """Raised when a core synthesis method's underlying LLM call returned an
    error dict that the engine layer caught and surfaced as `{"error": ...}`.

    This is a true LLM invocation failure (timeout, transport error, API
    rejection), distinct from a synthesis-quality hard-fail surfaced through
    `verify_synthesis_output`. REST handlers convert this to HTTP 500; MCP
    tools convert it to an error-message return. The wrapper raises BEFORE
    `finalize_synthesis` runs so the verifier never sees a content body
    that's actually an exception message.
    """


# ---------------------------------------------------------------------------
# SynthesisEngine wrappers
# ---------------------------------------------------------------------------


async def run_engine_synthesize(
    *,
    client,
    model: Optional[str] = None,
    query: str,
    sources: list[Source],
    system_prompt: Optional[str] = None,
    contradiction_result: Optional[ContradictionDetectionResult] = None,
    query_entities: Optional[list[str]] = None,
    surface: SurfaceName,
) -> FinalizedSynthesis:
    """Invoke `SynthesisEngine.synthesize` and finalize the result.

    The engine's dict result carries `content / citations / sources_used /
    model / usage` (or `{"error": ...}` on invocation failure). Wrapper
    raises `SynthesisInvocationError` on the error path so finalize never
    sees an exception-string content body.
    """
    engine = SynthesisEngine(client=client, model=model or settings.llm_model)
    result = await engine.synthesize(query, sources, system_prompt)
    if isinstance(result, dict) and "error" in result:
        raise SynthesisInvocationError(result["error"])
    return finalize_synthesis(
        query=query,
        result=result,
        sources=sources,
        contradiction_result=contradiction_result,
        query_entities=query_entities,
        surface=surface,
    )


async def run_engine_research(
    *,
    client,
    model: Optional[str] = None,
    query: str,
    sources: list[Source],
    reasoning_effort: str = "medium",
    contradiction_result: Optional[ContradictionDetectionResult] = None,
    query_entities: Optional[list[str]] = None,
    surface: SurfaceName,
) -> FinalizedSynthesis:
    """Invoke `SynthesisEngine.research` and finalize the result.

    `engine.research` adjusts the system prompt by reasoning_effort and
    delegates to `engine.synthesize`. Same error-dict semantics as
    `run_engine_synthesize`.
    """
    engine = SynthesisEngine(client=client, model=model or settings.llm_model)
    result = await engine.research(query, sources, reasoning_effort)
    if isinstance(result, dict) and "error" in result:
        raise SynthesisInvocationError(result["error"])
    return finalize_synthesis(
        query=query,
        result=result,
        sources=sources,
        contradiction_result=contradiction_result,
        query_entities=query_entities,
        surface=surface,
    )


# ---------------------------------------------------------------------------
# SynthesisAggregator wrappers
# ---------------------------------------------------------------------------


async def run_aggregator_synthesize(
    *,
    llm_client,
    model: Optional[str] = None,
    query: str,
    sources: list[PreGatheredSource],
    style: SynthesisStyle = SynthesisStyle.COMPREHENSIVE,
    max_tokens: int = 3000,
    guidance: Optional[list[str]] = None,
    contradiction_notes: Optional[str] = None,
    contradiction_result: Optional[ContradictionDetectionResult] = None,
    query_entities: Optional[list[str]] = None,
    surface: SurfaceName,
) -> FinalizedSynthesis:
    """Invoke `SynthesisAggregator.synthesize` and finalize the result.

    The aggregator returns `AggregatedSynthesis` with `content / citations /
    source_attribution / confidence / style_used / word_count / llm_output`.
    No invocation-error path: aggregator-internal failures surface through
    `llm_output.subcall_failed` / `llm_output.truncated` / empty content, all
    of which the verifier hard-fails through the normal channel.
    """
    aggregator = SynthesisAggregator(llm_client=llm_client, model=model or settings.llm_model)
    result = await aggregator.synthesize(
        query=query,
        sources=sources,
        style=style,
        max_tokens=max_tokens,
        guidance=guidance,
        contradiction_notes=contradiction_notes,
    )
    return finalize_synthesis(
        query=query,
        result=result,
        sources=sources,
        contradiction_result=contradiction_result,
        query_entities=query_entities,
        surface=surface,
    )


async def run_aggregator_synthesize_with_reasoning(
    *,
    llm_client,
    model: Optional[str] = None,
    query: str,
    sources: list[PreGatheredSource],
    style: SynthesisStyle = SynthesisStyle.COMPREHENSIVE,
    max_tokens: int = 4000,
    guidance: Optional[list[str]] = None,
    contradiction_notes: Optional[str] = None,
    contradiction_result: Optional[ContradictionDetectionResult] = None,
    query_entities: Optional[list[str]] = None,
    surface: SurfaceName,
) -> FinalizedSynthesis:
    """Invoke `SynthesisAggregator.synthesize_with_reasoning` and finalize.

    Same shape as `run_aggregator_synthesize`. The reasoning variant extracts
    only the `<synthesis>` block from the chain-of-thought response; if the
    model fails to emit that block, `result.content` is "" and the verifier
    hard-fails. `max_tokens` default is 4000 to match the aggregator's own
    default for this method.
    """
    aggregator = SynthesisAggregator(llm_client=llm_client, model=model or settings.llm_model)
    result = await aggregator.synthesize_with_reasoning(
        query=query,
        sources=sources,
        style=style,
        max_tokens=max_tokens,
        guidance=guidance,
        contradiction_notes=contradiction_notes,
    )
    return finalize_synthesis(
        query=query,
        result=result,
        sources=sources,
        contradiction_result=contradiction_result,
        query_entities=query_entities,
        surface=surface,
    )


# ---------------------------------------------------------------------------
# OutlineGuidedSynthesizer wrapper
# ---------------------------------------------------------------------------


async def run_outline_synthesize(
    *,
    llm_client,
    model: Optional[str] = None,
    max_refinement_rounds: int = 1,
    query: str,
    sources: list[PreGatheredSource],
    style: SynthesisStyle = SynthesisStyle.COMPREHENSIVE,
    max_tokens: int = 3000,
    guidance: Optional[list[str]] = None,
    contradiction_notes: Optional[str] = None,
    contradiction_result: Optional[ContradictionDetectionResult] = None,
    query_entities: Optional[list[str]] = None,
    surface: SurfaceName,
) -> FinalizedSynthesis:
    """Invoke `OutlineGuidedSynthesizer.synthesize` and finalize.

    Returns an `OutlinedSynthesis` with `content / outline / sections /
    critique / refined / word_count / llm_output`. The outline path has no
    `citations` field — finalize_synthesis runs the shared `[N]` extractor
    over the content + sources to compute citations identically to the
    aggregator path. Surface-specific extras (`outline_sections`, `sections`,
    `critique`, `refined`) are emitted in `FinalizedSynthesis.extras`.
    """
    synthesizer = OutlineGuidedSynthesizer(
        llm_client=llm_client,
        model=model or settings.llm_model,
        max_refinement_rounds=max_refinement_rounds,
    )
    result = await synthesizer.synthesize(
        query=query,
        sources=sources,
        style=style,
        max_tokens=max_tokens,
        guidance=guidance,
        contradiction_notes=contradiction_notes,
    )
    return finalize_synthesis(
        query=query,
        result=result,
        sources=sources,
        contradiction_result=contradiction_result,
        query_entities=query_entities,
        surface=surface,
    )
