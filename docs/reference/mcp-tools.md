# MCP tool reference

Full input/output reference for the **six** stdio MCP tools exposed by Gigaxity Deep Research. Tools register under whatever alias you set in `~/.claude.json` — `mcp__<alias>__<tool>` is the call syntax.

The stdio surface returns **markdown strings**, not JSON, so the agent can pipe results straight into a response. The matching REST endpoints (under `/api/v1/`) return structured JSON shapes — see [`rest-api.md`](rest-api.md) for those.

The tools split into **two primitives** (raw and combined behavior in one call) plus **four deep-research tools** (drive each step independently).

## Common parameter

Every tool accepts an optional `api_key: str | None = None` parameter. When set, it overrides `RESEARCH_LLM_API_KEY` for that call only — used in multi-tenant deployments to bill each user's calls to their own LLM endpoint account. `search` accepts the parameter for surface consistency but ignores it (no LLM call).

The matching REST endpoints accept the same per-request override either via the request body's `api_key` field or via the `X-LLM-Api-Key` header.

---

## Primitives

### search

Raw multi-source aggregation across SearXNG, Tavily, and LinkUp with RRF fusion. **No LLM call.**

**Input:**

| Field | Type | Default | Notes |
|---|---|---|---|
| `query` | str | required | The search query |
| `top_k` | int | `10` | Results per source (1–50) |
| `api_key` | str \| null | null | Accepted for consistency; ignored (no LLM call) |

**Output (markdown):**

```
# Search Results for: {query}

## [1] {title}
**URL:** {url}
**Source:** {connector_name} (score: {score:.3f})

{content snippet up to 500 chars}

## [2] ...

---
*{N} results from ['searxng', 'tavily', 'linkup'] (configured: ['searxng', 'tavily', 'linkup'])*
```

The trailer's first list shows connectors that **returned results** for this query; the parenthetical `configured:` list shows connectors that the aggregator initialized (i.e. their env keys were set at MCP boot). When the two lists diverge:

