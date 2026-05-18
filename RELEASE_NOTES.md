# Release notes

## v0.3.1 (2026-05-18)

Closes two BACKLOG items from the v0.3.0 ship cycle: F5 single-word lowercase tool detection in the query-entity extractor, and abbreviation-aware sentence splitting in the post-synthesis verifier. Both were tracked-not-fixed during v0.3.0 because they sat outside the citation-contract unification scope. Locked architecturally by a separate codex GPT-5.5 high DESIGN session (`019e3a66-313d-7121-b52f-541165732859`, single-turn, NONCE `codex-design-items-6-7-2026-05-18-7e3a9c4b`) per the TWO_SESSIONS rule, then reviewed for impl by the same continuous adversarial-review session that cleared v0.2.0 through v0.3.0 (`019e395a-8fe7-7e00-ad24-05e20fdb2e1a`).

This is a backward-compatible patch release. Public signatures of `extract_query_entities()` and `verify_synthesis_output()` are unchanged. The change touches user-visible verifier behavior (fewer false-fails on abbreviation-heavy synthesis output, more legitimate hard-fails on lowercase-tool comparison queries that previously slipped through with zero entities), which is why the version bumps rather than rides as a silent cleanup.

### What changed

**`extract_query_entities` adds a curated lowercase-tool allowlist.** Shape 5 closes the F5 gap codex documented at Turn 2 of v0.2.x review: a query like `compare bun vs npm` used to return an empty entity list because none of the four existing shapes (capitalized words, internal-cap identifiers, hyphenated identifiers, dotted module paths) caught single-word lowercase tools. The verifier's entity-coverage check then had nothing to verify, and the quality gate's entity-balanced promotion had nothing to promote. The new `src/synthesis/entity_allowlist.py` module carries two frozensets: `LOWERCASE_TOOL_ALLOWLIST` for always-safe names (`bun`, `npm`, `deno`, `pnpm`, `pip`, `yarn`, `cargo`, `docker`, `kubectl`, ...) and `CONTEXTUAL_LOWERCASE_TOOL_ALLOWLIST` for names that collide with ordinary English (`uv`, `go`, `rust`, `tar`, `make`, `mix`, `gem`, `swift`, `crystal`). The contextual tier only fires when the query also carries a technical/comparison cue (`compare`, `vs`, `install`, `runtime`, ...) or when shapes 2-4 (the inherently tech-shaped shapes) found a tech entity. Without that gating, `remove rust from metal` and `how to go faster` would mis-extract. Codex Turn 10 caught a regression where Shape 1 capitalized proper nouns like `Bob` or `Taylor Swift` were enabling the contextual tier, and the fix narrows the enabler to shapes 2-4 only.

**Detection is case-sensitive and respects hyphen/dot dedupe.** Shape 5 pattern `(?<![A-Za-z0-9_.\-])[a-z]+(?![A-Za-z0-9_.\-])` matches only the exact lowercase form, so `PIP` (the proper noun) stays as a Shape 1 entity rather than lowercase-folding to the Python installer. The negative lookbehind and lookahead exclude any letter, digit, dot, hyphen, or underscore on either side of the candidate, so `pip` inside `pip-tools` does not re-emit. Shape 3 already covers the hyphenated form. The standalone `pip` in `pip-tools is a wrapper around pip` still emits because the second occurrence is its own token.

**Post-synthesis sentence splitter no longer breaks at abbreviation periods.** `src/synthesis/sentence_utils.py` adds `protect_abbreviations()` / `restore_abbreviations()` / `split_sentences()` helpers. The protection step replaces the `.` characters inside known English abbreviations (`U.S.`, `U.K.`, `e.g.`, `i.e.`, `etc.`, `vs.`, `cf.`, `Mr.`, `Mrs.`, `Dr.`, `Prof.`, `Inc.`, `Ltd.`, `Ph.D.`, `M.D.`, `No.`, `a.m.`, `p.m.`, `et al.`, ...) with a private sentinel `\x00`, splits on the remaining terminators, then restores the sentinels back to `.`. Casing is preserved. The verifier's `_output_acknowledges_gap` helper now uses `split_sentences()` instead of the local `_SENTENCE_SPLIT` regex, so a sentence like `no source for U.S. market coverage of LinkUp` stays intact rather than splitting at `U.S.` into two fragments where the gap-framing phrase ends up in one half and the entity in the other.

