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
from typing import Optional

from ..llm_utils import LLMOutput
from .contradictions import ContradictionDetectionResult


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


def verify_synthesis_output(
    content: str,
    llm_output: Optional[LLMOutput],
    cited_count: int,
    source_count: int,
    contradiction_result: Optional[ContradictionDetectionResult] = None,
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

    # --- soft annotations: usable, but flag for the caller ---
    if has_content and source_count > 0 and 0 < cited_count < source_count:
        verdict.soft_warnings.append(
            f"partial citation coverage: {cited_count} of {source_count} sources cited"
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
