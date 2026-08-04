"""Shared stage-degradation record for structured LLM stages.

A structured stage (outline, critique, landscape, gaps, source scoring,
expansion, preview) that could not use its model output — starved by
reasoning, truncated, blank, or rejected by the stage grammar — records WHAT
failed and WHAT the caller did about it, instead of silently degrading.

The record is deliberately structured, not narrative: `code` carries the
primary cause, and `truncated` / `reasoning_only` are independent secondary
flags so one signal never hides the other. Precedence when several causes
hold at once: `truncated` wins (finish_reason == "length" is the one signal
that tells an operator to raise the budget), then `reasoning_only`, then
`malformed` (non-blank text the grammar rejected), then `empty`.

`message` is always a sanitized human-readable summary — NEVER a raw
provider exception string. Callers log the raw exception privately and pass
a fixed description here.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from .llm_utils import LLMOutput


class DegradationCode(str, Enum):
    """Primary cause of a stage degradation."""
    REASONING_ONLY = "reasoning_only"    # content empty; text only in a reasoning field
    TRUNCATED = "truncated"              # finish_reason == "length"
    EMPTY = "empty"                      # blank response, not truncated, no reasoning text
    MALFORMED = "malformed"              # non-blank text rejected by the stage grammar
    PARTIAL = "partial"                  # usable partial output (expansion only)
    TRANSPORT_ERROR = "transport_error"  # the LLM call itself failed


@dataclass
class StageDegradation:
    """One stage's degradation event, carried on the stage's result object."""
    stage: str                           # "outline" | "critique" | "landscape" | "gaps"
                                         # | "source_scoring" | "expansion" | "preview"
    code: DegradationCode
    parse_failed: bool                   # the stage grammar rejected the text
    fallback_used: bool                  # a deterministic fallback replaced the output
    message: str                         # sanitized — never a raw provider exception string
    truncated: bool = False              # structured secondary signal
    reasoning_only: bool = False         # structured secondary signal
    finish_reason: Optional[str] = None  # raw provider signal, for operators

    def to_dict(self) -> dict:
        """JSON-safe dict for schemas, diagnostics, and MCP footers."""
        return {
            "stage": self.stage,
            "code": self.code.value,
            "parse_failed": self.parse_failed,
            "fallback_used": self.fallback_used,
            "message": self.message,
            "truncated": self.truncated,
            "reasoning_only": self.reasoning_only,
            "finish_reason": self.finish_reason,
        }


def degradation_from_output(
    stage: str,
    output: LLMOutput,
    *,
    parse_failed: bool,
    fallback_used: bool,
    message: str,
) -> StageDegradation:
    """Classify an unusable or grammar-rejected LLMOutput.

    Precedence: truncated > reasoning_only > malformed (non-blank text the
    grammar rejected) > empty. A PARSE_REQUIRED extraction blanks truncated
    and reasoning-only text, so `output.text` alone cannot distinguish the
    causes — the LLMOutput provenance flags can, which is why helpers must
    return LLMOutput rather than str.
    """
    if output.truncated:
        code = DegradationCode.TRUNCATED
    elif output.reasoning_only:
        code = DegradationCode.REASONING_ONLY
    elif output.text and output.text.strip():
        code = DegradationCode.MALFORMED
    else:
        code = DegradationCode.EMPTY
    return StageDegradation(
        stage=stage,
        code=code,
        parse_failed=parse_failed,
        fallback_used=fallback_used,
        message=message,
        truncated=output.truncated,
        reasoning_only=output.reasoning_only,
        finish_reason=output.finish_reason,
    )


def partial_degradation(stage: str, message: str) -> StageDegradation:
    """Usable partial output (expansion's fewer-than-N case): no parse
    failure, no fallback — recorded so partial coverage is visible."""
    return StageDegradation(
        stage=stage,
        code=DegradationCode.PARTIAL,
        parse_failed=False,
        fallback_used=False,
        message=message,
    )


def transport_degradation(
    stage: str,
    message: str,
    *,
    fallback_used: bool = True,
) -> StageDegradation:
    """The LLM call itself failed and the stage fell back deterministically.

    Only for stages that CATCH transport errors (expansion, preview).
    Stages whose exceptions propagate (Explorer's structured stages) have no
    result object to carry a degradation — the request fails instead.
    """
    return StageDegradation(
        stage=stage,
        code=DegradationCode.TRANSPORT_ERROR,
        parse_failed=False,
        fallback_used=fallback_used,
        message=message,
    )
