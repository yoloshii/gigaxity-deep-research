"""Post-synthesis output verification.

The synthesize path can return output that superficially looks fine but is
not usable: an empty completion, a chain-of-thought trace returned in place
of an answer, a generation truncated by the token limit, a multi-section
synthesis with a failed contributing sub-call, or an answer with no citations
despite having sources. The pre-synthesis source quality gate does not catch
any of these - it scores input relevance, not output.

This module defines the verdict type and the shared post-synthesis verifier
used by both the MCP synthesize tool and the REST synthesis routes.
"""

from dataclasses import dataclass, field
from typing import Literal, Optional

from ..llm_utils import LLMOutput
from .citations import (
    detect_legacy_markers,
    detect_mixed_markers,
    has_numeric_citation_marker,
)
from .contradictions import ContradictionDetectionResult
from .quality_gate import _entity_in_text
from .sentence_utils import split_sentences


# Forward-compat verdict envelope (Phase 0 scaffolding). The existing
# `hard_failures` / `soft_warnings` / `.passed` shape is preserved verbatim
# below; these new fields are populated by future phases:
# - Phase 1: `failure_codes` (gap_unscoped, gap_section_polluted,
#   gap_declared_but_section_open, gap_group_heading_unsupported); also
#   `verdict_class == "calibrated_gap"` when the structural gap parser
#   accepts a declared gap.
# - Phase 5a: `warnings[code=coverage_grid_uncited_uncovered_cells]` etc.
# - Phase 5b: `warnings[code=uncovered_cell_unacknowledged]` (after a 14-day
#   fixture green-light) and the matching hard_failures entry.
# - Phase 6: `retry_advice` populated when the verifier can recommend a
#   surface-aware retry (gather_more_sources / resynthesize_same_sources /
#   abort) and `diagnostics.tier_composition`.
#
# Phase 0 only sets `verdict_class` automatically (= "hard_fail" if
# hard_failures else "pass"). Everything else defaults so a Phase 0 caller
# sees behavior identical to pre-envelope output.
VerdictClass = Literal["pass", "calibrated_gap", "hard_fail"]


@dataclass
class VerdictWarning:
    """A structured advisory warning, mirroring a `soft_warnings` string entry.

    The verifier emits both shapes in parallel: `soft_warnings` (list of
    strings) remains the existing human-readable channel; `warnings` (list of
    `VerdictWarning`) is the machine-readable parallel that downstream
    consumers (Phase 6 retry advice, Phase 7 evolution loop) can pattern-match
    on without re-parsing prose. Phase 0 emits empty.
    """
    code: str
    message: str
    severity: Literal["info", "warning"] = "warning"


@dataclass
class VerdictDiagnostics:
    """Structured diagnostics produced by the verifier.

    Field-granular dict slots so future phases can populate them independently
    without bumping a schema version. Phase 0 leaves all slots None / empty.
    """
    gate_diagnostics: Optional[dict] = None
    tier_composition: Optional[dict] = None
    gap_declarations: list[str] = field(default_factory=list)
    contracrow_result: Optional[dict] = None
    coverage_grid_summary: Optional[dict] = None
    bm25_mismatch_info: Optional[dict] = None


@dataclass
class RetryAdvice:
    """Surface-aware retry advice emitted on hard-failure (Phase 6 territory).

    Phase 0 always emits None. Phase 6 populates this when the verifier sees
    a hard fail that the caller can address by re-discovery, re-synthesis, or
    abort. Pure-synthesis surfaces never re-discover internally — the advice
    is emitted FOR the caller (an orchestrator or research-workflow).
    """
    caller_action: Literal["gather_more_sources", "resynthesize_same_sources", "abort"]
    missing_entities: list[str] = field(default_factory=list)
    missing_aspects: list[tuple[str, str]] = field(default_factory=list)
    suggested_queries: list[str] = field(default_factory=list)
    rationale: str = ""


# Explicit gap-framing phrases that indicate the synthesis acknowledged a
# missing source rather than hallucinating about an uncovered entity. The
# check is SENTENCE-LEVEL: the gap-framing phrase and the entity must appear
# in the same sentence (delimited by `.`, `!`, `?`). Earlier window-based
# check was too permissive — "no source available for LinkUp" leaked across
# the sentence boundary to frame a later "Serper" mention.
_GAP_FRAMING_PHRASES = (
    "no source", "without source", "no citation", "uncited",
    "not in the gathered", "not in our gathered", "not in any gathered",
    "not covered by", "not covered in", "not documented",
    "no data on", "no information on", "no information about",
    "not available", "no available", "could not find", "couldn't find",
    "no documentation", "missing from", "absent from",
    "gap in", "coverage gap", "not present in",
)


