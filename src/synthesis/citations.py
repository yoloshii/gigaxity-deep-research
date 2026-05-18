"""Numeric [N] citation extraction for pre-gathered synthesis surfaces.

Both `SynthesisAggregator` and `OutlineGuidedSynthesizer` prompt the LLM for
`[N]` citations (1-based, matching the input source order). Three call sites
previously parsed `[N]` independently with subtly different return shapes:

- `SynthesisAggregator._extract_citations` (engine path) returned `dict`
- REST `_extract_citations_from_content` returned `CitationSchema`
- MCP `synthesize` outline branch did NOT extract at all — `cited_count` was
  always 0 for outline results because `OutlinedSynthesis` has no `citations`
  field, hard-failing the verifier even when the model emitted valid `[N]`
  markers.

This module is the single resolver: one regex, one mapping rule, one return
shape. Callers convert to their preferred output type (dict vs CitationSchema)
themselves.

Codex Turn 5 (v0.2.1): MCP outline/REST parity drift — REST extracted from
outline content, MCP did not.
"""

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .aggregator import PreGatheredSource


_NUMERIC_CITATION_PATTERN = re.compile(r"\[(\d+)\]")


# Shared prompt guidance for `[N]` citation format. Imported by every
# synthesis prompt template that asks the model for inline citations.
# Three worked examples cover the single-citation, multi-citation, and
# co-citation cases. Negative examples disambiguate from the `[xx_hex]`
# format the legacy `SynthesisEngine` path uses, so the model knows this
# surface wants numeric markers (codex Turn 7 v0.2.2).
CITATION_FORMAT_GUIDE = """Citation format — every claim that draws on a source needs a `[N]` marker matching the source list above (1-based). Examples:

- "Anthropic released Claude Opus 4.7 on April 16 [1]."
- "The conference was held in San Francisco [2], with follow-on events in Tokyo [3]."
- "Two sources agree the model uses a 131K context window [1][3]."

Use only `[N]` — never `[xx_hex]`, never `(Author 2024)`, never footnotes."""


def extract_numeric_citations(
    content: str,
    sources: "list[PreGatheredSource]",
) -> list[dict]:
    """Extract `[N]`-style citations from synthesized content.

    Args:
        content: The synthesized text emitted by the LLM.
        sources: The source list passed into the synthesis prompt, in the same
            order the model was asked to cite by. `[1]` resolves to `sources[0]`.

    Returns:
        Deduplicated, in-order list of citation dicts. Each dict carries:
        `number` (1-based int), `title`, `url`, `origin`, `source_type`.
        Out-of-range or non-integer matches are silently skipped — the model
        sometimes emits `[99]` or `[abc]` that doesn't map to a source; the
        verifier handles total-zero as its own hard-fail class.
    """
    citations: list[dict] = []
    seen: set[int] = set()
    for match in _NUMERIC_CITATION_PATTERN.finditer(content):
        try:
            num = int(match.group(1))
        except ValueError:
            continue
        if num in seen:
            continue
        if 1 <= num <= len(sources):
            seen.add(num)
            source = sources[num - 1]
            citations.append({
                "number": num,
                "title": source.title,
                "url": source.url,
                "origin": source.origin,
                "source_type": source.source_type,
            })
    return citations
