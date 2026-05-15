"""Budget-aware source formatting for synthesis prompts.

The synthesize path used to truncate every source to a fixed character cap
(e.g. 2000 chars), which silently dropped evidence past the cap - a fact in
paragraph five of a source would never reach the model. This module formats
sources against the model's actual context window instead: if the sources fit,
they are included verbatim and in full; only under genuine budget pressure is
content compressed, and even then no source is dropped.

Advisory guidance (RCS contextual summaries, contradiction notes) is rendered
as its own section, distinct from the verbatim source evidence, so the model
can use it to orient without mistaking a summary for the source text.
"""

from typing import Optional

from ..llm_utils import get_context_window


# Rough chars-per-token ratio for budgeting. The budget math only needs to be
# approximately right - a model context window (e.g. 131072 tokens) is far
# larger than a typical synthesis input, so this guards the rare overflow case
# while letting the common case through untruncated.
CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
    """Approximate token count of `text` for budgeting purposes."""
    if not text:
        return 0
    return (len(text) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN


def derive_input_budget(model: str, output_budget: int, fixed_overhead_text: str) -> int:
    """Token budget available for source content in a synthesis prompt.

    = the model's context window
      - the output-token budget the call will use
      - the fixed prompt overhead (everything in the rendered prompt that is
        not source content, measured per call by the caller).

    Floors at 0.
    """
    overhead = estimate_tokens(fixed_overhead_text)
    return max(0, get_context_window(model) - output_budget - overhead)


def _source_header(index: int, source) -> str:
    """Structural header lines for a source in the evidence section."""
    origin = getattr(source, "origin", "unknown")
    source_type = getattr(source, "source_type", "unknown")
    url = getattr(source, "url", "")
    title = getattr(source, "title", "") or "Untitled"
    return f"[{index}] {title}\nOrigin: {origin} | Type: {source_type}\nURL: {url}"


def format_sources_for_synthesis(
    sources: list,
    input_budget_tokens: int,
    guidance: Optional[list[str]] = None,
    contradiction_notes: Optional[str] = None,
) -> str:
    """Format pre-gathered sources as a synthesis prompt section, budget-aware.

    If every source's full content fits within `input_budget_tokens`, every
    source is included verbatim and in full - no truncation. Only under budget
    pressure (the sources collectively exceed the budget) is per-source content
    compressed: to the source's query-focused guidance summary if one is
    available, otherwise truncated to an even share of the budget. No source is
    ever dropped.

    `guidance` (per-source advisory summaries, aligned with `sources`) and
    `contradiction_notes` (cross-source contradiction analysis) are each
    rendered as their own leading section, distinct from the verbatim source
    evidence. They are advisory: they must not be cited as source content and
    are reserved out of the budget so they never consume the evidence budget.
    """
    if not sources:
        return ""

    headers = [_source_header(i, s) for i, s in enumerate(sources, 1)]

    # Advisory sections are reserved out of the budget first - they never
    # consume the verbatim-evidence budget.
    advisory_parts = []
    if contradiction_notes and contradiction_notes.strip():
        advisory_parts.append(contradiction_notes.strip())
    if guidance:
        guidance_lines = [f"[{i}] {g}" for i, g in enumerate(guidance, 1) if g]
        if guidance_lines:
            advisory_parts.append(
                "CONTEXTUAL GUIDANCE (advisory query-focused summaries - use "
                "to orient; cite the numbered SOURCE EVIDENCE below, not this "
                "section):\n" + "\n".join(guidance_lines)
            )
    advisory_section = "\n\n".join(advisory_parts)
    advisory_tokens = estimate_tokens(advisory_section)

    header_tokens = sum(estimate_tokens(h) for h in headers)
    content_budget = max(0, input_budget_tokens - header_tokens - advisory_tokens)
    total_content_tokens = sum(
        estimate_tokens(getattr(s, "content", "") or "") for s in sources
    )
    budget_pressure = total_content_tokens > content_budget

    per_source_chars = (
        (content_budget // len(sources)) * CHARS_PER_TOKEN if budget_pressure else 0
    )

    evidence_parts = []
    for header, source in zip(headers, sources):
        source_content = getattr(source, "content", "") or ""
        if not budget_pressure:
            # Everything fits - full verbatim content, no truncation.
            body = source_content
        else:
            # Budget pressure: truncate the VERBATIM source to an even share of
            # the budget. Never substitute the advisory guidance summary into
            # the evidence section - that would make advisory text citable as
            # source evidence, the contamination class this module exists to
            # prevent. The query-focused summary stays in the CONTEXTUAL
            # GUIDANCE section only; no source is dropped.
            body = source_content[:per_source_chars]
            if len(source_content) > per_source_chars:
                body += "\n...[truncated under prompt budget pressure]"
        evidence_parts.append(f"{header}\nContent:\n{body}\n---")

    evidence_section = "SOURCE EVIDENCE:\n\n" + "\n\n".join(evidence_parts)

    if advisory_section:
        return f"{advisory_section}\n\n{evidence_section}"
    return evidence_section
