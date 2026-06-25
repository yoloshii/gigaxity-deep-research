# Release notes

## v0.6.0 (2026-06-25)

Generalizes the v0.5.0 entity-coverage demotion to the **pre-synthesis relevance gate**: a content gate scores and labels, it never silently deletes or refuses. Previously a REJECT decision (average source relevance below `reject_threshold`) or the PARTIAL-with-zero-good edge case returned a `## Source quality insufficient` block and never invoked the synthesizer — so a query whose corpus held weak-but-usable sources got a flat refusal instead of a best-effort answer. The gate now **fails open**: when at least one source clears a fail-open floor (`RESEARCH_FAIL_OPEN_MIN_SOURCE_SCORE`, default 0.3 = the REJECT threshold), `synthesize` runs over the set-aside (rejected) sources, prepends a low-relevance caveat to the answer, and marks the result non-cacheable. Only when *no* source clears the floor — no positive evidence to ground an answer — does the gate still hard-refuse with the `## Source quality insufficient` block. Four companion demotions ride along: MINOR contradictions stay internal diagnostics (only MODERATE/MAJOR reach the synthesis prompt, via a canonical `surfaced` view); the contradiction detector's per-source input budget binds (a large source set no longer overruns it, and a starved budget suppresses source content rather than overflowing); rejected sources keep their provenance (identity, score, reason) in the gate result instead of vanishing; and a contradiction-detector error soft-warns rather than hard-failing the synthesis.

