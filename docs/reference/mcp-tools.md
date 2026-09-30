# MCP tool reference

Full input/output reference for the **seven** stdio MCP tools exposed by Gigaxity Deep Research. Tools register under whatever alias you set in `~/.claude.json` — `mcp__<alias>__<tool>` is the call syntax.

The stdio surface returns **markdown strings**, not JSON, so the agent can pipe results straight into a response. The matching REST endpoints (under `/api/v1/`) return structured JSON shapes — see [`rest-api.md`](rest-api.md) for those.

The tools split into **three primitives** (raw, single-category and combined behavior in one call) plus **four deep-research tools** (drive each step independently).

## Progress notifications

Every tool that makes an LLM call — `research`, `ask`, `discover`, `synthesize`, `reason` — emits
`notifications/progress` while it works, provided your client sends a `progressToken` with the
request. `search` and `vertical_search` do not: they make no LLM call.

You get an opening notification when the tool starts, one on either side of every model call, and a
`model call still running` heartbeat every `RESEARCH_PROGRESS_HEARTBEAT_INTERVAL` seconds while a
call is in flight. Messages are prefixed with the tool name (`synthesize: model call started`). The
`progress` value increases monotonically; `total` is not sent, because the number of model calls a
synthesis will make is not known when it begins.

This matters because MCP clients typically enforce an **idle** deadline that a progress notification
resets. A long synthesis that emits nothing looks indistinguishable from a hung connection, and the
client aborts it mid-flight while the server is still working.

> ⚠️ **stdio transport only.** The HTTP MCP surface mounted at `/mcp` (see
> [`../guides/setup-rest.md`](../guides/setup-rest.md)) is served by `FastApiMCP`, which forwards
> tool calls into the REST routes and does not run the progress adapter — those calls emit **no**
> progress notifications. If your client enforces an idle deadline, use the stdio transport, or set
> `RESEARCH_LLM_WALL_CLOCK_CAP` so a stalled call fails as a reportable error instead of a silent
> abort.

---

## Common parameter

Every tool accepts an optional `api_key: str | None = None` parameter. When set, it overrides `RESEARCH_LLM_API_KEY` for that call only — used in multi-tenant deployments to bill each user's calls to their own LLM endpoint account. `search` and `vertical_search` accept the parameter for surface consistency but ignore it (no LLM call).

The matching REST endpoints accept the same per-request override either via the request body's `api_key` field or via the `X-LLM-Api-Key` header.

---

## Primitives

### search

Raw multi-source aggregation across SearXNG, Tavily, LinkUp, Brave and Parallel with RRF fusion. **No LLM call.**

SearXNG contributes its `general` category on every call, plus at most one **vertical lane** — a second SearXNG list from `science`, `it` or `videos` — chosen by `focus_mode` or, without one, by a keyword heuristic — and only when the instance's `/config` shows an enabled engine in that category. The lane fuses as its own ranked list, named `searxng:<category>`; it keeps only results an engine of the category returned, and a URL the base list already holds is kept there only. See [configuration.md → Vertical lanes](configuration.md#vertical-lanes) for the mapping.

**Input:**

| Field | Type | Default | Notes |
|---|---|---|---|
| `query` | str | required | The search query |
| `top_k` | int | `10` | Results per source (1–50) |
| `api_key` | str \| null | null | Accepted for consistency; ignored (no LLM call) |
| `focus_mode` | str \| null | null | `academic` → science lane · `debugging` / `documentation` → it lane · `tutorial` → videos lane · `general` → no lane · `comparison` / `news` / null → keyword heuristic |

**Output (markdown):**

```
# Search Results for: {query}

## [1] {title}
**URL:** {url}
**Source:** {connector_name} (score: {score:.3f})
*channel: {author} · length: {length}*          ← video results only

{content snippet up to 500 chars}

## [2] ...

---
*{N} results from ['searxng', 'searxng:videos', 'tavily', 'linkup'] (configured: ['searxng', 'tavily', 'linkup'])*
```

The trailer's first list shows connectors that **returned results** for this query — including a vertical lane such as `searxng:videos`; the parenthetical `configured:` list shows connectors that the aggregator initialized (i.e. their env keys were set at MCP boot). Lanes never appear under `configured:`. When the two lists diverge:

- `configured: ['searxng']` only → the keyed connectors' env keys are unset; the aggregator silently dropped them at init. See [troubleshooting.md](../troubleshooting.md#search--connector-errors) to enable multi-way fan-out.
- `from ['searxng']` with `configured: ['searxng', 'tavily', 'linkup']` → the other connectors errored or returned empty for this query. Check the MCP's stderr log for `Tavily search error` / `LinkUp search error`.

**Use when:** you want raw search hits without paying for synthesis tokens, or when you'll feed the results into your own pipeline.

### vertical_search

One SearXNG category on its own. **No LLM call and no search-API quota** — it only queries your SearXNG instance.

**Input:**

| Field | Type | Default | Notes |
|---|---|---|---|
| `query` | str | required | The search query |
| `vertical` | str | `videos` | `videos` · `science` · `it` · `docs` · `packages` · `general` |
| `top_k` | int | `10` | Maximum results (1–50) |
| `api_key` | str \| null | null | Accepted for consistency; ignored (no LLM call) |