@dataclass
class SynthesisVerdict:
    """Result of verifying synthesis output before it is returned to a caller.

    `hard_failures` are blocking - the output must not be cached or relayed as
    a successful synthesis. `soft_warnings` are advisory - the output is usable
    but should be annotated for the caller.

    The remaining fields (`verdict_class`, `failure_codes`, `warnings`,
    `diagnostics`, `retry_advice`) are forward-compat envelope scaffolding
    populated by later phases (1, 5a, 5b, 6). Phase 0 sets `verdict_class`
    automatically (= "hard_fail" if any hard_failures else "pass") and leaves
    the rest at their defaults so existing callers see identical observable
    behavior. The `passed` property's semantics are unchanged.
    """
    hard_failures: list[str] = field(default_factory=list)
    soft_warnings: list[str] = field(default_factory=list)
    verdict_class: VerdictClass = "pass"
    failure_codes: list[str] = field(default_factory=list)
    warnings: list[VerdictWarning] = field(default_factory=list)
    diagnostics: VerdictDiagnostics = field(default_factory=VerdictDiagnostics)
    retry_advice: Optional[RetryAdvice] = None

    def __post_init__(self) -> None:
        """Reconcile `verdict_class` with `hard_failures` shape on construction.

        Without this, a direct constructor `SynthesisVerdict(hard_failures=["x"])`
        would have `passed=False` but `verdict_class="pass"` (the default), which
        is a contradictory state Phase 1/5/6 callers could easily produce. The
        rule is:
        - hard_failures non-empty → verdict_class = "hard_fail" (overrides any
          other value; a hard failure dominates).
        - hard_failures empty AND verdict_class == "hard_fail" → demote to
          "pass" (verdict_class lied about an empty hard_failures list).
        - hard_failures empty AND verdict_class == "calibrated_gap" → preserve
          the calibrated_gap signal (Phase 1's structural-gap acknowledgement).
        - hard_failures empty AND verdict_class == "pass" → no change.

        Codex Turn 1 F5 (consistency on direct construction).
        """
        if self.hard_failures:
            self.verdict_class = "hard_fail"
        elif self.verdict_class == "hard_fail":
            self.verdict_class = "pass"
        # "pass" and "calibrated_gap" with empty hard_failures: preserve.

    @property
    def passed(self) -> bool:
        """True when there are no blocking failures (output is safe to cache and relay)."""
        return not self.hard_failures


def _entity_acknowledges_gap(content_lower: str, entity_lower: str) -> bool:
    """True if some sentence frames THIS entity as a source gap.

    Sentence-level: the entity must appear in at least one sentence that also
    contains a gap-framing phrase. Boundary-safe entity match (Turn 3 codex
    T3F1): otherwise a sentence mentioning "example" would falsely frame an
    "Exa" entity that's never actually discussed in it.

    Sentence-level vs window-level (Turn 2 fix-iteration): an earlier
    window-based check (40 chars before/after entity) leaked framing across
    sentence boundaries — "no source available for LinkUp. Serper costs..."
    incorrectly framed Serper. Splitting at sentence delimiters scopes the
    framing to the entity it actually qualifies.
    """
    for sentence in split_sentences(content_lower):
        if _entity_in_text(sentence, entity_lower) and any(
            phrase in sentence for phrase in _GAP_FRAMING_PHRASES
        ):
            return True
    return False


def _output_acknowledges_gap(content_lower: str, uncovered_entities: list[str]) -> bool:
    """True if the synthesis explicitly frames EVERY uncovered entity as a gap.

    Thin all-or-nothing wrapper over `_entity_acknowledges_gap` (codex T2). The
    verifier now classifies framing PER ENTITY (a framed entity is soft-warned
    even when others are un-framed); this aggregate predicate is retained for
    callers that want the original "all framed" semantics.
    """
    return all(
        _entity_acknowledges_gap(content_lower, e.lower()) for e in uncovered_entities
    )


def _entity_has_adjacent_citation(content_lower: str, entity_lower: str) -> bool:
    """True if some sentence mentions the entity AND carries a `[N]` citation.

    Sentence-level, mirroring `_output_acknowledges_gap`: the numeric citation
    marker must co-occur with the entity in the same sentence (delimited by
    `.`, `!`, `?`) for the synthesis to count as binding a source to that
    entity. Used to split a discussed-but-uncovered entity into cited (a
    fabricated source attribution -> hard fail) vs uncited (query-framing /
    coined-label vocabulary -> soft warning). ISS-20260604-001.
    """
    for sentence in split_sentences(content_lower):
        if _entity_in_text(sentence, entity_lower) and has_numeric_citation_marker(sentence):
            return True
    return False


