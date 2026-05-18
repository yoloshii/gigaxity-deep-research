"""Numeric [N] citation extraction — single resolver for every synthesis path.

`SynthesisAggregator`, `OutlineGuidedSynthesizer`, and `SynthesisEngine` all
prompt the LLM for `[N]` citations (1-based, matching the input source order).
Three call sites previously parsed independently with subtly different return
shapes:

- `SynthesisAggregator._extract_citations` returned `dict`
- REST `_extract_citations_from_content` returned `CitationSchema`
- MCP `synthesize` outline branch did NOT extract at all — `cited_count` was
  always 0 for outline results (codex Turn 5, v0.2.1 fix)

`SynthesisEngine` historically used a different contract entirely — `[source_id]`
markers like `[tv_a1b2c3d4]` resolved through an inline regex. v0.3.0 unifies
on `[N]` everywhere (codex DESIGN session 019e39f7, Q1 hard cutover) so this
module is now the single resolver for ALL synthesis surfaces, including the
engine path that previously parsed `[source_id]`.

Source types diverge across paths:

- `synthesis.aggregator.PreGatheredSource` carries `origin` + `source_type`
  (e.g. "exa" / "documentation") but no `id`
- `connectors.base.Source` carries `id` (e.g. `tv_a1b2c3d4`) but no `origin`
  or `source_type`

`extract_numeric_citations()` accepts either via duck typing — `getattr`
fallbacks fill missing fields with `None`. The canonical citation dict always
has the same keys regardless of input source type (codex DESIGN Q2, Q4, Q5).
"""

import re
from typing import Protocol, runtime_checkable


_NUMERIC_CITATION_PATTERN = re.compile(r"\[(\d+)\]")

# Legacy `[xx_<hex>]` markers from the pre-v0.3.0 `SynthesisEngine` contract.
# `\b` anchors prevent partial matches inside longer tokens. Detected for
# verifier drift warnings (codex DESIGN Q7) — the LLM may still emit these
# under deep-synthesis pressure even after the prompt migration.
_LEGACY_CITATION_PATTERN = re.compile(r"\[([a-z]{2}_[a-f0-9]+)\]")


CITATION_FORMAT_GUIDE = """Citation format — every claim that draws on a source needs a `[N]` marker matching the source list above (1-based). Examples:

- "Anthropic released Claude Opus 4.7 on April 16 [1]."
- "The conference was held in San Francisco [2], with follow-on events in Tokyo [3]."
- "Two sources agree the model uses a 131K context window [1][3]."

Use only `[N]` — never `[xx_hex]`, never `(Author 2024)`, never footnotes."""


@runtime_checkable
class CitationSource(Protocol):
    """Duck-typed source contract for citation extraction.

    Both `PreGatheredSource` (synthesis path) and `Source` (connector path)
    satisfy this without inheriting from it. Required fields are present on
    both; optional fields use `getattr` fallbacks at the extraction site.
    """

    title: str
    url: str


def extract_numeric_citations(
    content: str,
    sources: list[CitationSource],
) -> list[dict]:
    """Extract `[N]`-style citations from synthesized content.

    Args:
        content: The synthesized text emitted by the LLM.
        sources: The source list passed into the synthesis prompt, in the same
            order the model was asked to cite by. `[1]` resolves to `sources[0]`.
            Accepts any object with `title` and `url`; uses `getattr` fallbacks
            for `id`, `origin`, `source_type`.

    Returns:
        Deduplicated, in-order list of citation dicts. Canonical shape (codex
        DESIGN Q2, v0.3.0):
            number: int          — 1-based marker number
            id: str              — public compatibility alias, always str(number)
            source_id: str|None  — connector source.id (e.g. "tv_a1b2c3d4") when
                                   available; None for sources without .id
            title: str
            url: str
            origin: str|None     — provenance origin (e.g. "exa") when available
            source_type: str|None — content type (e.g. "documentation") when available

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
                "id": str(num),
                "source_id": getattr(source, "id", None),
                "title": source.title,
                "url": source.url,
                "origin": getattr(source, "origin", None),
                "source_type": getattr(source, "source_type", None),
            })
    return citations


def detect_legacy_markers(content: str) -> list[str]:
    """Return unique `[xx_<hex>]` markers found in content, in first-seen order.

    Used by `output_verifier` to surface a soft warning when the LLM emitted
    the pre-v0.3.0 `[source_id]` contract markers despite being prompted for
    `[N]`. Empty list means clean.
    """
    seen: set[str] = set()
    ordered: list[str] = []
    for match in _LEGACY_CITATION_PATTERN.finditer(content):
        marker = match.group(1)
        if marker not in seen:
            seen.add(marker)
            ordered.append(marker)
    return ordered


def detect_mixed_markers(content: str) -> bool:
    """True when both `[N]` and `[xx_<hex>]` markers appear in the same content.

    Mixed output is a softer signal than legacy-only — the model partially
    followed the new contract but regressed for some citations. Surfaces as a
    verifier soft warning so operators see the migration drift without a
    hard-fail.
    """
    has_numeric = bool(_NUMERIC_CITATION_PATTERN.search(content))
    has_legacy = bool(_LEGACY_CITATION_PATTERN.search(content))
    return has_numeric and has_legacy