**Public contract change.** REJECT and PARTIAL-with-zero-good above the floor now return a caveated synthesis instead of refusing; fail-open syntheses are explicitly non-cacheable (the flag is set on the result, never inferred from the caveat's presence, so the v0.5.0 entity-coverage soft-pass stays cacheable and the two demotions compose); the quality-gate schema gains a `rejected_sources` array; and `SYNTH_CACHE_VERSION` is bumped (4 → 5) so results produced under the old refuse-by-default behavior are not served. A passing synthesis can now open with a `low source relevance (fail-open)` caveat — clients should treat such a result as weakly grounded, not authoritative. The new `RESEARCH_FAIL_OPEN_MIN_SOURCE_SCORE` knob (default 0.3) sets the floor: raise it to refuse more aggressively, lower it to fail open over weaker corpora. The five STRUCTURAL verifier hard gates (empty / reasoning-only / truncated-at-ceiling / failed subcall / zero citations when sources were provided) and the safety gate are unchanged. The agent-instruction block in `CLAUDE.md` / `AGENTS.md`, the bundled `skills/research-workflow/SKILL.md` verdict-handling, and `docs/reference/mcp-tools.md` are updated accordingly. Cleared by codex GPT-5.5 high adversarial review (session `019ef81c`) across design, implementation, and backport turns to verbatim "Zero remaining findings — ship as is."; the suite passes with no new failures on the existing baseline (the pristine-vs-port FAILED set is identical on both `main` and `local-inference`, with thirteen new gate-demotion tests added). Identical gate behavior on `main` and `local-inference`; each branch keeps its own backend default.

## v0.5.2 (2026-06-24)

Documentation alignment — no code change. The bundled `skills/research-workflow/SKILL.md` still taught the pre-v0.5.0 behavior: its "Verifier Verdict Handling" section listed a discussed query entity absent from every retained source as a hard-gate condition (prepending `# Synthesis verification FAILED`) and told agents to retry or re-frame on it. That contradicted the same repo's `CLAUDE.md` / `AGENTS.md` instruction block and `docs/reference/`, both already on the v0.5.0 advisory contract — so an agent following the bundled skill would retry or fall back on a synthesis the engine now passes. The skill's verdict-handling section now matches: entity-coverage is removed from the structural hard-gate list and from the failure-diagnosis bullets, and the soft-warning note documents the graduated advisory caveats (`treat as UNVERIFIED` / "frames the gap" / `surface-form variant` / `emphasis/framing`), that the synthesis still PASSES, that a passed result (or cache hit) no longer implies clean entity-coverage, and that soft warnings are not retried. The five STRUCTURAL hard gates are unchanged. Identical on `main` and `local-inference` (the bundled skill is byte-identical across branches).

## v0.5.1 (2026-06-24)

Documentation alignment for the v0.4.2 / v0.5.0 changes — no code change. `docs/reference/mcp-tools.md` still listed "any discussed entity absent from every retained source" as a hard-failure condition and described the entity-coverage check as hard-failing; both now reflect the v0.5.0 demotion (advisory soft caveat, graduated `treat as UNVERIFIED` / `surface-form variant` / `emphasis/framing`, and the note that a passed result no longer implies clean entity-coverage). `docs/reference/rest-api.md` gains the same soft-condition note. The new `RESEARCH_RCS_CONCURRENCY` knob (v0.4.2) is now documented in `docs/reference/configuration.md` and `.env.example`. `docs/troubleshooting.md` already listed only the structural hard-fail conditions and is unchanged.

## v0.5.0 (2026-06-24)

Demotes the post-synthesis entity-coverage check from a hard gate to an advisory soft warning. A synthesis that discusses a query entity absent from every retained source previously hard-failed (`# Synthesis verification FAILED`, result not cached); it now PASSES with a `*Verification notes:*` caveat folded into the returned text. This removes four documented false-positive classes — ALL-CAPS query framing ("MEASUREMENT PLANE"), lexical surface variants (a source saying "dockerd" for "Docker Engine", "wsl2" for "WSL"), version-suffix forms, and legitimately-absent specific entities — that were destroying correct, well-cited syntheses. The split now only graduates caveat strength: cited-adjacent uncovered entities get a "treat as UNVERIFIED" note, surface variants get a "surface-form variant" note, and framing gets an "emphasis/framing" note. The five STRUCTURAL gates (empty / reasoning-only / truncated-at-ceiling / failed subcall / zero citations when sources were provided) remain HARD.

**Public contract change.** Because `cache_eligible = verdict.passed`, demoted outputs now pass, are cached, and are returned with their `UNVERIFIED` caveat at every cache site (including `/reason`, whose entity set is back-filled by `finalize_synthesis`). So `passed=True` (or a cache hit) no longer implies entity-coverage is clean — clients must inspect `soft_warnings` / the `*Verification notes:*` line for grounding caveats. The agent-instruction block in `CLAUDE.md` / `AGENTS.md` is updated accordingly. Adds surface-variant and uppercase-framing verifier test suites and flips ten existing entity-coverage assertions from hard-fail to soft. Cleared by codex GPT-5.5 high adversarial review (session `019ef81c`, three turns to "Zero remaining findings"); the verifier and entity-extraction suites pass with no new failures on the existing baseline.

## v0.4.2 (2026-06-24)

Sizes the quality-gate relevance scorer and the RCS contextual-summarizer with the same reasoning-aware budget the synthesis and contradiction stages already use (`derive_effective_budget`), and runs the per-source RCS summaries with bounded concurrency. On a reasoning model (the default `Qwen3-30B-A3B-Thinking`), the old flat scorer budget (500 plus a separate 1536 headroom) and the flat 400-token RCS budget were consumed by chain-of-thought before the scores/summary landed in `content`, silently degrading the gate to its keyword-heuristic fallback and dropping contextual summaries; both now share the model's reasoning headroom (scorer budget 2036 → 8692 on the default model). The serial per-source summarize loop becomes a bounded `asyncio.Semaphore` (new `RESEARCH_RCS_CONCURRENCY`, default 4) that preserves source order and propagates errors unwrapped, removing the N×latency cost that timed out the comprehensive preset over many sources. `RESEARCH_LLM_SCORING_HEADROOM` is now a no-op (retained for compatibility). The synthesis cache version is bumped (3 → 4) so results produced under the old starved budgets are not served. Adds RCS budget and concurrency unit tests and updates the scorer-budget test. No API or pipeline-contract change. Cleared by codex GPT-5.5 high adversarial review (session `019ef81c`); the suite passes with no new failures on the existing baseline.

## v0.4.1 (2026-06-24)

Fixes a live `TypeError` on the `reason` MCP tool and the `/reason` REST route. The synthesis wrapper passed a `style` argument that `SynthesisAggregator.synthesize_with_reasoning` did not accept, so any sources-bearing reasoning call (every REST `/reason`, and `reason` in sources-aware mode) raised `synthesize_with_reasoning() got an unexpected keyword argument 'style'`. The signature now accepts `style` (default `comprehensive`) and threads it to the returned `style_used`; the fixed chain-of-thought prompt is unchanged, so `style` only labels the response. The whole suite had masked the crash by mocking the method — a non-mocked regression test now exercises the real signature. No configuration or pipeline-contract change. Cleared by codex GPT-5.5 high adversarial review (session `019ef81c`); the suite passes with no new failures on the existing baseline.

## v0.4.0 (2026-06-19)

Replaces Ref with [Context7](https://context7.com) as the Triple Stack's documentation-lookup MCP. The bundled `research-workflow` skill, the pasteable agent-instruction block in `CLAUDE.md` / `AGENTS.md`, and every setup and reference doc now route library and API documentation queries to Context7. The Triple Stack keeps its name and three-tool shape — it is now Context7 + Exa + Jina. No change to this server's API, configuration, or synthesis pipeline; the only source edits are docstrings, and the unit suite shows no new failures against the existing baseline.

### Context7 as the documentation tool

Context7 is a two-step lookup: `resolve-library-id` maps a library name to a Context7 ID, then `query-docs` returns up-to-date, version-aware documentation. Every routing reference that previously called `ref_search_documentation` now resolves the library and queries its docs. The companion registration changes from Ref's hosted HTTP endpoint to Context7's stdio form (`npx @upstash/context7-mcp`); the updated block is in `docs/reference/mcp-configs.md` and `docs/guides/triple-stack-setup.md`.

### Ref's URL-reading role is retired, not reassigned

Ref also exposed `ref_read_url` for fetching a documentation URL as markdown. Context7 looks up docs by library name, not by reading an arbitrary URL, so it cannot fill that slot — and rather than substitute a weaker tool, the URL-reading fallback chains drop the step. Documentation URLs now read through Jina's free reader (then Brightdata), like any other URL.

Ref stays a drop-in alternative if you prefer it: the docs note that switching the documentation MCP back is a matter of editing the routing references in `skills/research-workflow/SKILL.md` and `CLAUDE.md`.

## v0.3.9 (2026-06-06)

Fixes a residual of the v0.3.8 entity-coverage false-positive: descriptive query vocabulary was still over-extracted as groundable entities, so a correct, well-cited synthesis whose query carried an evaluative adjective ("Optimal hosting...") or an acronym-prefixed compound ("AI-served sites") could still hard-fail the coverage check. Extraction-only change — no API, configuration, or pipeline-contract change, and the post-synthesis verdict logic is untouched. Cleared by codex GPT-5.5 high adversarial review (session `019e721b`) across design, implementation, and port turns with verbatim "Zero remaining findings — ship as is."; the entity-extraction precision suite passes and this change introduces no new unit-test failures on either `main` or `local-inference` (the full-suite baseline is unchanged).

### Descriptive query vocabulary precision

The v0.3.8 citation-adjacency split decides a discussed-but-uncovered query entity by whether it carries an in-sentence `[N]` citation, which separates a fabricated source attribution from coined-label framing. A common descriptive word breaks that signal: an evaluative adjective like "optimal" pervades a synthesis about optimal hosting and lands next to a citation by sheer frequency, so the split misreads it as a fabricated attribution and hard-fails. The fix is upstream, in query-entity extraction, where these words should never have become candidate entities in the first place:

- Evaluative and superlative adjectives ("Optimal", "Best", "Fastest", "Cheapest", "Scalable", ...) are dropped, but only as standalone single-word candidates. A multi-word entity that merely opens with one ("Scalable Capital", "Optimal Dynamics", "Best Buy") is preserved whole.
- Acronym-prefixed descriptive compounds ("AI-served", "ML-based", "LLM-driven", "API-first") are dropped via a curated descriptive-tail set. Real hyphenated identifiers whose tail is a proper noun or ecosystem name ("AR-Foundation", "gRPC-Web", "AI-SDK"), or which carry a digit ("AI-2027") or a non-acronym prefix ("Web-LLM"), are preserved.

The verdict logic is unchanged: any entity that is still extracted remains fully hard-fail eligible. Surface-form / alias normalization (a real entity that sources name only by a paraphrase) is a separate, harder problem and is explicitly out of scope.

## v0.3.8 (2026-06-05)

Fixes a false-positive in the post-synthesis entity-coverage verifier: queries framed around coined or internal labels (a project codename, a decision-option label, or a real name the search missed) were hard-failing otherwise-sound syntheses. No API, configuration, or pipeline-contract change. Cleared by codex GPT-5.5 high adversarial review across design, implementation, and port turns; the unit suite passes on both `main` and `local-inference`.

### Entity-coverage citation adjacency

The entity-coverage check only ever inspects entities drawn from the query, so a discussed-but-uncovered entity is one of two things: a fabricated source attribution (the synthesis binds a `[N]` citation to an entity no retained source covers) or query-framing vocabulary the corpus cannot contain. Only the first is treated as a hard failure. An uncovered entity that appears without an in-sentence citation now downgrades to a soft warning instead of hard-failing the whole synthesis. Gap-framing ("no source for X") is classified per entity, so an entity the synthesis explicitly frames as a gap stays a soft warning even when a different uncovered entity is not framed.

The trade is deliberate: an uncited factual claim about an uncovered entity is soft-flagged rather than blocked, because no offline signal separates it from legitimate framing vocabulary. A citation bound to an uncovered entity still hard-fails, so the fabricated-source-attribution case the verifier was built to catch is preserved.

## v0.3.7 (2026-05-29)

Fixes a cosmetic leak in `synthesize` output and restores FastMCP 3.3.1 test compatibility. The synthesis fix is a behavior change in the free-form synthesis path only — no API, configuration, or pipeline-contract change. Cleared by codex GPT-5.5 high review session `019e721b` ("Zero remaining findings — ship as is.") after nine review turns; the full unit suite passes on both `main` and `local-inference`.

### Synthesis output discipline

The thinking-mode default model (Qwen3-30B-A3B-Thinking) sometimes appended a self-narrated changelog ("Key Corrections Implemented: ...") to its free-form synthesis, and the aggregator returned it verbatim. The four free-form synthesis prompts (`comprehensive` / `concise` / `comparative` / `academic`) and the engine's `RESEARCH_SYSTEM_PROMPT` now instruct the model to wrap its entire answer in `<answer>...</answer>` and to put nothing after the closing tag. A new extractor (`src/synthesis/output_cleanup.py::extract_delimited_answer`) returns the wrapped content and drops anything after `</answer>`, where the changelog lands.

The extractor acts only on the delimiter boundary, never on content semantics, so a legitimate errata or "corrections made" section *inside* the answer is preserved. This delimiter approach was chosen after a nine-turn adversarial review proved that no content heuristic can reliably separate the model's self-changelog from a legitimate correction-notice section. The fallback is non-destructive: when the tags are absent, the full text is returned unchanged. The reasoning path (`synthesize_with_reasoning`) was already immune via its `<synthesis>` tags.

### FastMCP 3.3.1 test compatibility

The MCP tool-registry tests reached into FastMCP's private `_tool_manager._tools`, which FastMCP 3.3.1 removed. They now resolve each tool's coroutine by module attribute (`getattr(mcp_server, name)` — the `@mcp.tool()` decorator leaves the original function bound at the module name) and enumerate registered tools via the public `await mcp.list_tools()`. Test-only change; no runtime behavior is affected.

## v0.3.6 (2026-05-29)

Migrates the default synthesis model off `alibaba/tongyi-deepresearch-30b-a3b` (HuggingFace `Alibaba-NLP/Tongyi-DeepResearch-30B-A3B`), which was delisted from OpenRouter — its provider endpoint now returns HTTP 404 `No endpoints found`, breaking every hosted call. The replacement is **Qwen3-30B-A3B-Thinking-2507** (`qwen/qwen3-30b-a3b-thinking-2507` on OpenRouter, `Qwen/Qwen3-30B-A3B-Thinking-2507` on HuggingFace). This is a default-and-documentation change only — no pipeline logic, API surface, or configuration contract changed. Cleared by a fresh codex GPT-5.5 high review session (`019e721b`) with verbatim "Zero remaining findings — ship as is."

### Why this model

Qwen3-30B-A3B-Thinking-2507 is a near-exact structural match for the model it replaces: a 30B-A3B mixture-of-experts model (~3B active parameters, so the same cheap-to-serve profile), 131072-token context as served on OpenRouter (262K native on HuggingFace), reasoning-tuned, and thinking-mode-only — it emits chain-of-thought to the `reasoning` / `reasoning_content` field that the pipeline's extraction layer (`src/llm_utils.py`) already reads. Its OpenRouter slug contains `thinking`, which the existing reasoning-model detector (`is_reasoning_model`) already matches, so the chain-of-thought output-token headroom (`llm_reasoning_headroom` for synthesis, `llm_scoring_headroom` for the quality-gate scorer) applies with no code change. The synthesis pipeline remains model-agnostic — any OpenAI-compatible chat-completions model still works; this only changes the recommended default.

### What changed

- **Default model.** `src/config.py` `llm_model` default → the new model (OpenRouter slug on `main`, HuggingFace path on `local-inference`). `.env.example` and `docker-compose.yml` updated to match.
- **Context-window map.** `src/llm_utils.py` `MODEL_CONTEXT_WINDOWS` maps both id spellings to 131072 (the OpenRouter-served window; conservative for local HuggingFace deployments that can run the 262K native context). Unknown models still fall back to 32768, so the budget-aware source formatter never over-bounds.
- **Reasoning-model detection unchanged.** `_REASONING_MODEL_MARKERS` already contains `thinking`; no change was needed for the new model to receive chain-of-thought budget headroom.
- **Documentation.** README, CLAUDE.md / AGENTS.md (kept byte-identical), CONTRIBUTING, SKILL, and the `docs/` guides now reference the new model. GGUF quant guidance was genericized: specific community quanter repositories were replaced with a HuggingFace quant-search link and a `<quanter>` placeholder, because Qwen3 GGUF repos were not independently verified at release time. Quantization sizes and VRAM figures (≈18.7 GB Q4_K_M on a 24 GB GPU) are unchanged — they are determined by the 30B-A3B architecture, which is identical between the old and new model.
- **Packaging.** `pyproject.toml` description made model-agnostic (it no longer names a specific model, to avoid another migration the next time a hosted model is delisted); the `tongyi-deepresearch` keyword became `qwen3`.

### What did not change

The six MCP tool names and surface contracts, the REST endpoint shapes, the `[N]` citation contract, the preset thresholds, the `RESEARCH_*` environment variables, the synthesis / quality-gate / contradiction-detection / outline / RCS behavior, the verdict envelope, and the cache key fingerprint. The Phase 0 synthesis chokepoint shipped in v0.3.5 is untouched. This release changes which model the pipeline talks to by default and the docs that name it — nothing else.

---

## v0.3.5 (2026-05-25)

Centralizes every synthesis-result code path through a single post-synthesis chokepoint and fixes three real call-site deficiencies the prior architecture left in place. Locked by a codex GPT-5.5 high DESIGN session (`019e5b0f`, 6 turns) and cleared by the same continuous-review session across IMPL (3 turns), both with verbatim "Zero remaining findings — ship as is." per the ONE_SESSION rule (codex was reviewer-only throughout — Claude authored DESIGN and IMPL, codex critiqued both against the locked specification). The DESIGN draws on two May 2026 Peking University papers — LIFE-HARNESS (arXiv:2605.22166) on runtime-harness adaptation for deterministic LLM agents, and DeepWebBench (arXiv:2605.21482) on deep-research evaluation finding derivation and calibration carry ~70% of failure mass while retrieval is only ~12-14% — and treats Phase 0 as the substrate every later phase (1 through 7) builds on.

This is a backward-compatible release. No public signature, configuration, citation contract, or env-var change: the MCP tool surface and REST endpoint shapes are byte-stable; the `SynthesisVerdict` / `SynthesisVerdictSchema` envelope is extended with new fields that all default to empty/None so existing clients reading only `passed` / `hard_failures` / `soft_warnings` observe no change. The release lifts an architectural invariant on the synthesis call graph and closes a hallucination-detection silent-no-op on three call sites; the user-visible behavior change is that those three previously-quiet surfaces now run the full post-synthesis verifier with entity-coverage threaded through.

### The bugs

Three of the ten synthesis call sites had real deficiencies hidden by the pre-Phase-0 hand-rolled verification pattern:

1. **REST `/research` (preset path)** went directly from `aggregator.synthesize(...)` to `ResearchResponse(...)` with **no `verify_synthesis_output()` call between them**. Every other synthesis surface ran the verifier; this one shipped its content untouched. A preset-driven research call that hallucinated entities absent from every retained source was returned as the canonical answer with no failure header, no soft warning, no cache-eligibility check.
2. **REST `/research` (no-preset path)** called the verifier but passed `query_entities=None` and `sources_text=None`. The entity-coverage check inside `verify_synthesis_output` only fires when both are non-None, so the strongest hallucination-catch the v0.3.x line introduced was silently no-op on this surface.
3. **MCP `research`** had the same shape as the REST no-preset path — verifier invoked, both entity-coverage arguments passed as None, check silently bypassed.

Pre-Phase-0 the verification logic was duplicated at every call site (~30-50 lines per surface across REST `/synthesize`, `/reason`, `/synthesize/enhanced`, `/synthesize/p1`, MCP `synthesize` preset+no-preset, MCP `reason` sources-mode, plus the three real-debt sites above). Each surface independently constructed `query_entities`, `sources_text`, called `verify_synthesis_output(...)`, decided whether to `annotate_with_verdict(...)`, decided whether to cache. The duplication invited drift: every new surface had to remember the full pattern, and the three call sites above demonstrably forgot pieces of it.

### What changed

**A `FinalizedSynthesis` chokepoint normalizes every synthesis result through a single pure post-result function.** `src/synthesis/finalization.py` introduces `finalize_synthesis(*, query, result, sources, contradiction_result=None, query_entities=None, surface)` — the one function that takes a `dict` (`SynthesisEngine.synthesize` / `.research` return shape), an `AggregatedSynthesis` (`SynthesisAggregator.synthesize` / `.synthesize_with_reasoning`), or an `OutlinedSynthesis` (`OutlineGuidedSynthesizer.synthesize`), normalizes the three shapes into one common view, computes `query_entities` if not pre-provided, composes `sources_text` from the post-gate source set, calls `verify_synthesis_output(...)`, produces a `safe_content` (raw content if the verdict passed without soft warnings, otherwise `annotate_with_verdict` output), and returns a `FinalizedSynthesis` carrying `raw_content`, `safe_content`, `citations`, `source_attribution`, `confidence`, `style_used`, `llm_output`, the verdict, a `cache_eligible` flag (strict `verdict.passed` mirror), the surface tag, and any surface-specific `extras` (outline sections / sections dict / critique payload / engine `model` / engine `usage`). The function imports nothing from `engine.py`, `aggregator.py`, or `outline.py` beyond the result-type dataclasses — it cannot call a core synthesis method.

**Five surface wrappers are the only allowed callers of the three core synthesis classes.** `src/synthesis/wrappers.py` exposes `run_engine_synthesize`, `run_engine_research`, `run_aggregator_synthesize`, `run_aggregator_synthesize_with_reasoning`, and `run_outline_synthesize`. Each wrapper constructs the class instance internally (so callers do not import the class names), invokes its single method, and pipes the result through `finalize_synthesis(...)`. Engine wrappers raise a new `SynthesisInvocationError` when the engine returned an `{"error": ...}` dict — a true LLM-invocation failure distinct from a verifier hard-fail — so callers can surface that as HTTP 500 or an MCP error-line without the verifier seeing exception-message content as a synthesis attempt.

**An AST CI gate enforces the chokepoint.** `scripts/audit_synthesis_callers.py` walks `src/` and flags any non-allowlisted file that imports `SynthesisEngine`, `SynthesisAggregator`, or `OutlineGuidedSynthesizer` by static name (catches simple, aliased, submodule-pathed, and parenthesized-multiline `from ... import` forms), any `import src.synthesis.<core_module>` form, any `from src.synthesis import {aggregator|engine|outline}` submodule-name alias, any `<expr>.SynthesisAggregator|SynthesisEngine|OutlineGuidedSynthesizer` attribute access, any `<mapping>["SynthesisAggregator"|...]` subscript access, and the dynamic-lookup escape hatches (`getattr(_, "CoreClass")`, `globals()["CoreClass"]`, `eval("CoreClass")`, `importlib.import_module("...synthesis.aggregator")`, `__import__("src.synthesis.aggregator", fromlist=[...])`). The allowlist is the three core modules, the synthesis package `__init__.py`, and `wrappers.py`. `finalization.py` is intentionally NOT allowlisted: the gate must continue to enforce its "no core-method calls" contract; its existing imports are all result types or helpers, none of which appear in the forbidden-class set.

**All ten call sites are refactored through the wrappers.** REST `/research` (both preset and no-preset branches), `/synthesize`, `/reason`, `/synthesize/enhanced`, `/synthesize/p1` (outline + aggregator branches), and MCP `research`, `synthesize` (preset+outline, preset+aggregator, no-preset branches), and `reason` (sources mode) now route through the appropriate wrapper. Seven of the ten refactors are mechanical surface-parity (same observable behavior, hand-rolled verification block replaced by the wrapper-internal finalize call). Three are the real-debt fixes named in **The bugs** above: REST `/research` preset gains verification, REST `/research` no-preset and MCP `research` gain `query_entities` + `sources_text` threading. After Phase 0, every synthesis surface guaranteedly runs the full verifier with entity-coverage active over the post-gate source set, and the cache-only-on-pass invariant is enforced at one site.

**The verdict envelope is extended for the later phases.** `SynthesisVerdict` (in `src/synthesis/output_verifier.py`) adds five fields with backward-compatible defaults: `verdict_class: Literal["pass", "calibrated_gap", "hard_fail"]` (Phase 0 derives this from `hard_failures` shape via `__post_init__` so direct construction can't produce a contradictory state — e.g. `SynthesisVerdict(hard_failures=["x"])` now has `verdict_class == "hard_fail"` rather than the dataclass default `"pass"`; an explicit `"calibrated_gap"` with empty `hard_failures` is preserved as the Phase 1 acknowledged-gap signal), `failure_codes: list[str]` (Phase 1 will populate with `gap_unscoped` / `gap_section_polluted` / `gap_declared_but_section_open` / `gap_group_heading_unsupported`), `warnings: list[VerdictWarning]` (Phase 5a/5b/6 will populate with `coverage_grid_uncited_uncovered_cells`, `uncovered_cell_unacknowledged`, and `tier_insufficient` signals respectively), `diagnostics: VerdictDiagnostics` (field-granular slots for gate diagnostics, tier composition, gap declarations, contracrow result, coverage-grid summary, BM25 mismatch info), and `retry_advice: RetryAdvice | None` (Phase 6 surface-aware caller-action signal). `src/api/schemas.py` adds matching Pydantic schemas (`VerdictWarningSchema`, `VerdictDiagnosticsSchema`, `RetryAdviceSchema`) and extends `SynthesisVerdictSchema` with the same five fields. A new `verdict_to_schema(verdict)` converter in `schemas.py` is the single conversion path; all four REST `verification=` blocks (`/synthesize`, `/reason`, `/synthesize/enhanced`, `/synthesize/p1`) route through it. Existing REST clients that read only `passed` / `hard_failures` / `soft_warnings` observe identical responses.

### Tests

68 new bug-first Phase 0 tests, plus 5 existing test files migrated from `MagicMock()`-shaped synthesis results to real `AggregatedSynthesis` / `OutlinedSynthesis` instances (the prior duck-typed mocks tripped `finalize_synthesis`'s `isinstance` dispatch under the new strict typing). `tests/test_finalization.py` (29 tests) covers the three result-shape normalizers, the soft / hard-fail / clean verdict paths, query-entities short-circuit when pre-extracted, the duck-typed `Source` / `PreGatheredSource` corpus, the new envelope `__post_init__` rules (verdict_class reconciliation across all four `(hard_failures, verdict_class)` combinations), the `verdict_to_schema` round-trip including warnings + diagnostics + retry-advice payloads, and the unsupported-result-type TypeError guard. `tests/test_wrappers.py` (10 tests) covers each wrapper's class-instantiation + method-invocation contract, the `SynthesisInvocationError` raise-before-finalize path on engine error dicts, and the threading of `surface` / `contradiction_result` / `query_entities` into finalize. `tests/test_audit_synthesis_callers.py` (29 tests) covers positive controls for every bypass shape the gate must catch (simple, multiline parenthesized — codex T5 regression, aliased, submodule-pathed, bare-module, dynamic via `getattr` / `globals` / `eval` / `importlib.import_module` / `__import__`, submodule-name alias — codex T1 F2, attribute access — codex T1 F2, subscript access via `vars` / `__dict__` / `locals` — codex T2 F1), negative controls (legitimate `SynthesisStyle` / `PreGatheredSource` / submodule type-only imports), allowlist behavior, the no-finalization-allowlist rule (codex T1 F1), the `src/`-only scope, and the exit-code contract. Full sweep: 518 pass / 52 skip / 0 fail on `local-inference`. On `main` the OpenRouter-client integration tests remain auth-gated, the same as prior releases.

### What did not change

The six MCP tool names + surface contracts (`search`, `research`, `ask`, `discover`, `synthesize`, `reason`), the REST endpoint shapes, the `[N]` citation contract, the preset thresholds, the `RESEARCH_*` environment variables, the `extract_query_entities()` / `verify_synthesis_output()` public signatures, the `annotate_with_verdict()` output format, the cache key fingerprint, the quality gate / contradiction detector / RCS / outline-synthesis / entity-balanced-promotion behavior, and the citation-marker drift warnings. The five later phases (1 entity-section parser → 2a skill layer → 3 provenance tiering → 4 contracrow unification → 5a/b coverage grid → 6 audited preset + retry advice → 7 offline evolution loop) all land on top of this Phase 0 substrate; nothing about them ships today.

---

## v0.3.4 (2026-05-23)

Fixes a false-positive class in the post-synthesis entity-coverage verifier. The verifier extracts candidate entity names from the query and hard-fails the synthesis when it discusses an entity that is absent from every retained source — the hallucination-catch for "the relevance gate filtered the only source for a vendor, but the model wrote about it anyway". The query-entity extractor over-matched: it pulled generic hyphenated compounds (`opt-out`, `real-time`, `pre-recorded`), capitalized common words and language names (`English`), compliance acronyms (`BAA`), and bare query verbs (`Need`), then hard-failed an otherwise-good synthesis whenever one of those pseudo-entities was missing from the post-gate sources. Locked by a codex GPT-5.5 high DESIGN session (`019e5031`) and cleared by a fresh IMPL session (`019e5047`) per the TWO_SESSIONS rule, both with verbatim "zero remaining findings".

This is a backward-compatible patch release. No public signature, configuration, citation contract, or env-var change: `extract_query_entities()` and `verify_synthesis_output()` keep their signatures; the extractor simply returns fewer junk candidates, so the verifier and the gate's entity-balanced promotion both see a cleaner entity set.

### The bug

`extract_query_entities` had two over-matching classes. The hyphenated-identifier shape matched any all-lowercase English compound (`opt-out`, `real-time`, `speech-to-text`) despite its contract of "identifiers with caps or digits", and the capitalized-word shape emitted common words it should have filtered (`English`, `Need`, `BAA`). Every extracted pseudo-entity is treated as hard-fail-eligible by the verifier, so a synthesis that discussed `opt-out` while the retained sources happened not to contain that exact token was hard-failed and relayed with a "Synthesis verification FAILED" header — even though `opt-out` is not a vendor/product/library and its absence is not a hallucination signal.

### What changed

- **The hyphenated shape is gated by a `cap-or-digit` predicate**: a hyphenated candidate is kept only if it carries an uppercase letter or digit (`gpt-4o`, `claude-3-5`, `Nova-3`) or is a curated all-lowercase package name in the new `LOWERCASE_HYPHENATED_TOOL_ALLOWLIST` (`scikit-learn`, `llama-cpp`, `react-dom`, ...). Generic compounds (`opt-out`, `real-time`, `pre-recorded`) are dropped. The pattern uses negative lookarounds instead of `\b` so a 4+-hyphen chain cannot partial-match its prefix, while a trailing sentence period is still allowed (`Nova-3.` matches).
- **The dotted-path shape gets the same boundary**, so it can no longer start after a hyphen and resurrect the suffix of a gated-out compound (`pre-recorded.wav` no longer yields `recorded.wav`).
- **Stopwords gained language names and compliance acronyms** (`English`, `French`, ...; `BAA`, `SOC`, `HIPAA`, `GDPR`, `PCI`, ...), which are subjects of comparison, not vendor entities. Vendor acronyms (`AWS`, `GCP`, `IBM`) stay extractable.
- **Sentence-initial query verbs are stripped before lowercase prose** (`Recommend the best ...` → nothing; `...Nova-3. Need diarization` → drop `Need`). A verb that fronts a capitalized product name (`Review Board`, `Find My Device`) is kept at every position, so it can never degrade into a generic tail bucket that would spuriously satisfy coverage.

### Tests

28 new bug-first tests in `tests/test_entity_extraction_precision.py`: the reported query yields exactly its three real entities; generic compounds / language names / acronyms are dropped; real identifiers (`gpt-4o`, `Nova-3`, `scikit-learn`, `llama-cpp`, ...) still extract; the dotted shape no longer resurrects compound suffixes; sentence-initial verbs strip before lowercase but verb-fronted products survive at every position; the verifier no longer hard-fails on a dropped generic term but still catches a genuine uncovered entity; and the gate's entity-balanced promotion buckets only real entities. Full sweep on `local-inference`: 450 pass / 52 skip / 0 fail (+28 from this fix, no regressions). On `main` the OpenRouter-client integration tests remain auth-gated, the same as prior releases.

### What did not change

The verifier's hard-fail semantics and gap-framing escape hatch, the entity-coverage check itself, the `[N]` citation contract, the preset thresholds, the `RESEARCH_*` environment variables, the MCP tool and REST endpoint surfaces, and the public signatures of `extract_query_entities()` and `verify_synthesis_output()`. Two residuals are documented in code rather than fixed here: a lowercase-plus-digit compound (`tier-2`) still passes the cap-or-digit test, and recovering an entity from a leading imperative (`Evaluate Tavily` → `Tavily`) is deferred to a future typed-entity-metadata layer — both are bounded and neither affects the no-degradation invariant.

---

## v0.3.3 (2026-05-21)

Fixes contradiction detection silently no-op'ing on the configured reasoning model. The contradiction detector was the one structured LLM call left in the synthesis pipeline still using a flat output-token budget; on the 30B reasoning model its chain-of-thought consumed that budget before the structured output landed, so the detector reported a parse failure and surfaced no contradictions — even for the `contracrow` preset whose entire purpose is finding source disagreements. Locked by a codex GPT-5.5 high DESIGN session (`019e48fe`) and cleared by a fresh IMPL session (`019e4904`) per the TWO_SESSIONS rule, both with verbatim "zero remaining findings".

This is a backward-compatible patch release. No public signature, configuration, citation contract, or env-var change: the fix reuses the existing `RESEARCH_LLM_REASONING_HEADROOM` budget helper that the synthesis aggregator, the outline synthesizer, and the relevance scorer already use.

### The bug

`ContradictionDetector.detect` called the LLM with a flat 2000-token output budget under `PARSE_REQUIRED`. On the configured reasoning model (Tongyi-DeepResearch 30B), chain-of-thought consumed that budget, so the structured `TOPIC / POSITION_A / SOURCE_A / ...` blocks arrived truncated or never left the `reasoning` field. The extractor correctly rejected the non-answer (a truncated or reasoning-only response is not a valid parse source), `detect()` returned `parse_failed=True`, and the post-synthesis verifier emitted "contradiction detection could not be parsed — contradictions may exist but were not surfaced". Every other LLM call in the synthesis pipeline already derived a reasoning-aware budget; the contradiction detector was the one that did not.

### What changed

`detect()` now derives the model-aware output budget with the shared `derive_effective_budget(2000, model)` helper: reasoning models get `min(2000 + RESEARCH_LLM_REASONING_HEADROOM, RESEARCH_LLM_MAX_TOKENS)` so chain-of-thought finishes before the structured blocks are emitted, while non-reasoning models keep the flat 2000. The budget is computed at the `detect()` operation boundary and passed explicitly; `_call_llm` stays a raw forwarder. No retry was added, and a parse failure still surfaces as the honest advisory warning rather than falling back to the noisy keyword heuristic — budget starvation was the root cause, and the detector's failure is already advisory (the verifier annotates it), not a silent degradation. The keyword heuristic remains scoped to the transport-error and no-LLM-client paths, unchanged.

### Tests

8 new bug-first tests in `tests/test_contradiction_budget.py`: the reasoning-model budget is derived and passed (`min(2000 + headroom, max_tokens)`); a non-reasoning model keeps the flat 2000; empty and malformed responses stay `parse_failed=True` without invoking the heuristic; a transport exception still degrades to the heuristic; `NO_CONTRADICTIONS` and a well-formed contradiction block behave unchanged; and the fewer-than-two-sources short-circuit makes no LLM call. Full sweep on `local-inference`: 422 pass / 52 skip / 0 fail (+8 from this fix, no regressions). On `main` the OpenRouter-client integration tests remain auth-gated, the same as prior releases.

### What did not change

The contradiction prompt and parser, the `detect()` public signature, the answer-budget base (2000) — and therefore the product behavior for non-reasoning models — the preset thresholds, the `[N]` citation contract, and the `RESEARCH_*` environment variables (the fix reuses the existing `RESEARCH_LLM_REASONING_HEADROOM`; no new setting).

---

## v0.3.2 (2026-05-21)

Hardens the pre-synthesis source-relevance gate (which rejected on-topic sources for verbose multi-sentence queries) and adds an optional `gate_focus` precision lever. The gate work fixes two compounding causes (A2 + Q1) with a safety cap (A1) on the degraded path; `gate_focus` (Q2) lets a caller score source relevance against a short focus string instead of the full query. Each piece was locked by a separate codex GPT-5.5 high DESIGN session per the TWO_SESSIONS rule and reviewed for impl by a fresh session, all cleared with verbatim "zero remaining findings": the scorer-gate hardening (DESIGN `019e4569` Turns 3-5 / IMPL `019e4640`) and `gate_focus` (DESIGN `019e4683` / IMPL `019e469b`). It builds on the scorer-provenance observability (`scorer_path` / `fallback_reason`) shipped earlier.

This is a backward-compatible release. The new configuration is one additive, defaulted setting (`RESEARCH_LLM_SCORING_HEADROOM`); the new response fields are the additive, defaulted `gate_degraded` and `gate_focus`; the new request inputs are the optional `gate_focus` parameter on MCP `synthesize` and the field on REST `/research`, `/synthesize/enhanced`, and `/synthesize/p1`. No public signature, citation contract, or env-var rename.

### The bug

The relevance gate scores each source 0-1 for query relevance, then PROCEED/PARTIAL/REJECTs on the average. For verbose queries it wrongly REJECTed relevant sources because:

1. **The LLM scorer silently fell back to a keyword heuristic.** The configured reasoning model (Tongyi-DeepResearch 30B) ran its chain-of-thought inside a flat 500-token scoring budget, so the per-source score block never landed in `content` (empty or truncated). The extractor returned an empty string, the parsed score count mismatched the source count, and the gate dropped to the keyword heuristic with no retry.
2. **The keyword heuristic diluted with query length.** It scored `matched_terms / len(query_terms)`, so a 38-term brief drove every source's score toward zero while a 4-term query over the same sources passed. Long, on-topic queries were the worst case.

### What changed

**A2 — reasoning-aware scoring budget, a strict-JSON retry, and a count-validated parser.** The scoring `_call_llm` now uses `min(500 + RESEARCH_LLM_SCORING_HEADROOM, RESEARCH_LLM_MAX_TOKENS)` on reasoning models (headroom default 1536; non-reasoning models keep the flat 500), so chain-of-thought finishes before the scores are emitted. On an empty or count-mismatched first attempt, `_score_sources` issues one retry with a strict-JSON directive ("Output ONLY a JSON array of exactly N floats in [0,1]..."); a second failure falls back to the keyword heuristic with provenance `llm_fallback_heuristic`. At most two scoring calls. The rewritten `_parse_scores(response, expected_count)` scans every `[...]` candidate and accepts the first JSON array of exactly `expected_count` values that are *already* in [0,1] — an out-of-range value disqualifies the whole candidate, with no clamp-to-accept — and falls back to a per-line scan that strips a leading enumerator or source-label and prefers a decimal in [0,1] over a bare integer.

**Q1 — a de-diluted keyword heuristic.** The fallback heuristic now scores `1 - exp(-0.25 * matched_core_terms)`, where `matched_core_terms` counts the distinct query terms (minus a dedicated lowercase scoring stopword set, separate from the entity stopword set) matched at a token boundary across title + content. The score no longer depends on query length, so a long brief and a short query over the same relevant sources land in the same band (roughly 0.22 / 0.39 / 0.53 for 1 / 2 / 3 matched terms). It ships with the current preset thresholds; threshold recalibration against a fixture corpus is tracked separately.

**A1 — an evidence-gated rescue on the degraded path, plus a `gate_degraded` signal.** When the LLM scorer has failed (`scorer_path == "llm_fallback_heuristic"`) and the keyword-heuristic average would force a REJECT, the gate now retains the sources that individually clear the pass threshold — a PARTIAL over a strict subset — instead of discarding everything; when no source clears it, the REJECT stands (fail-closed). The rescue is scoped to the degraded fallback only: a confident LLM REJECT and the primary `heuristic_only` path are untouched. A new `gate_degraded: bool` (default `False`) is set on every `llm_fallback_heuristic` result and surfaced so consumers know the keyword heuristic produced the scores — as a field on the REST `QualityGateSchema` (`/research`, `/synthesize/enhanced`, `/synthesize/p1`) and as a one-line caveat in the MCP `synthesize` markdown.

**Q2 — an optional `gate_focus` precision lever.** `SourceQualityGate.evaluate` / `evaluate_sync`, the MCP `synthesize` tool, and the three gated REST endpoints (`/research`, `/synthesize/enhanced`, `/synthesize/p1`) accept an optional `gate_focus`. When set, source relevance is scored against the focus instead of the (possibly verbose) full query — both the LLM scorer and the keyword heuristic judge against it. The full query still drives entity extraction, the REJECT search suggestions, and the post-synthesis verifier. Entity-balanced promotion is skipped under an active focus: it ranks rejected sources by full-query entity centrality with no focus-relevance floor, so under a focus it could otherwise resurrect a vendor-central but focus-irrelevant source and defeat the narrowing. The applied focus is echoed back for observability — `QualityGateResult.gate_focus`, the REST `QualityGateSchema`, the MCP `quality_gate` metadata, and a one-line caveat on the success / REJECT / zero-good render paths. The MCP `synthesize` cache key appends the normalized focus only when one is active, so existing unfocused cache entries still hit. `gate_focus` is never auto-derived; omitted / None / whitespace is byte-identical to prior behavior.

### Tests

30 new regression tests for the gate hardening, written bug-first: `tests/test_a2_scorer_hardening.py` (14), `tests/test_q1_heuristic_dedilution.py` (7), `tests/test_a1_evidence_gated_rescue.py` (9). They assert directly at `_score_sources` (call count, `scorer_path`, budget) so an A1 rescue cannot mask an A2 regression, plus parser edge cases, length-independence of the heuristic, and the rescue / fail-closed / `gate_degraded` matrix across all scorer paths. `tests/test_p0_enhancements.py` recalibrated one heuristic-evaluation assertion onto the comprehensive-preset path. A further 12 cover `gate_focus` in `tests/test_q2_gate_focus.py`: both scorers judge against the focus; entity-balanced promotion runs without a focus but is skipped under one; the echo lands on every result branch including REJECT and zero-good; the cache discriminator separates focused from unfocused while leaving the unfocused key unchanged; `evaluate_sync` symmetry; whitespace falls back to no-focus. Full sweep on `local-inference`: 414 pass / 52 skip / 0 fail. On `main` the integration tests that construct an OpenRouter client require a configured LLM endpoint, the same auth-gated tests as prior releases.

### What did not change

The preset thresholds (benchmark recalibration against a fixture corpus is still deferred), the post-synthesis verifier (it still scores entity coverage against the full query, unaffected by `gate_focus`), the `[N]` citation contract, the `RESEARCH_*` environment variables apart from the additive `RESEARCH_LLM_SCORING_HEADROOM`, and the existing MCP tool / REST endpoint surfaces apart from the additive optional `gate_focus` inputs and the additive `gate_degraded` / `gate_focus` response fields.

---

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