def verify_synthesis_output(
    content: str,
    llm_output: Optional[LLMOutput],
    cited_count: int,
    source_count: int,
    contradiction_result: Optional[ContradictionDetectionResult] = None,
    *,
    query_entities: Optional[list[str]] = None,
    sources_text: Optional[str] = None,
) -> SynthesisVerdict:
    """Verify a completed synthesis before it is cached or relayed to a caller.

    Hard failures mean the output is not a usable synthesis and must not be
    presented as a successful one (and must not be cached). Soft warnings mean
    the output is usable but should be annotated for the caller.

    Args:
        content: the final synthesis text.
        llm_output: the provenance/truncation signal carried from the synthesis
            call(s), or None if unavailable.
        cited_count: number of distinct sources cited in `content`.
        source_count: number of sources provided to the synthesis.
        contradiction_result: the contradiction-detection result, if the path
            ran contradiction detection.
        query_entities: optional list of capitalized entities extracted from
            the query (vendor / product / library names). When provided
            alongside `sources_text`, the verifier checks that every entity
            the synthesis discusses is grounded in at least one retained
            source. Catches the hallucination class where the relevance gate
            filters out all sources for an entity but the LLM writes about
            it anyway from prior knowledge.
        sources_text: optional concatenated lowercase text (title + content)
            of all retained sources, used for the entity-coverage check.
            Pre-lowercased to avoid per-call cost.
    """
    verdict = SynthesisVerdict()
    has_content = bool(content and content.strip())

    # --- hard gates: the output is not a usable synthesis ---
    if not has_content:
        verdict.hard_failures.append("synthesis produced no answer content")
    elif llm_output is not None and llm_output.reasoning_only:
        verdict.hard_failures.append(
            "synthesis returned a reasoning trace instead of an answer"
        )

    if llm_output is not None and llm_output.truncated:
        verdict.hard_failures.append(
            "synthesis was truncated by the token limit (finish_reason=length) "
            "even after the retry at the ceiling"
        )

    if llm_output is not None and llm_output.subcall_failed:
        verdict.hard_failures.append(
            "a contributing synthesis sub-call produced no usable answer "
            "(empty, reasoning-only, or truncated) - the assembled synthesis "
            "is incomplete"
        )

    if has_content and source_count > 0 and cited_count == 0:
        verdict.hard_failures.append(
            f"synthesis cites none of the {source_count} provided sources"
        )

    # Entity-coverage check. An entity discussed in the synthesis but absent
    # from every retained source is most likely hallucinated from prior model
    # knowledge — the gate filtered the source(s) covering it, and the model
    # responded to the query anyway. Default: hard-fail any uncovered entity
    # (Turn 2 codex F1: partial-uncovered shouldn't get a free pass and end
    # up cached as a verified synthesis). Escape hatch: if the synthesis
    # explicitly frames the gap ("no source available for X", "not in the
    # gathered sources"), downgrade to soft warning — that's grounded
    # acknowledgement, not hallucination.
    if has_content and query_entities and sources_text:
        content_lower = content.lower()
        # Only consider entities the synthesis actually discusses.
        # Turn 3 codex T3F1: boundary-safe matching — substring `in` would
        # let "Exa" match "example" so the synthesis appears to discuss
        # entities it doesn't, and an "Exa" entity appears "covered" by
        # an unrelated source whose body contains "example".
        discussed = [e for e in query_entities if _entity_in_text(content_lower, e.lower())]
        if discussed:
            uncovered = [e for e in discussed if not _entity_in_text(sources_text, e.lower())]
            if uncovered:
                # Per-entity gap framing FIRST (codex T2). An entity the
                # synthesis explicitly frames as a source gap ("no source for
                # X") is a grounded acknowledgement, not a hallucination - even
                # when its framing sentence happens to carry a citation marker.
                # Classifying framing per entity (not all-or-nothing) keeps a
                # framed entity OUT of the citation-adjacency check below, which
                # would otherwise hard-fail it on the marker in its own gap
                # sentence when SOME other uncovered entity is un-framed.
                framed_uncovered = [
                    e for e in uncovered
                    if _entity_acknowledges_gap(content_lower, e.lower())
                ]
                remaining = [e for e in uncovered if e not in framed_uncovered]
                if framed_uncovered:
                    verdict.soft_warnings.append(
                        f"synthesis discusses {framed_uncovered} without source "
                        "evidence but explicitly frames the gap - operator "
                        "should still verify the framing is accurate"
                    )
                if remaining:
                    # Citation-adjacency split (ISS-20260604-001) over the
                    # UN-framed remainder. The entity-coverage check only ever
                    # sees QUERY entities, so a discussed-but-uncovered, un-framed
                    # entity is either (a) a fabricated source attribution - the
                    # synthesis binds a `[N]` citation to an entity no retained
                    # source covers (the ISS-20260514 "Prisma is SSPL [3]" shape)
                    # - or (b) query-framing vocabulary the corpus cannot contain
                    # (an internal project codename, a decision-option label, or a
                    # real name the search simply missed) that the model mentions
                    # WITHOUT binding a source to it. Only (a) is a hard
                    # fabrication; (b) is a coverage/coinage gap -> soft warning.
                    # The discriminator: does some sentence mention the entity AND
                    # carry a `[N]` marker.
                    #
                    # Mixed sentences are intentionally NOT exempted (codex T1
                    # F2): "AssemblyAI and SufiSR both support X [1]" with SufiSR
                    # uncovered is a JOINT cited claim about SufiSR, so it still
                    # hard-fails - the citation is not re-attributed to the
                    # covered co-entity. Tighten to clause/proximity later behind
                    # fixtures if this proves noisy.
                    cited_uncovered = [
                        e for e in remaining
                        if _entity_has_adjacent_citation(content_lower, e.lower())
                    ]
                    uncited_uncovered = [
                        e for e in remaining if e not in cited_uncovered
                    ]
                    if cited_uncovered:
                        verdict.hard_failures.append(
                            f"synthesis binds source citations to entities "
                            f"{cited_uncovered} that are absent from every "
                            "retained source - a source-backed claim is "
                            "attributed to an uncovered entity (likely "
                            "fabricated source support)"
                        )
                    if uncited_uncovered:
                        verdict.soft_warnings.append(
                            f"synthesis discusses {uncited_uncovered} with no "
                            "in-sentence citation and no retained source covers "
                            "them - verify these are query-framing labels and "
                            "not ungrounded claims; gather more sources if they "
                            "should be source-grounded"
                        )

    # --- soft annotations: usable, but flag for the caller ---
    if has_content and source_count > 0 and 0 < cited_count < source_count:
        verdict.soft_warnings.append(
            f"partial citation coverage: {cited_count} of {source_count} sources cited"
        )

    # Citation marker drift (v0.3.0, codex DESIGN session 019e39f7 Q7).
    # v0.3.0 unified every synthesis surface onto `[N]`. If the LLM still
    # emits legacy `[xx_<hex>]` markers the prompt has regressed (or the
    # model ignored the contract under deep-synthesis pressure). Surface
    # the drift as a soft warning so operators see a concrete diagnostic
    # rather than the generic "cites none" hard-fail message — especially
    # useful during the v0.3.0 migration window when prompt + extractor
    # changes are still bedding in. Hard-fail at cited_count==0 above still
    # fires for legacy-only output (because numeric extraction returns 0);
    # this warning is the diagnostic that explains WHY it fired.
    if has_content:
        legacy_markers = detect_legacy_markers(content)
        if legacy_markers:
            preview = ", ".join(legacy_markers[:3])
            more = f" (+{len(legacy_markers) - 3} more)" if len(legacy_markers) > 3 else ""
            if detect_mixed_markers(content):
                verdict.soft_warnings.append(
                    f"citation marker drift: synthesis emitted both `[N]` and "
                    f"legacy `[xx_<hex>]` markers — {len(legacy_markers)} legacy "
                    f"marker(s) ignored by numeric extractor: {preview}{more}"
                )
            else:
                verdict.soft_warnings.append(
                    f"citation marker drift: synthesis emitted only legacy "
                    f"`[xx_<hex>]` markers (no `[N]`) — prompt regression or "
                    f"model ignored the v0.3.0 contract. Markers: "
                    f"{preview}{more}"
                )

    if contradiction_result is not None:
        if contradiction_result.parse_failed:
            verdict.soft_warnings.append(
                "contradiction detection could not be parsed - contradictions "
                "may exist but were not surfaced"
            )
        elif contradiction_result.contradictions:
            verdict.soft_warnings.append(
                f"{len(contradiction_result.contradictions)} contradiction(s) detected "
                "- verify the synthesis surfaces them"
            )

    # Phase 0 envelope: derive `verdict_class` from the hard_failures shape.
    # "calibrated_gap" is reserved for Phase 1 (structural entity-section
    # parser); Phase 0 only distinguishes pass vs hard_fail, mirroring the
    # existing `.passed` property's semantics.
    verdict.verdict_class = "hard_fail" if verdict.hard_failures else "pass"

    return verdict


def annotate_with_verdict(output: str, verdict: SynthesisVerdict) -> str:
    """Annotate a synthesis output string with its verification verdict.

    Soft warnings are appended as advisory notes. A hard-gate failure prepends a
    clear failure header so the output is never relayed as a clean success.
    """
    result = output
    if verdict.soft_warnings:
        result = (
            result
            + "\n\n---\n*Verification notes: "
            + "; ".join(verdict.soft_warnings)
            + "*"
        )
    if not verdict.passed:
        result = (
            "# Synthesis verification FAILED\n\n"
            "This output is not a reliable synthesis:\n"
            + "\n".join(f"- {f}" for f in verdict.hard_failures)
            + "\n\n---\n(unverified output below, for debugging)\n\n"
            + result
        )
    return result