**`verification.py:extract_claims_with_citations` gets the same abbreviation protection.** The claim-extraction regex `([^.!?]+[.!?])\s*\[(\d+)\]` had the same truncation bug: a synthesis line `The U.S. has X.[1]` extracted only ` has X.` as the claim because the regex broke at the first period. Wrapping the regex match in `protect_abbreviations()` / `restore_abbreviations()` (in-function import to avoid module-load coupling) preserves the full claim text.

**Verifier policy stays fail-closed.** Codex's Turn 3 verdict ("fail-closed is correct for the verifier" — false-fail preferred over false-pass) holds. The fix is not a policy relaxation; it makes sentence segmentation precise enough that hard-failures point at real defects rather than splitter artifacts.

### Tests

58 new regression tests in `tests/test_codex_items_6_7_cleanup.py`. Item 7 coverage: shape 1-4 regressions; always-safe lowercase detection for `bun`/`npm`/`deno`/`pnpm`/`pip`; contextual tier suppressed without cues; contextual tier enabled by `compare`/`vs`/`install`/`runtime` cues; contextual tier enabled by a shape 2-4 tech entity; case sensitivity (`PIP` not lowercase-folded); dedupe against existing entities; no bare emission inside hyphenated/dotted matches; verifier hard-fail integration for `compare bun vs npm` with uncovered `npm`; and Q18 + Turn 10 negative regression coverage for `what did Bob make for dinner?` / `how does Alice go faster?` / `did Taylor Swift make an announcement?` / `remove rust from metal`. Item 6 coverage: `protect_abbreviations` / `restore_abbreviations` roundtrip + case preservation + case-insensitive matching; `split_sentences` empty input + non-abbreviation parity + `U.S.` / `e.g.` / honorifics not breaking; verifier integration false-fail elimination per Q6 (3 abbreviation classes) with sources_text explicitly NOT containing the entity so the gap-framing branch runs; verifier integration false-pass guards per Q18 (3 paired abbreviation/entity sentences); claim extractor preserving `U.S.` / `e.g.` / `Mr.` abbreviations.

The prior `test_extract_entities_known_limitations_documented` in `tests/test_codex_t1_fixes.py` inverted: it used to assert `bun`/`npm`/`deno` were NOT extracted as proof of the F5 limitation; the renamed `test_extract_entities_lowercase_tools_now_detected_post_items_6_7` now asserts they ARE extracted.

Full sweep: 364 pass / 52 skip / 0 fail on both branches (delta from v0.3.0 + post-cleanup baseline: +58 new tests, no regressions).

### What did not change

The verifier's hard-fail semantics, the entity-coverage check's escape hatch, the citation-contract `[N]` shape from v0.3.0, the `RESEARCH_*` environment variables, the MCP tool surface, the REST endpoint surface, and the public function signatures of `extract_query_entities()` and `verify_synthesis_output()`. This is a behavior refinement at two narrow seams, not an API change.

---

## v0.3.0 (2026-05-18)

Unifies the two citation contracts that the synthesis stack carried in parallel since the engine and aggregator paths landed in different releases. Locked architecturally by a separate codex GPT-5.5 high DESIGN session (`019e39f7-33ab-7691-ac6d-30c0804b6cdc`, single-turn), then reviewed for impl by the same continuous adversarial-review session that cleared v0.2.0 through v0.2.2 (`019e395a-8fe7-7e00-ad24-05e20fdb2e1a`).

This is the v0.3.0 minor bump because the citation field shape on `mcp__research` and REST `/research` no-preset changes in a way that breaks any caller relying on the old `[xx_<hex>]` contract.

### What changed

**`SynthesisEngine` now speaks the same `[N]` citation contract as everything else.** The aggregator and outline-guided paths have used `[N]` numeric markers since they landed; the engine path (which powers `mcp__research` and REST `/research` no-preset) was the lone holdout, prompting the model for `[source_id]` markers (literal connector hashes like `[tv_a1b2c3d4]`) and parsing them with an inline regex at `src/synthesis/engine.py:128`. After v0.3.0, a caller hitting both surfaces sees the same shape in both outputs.