With the bundled [SearXNG companion](../../companions/searxng/README.md) the categories hold: `videos` — YouTube; `science` — arXiv, Google Scholar, Semantic Scholar, OpenAlex, PubMed, Crossref; `it` — Stack Overflow, Ask Ubuntu, Super User, GitHub, Hacker News; `docs` — MDN, Microsoft Learn, Arch Linux wiki; `packages` — PyPI, npm, crates.io, pkg.go.dev, Docker Hub, Hugging Face; `general` — DuckDuckGo, Brave, Wikipedia, Wikidata.

**Output (markdown):**

```
# {vertical} results for: {query}

## [1] {title}
**URL:** {url}
*engine: {engine} · channel: {author} · length: {length}*

{content snippet up to 400 chars}

---
*{N} results from SearXNG category `{vertical}`*
*⚠️ Off-category results dropped: 3 came only from engines outside `videos` (...)*   ← only when some were dropped
*⚠️ Unresponsive engines: duckduckgo (CAPTCHA)*          ← only when an engine failed
```

SearXNG answers HTTP 200 even when engines fail, so the `Unresponsive engines` line is the only degradation signal. SearXNG also answers a category it does not define from its default categories, without an error, so the tool first reads the instance's `/config`: a category with no enabled engine (`docs` and `packages` are custom to the companion config) is not queried, and the footer says so. When `/config` cannot be read, the tool queries anyway and the footer marks the results as unverified. A locked `categories` preference on the instance, or a `!bang` in the query, makes SearXNG answer from other engines; the tool keeps only results an engine of the category returned and counts the rest in the footer. `RESEARCH_SEARXNG_ENGINES` is never sent here.

**Use when:** you want videos to pair with a transcript tool, papers without the general web's noise, practitioner Q&A, or package-registry hits.

### research

Combined pipeline: multi-source search **plus** LLM synthesis with citations, in a single call. The search step adds a SearXNG vertical lane when the keyword heuristic matches (see [`search`](#search)).

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
| `focus_mode` | str \| null | null | One of `general`, `academic`, `documentation`, `comparison`, `debugging`, `tutorial`, `news`; null behaves as `general` for discovery. Also picks the SearXNG vertical lane for every search `discover` runs, as in [`search`](#search): null lets the keyword heuristic decide, `general` means none |
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

The final `configured:` line surfaces which connectors initialized at MCP boot. If only `['searxng']` is shown, no keyed connector (Tavily, LinkUp, Brave, Parallel) has its key set — see `search` above and [troubleshooting.md](../troubleshooting.md#search--connector-errors) to enable the multi-connector fan-out. (Unlike `search` / `research`, `discover` does not surface which connectors actually returned content for this query — the Explorer wraps the aggregator and doesn't expose per-connector raw results.)

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
    "origin": str,                   # optional, e.g. "context7", "exa", "jina"
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

**Output verification.** A post-synthesis verifier runs before relay. Hard failures (empty answer, reasoning-only output, truncation by `max_tokens` even after a one-shot retry at the ceiling, a failed contributing sub-call, or zero citations when sources exist) prepend a `# Synthesis verification FAILED` header listing the specific failure(s) with the unverified output following for debugging. Soft conditions (partial citation coverage, the contradiction-detector advisories below, surfaced contradictions, gap-framed uncited entities, **a discussed query entity absent from every retained source — flagged `treat as UNVERIFIED` / `surface-form variant` / `emphasis/framing` per the entity-coverage check below (v0.5.0)**, legacy-only `[xx_<hex>]` citation markers from a regressed model, mixed `[N]` + `[xx_<hex>]` markers in the same response) append a `*Verification notes: ...*` line. Hard-failed outputs are not cached.

**Contradiction-detector advisories (v0.12.0).** The detector never silently reports "no contradictions" for a run that failed — every degraded outcome appends a distinct `*Verification notes:*` line, and each one means the conflict list you got is **non-exhaustive**:

| Advisory | What happened | What to do |
|---|---|---|
| `contradiction detection could not be parsed` | The model emitted the requested labels but the block could not be read. | A grammar problem — worth reporting. |
| `contradiction detection returned no structured output` | The model never attempted the format (answered in prose, refused, used its own shape). | Prompt/model-behavior, not a parser bug. Re-run or accept the gap. |
| `contradiction detection returned both findings and a 'no contradictions' declaration` | The response disagreed with itself. Findings are retained but unconfirmed. | Treat the listed contradictions as candidates, not confirmed. |
| `contradiction detection used the degraded heuristic detector` | No LLM client — keyword-pair heuristic ran instead. | Low-confidence output by construction. |
| `contradiction detection failed and fell back to a heuristic (<error>)` | The detector call raised. | Transport/config problem named in the error. |

Label decoration is tolerated on input: the parser normalizes bold, italic, inline-code, bullet, numbered, heading, blockquote and table-cell labels (`**TOPIC:**`, `- TOPIC:`, `1. TOPIC:`, `` `TOPIC`: ``) to the bare label before matching. Before v0.12.0 any of those produced an unparseable block, so a markdown-heavy model could lose its entire contradiction list to formatting alone.

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
    "origin": str,                   # optional, e.g. "context7", "exa", "jina"
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