- `configured: ['searxng']` only → Tavily / LinkUp env keys are unset; the aggregator silently dropped them at init. See [troubleshooting.md](../troubleshooting.md#search--connector-errors) to enable 3-way fan-out.
- `from ['searxng']` with `configured: ['searxng', 'tavily', 'linkup']` → the other connectors errored or returned empty for this query. Check the MCP's stderr log for `Tavily search error` / `LinkUp search error`.

**Use when:** you want raw search hits without paying for synthesis tokens, or when you'll feed the results into your own pipeline.

### research

Combined pipeline: multi-source search **plus** LLM synthesis with citations, in a single call.

**Input:**

| Field | Type | Default | Notes |
|---|---|---|---|
| `query` | str | required | Research query |
| `top_k` | int | `10` | Results per source |
| `reasoning_effort` | str | `"medium"` | `"low"` (concise) / `"medium"` (balanced) / `"high"` (academic) |
| `api_key` | str \| null | null | Per-request LLM key override |

**Output (markdown):**

```
# Research: {query}

{synthesized answer with inline [1], [2] citation markers}

## Citations

- [1] [{title}]({url})
- [2] [{title}]({url})

---
*{N} sources from ['searxng', 'tavily', 'linkup'] (configured: ['searxng', 'tavily', 'linkup'])*
```

The trailer follows the same shape as `search` — see the `search` notes above on interpreting `from` vs `configured` divergence.

**Use when:** you want the simple search-then-synthesize pipeline without managing the discover→read→synthesize chain manually.

---

## Deep-research tools

### ask

Quick conversational answer. **Direct LLM call, no search hop.**

**Input:**

| Field | Type | Default | Notes |
|---|---|---|---|
| `query` | str | required | Question to answer |
| `context` | str | `""` | Optional system-context string fed to the LLM |
| `api_key` | str \| null | null | Per-request LLM key override |

**Output:** the LLM's response text, returned as-is.

**Use when:** the question is answerable from model knowledge, speed matters, and you don't need citations.

### discover

Exploratory expansion plus knowledge-gap detection. Returns the knowledge landscape and a ranked source set scored against detected gaps.

**Input:**

| Field | Type | Default | Notes |
|---|---|---|---|
| `query` | str | required | Topic to explore |
| `top_k` | int | `10` | Results per source |
| `identify_gaps` | bool | `true` | Run gap-detection LLM call |
| `focus_mode` | str | `"general"` | One of `general`, `academic`, `documentation`, `comparison`, `debugging`, `tutorial`, `news` |
| `api_key` | str \| null | null | Per-request LLM key override |

**Output (markdown):**

```
# Discovery: {query}

*Focus Mode: {name}* - {description}

## Knowledge Landscape

**Explicit Topics:** topic_a, topic_b, ...
**Implicit Topics:** topic_c, ...
**Related Concepts:** concept_a, ...

## Knowledge Gaps

- 🎯 **{gap}** ({importance}): {description}
- ...

## Sources ({N})

- [{title}]({url})
- ...

## Recommended Deep Dives

- {url}
- ...

---
*Search expansion: enabled*
*Gap focus: {comma-separated categories}*
*Search backends configured: ['searxng', 'tavily', 'linkup']*
```

The final `configured:` line surfaces which connectors initialized at MCP boot. If only `['searxng']` is shown, Tavily / LinkUp env keys were unset — see `search` above and [troubleshooting.md](../troubleshooting.md#search--connector-errors) to enable 3-way fan-out. (Unlike `search` / `research`, `discover` does not surface which connectors actually returned content for this query — the Explorer wraps the aggregator and doesn't expose per-connector raw results.)

**Use when:** cold-start research, mapping a topic before drilling, or driving a follow-up `synthesize`/`reason` step from the recommended deep-dive URLs.

### synthesize

Citation-aware synthesis over caller-provided sources. **Does not search.** Pass sources you've already fetched (e.g. via `mcp__jina__parallel_read_url`).

**Input:**

| Field | Type | Default | Notes |
|---|---|---|---|
| `query` | str | required | Synthesis focus / question |
| `sources` | list[dict] | required | Pre-gathered sources (see shape below) |
| `style` | str \| null | `null` | One of `comprehensive`, `concise`, `comparative`, `academic`, `tutorial`. When `null` and `preset` is set, falls through to the preset's own style; when `null` and no preset, defaults to `comprehensive`. Explicit value always overrides the preset. |
| `preset` | str \| null | null | Pipeline preset: `comprehensive`, `fast`, `contracrow`, `academic`, `tutorial` |
| `api_key` | str \| null | null | Per-request LLM key override |

Each `sources[i]` dict:

```python
{
    "title": str,                    # required
    "content": str,                  # required
    "url": str,                      # optional
    "origin": str,                   # optional, e.g. "ref", "exa", "jina"
    "source_type": str,              # optional, e.g. "documentation", "article"
}
```

**Output (markdown):**

```
# Synthesis: {query}

*Preset: {preset_name}*

{synthesized text with inline [1], [2] citation markers}

## Contradictions Detected

- **{topic}** ({severity}): {position_a} vs {position_b}
  - Resolution: {hint}

## Citations

- [1] [{title}]({url})
- [2] [{title}]({url})

---
*Quality gate: {passed} passed, {filtered} filtered (avg quality: {score})*
*RCS: {N} sources processed*
```

The `Contradictions Detected` section appears only when a preset that runs contradiction detection is selected (e.g. `comprehensive`, `contracrow`). The `Quality gate` and `RCS` footer lines appear only when the preset enables those stages.

**Output verification.** A post-synthesis verifier runs before relay. Hard failures (empty answer, reasoning-only output, truncation by `max_tokens` even after a one-shot retry at the ceiling, a failed contributing sub-call, or zero citations when sources exist) prepend a `# Synthesis verification FAILED` header listing the specific failure(s) with the unverified output following for debugging. Soft conditions (partial citation coverage, parse-failed contradiction detection, surfaced contradictions, gap-framed uncited entities, **a discussed query entity absent from every retained source — flagged `treat as UNVERIFIED` / `surface-form variant` / `emphasis/framing` per the entity-coverage check below (v0.5.0)**, legacy-only `[xx_<hex>]` citation markers from a regressed model, mixed `[N]` + `[xx_<hex>]` markers in the same response) append a `*Verification notes: ...*` line. Hard-failed outputs are not cached.

**Synthesis output discipline (v0.3.7).** The free-form `synthesize` prompts (and the `research` system prompt) wrap the answer in `<answer>…</answer>`; the server returns the wrapped content and drops anything after the closing tag — where a verbose thinking model sometimes appends a self-narrated changelog ("Key Corrections Implemented: …"). This is transparent to callers: the tags never reach you, and the fallback is non-destructive (tags absent → full text returned unchanged). The `reason` sources-aware path is immune by the same mechanism via its `<synthesis>` tags.

**Citation marker drift (v0.3.0).** Both `research` and `synthesize` ask the model for `[N]` numeric citations. If the model emits the pre-v0.3.0 `[xx_<hex>]` shape (e.g. `[tv_a1b2c3d4]`) instead of or alongside `[N]`, the verifier surfaces a `citation marker drift` soft warning identifying the legacy markers it found — operators see *why* a `cites none` hard-fail fired, or that a partially-numeric response is contractually mixed. The numeric extractor never resolves legacy markers, so legacy-only output also produces the existing `cites none` hard-fail; the drift warning is the diagnostic.

**Entity-coverage check (advisory, v0.5.0).** When a query entity the synthesis discusses is absent from every retained source, the verifier appends a soft `*Verification notes:*` caveat and PASSES the synthesis — it is no longer a hard failure. The caveat strength is graduated: a cited-adjacent uncovered entity gets a `treat as UNVERIFIED` note (the "Serper pricing asserted without a covering source" shape), an explicit gap-framing in the entity's sentence (`no source available for X`, `not in the gathered sources`, `could not find`, `not documented`) gets a lighter "frames the gap" note, a known alias or version variant (a source saying `dockerd` for "Docker Engine", `wsl2` for "WSL") gets a `surface-form variant` note, and shouted ALL-CAPS query framing (`MEASUREMENT PLANE`) gets an `emphasis/framing` note. Because the outputs that pass are the ones that get cached, **a passed result (or a cache hit) no longer implies entity-coverage is clean** — inspect `soft_warnings` / the `*Verification notes:*` line for grounding caveats. Rationale: grounding is a per-claim advisory signal, not an answer-level gate; a false-positive hard fail would discard a correct, well-cited synthesis, whereas a caveat lets the consumer discount a genuinely unsupported claim.

**Quality-gate fail-open (REJECT and PARTIAL-with-zero-good, v0.6.0).** When the pre-synthesis relevance gate rejects the input source set — either via the REJECT decision (avg relevance below `reject_threshold`) or the PARTIAL-with-zero-good edge case (avg above the floor but no individual source clears `pass_threshold`) — the outcome depends on whether any single source clears the fail-open floor (`RESEARCH_FAIL_OPEN_MIN_SOURCE_SCORE`, default 0.3). If at least one source clears it, `synthesize` **fails open**: it synthesizes over the set-aside (rejected) sources, opens the answer with a `low source relevance (fail-open)` caveat, and marks the result non-cacheable. Only when *no* source clears the floor does `synthesize` return the `## Source quality insufficient` block without invoking the synthesizer (the gate's `suggestion` field is included, and that refusal is NOT cached). Either way the rejected sources keep their provenance (identity, score, reason). This mirrors the REST `/synthesize/enhanced` and `/synthesize/p1` behavior at `routes.py`.

**Use when:** you have sources from your own fetcher and want a citation-aware synthesis with optional CRAG-style quality gating, RCS preprocessing, and PaperQA2-style contradiction surfacing.

### reason

Deep reasoning with chain-of-thought analysis. Two modes, picked automatically by whether `sources` is non-empty.

**Input:**

| Field | Type | Default | Notes |
|---|---|---|---|
| `query` | str | required | Problem or question |
| `context` | str | `""` | Background information or constraints (no-sources mode only) |
| `sources` | list[dict] \| null | null | Pre-gathered sources. If non-empty, switches to sources-aware mode |
| `reasoning_depth` | str | `"moderate"` | `"shallow"` (2–3 steps) / `"moderate"` (4–6) / `"deep"` (7+). No-sources mode only — ignored when `sources` is provided |
| `api_key` | str \| null | null | Per-request LLM key override |

`reason` does not accept a `style` parameter — the chain-of-thought prompt is fixed because the reasoning shape is what matters here, not the prose register. For style variants over pre-gathered sources, call `synthesize` instead.

Each `sources[i]` dict (sources-aware mode):

```python
{
    "title": str,                    # required
    "content": str,                  # required
    "url": str,                      # optional
    "origin": str,                   # optional, e.g. "ref", "exa", "jina"
    "source_type": str,              # optional, e.g. "documentation", "article"
}
```

**Output (markdown):**

- **No-sources mode** — the LLM's response text. The system prompt is structured to elicit a CoT-style breakdown ("Understanding the problem / Key considerations / Step-by-step reasoning / Conclusion"); the chain-of-thought is part of the body, not a separate field.
- **Sources-aware mode** — markdown wrapping the synthesis with reasoning, plus a `## Citations` section:

```
# Reasoning: {query}

{synthesized answer — the chain-of-thought is consumed by the prompt and not echoed back; if the model fails to emit the expected `<synthesis>` tags, the full raw response is returned here as a fallback}

## Citations

- [1] [{title}]({url})
- [2] [{title}]({url})
```

In sources-aware mode, `reason` runs the same post-synthesis verifier as `synthesize` (see above) — a degraded output is prepended with a `# Synthesis verification FAILED` header listing what went wrong.

**Use when:** the user explicitly asks "why" or "explain the reasoning"; the answer's logic matters as much as the conclusion. Pass `sources` when you have pre-gathered evidence; omit it when the model should reason from its own knowledge plus optional `context`.

---

## Errors

Connector errors are logged to `stderr` (never `stdout`, which would corrupt the MCP transport) and do not abort the call — the aggregator returns whatever the surviving connectors found. The LLM client raises on:

| Cause | Symptom | Recovery |
|---|---|---|
| `RESEARCH_LLM_API_KEY` missing on startup | `RuntimeError` from `settings.require_llm_key()` | Set the env var; see `CLAUDE.md` Environment variables |
| LLM endpoint 401 | exception bubbles up | Refresh the key (or set a non-empty placeholder for an open local server) |
| LLM endpoint 429 | exception bubbles up | Reduce `top_k`, use the `fast` preset, or wait the indicated retry-after |
| Model not loaded | exception bubbles up | Verify with `curl $RESEARCH_LLM_API_BASE/models`; for vLLM/SGLang ensure the `--model` slug matches `RESEARCH_LLM_MODEL` |
| `RESEARCH_LLM_TIMEOUT` exceeded | exception bubbles up | Lower `top_k`, switch preset, raise the timeout |

For richer error envelopes (status codes, structured detail), use the REST endpoints documented in [`rest-api.md`](rest-api.md).
