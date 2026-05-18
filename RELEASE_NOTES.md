# Release notes

## v0.2.1 (2026-05-18)

Closes 2 additional turns (5-6) of the same codex GPT-5.5 high session `019e395a-8fe7-7e00-ad24-05e20fdb2e1a`, again with verbatim "zero remaining findings". Real-world end-to-end testing of the research-workflow skill against the live MCP after v0.2.0 ship surfaced a citation extraction parity drift between MCP and REST that had been latent since the outline-guided synthesis pipeline landed.

### What was broken

MCP `synthesize` with any preset that has `use_outline=True` (`comprehensive`, `academic`, `tutorial`) hard-failed the verifier with "cites none of N provided sources" even when the LLM emitted valid `[N]` citation markers in its output. The OutlinedSynthesis dataclass has no `citations` field, and the MCP wrapper computed `cited_count = len(result.citations) if getattr(...)` which returned zero for every outline result. REST `/synthesize/p1` had parity (it called `_extract_citations_from_content` after outline) but MCP did not.

Real-world impact: any caller using `preset="comprehensive"` over a news-style or narrative-prose source set saw the failure consistently. Aggregator-path presets (`contracrow`, `fast`) were unaffected because the aggregator extracts citations internally.

### What changed

A shared `extract_numeric_citations()` resolver lives in the new `src/synthesis/citations.py`. The aggregator and REST extractors now delegate to it, removing prior subtle divergence (aggregator used 0-indexed bounds; REST used 1-based; results identical for valid input but the unified rule is the REST style which matches the source formatter at `source_formatting.py`).

MCP `synthesize` (preset path) now normalizes citations before building the footer and computing `cited_count`:

```python
result_citations = getattr(result, "citations", None)
if not result_citations:
    result_citations = extract_numeric_citations(result.content, processed_sources)
```

Aggregator-path callers fall through to `result.citations` and pay zero extra cost. Outline-path callers get the parity fix that REST had all along.

### Tests

12 regression tests added in `tests/test_codex_t5_citation_extraction.py`, covering the unit resolver (happy path, dedup, out-of-range, empty inputs, zero-index) plus the headline MCP-level regression: `preset="comprehensive"` with mocked outline content containing `[1][2][3]` must pass verifier and render a citations footer; same preset with no `[N]` must still hard-fail and must not cache. Full sweep: 112 passing, 7 skipped (LLM-required, unchanged from prior baseline).

### Migration notes

Zero caller-visible API changes. Callers that previously got the "cites none" verifier failure on outline presets now get a clean synthesis with a populated citations footer. No prompt changes, no schema changes, no preset behavior changes.

The `[xx_<hex>]` citation format used by `SynthesisEngine` (powering `mcp__research` and REST `/research` no-preset) is untouched. That surface uses a different prompt and a different ID scheme; unifying the two contracts is tracked as a v0.2.2+ candidate.

## v0.2.0 (2026-05-18)

Closes 4 turns of adversarial code review by codex GPT-5.5 high (session `019e395a-8fe7-7e00-ad24-05e20fdb2e1a`, cleared with verbatim "zero remaining findings"). The synthesis pipeline gained two new safety contracts and several latent bug-fixes that affect MCP `synthesize` callers.

### Behavior changes operators will hit

The MCP `synthesize` tool's `style` parameter default changed from `"comprehensive"` to `None`. When `style` is omitted and a `preset` is provided, the preset's own style now wins (`contracrow` → COMPARATIVE, `academic` → ACADEMIC). Previously `style` defaulted to the string `"comprehensive"`, which silently overrode every preset's intended style. The documented call shape `synthesize(query, sources, preset)` was always taking the wrong style. Callers that pass `style` explicitly are unaffected.

The pre-synthesis relevance gate now short-circuits on REJECT and on PARTIAL with zero passed sources. Previously REJECT silently fell through and synthesis ran over all original sources; PARTIAL with empty `good_sources` fell back to all original sources too. Both cases now return a `## Source quality insufficient` block without invoking the synthesizer. Output is not cached; re-call with better sources rather than retrying the same set. The REST `/synthesize/enhanced` and `/synthesize/p1` routes mirror this behavior.

The post-synthesis verifier gained an entity-coverage check. When the synthesis discusses entities from the query that are absent from every retained source, the verifier hard-fails the output. Exception: if the synthesis frames the gap in the same sentence as the uncovered entity ("we have no source available for X", "not in the gathered sources", "could not find", etc.), the verifier downgrades to a soft warning. This catches the hallucination class where the gate filters all sources for one named entity and the LLM writes about it anyway from prior knowledge.

### Per-preset gate config

`SynthesisPreset` gained three optional fields: `quality_gate_reject_threshold`, `quality_gate_pass_threshold`, and `quality_gate_entity_balanced`. The `comprehensive` and `contracrow` presets relax thresholds to 0.2 / 0.4 (from class defaults 0.3 / 0.5) and enable entity-balanced promotion. Multi-vendor comparison queries scored at ~0.4 under the scalar relevance scorer, so the old thresholds rejected legitimate per-vendor sources. Other presets keep class defaults.

Entity-balanced promotion: when the gate filters sources from a multi-entity comparison query, the gate promotes the highest-centrality rejected source per uncovered entity. Centrality favors title matches (3.0) over body density (1.0 base + 0.5 per additional mention, capped at 3.0). Promotion threshold is `>= 2.0`, so one-off incidental mentions do not promote. `apply_overrides()` preserves the new fields.

### Internals

The LLM source-scoring window expanded from 300 to 1500 chars; the prior cap silently truncated relevant evidence in longer sources. Entity matching uses token-boundary regex throughout (so `Exa` no longer matches `example`). The query-entity extractor handles four shapes: capitalized words, lowercase-with-internal-caps (`vLLM`, `iOS`), hyphenated identifiers (`gpt-4o`, `claude-3-5`), and dotted module paths (`llama.cpp`). Single-word lowercase tools (`bun`, `npm`) remain undetected; callers needing precision should pass entities out-of-band.

### Tests

43 regression tests added in `tests/test_codex_t1_fixes.py` covering the new contracts and bug-fixes. Full sweep: 100 passing, 7 skipped (LLM-required, unchanged from prior baseline).

### Migration notes

No required code changes for existing callers. Two behavior shifts worth knowing:

- Callers that omit `style` and pass a `preset` will see the preset's intended style applied (this was the documented behavior all along; previously a bug). If you relied on the silent default override, pass `style="comprehensive"` explicitly.
- Callers that previously got partial synthesis output over gate-rejected sources will now see a `## Source quality insufficient` response instead. Treat as signal: gather better sources, then re-call.

## v0.1.0

Initial release.