The migration touches four layers in lockstep. `RESEARCH_SYSTEM_PROMPT` in `src/synthesis/prompts.py` now embeds the shared `CITATION_FORMAT_GUIDE` (the same guide v0.2.2 wired into the aggregator and outline templates), and `build_research_prompt()` renders source blocks as `[1]`, `[2]`, ... so the IDs the model sees match the IDs it is asked to cite back. The engine drops its inline regex in favor of `extract_numeric_citations()` from `src/synthesis/citations.py`, which gained a `CitationSource` protocol and `getattr` fallbacks so it works against both `connectors.base.Source` (engine path, has `.id`, no `.origin`/`.source_type`) and `synthesis.aggregator.PreGatheredSource` (aggregator path, has `.origin`/`.source_type`, no `.id`).

**Citation dicts have a canonical shape across every surface.** Each citation dict now carries seven keys: `number: int`, `id: str` (always `str(number)`, kept string-typed for back-compat), `source_id: str | None` (connector trace like `"tv_a1b2c3d4"` when available), `title`, `url`, `origin`, `source_type`. The two type-divergent fields fall back to `None` when the underlying source does not carry them. `CitationSchema` in `src/api/schemas.py` mirrors the dict: `number` and `source_id` are new fields, `id` retains its string type but its value migrates from connector hash to numeric string.

**`src/synthesis/enhanced.py` is deleted.** A grep at design lock time found no importer in `src`, in tests, in docs, or in `__init__.py`. The file was 677 lines of `[source_id]`-based passage machinery (`EnhancedSynthesizer`, source-id-keyed EVIDENCE blocks, source-id-tagged passages) sitting in-tree but never reachable. The `/synthesize/enhanced` REST route still works as before — it has been built on `SynthesisAggregator` since P0 landed, not `EnhancedSynthesizer`.

**Verifier soft warnings surface citation marker drift.** `output_verifier` now calls `detect_legacy_markers()` and `detect_mixed_markers()` from `src/synthesis/citations.py` and appends a soft warning when the LLM emits the old `[xx_<hex>]` markers despite being prompted for `[N]`, or when it mixes both contracts in a single response. The existing hard-fail at `cited_count == 0` still fires for legacy-only output (because numeric extraction returns zero), and the new soft warning is the diagnostic that explains why.

### Tests

Twenty-eight new regression tests in `tests/test_codex_t8_v030_citation_unification.py` cover the `CitationSource` duck-typing across both source types, the canonical dict shape, drift detection helpers, the engine end-to-end with mocked LLM output, the extended `CitationSchema`, verifier soft warnings on legacy-only and mixed marker output, and a regression asserting `enhanced.py` stays deleted. `tests/test_synthesis.py` reworked to assert the v0.3.0 contract (the prior asserts on `[source_id]` and `[xx_<hex>]` regex were locking the contract this release deletes). `tests/test_codex_t5_citation_extraction.py` updated to match the new seven-key canonical dict shape. `tests/test_cache.py::test_research_tool_signature` mock updated to return the canonical dict shape. Full sweep on the v0.3.0 source tree: 308 passing, 38 skipped, 7 pre-existing LLM-auth failures unchanged from the v0.2.2 baseline (integration tests that need a configured LLM endpoint).

### Migration notes

This is a breaking change for two surfaces. Plan the upgrade if you hit either.

**`mcp__research` and REST `/research` no-preset:** the rendered citation markers change from connector hashes to numeric markers. Any text-level parser that grepped for `\[([a-z]{2}_[a-f0-9]+)\]` against MCP or REST output will see zero matches against v0.3.0 output. The structured citation field on REST also changes: `citation.id` was `"tv_a1b2c3d4"` and is now `"1"`. The connector hash moves to a new `citation.source_id` field. Callers that pattern-matched on `citation.id.startswith("tv_")` to identify Tavily-origin citations should read `citation.source_id` instead.

The shape change on the JSON envelope is additive at the schema level (`number` and `source_id` are new optional fields with sensible defaults), but the value of the existing `id` field is the breaking part. There is no opt-out flag; the design intentionally avoided a permanent dual-emit surface.

REST `/synthesize`, `/synthesize/enhanced`, and `/synthesize/p1`, and MCP `synthesize` already used the `[N]` contract end to end; this release surfaces the `number` and `source_id` fields on those responses too. Existing callers see the same `id` values they always did (numeric string), plus the new fields.

