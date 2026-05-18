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

import re
from dataclasses import dataclass, field
from typing import Optional

from ..llm_utils import LLMOutput
from .citations import detect_legacy_markers, detect_mixed_markers
from .contradictions import ContradictionDetectionResult
from .quality_gate import _entity_in_text


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
_SENTENCE_SPLIT = re.compile(r"[.!?]\s+|\n+")


@dataclass
class SynthesisVerdict:
    """Result of verifying synthesis output before it is returned to a caller.

    `hard_failures` are blocking - the output must not be cached or relayed as
    a successful synthesis. `soft_warnings` are advisory - the output is usable
    but should be annotated for the caller.
    """
    hard_failures: list[str] = field(default_factory=list)
    soft_warnings: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        """True when there are no blocking failures (output is safe to cache and relay)."""
        return not self.hard_failures


def _output_acknowledges_gap(content_lower: str, uncovered_entities: list[str]) -> bool:
    """True if the synthesis explicitly frames every uncovered entity as a gap.

    Sentence-level: each uncovered entity must appear in at least one
    sentence that also contains a gap-framing phrase. Requires EVERY
    uncovered entity to be gap-framed for the check to return True — a
    single un-framed entity still triggers the hard-fail path. This is
    deliberately strict: the escape hatch should only fire when the
    synthesis is internally consistent about what it does and doesn't have
    source evidence for.

    Sentence-level vs window-level (Turn 2 fix-iteration): an earlier
    window-based check (40 chars before/after entity) leaked framing across
    sentence boundaries — "no source available for LinkUp. Serper costs..."
    incorrectly framed Serper. Splitting at sentence delimiters scopes the
    framing to the entity it actually qualifies.
    """
    sentences = _SENTENCE_SPLIT.split(content_lower)
    for entity in uncovered_entities:
        entity_lower = entity.lower()
        framed = False
        for sentence in sentences:
            # Boundary-safe entity match (Turn 3 codex T3F1): otherwise
            # a sentence mentioning "example" would falsely frame an "Exa"
            # entity that's never actually discussed in that sentence.
            if _entity_in_text(sentence, entity_lower) and any(
                phrase in sentence for phrase in _GAP_FRAMING_PHRASES
            ):
                framed = True
                break
        if not framed:
            return False
    return True


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
                if _output_acknowledges_gap(content_lower, uncovered):
                    verdict.soft_warnings.append(
                        f"synthesis discusses {uncovered} without source "
                        "evidence but explicitly frames the gap - operator "
                        "should still verify the framing is accurate"
                    )
                else:
                    verdict.hard_failures.append(
                        f"synthesis discusses entities {uncovered} but those "
                        "entities are absent from every retained source AND "
                        "the synthesis does not explicitly frame the gap - "
                        "likely uncited hallucination from prior model "
                        "knowledge"
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