If a downstream consumer breaks unexpectedly, the rollback path is a coordinated revert across `engine.py`, `prompts.py`, `citations.py`, `schemas.py`, `routes.py`, `mcp_server.py`, and the test files. Reverting one file is unsafe because prompt labels and extractors must agree on the same contract.

## v0.2.2 (2026-05-18)

Slice 1 of the post-v0.2.1 BACKLOG cleanup, scoped to three surgical fixes that fit the codex review loop without architectural reshape. Closes another turn of the same codex GPT-5.5 high session `019e395a-8fe7-7e00-ad24-05e20fdb2e1a` (Turn 7) with verbatim "zero remaining findings".

### What changed

**`Contradictions Detected` no longer renders empty stanzas.** The contradiction parser previously appended a placeholder when the LLM emitted a block where `TOPIC:` appeared only as a prose substring, producing `- **Unknown** (moderate):  vs ` in the rendered output (visible on both controlled fixtures and live calls during the v0.2.1 re-test). `_parse_contradictions` now rejects any block missing topic, position_a, or position_b. The MCP render site at `src/mcp_server.py:462` carries a defense-in-depth guard so any malformed `Contradiction` slipping through a different code path (heuristic detector, future caller) also drops cleanly.

**Synthesis prompts now embed a shared citation format guide.** Every prompt that asks the model for inline `[N]` citations (five aggregator styles plus the outline `SECTION` and `REFINE` templates) imports `CITATION_FORMAT_GUIDE` from `src/synthesis/citations.py`. The guide carries three worked examples (single, multi, and co-citation) plus explicit negative examples disambiguating from the `[xx_hex]` format the legacy `SynthesisEngine` path uses. The prior single-sentence instruction (`Use [1], [2], etc.`) was thin enough that smaller models occasionally drifted to `(Author 2024)` or numbered footnotes; the worked examples tighten format compliance without changing any preset behavior. `OUTLINE_PROMPT` is intentionally untouched, since it produces section headings, not citations.

**REST `/research` honors REJECT and PARTIAL-with-zero-good.** The endpoint silently fell through both verdicts since v0.2.0, running synthesis over the same sources the quality gate had just rejected. It now short-circuits with a `Source quality insufficient` response on REJECT (sources all below the reject threshold) and on PARTIAL where no source cleared the pass threshold. Mirrors the v0.2.0 fix already in `/synthesize/p1` and `/synthesize/enhanced`.

### Tests

Fourteen regression tests added in `tests/test_codex_t7_v022_fixes.py`, covering the six contradiction parser edge cases (empty topic, empty position_a, empty position_b, topic-as-prose-substring, fully-populated positive control, mixed-validity); the renderer guard sanity check; four prompt-content assertions for the citation guide; and three REST `/research` integration tests using a mocked `SearchAggregator` + `SourceQualityGate` to confirm REJECT and PARTIAL-zero-good short-circuit before synthesis runs, plus a negative control proving PARTIAL with at least one good source still synthesizes over the good source set only. Full sweep: 126 passing, 7 skipped (LLM-required, unchanged from prior baseline).

### Migration notes

Zero caller-visible API changes for legitimate inputs. Two behavior shifts worth noting:

- Callers that previously got placeholder `- **Unknown** (moderate):  vs ` entries in MCP `synthesize` output will no longer see them. The corresponding REST `/research` and `/synthesize/p1` response field `contradictions` may also be shorter (rejected entries dropped). If any downstream consumer counted contradictions for telemetry, expect the count to be lower-but-truer.
- Callers hitting REST `/research` with a preset (`comprehensive`, `contracrow`, `academic`) and source sets that the quality gate would reject will now receive a `Source quality insufficient` response instead of partial synthesis output. Treat as signal: gather better sources, then re-call. Presets with `run_quality_gate=False` (`fast`, `tutorial`) are unaffected.

The `[xx_hex]` citation format used by `SynthesisEngine` (powering `mcp__research` and REST `/research` no-preset path) remains untouched. Unifying with `[N]` is tracked as v0.3.0 — that's an architectural decision deserving a codex DESIGN pass per the canon-architecture rule.

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
