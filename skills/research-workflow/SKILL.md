---
name: research-workflow
description: This skill should be used when the user asks research questions, needs information lookup, wants comparisons, asks "what is", "how does", "explain", "compare", "best practices", "latest developments", or any query requiring web search, documentation lookup, or synthesis of multiple sources. Provides optimal routing between DIRECT, EXPLORATORY, and SYNTHESIS workflows using Triple Stack (Ref, Exa, Jina) and gigaxity-deep-research tools.
version: 1.0.0
---

# Research Workflow Skill

## Overview

This skill orchestrates research queries using the optimal workflow based on query type. It integrates:
- **Triple Stack**: Ref (docs) + Exa (code/web) + Jina (web/academic/parallel)
- **gigaxity-deep-research**: synthesis engine over any OpenAI-compatible chat-completions endpoint (self-hosted vLLM/SGLang/llama.cpp on the `local-inference` branch, OpenRouter on `main`)
- **exa-answer**: speed-critical 1–2 s factual lookups
- **brightdata_fallback**: blocked-URL recovery (CAPTCHA / paywall / Cloudflare)
- **gptr-mcp**: social-first research over Reddit, X/Twitter, YouTube — wraps [GPT Researcher](https://github.com/assafelovic/gpt-researcher)

---

## Tool Schema Loading (MANDATORY)

MCP tool schemas are deferred. Bare `mcp__X__Y(...)` calls fail with `InputValidationError` because the schema isn't loaded. Load schemas first via `ToolSearch`:

```
ToolSearch(query='select:mcp__Ref__ref_search_documentation')                # one tool
ToolSearch(query='select:mcp__exa__web_search_exa,mcp__jina__read_url')      # multiple
ToolSearch(query='+exa-answer')                                              # keyword (rank by relevance)
```

After `ToolSearch` returns the `<function>...` block for a tool, that tool is callable for the rest of the session — no need to re-load.

**Why this matters:** if you skip `ToolSearch` and the bare call fails, the path of least resistance is to fall through to `WebFetch` / `WebSearch` — neither is in the Triple Stack. Using them is the strongest signal that schema loading was skipped.

```
❌ mcp__Ref__ref_search_documentation(query="...")              # fails — schema not loaded
✅ ToolSearch(query='select:mcp__Ref__ref_search_documentation')
   → then mcp__Ref__ref_search_documentation(query="...")       # works

❌ Tool fails silently → fall back to WebFetch
✅ Tool fails → check whether schema was loaded → ToolSearch + retry
```

Subagents inherit the same deferred-loading discipline — when spawning a research subagent via the Task tool, the subagent prompt MUST include `ToolSearch(query='select:...')` ahead of every `mcp__X__Y` reference, otherwise the subagent will fall through to WebFetch the same way.

---

## Tool Output Persistence (MANDATORY)

When tool output exceeds the Claude Code harness threshold (~16 KB), the full result is written to disk and replaced with a preview-and-path wrapper:

```
<persisted-output>
Output too large (XXX KB). Full output saved to: /home/<user>/.claude/projects/<encoded>/<session>/tool-results/<random>.txt

Preview (first 2KB):
<truncated content>
...
</persisted-output>
```

**Rule:** Any time you see `<persisted-output>` wrapping a tool result, the 2KB preview is **NOT** evidence. You **MUST** call `Read(path)` on the persisted path before:

- citing the source
- making any factual claim derived from the result
- passing the result into `mcp__gigaxity-deep-research__synthesize` sources

The auto-reload mechanism does not exist. The result is on disk until you read it.

```
❌ See <persisted-output> → synthesize from the 2KB preview
✅ See <persisted-output> → Read(path) → synthesize from the full content

❌ Multiple persisted-output tools chained → synthesize from previews only
✅ For each persisted-output, Read(path) before the next dependent call
```

**Typical triggers (observed):** `mcp__exa__web_search_advanced_exa` with `numResults>=10`, `mcp__jina__parallel_read_url` on long pages, `mcp__exa__crawling_exa` with `subpages`.

---

## Tool Health Detection (MANDATORY)

MCP wrappers convert HTTP errors into 200-OK text envelopes — a quota-exhausted Jina call looks structurally like a normal "no results" response. Silent failures slip through. After every research-tool call, scan the response for error signatures BEFORE treating the result as evidence.

### Error signatures

| Tool | Quota / billing | Auth | Rate limit | Degraded empty |
|---|---|---|---|---|
| Jina (any `mcp__jina__*`) | `402` / `Insufficient balance` / `out of credits` / `payment required` / `quota` | `401` / `Invalid API key` / `Unauthorized` | `429` / `rate limit` / `too many requests` | `results: []` + no error field (could be legit — verify against query specificity) |
| Exa (any `mcp__exa__*`) | `402` / `credits` / `Insufficient` | `401` / `authentication` | `429` / `rate limit` | empty `results` array |
| Exa-answer | same as Exa | same as Exa | same as Exa | empty `answer` field |
| gptr-mcp `quick_search` | upstream OpenAI `429` / `quota` | OpenAI `401` | OpenAI `429` | `search_results: []` or `result_count: 0` — distinguish "anti-scraped href-only" (Anti-Pattern #6) from "genuine empty" |
| gigaxity-deep-research `synthesize` / `reason` | upstream LLM `402` | upstream LLM `401` | upstream LLM `429` | already covered: `# Synthesis verification FAILED` header (per Verifier Verdict Handling) |
| brightdata_fallback | `402` | `401` | `429` | empty markdown body |

### Optional pre-flight Jina probe (0 tokens)

At the start of a long-running session where Jina is load-bearing, call `mcp__jina__show_api_key()` once. It returns the bearer the server sees. Use cases:

- **Auth verification**: confirms the key the MCP loaded matches expectation. If it errors, all subsequent Jina calls will fail too — bail and notify the user before burning the rest of the workflow.
- **NOT a quota probe** — does not return remaining balance. Quota exhaustion only surfaces on the first failing call.

Jina is uniquely vulnerable to silent quota exhaustion: 10M trial tier + primary high-frequency tool in the SYNTHESIS workflow = first to deplete. Notify the user immediately on the first 402.

### Detection → escalation schema (MANDATORY for subagents)

When a tool error is detected during a research subagent run, the subagent MUST emit a structured health header at the TOP of its final response — BEFORE the synthesis content. Schema:

```
## ⚠️ Tool Health Issues

- **mcp__jina__search_web** (5 calls): 2 quota errors (HTTP 402 / "Insufficient balance" at calls 3 and 4). Fell back to mcp__exa__web_search_exa for remaining queries.
- **mcp__exa__web_search_advanced_exa** (3 calls): 1 rate limit (429) on call 2. Single retry succeeded.
- **mcp__gptr-mcp__quick_search** (2 calls): both returned empty results on Reddit slugs (anti-scrape, expected); not flagged per Anti-Pattern #6.

**Impact:** synthesis below uses Exa-heavy mix (2/5 Jina queries succeeded). Coverage may be skewed toward Exa-indexed content. JINA QUOTA EXHAUSTED — pause further Jina-dependent research until user addresses.

---

[Normal synthesis content below]
```

If no issues encountered, omit the header entirely — its absence signals a clean run.

### Trigger rules (when to emit)

Emit the health header if ANY of the following occurred during the run, even if the workflow completed overall:

- Any error envelope per the signature table
- Any fallback chain invocation (the `ON FAIL →` chain was triggered because the primary tool failed)
- Empty result on a non-trivial query that was expected to return content (skip for known degraded-empty patterns like gptr-mcp Reddit slugs per Anti-Pattern #6)
- Visible timeout signal (Jina parallel calls past the configured `timeout`)
- Persisted-output handling skipped (per Tool Output Persistence — agent didn't `Read(path)` on a `<persisted-output>` wrapper)

### Severity language for the Impact line

Use these exact phrases in the Impact line so the main agent's scanner catches them:

- `<TOOL> QUOTA EXHAUSTED` — 402 / billing / credits / quota errors. User-facing escalation required; further calls to that tool will fail. (e.g. `JINA QUOTA EXHAUSTED`)
- `<TOOL> AUTH FAILURE` — 401 errors. Tool is effectively dead for this session; user must address before any further use. Highest priority.
- `<TOOL> RATE LIMITED` — 429 errors, transient. Single retry permitted; if persists, fall back.
- `<TOOL> DEGRADED` — empty results when content expected; backend may be partial or query may be poorly-formed.

### Recovery decision tree

```
Tool error detected
  ↓
Single transient (429 / timeout)?
  YES → retry once after short backoff (5s for 429)
       → if succeeds: optional health flag (note recovered transient)
       → if fails: escalate per category below
  NO ↓

Quota / billing (402)?
  YES → switch to fallback chain (do NOT retry — quota persists across calls)
       → flag QUOTA EXHAUSTED in health header
       → skip this tool for the rest of the run
  NO ↓

Auth (401)?
  YES → BAIL the entire tool category (all calls to this MCP will fail)
       → flag AUTH FAILURE in health header
       → ⚠️ The whole run may be unrecoverable — surface IMMEDIATELY to user
  NO ↓

Empty result on non-trivial query?
  YES → is this a known degraded-empty pattern? (e.g. gptr-mcp Reddit slugs per Anti-Pattern #6)
        YES → not an error; continue
        NO → retry once with reformulated query
             → if still empty, flag DEGRADED in health header
```

---

## Query Classification

### QUICK FACTUAL Queries (15-20% of queries)

Speed-critical factual lookups during ongoing agent operations. Exa /answer handles search + LLM answer + citations in a single 1-2s call (94% SimpleQA accuracy).

**Trigger Patterns:**
- Mid-task factual lookup during an ongoing workflow
- "What is the current version of X?"
- "What is X's latest pricing?"
- "When was X released?"
- Speed matters more than depth
- Single factual answer sufficient (no exploration or cross-validation)

**Decision Criteria:**
- Agent is mid-task and needs a quick fact
- A single direct answer with sources is sufficient
- No comparison, synthesis, or deep analysis needed
- Latency budget is <3 seconds

**Tool:** `exa_answer` (exa-answer MCP) — 1-2s, $0.005/query

### DIRECT Queries (25-35% of queries)

Single-source factual lookups. Use Triple Stack directly.

**Trigger Patterns:**
- "Read this URL" → Jina read_url
- "Get documentation for [library]" → Ref ref_search_documentation
- "Find code examples for [function]" → Exa get_code_context_exa
- "How does [specific API] work?" → Ref ref_search_documentation
- "Explain [library feature]" → Ref ref_search_documentation
- "What is [programming concept]?" → Ref ref_search_documentation
- "Search images for..." → Jina search_images
- Factual lookups with single source
- Specific library/API/framework with official docs

**Decision Criteria:**
- Query targets a SPECIFIC library, API, or framework
- Official documentation exists and would answer it
- Single source sufficient (no cross-validation needed)
- User knows what they're looking for

### EXPLORATORY Queries (40-50% of queries)

Cold-start discovery for unfamiliar topics. gigaxity-deep-research leads.

**Trigger Patterns:**
- "What is [unfamiliar topic]?" (cold start)
- "Explain [general concept/technology]" (e.g., "Explain transformers")
- "How does [general system] work?" (e.g., "How do vector databases work?")
- "Latest developments/advances in [field]"
- "Tell me about [emerging technology] in 2026"
- "Research [topic]" without specific library focus
- User doesn't know what they don't know

**Decision Criteria:**
- Unfamiliar domain (cold start)
- General concept, not specific library
- Speed priority (1-2 min target)
- Targeted depth, not comprehensive coverage
- No cross-validation required

### SYNTHESIS Queries (20-30% of queries)

Cross-source validation and comprehensive analysis. Triple Stack → gigaxity-deep-research.

**Trigger Patterns:**
- "What is the recommended/best..." (need consensus)
- "Compare X vs Y" (need multiple perspectives)
- "What are best practices for..." (need validated patterns)
- "Which is better/faster..." (need benchmarks)
- "How should I approach..." (need strategic guidance)
- "Pros and cons of..."
- "Trade-offs between..."

**Decision Criteria:**
- Cross-source validation required
- Comparison or evaluation needed
- Comprehensive coverage required
- Multiple perspectives expected
- Consensus or best practice sought

## Decision Tree

```
Query arrives
     ↓
Mid-task factual lookup? (speed-critical, single answer sufficient)
  YES → QUICK FACTUAL (exa_answer — 1-2s, 94% accuracy)
  NO ↓

Single-source factual lookup? (specific library/API/framework)
  YES → DIRECT (Triple Stack tool directly)
  NO ↓

Specific library/API/framework with official docs?
  YES → DIRECT (Ref → Exa fallback)
  NO ↓

Requires cross-validation, comparison, or comprehensive coverage?
  YES → SYNTHESIS (Triple Stack → gigaxity-deep-research synthesize/reason)
  NO ↓

Default → EXPLORATORY (gigaxity-deep-research discover → Jina → synthesize)
  # NOTE: Exa 3.2.0 MCP does NOT expose type="deep" on web_search_exa (enum: auto|fast)
  #       or web_search_advanced_exa (enum: auto|fast|instant). The deprecated
  #       deep_researcher_start/check have no MCP-surface replacement. Use the
  #       gigaxity-deep-research discover chain above for async multi-hop research.
```

---

## QUICK FACTUAL Workflow

**Use when:** Mid-task factual lookup, speed-critical, single answer sufficient

**Tool:** `exa_answer` from exa-answer MCP

```
# Simple factual lookup (1-2s, 94% SimpleQA accuracy)
exa_answer(query="What is the latest version of Next.js?")

# With sources disabled for minimal output
exa_answer(query="What port does Redis use by default?", include_sources=False)

# Detailed with full source text (for verification)
exa_answer_detailed(query="What are the system requirements for Bun?")
```

**Token cost:** ~200-500 tokens
**Time:** 1-2 seconds
**Cost:** $0.005/query

**Fallback:** If exa_answer fails, fall back to DIRECT workflow.

---

## DIRECT Workflow

**Use when:** Single-source factual lookup, specific library/API query

**Tool Selection (Jina-first for high-frequency calls — reserve Exa budget for its unique capabilities):**

| Query Type | Primary Tool | Fallback |
|------------|--------------|----------|
| API docs | `mcp__Ref__ref_search_documentation` | `mcp__exa__get_code_context_exa` |
| Code examples / patterns | `mcp__exa__get_code_context_exa` | `mcp__jina__search_web` with `site:github.com` |
| URL reading | `mcp__jina__read_url` (0 tokens) | `mcp__Ref__ref_read_url`, `mcp__exa__crawling_exa` |
| Bulk URL reading (3-5) | `mcp__jina__parallel_read_url` (content-proportional) | `mcp__exa__crawling_exa` with urls array |
| URL subpage crawl | `mcp__exa__crawling_exa` with `subpages` + `subpageTarget` | — (Jina has no subpage mode) |
| Academic (arXiv) | `mcp__jina__search_arxiv` / `mcp__jina__parallel_search_arxiv` | `mcp__exa__web_search_advanced_exa category="research paper"` |
| Academic (SSRN — econ/law/finance) | `mcp__jina__search_ssrn` / `mcp__jina__parallel_search_ssrn` | — |
| BibTeX citations | `mcp__jina__search_bibtex` (DBLP + Semantic Scholar) | — |
| PDF layout extraction (figures/tables) | `mcp__jina__extract_pdf` | — |
| Images | `mcp__jina__search_images` | — |
| Screenshots | `mcp__jina__capture_screenshot_url` | — |
| General web | `mcp__jina__search_web` (63 tokens) | `mcp__exa__web_search_exa` |
| Parallel multi-query web (3-5 variants) | `mcp__jina__parallel_search_web` (107 tokens for 3 queries) | — (Exa has no parallel mode) |
| Advanced web (category/domain/date filters) | `mcp__exa__web_search_advanced_exa` | `mcp__exa__web_search_exa` |
| Company info | `mcp__exa__web_search_advanced_exa category="company"` | `mcp__jina__search_web "<name> company"` |
| People / OSINT / attribute-based | `mcp__exa__web_search_advanced_exa category="people"` | `mcp__jina__search_web "<name> site:linkedin.com"` |
| Financial reports (SEC, earnings) | `mcp__exa__web_search_advanced_exa category="financial report"` | `mcp__exa__web_search_advanced_exa category="pdf"` |
| News (date-bounded) | `mcp__exa__web_search_advanced_exa category="news"` with `startPublishedDate/endPublishedDate` | `mcp__jina__search_web` |
| GitHub repo discovery | `mcp__exa__web_search_advanced_exa category="github"` | `mcp__jina__search_web "site:github.com"` |
| PDFs / whitepapers | `mcp__exa__web_search_advanced_exa category="pdf"` | — |
| URL freshness inference | `mcp__jina__guess_datetime_url` | — (credibility/staleness checks) |
| Deep multi-hop async research | gigaxity-deep-research discover → Jina parallel_read_url → synthesize | — (Exa MCP 3.2.0 does not expose `type="deep"`) |
| Free reranker | `mcp__jina__sort_by_relevance` (0 tokens) | — |
| Free semantic dedup | `mcp__jina__deduplicate_strings` (0 tokens) | — |
| Text classification | `mcp__jina__classify_text` | — |
| Time-aware session context | `mcp__jina__primer` (current UTC / timezone) | — |
| Quick LLM answer | `mcp__gigaxity-deep-research__ask` | — |

**AVOID:** `mcp__jina__expand_query` (12k tokens/call — rewrite queries manually instead).

**Implementation:**

```
# Documentation lookup
mcp__Ref__ref_search_documentation(query="FastAPI WebSocket API")

# Code examples
mcp__exa__get_code_context_exa(query="React useState patterns")

# URL reading
mcp__jina__read_url(url="https://docs.example.com/api")

# Academic papers
mcp__jina__search_arxiv(query="transformer architecture", num=5)

# Quick answer (no search needed)
mcp__gigaxity-deep-research__ask(query="What is dependency injection?")
```

**Token cost:** ~100-500 tokens
**Time:** <10 seconds

---

## EXPLORATORY Workflow

**Use when:** Cold-start, unfamiliar topic, general concepts, speed priority

**Flow:**
```
gigaxity-deep-research discover → (scored URLs) → Jina parallel_read_url → gigaxity-deep-research synthesize
```

**Focus Mode Selection:**
| Query Type | focus_mode | Why |
|------------|------------|-----|
| General tech question | `general` | Broad gaps: docs, examples, alternatives |
| Research/academic | `academic` | Gaps: methodology, limitations, citations |
| Library/API specific | `documentation` | Focused: api_reference, migration, config |
| "Which should I use?" | `comparison` | Gaps: criteria, tradeoffs, benchmarks |
| Error/bug investigation | `debugging` | Gaps: root_cause, workarounds, fixes |
| Learning/getting started | `tutorial` | Gaps: prerequisites, step_by_step |
| Recent news/announcements | `news` | Time-filtered, announcement gaps |

**Implementation:**

```
# Step 1: Discovery with gap analysis
result = mcp__gigaxity-deep-research__discover(
    query="quantum memory systems",
    top_k=10,
    identify_gaps=True,
    focus_mode="academic"  # → scientific topic, need methodology gaps
)
# Returns: landscape, knowledge_gaps, sources with scores, recommended_deep_dives

# Step 2: Score URLs from discovery result (extended thinking)
# - Gap relevance (does it fill identified gaps?)
# - Source authority (official docs, academic, reputable)
# - Uniqueness (not redundant with other sources)
# - Recency (recent for evolving topics)
# Select top 3-5 URLs based on scoring

# Step 3: Deep content fetch via Jina
content = mcp__jina__parallel_read_url(
    urls=[top_scored_urls],  # 3-5 URLs from step 2
    timeout=60000
)

# Step 4: Synthesize findings
synthesis = mcp__gigaxity-deep-research__synthesize(
    query="quantum memory systems",
    sources=[
        {"title": "Source 1", "url": "url1", "content": "fetched content 1"},
        {"title": "Source 2", "url": "url2", "content": "fetched content 2"},
        ...
    ],
    style="comprehensive",
    preset="academic"  # → matches focus_mode, structured with citations
)
```

**Key Insight:** `discover` outputs `recommended_deep_dives` URLs specifically for Jina to fetch. This prevents redundant searching.

**Token cost:** ~2000-5000 tokens
**Time:** 1-2 min

---

## SYNTHESIS Workflow

**Use when:** Comparisons, best practices, cross-validation, comprehensive coverage

**Flow:**
```
Triple Stack (Ref + Exa + Jina parallel) → gigaxity-deep-research synthesize/reason
```

**Preset Selection:**
| Query Type | preset | Why |
|------------|--------|-----|
| Important research | `comprehensive` | Full pipeline: CRAG + RCS + contradiction + outline |
| Quick synthesis | `fast` | Direct synthesis, no preprocessing |
| Comparisons (X vs Y) | `contracrow` | Highlights conflicting claims |
| Formal reports | `academic` | Structured with proper citations |
| How-to guides | `tutorial` | Step-by-step format |

**Implementation:**

```
# Step 1: Triple Stack parallel search
# Execute ALL THREE in parallel for comprehensive coverage

ref_results  = mcp__Ref__ref_search_documentation(query="FastAPI vs Flask production")
exa_results  = mcp__exa__get_code_context_exa(query="FastAPI Flask production patterns")
jina_results = mcp__jina__parallel_search_web(searches=[
    {"query": "FastAPI Flask benchmarks 2026"},
    {"query": "FastAPI Flask production tradeoffs"},
    {"query": "FastAPI vs Flask async performance"},
])  # 107 tokens for 3 parallel queries — broader coverage than a single search_web

# Step 2 (optional depth boost): second-pass bulk-read of top URLs surfaced by Step 1
# Rank union of URLs, bulk-read top 3-5, feed richer content to synthesis
urls = [u for r in [exa_results, jina_results] for u in extract_urls(r)]
ranked = mcp__jina__sort_by_relevance(               # 0 tokens (free reranker)
    query="FastAPI vs Flask production tradeoffs",
    documents=urls
)
top_urls = ranked[:5]
deep_content = mcp__jina__parallel_read_url(         # ~17k tokens (content-proportional)
    urls=top_urls,
    timeout=60000
)

# Step 3 (optional dedup): filter near-duplicate snippets before synthesis
deduped = mcp__jina__deduplicate_strings(            # 0 tokens (free dedup)
    strings=[src["content"] for src in all_sources]
)

# Step 4: IMMEDIATELY synthesize (no waiting for user)
synthesis = mcp__gigaxity-deep-research__synthesize(
    query="Compare FastAPI vs Flask for production APIs",
    sources=[
        {"title": "Ref: FastAPI docs", "url": "url", "content": "ref content", "origin": "ref"},
        {"title": "Exa: Production patterns", "url": "url", "content": "exa content", "origin": "exa"},
        {"title": "Jina: Benchmarks", "url": "url", "content": "jina content", "origin": "jina"},
        # ...deep_content items appended as "origin": "jina-read"
    ],
    style="comparative",
    preset="contracrow"  # → comparison query, highlight conflicts
)

# OR use reason for chain-of-thought analysis (critical decisions)
reasoning = mcp__gigaxity-deep-research__reason(
    query="Which framework is better for high-traffic production APIs?",
    context="[Summary of Triple Stack findings]",
    reasoning_depth="deep"  # → critical architectural decision
)
```

**Key Insight:** gigaxity-deep-research does NOT re-search. Triple Stack already gathered content - just synthesize it. This is the critical difference from deprecated Perplexity which would re-search.

**Free middleware (use liberally — 0 token cost on Jina):**
- `mcp__jina__sort_by_relevance(query, documents)` — rerank Triple Stack URL union before deciding which to deep-read
- `mcp__jina__deduplicate_strings(strings)` — filter near-duplicate snippets before feeding to synthesize (reduces synthesis token burn)
- `mcp__jina__guess_datetime_url(url)` — verify source freshness/credibility per-URL before trusting it

**MANDATORY:** SYNTHESIS workflow MUST end with `mcp__gigaxity-deep-research__synthesize` (or `reason` for chain-of-thought). Do NOT freehand the synthesis in the main thread. Do NOT stop after Triple Stack and wait for user input. The only valid escape hatch is the post-synthesis verifier verdict (see next).

### Verifier Verdict Handling

`synthesize` runs a post-synthesis verifier and prepends a structural header on hard-gate failure (empty content, reasoning-only trace, truncated by token limit, sub-call failure, zero citations on non-empty sources, or **any query entity discussed in the synthesis that is absent from every retained source unless the synthesis explicitly frames the gap**):

```
# Synthesis verification FAILED

This output is not a reliable synthesis:
- <reason 1>
- <reason 2>

---
(unverified output below, for debugging)

<original output>
```

**When you see this header:**

1. Do NOT relay the failed output to the user as-is — the verifier explicitly says it is not a reliable synthesis.
2. Diagnose the failure reasons. Common patterns:
   - `truncated by token limit` → raise `RESEARCH_LLM_MAX_TOKENS` (env var on the MCP), or switch preset to `fast` (less preprocessing budget burn).
   - `reasoning trace instead of answer` → model spending budget on chain-of-thought; raise `RESEARCH_LLM_REASONING_HEADROOM` or pick a non-reasoning model.
   - `zero citations on N sources` → source content may not have reached the model; check disk-spill on the source-gathering tools (per "Tool Output Persistence" above) — agents commonly synthesize from 2KB previews and end up with sources whose content never made it to the model.
   - `synthesis discusses entities [...] but those entities are absent from every retained source` → **entity-coverage hard-fail**. The relevance gate filtered out the only sources covering one of the named entities, but the LLM wrote about it anyway from prior knowledge. Either (a) gather more sources covering the missing entity and re-call, or (b) re-frame the synthesis to explicitly acknowledge the gap ("we have no source available for X") — the verifier downgrades to a soft warning when uncovered entities appear in the same sentence as a gap-framing phrase ("no source", "not in the gathered", "not documented", "could not find", etc.).
3. ONE retry is permitted: re-call synthesize with a different `preset` (e.g., `contracrow` → `fast`) or fewer sources.
4. If the retry also FAILS, fall back to main-thread synthesis from the raw sources, AND prepend the user-facing answer with: `> Note: gigaxity-deep-research synthesize failed verification on retry; this is a main-thread synthesis from raw sources without the verifier guarantees.`
5. Hard-failed outputs are NOT cached, so the next call will re-run — do not cache-bust manually.

Soft warnings append `*Verification notes: <warning>*` at the end of the output and are advisory; the synthesis is usable, but flag the gap in your final answer.

### Gate Early-Return (distinct from verifier hard-fail)

`synthesize` can also return WITHOUT invoking the synthesizer at all — when the **pre-synthesis relevance gate** decides the input source set isn't synthesizable. Two cases:

1. **`## Source quality insufficient`** (REJECT decision) — average source relevance below the gate's `reject_threshold` (defaults 0.2 for `comprehensive`/`contracrow`, 0.3 for class default). Returned as a markdown response with a header like:

   ```
   # Synthesis: {query}
   *Preset: Comprehensive*
   ## Source quality insufficient

   The pre-synthesis relevance gate rejected the input source set (avg relevance 0.15 below threshold 0.2). Synthesis skipped to prevent hallucination over irrelevant sources.

   **Suggested follow-up searches:** ...

   ---
   *Pre-synthesis source-relevance gate: 0 passed, N filtered (avg source relevance: 0.15). Synthesis NOT cached — gather better sources and re-call.*
   ```

2. **`## Source quality insufficient (partial, zero passed)`** (PARTIAL-with-zero-good edge case) — average relevance above the reject floor but no individual source clears the `pass_threshold`. Same shape, different header.

**When you see either of these:**
- The synthesizer was **never invoked**; there is no synthesis to retry.
- The output is **NOT cached** — re-calling with the same sources will re-evaluate.
- Action: gather more relevant sources (Triple Stack again, broader queries, different focus mode) and re-call. Do NOT retry with the same source set; the gate's verdict is data-driven, not flaky.
- Distinct from the verifier hard-fail above — those mean the synthesizer ran but produced unreliable output; these mean the synthesizer was deliberately skipped.

**Token cost:** ~5000-10000 tokens
**Time:** 3-5 min

---

## Tool Reference

### gigaxity-deep-research Tools

| Tool | Role | Description |
|------|------|-------------|
| `discover` | EXPLORATORY | Cold-start discovery with gap analysis, returns scored URLs |
| `synthesize` | SYNTHESIS | Weave pre-gathered content into coherent narrative with citations |
| `reason` | SYNTHESIS | Chain-of-thought reasoning on pre-gathered content |
| `ask` | DIRECT | Quick LLM answer without search |
| `search` | Utility | RRF fusion search (use when simple search needed) |
| `research` | Convenience | Combined search+synthesis (standalone use only) |

#### discover: focus_mode Parameter

Controls domain-specific gap analysis and search strategy:

| Mode | Gap Categories | Search Expansion | Use When |
|------|---------------|------------------|----------|
| `general` | documentation, examples, alternatives, gotchas | ON | Broad technical questions |
| `academic` | methodology, limitations, replications, critiques | ON | Research papers, scientific topics |
| `documentation` | api_reference, examples, migration, changelog, configuration | OFF (focused) | Library/framework questions |
| `comparison` | criteria, tradeoffs, edge_cases, benchmarks, community_preference | ON | "Which should I use?" questions |
| `debugging` | error_context, similar_issues, root_cause, workarounds, fixes | ON | Error messages, stack traces |
| `tutorial` | prerequisites, step_by_step, common_mistakes, next_steps | OFF | Learning, getting started |
| `news` | announcement, reaction, impact, timeline | ON + time-filtered | "Latest" or "announced" queries |

```
# Example: Debugging query
mcp__gigaxity-deep-research__discover(
    query="TypeError: Cannot read property 'map' of undefined React",
    focus_mode="debugging"  # → triggers error_context, root_cause gaps
)

# Example: Learning query
mcp__gigaxity-deep-research__discover(
    query="How to get started with FastAPI",
    focus_mode="tutorial"  # → triggers prerequisites, step_by_step gaps
)
```

#### synthesize: preset Parameter

Controls which pipeline components run before synthesis:

| Preset | Pipeline Components | Use When |
|--------|---------------------|----------|
| `comprehensive` | Quality Gate → RCS → Contradiction Detection → Outline-Guided | Important research, best quality |
| `fast` | Direct synthesis only | Sources already high-quality, need speed |
| `contracrow` | Quality Gate → RCS → Contradiction Detection | Sources may disagree, comparisons |
| `academic` | Quality Gate → RCS → Contradiction Detection → Outline-Guided | Formal reports, documentation |
| `tutorial` | Outline-Guided only | Guides, tutorials, explanations |

**Pipeline components:**
- **Quality Gate (CRAG)**: Filter low-quality/irrelevant sources
- **RCS**: Query-focused summarization (summarize each source for the specific question)
- **Contradiction Detection**: Find conflicting claims between sources
- **Outline-Guided**: Plan structure before writing (better coverage)

```
# Example: Comparison with potential conflicts
mcp__gigaxity-deep-research__synthesize(
    query="FastAPI vs Flask",
    sources=[...],
    style="comparative",
    preset="contracrow"  # → highlights conflicting claims
)

# Example: Quick synthesis of trusted sources
mcp__gigaxity-deep-research__synthesize(
    query="React hooks",
    sources=[official_docs],
    preset="fast"  # → no preprocessing, direct synthesis
)
```

#### reason: reasoning_depth Parameter

Controls chain-of-thought thoroughness:

| Depth | Steps | Use When |
|-------|-------|----------|
| `shallow` | 2-3 | Simple deductions, sanity checks |
| `moderate` | 4-6 | Standard decisions, trade-off analysis (default) |
| `deep` | 7+ with backtracking | Critical architectural decisions, complex debugging |

```
# Example: Critical decision
mcp__gigaxity-deep-research__reason(
    query="Should we use microservices or monolith for this system?",
    context="[requirements and constraints]",
    reasoning_depth="deep"  # → exhaustive analysis
)
```

### Triple Stack Tools

**Ref (Documentation):**
- `mcp__Ref__ref_search_documentation(query)` - Search docs
- `mcp__Ref__ref_read_url(url)` - Read URL to markdown

**Exa 3.2.0 (4 active tools — 6 deprecated tools removed):**
- `mcp__exa__web_search_exa(query, numResults, type)` — Semantic web search. `type` enum: `auto` | `fast`. **Note:** `type="deep"` was documented in prior skill revisions as a replacement for deprecated `deep_researcher_start/check` — that was wrong. The 3.2.0 MCP does not expose a `deep` type on either search tool. For deep multi-hop research, use gigaxity-deep-research `discover` → Jina `parallel_read_url` → `synthesize`.
- `mcp__exa__web_search_advanced_exa(query, category, ...)` — Full control: categories (`company` / `research paper` / `news` / `pdf` / `github` / `personal site` / `people` / `financial report`), `includeDomains` / `excludeDomains`, `startPublishedDate` / `endPublishedDate` / `startCrawlDate` / `endCrawlDate`, `includeText` / `excludeText`, `userLocation`, `moderation`, `additionalQueries` (query variations in one call), `enableHighlights`, `enableSummary`, `subpages` + `subpageTarget` (crawl linked pages from result URLs).
- `mcp__exa__get_code_context_exa(query, tokensNum)` — Code examples and patterns from curated code index (GitHub/SO/docs). No Jina equivalent.
- `mcp__exa__crawling_exa(urls, maxCharacters, subpages, subpageTarget)` — Batch URL content extraction. Unique `subpages`/`subpageTarget` mode crawls linked pages per seed URL.

**Exa Deprecated (do NOT use — replacements via `web_search_advanced_exa` category filter):**
- ~~`company_research_exa`~~ → `web_search_advanced_exa category="company"`
- ~~`people_search_exa`~~ / ~~`linkedin_search_exa`~~ → `web_search_advanced_exa category="people"`
- ~~`deep_researcher_start`~~ / ~~`deep_researcher_check`~~ → **no MCP replacement.** Use gigaxity-deep-research `discover` → Jina `parallel_read_url` → `synthesize`. (Exa 3.2.0 MCP does not expose `type="deep"` on any search tool; the underlying `/research` API endpoint exists but is not wrapped.)
- ~~`deep_search_exa`~~ → `web_search_advanced_exa`
- ~~`find_similar`~~ → `web_search_exa` on related content

**Exa Answer (exa-answer MCP — fast factual):**
- `exa_answer(query, include_sources)` — Fast factual answer with citations (1-2s, 94% SimpleQA accuracy). Unique — no Jina equivalent.
- `exa_answer_detailed(query, system_prompt)` — Detailed answer with full source text.

**Jina 1.4.0 (21 tools):**

*Search (Jina-native — free-tier enabled):*
- `mcp__jina__search_web(query, num)` — General web search (~63 tokens/call)
- `mcp__jina__search_arxiv(query, num)` — arXiv papers, structured author/abstract/version
- `mcp__jina__search_ssrn(query, num)` — SSRN papers (econ/law/finance/social sciences)
- `mcp__jina__search_bibtex(query, num)` — DBLP + Semantic Scholar → BibTeX
- `mcp__jina__search_images(query, return_url=True)` — Image search (ALWAYS use `return_url=True` — base64 causes API Error 400)
- `mcp__jina__search_jina_blog(query)` — Jina AI blog search

*Parallel multi-query / multi-URL:*
- `mcp__jina__parallel_search_web(searches)` — 3-5 queries in one call (~107 tokens for 3 = 36/query)
- `mcp__jina__parallel_search_arxiv(searches)` — parallel arXiv
- `mcp__jina__parallel_search_ssrn(searches)` — parallel SSRN
- `mcp__jina__parallel_read_url(urls, timeout=60000)` — Bulk read 3-5 URLs (~17k tokens, content-proportional)

*URL reading:*
- `mcp__jina__read_url(url)` — URL to clean markdown (0 tokens, free reader tier)

*Visual / PDF:*
- `mcp__jina__capture_screenshot_url(url)` — Webpage screenshot (base64 JPEG)
- `mcp__jina__extract_pdf(url)` — Layout-detected figures, tables, equations from PDFs

*Free post-processing middleware (0 tokens — use liberally):*
- `mcp__jina__sort_by_relevance(query, documents)` — Reranker (insert between Triple Stack gather and synthesize)
- `mcp__jina__deduplicate_strings(strings)` — Semantic dedup (save synthesis tokens)
- `mcp__jina__deduplicate_images(images)` — CLIP v2 image dedup
- `mcp__jina__classify_text(input, labels)` — Label/route content via embeddings

*Free pre-processing / utility:*
- `mcp__jina__primer()` — Current UTC / user timezone / session time context
- `mcp__jina__guess_datetime_url(url)` — Infer published/updated datetime from headers + Schema.org + markers (credibility/staleness check)
- `mcp__jina__show_api_key()` — Debug: return bearer token the server sees

**AVOID:** `mcp__jina__expand_query(query)` — 12,000 tokens/call (LLM-backed rewrite). Manually rewrite queries in the prompt instead.

---

## Perplexity Replacement Mapping

| Deprecated Perplexity Tool | gigaxity-deep-research Replacement | Workflow |
|----------------------------|--------------------------|----------|
| `perplexity_search` | `discover` | EXPLORATORY entry |
| `perplexity_ask` | `ask` | DIRECT quick answer |
| `perplexity_research` | `synthesize` | SYNTHESIS (post-Triple Stack) |
| `perplexity_reason` | `reason` | SYNTHESIS (chain-of-thought) |
| `perplexity_deep_research` | `discover` → Jina → `synthesize` | EXPLORATORY full flow |

---

## Synthesis Styles

| Style | Use When |
|-------|----------|
| `comprehensive` | Default, full analysis with sections |
| `concise` | Brief, focused answer (2-4 paragraphs) |
| `comparative` | X vs Y comparisons, side-by-side |
| `technical` | Deep technical analysis |
| `explanatory` | Tutorial-style explanation |

---

## Common Patterns

### Pattern 1: Library Documentation Lookup (DIRECT)

```
User: "How do I use FastAPI's Depends?"

# DIRECT - specific library, official docs exist
mcp__Ref__ref_search_documentation(query="FastAPI Depends dependency injection")
```

### Pattern 2: General Concept Exploration (EXPLORATORY)

```
User: "What are vector databases?"

# EXPLORATORY - general concept, cold start
discover_result = mcp__gigaxity-deep-research__discover(
    query="vector databases",
    top_k=10,
    focus_mode="general"  # → gaps: documentation, examples, alternatives
)
# Score URLs, select top 3-5
content = mcp__jina__parallel_read_url(urls=[top_urls], timeout=60000)
mcp__gigaxity-deep-research__synthesize(
    query="vector databases",
    sources=[...],
    preset="comprehensive"  # → full pipeline for important research
)
```

### Pattern 3: Framework Comparison (SYNTHESIS)

```
User: "Compare React vs Vue for large applications"

# SYNTHESIS - comparison, need multiple perspectives
ref = mcp__Ref__ref_search_documentation(query="React Vue large scale")
exa = mcp__exa__get_code_context_exa(query="React Vue enterprise patterns")
jina = mcp__jina__search_web(query="React vs Vue 2026 comparison", num=5)

mcp__gigaxity-deep-research__synthesize(
    query="Compare React vs Vue for large applications",
    sources=[...converted results...],
    style="comparative",
    preset="contracrow"  # → highlights conflicting claims between sources
)
```

### Pattern 4: Best Practices Query (SYNTHESIS)

```
User: "What are best practices for Python error handling?"

# SYNTHESIS - need validated patterns, consensus
ref = mcp__Ref__ref_search_documentation(query="Python error handling best practices")
exa = mcp__exa__get_code_context_exa(query="Python exception handling patterns")
jina = mcp__jina__search_web(query="Python error handling 2026 best practices", num=5)

mcp__gigaxity-deep-research__synthesize(
    query="Best practices for Python error handling",
    sources=[...],
    style="comprehensive",
    preset="academic"  # → structured output with proper citations
)
```

### Pattern 5: Latest Developments (EXPLORATORY)

```
User: "What are the latest developments in AI agents?"

# EXPLORATORY - recent developments, evolving field
discover_result = mcp__gigaxity-deep-research__discover(
    query="AI agents latest developments 2026",
    top_k=10,
    focus_mode="news"  # → time-filtered, announcement/impact gaps
)
content = mcp__jina__parallel_read_url(urls=[...], timeout=60000)
mcp__gigaxity-deep-research__synthesize(
    query="latest AI agent developments",
    sources=[...],
    preset="fast"  # → news doesn't need heavy preprocessing
)
```

### Pattern 6: Quick Answer (DIRECT)

```
User: "What is the difference between let and const in JavaScript?"

# DIRECT - simple factual, quick LLM sufficient
mcp__gigaxity-deep-research__ask(query="difference between let and const in JavaScript")
```

### Pattern 7: Error Debugging (EXPLORATORY)

```
User: "Getting 'CORS policy' error when calling my API"

# EXPLORATORY with debugging focus
discover_result = mcp__gigaxity-deep-research__discover(
    query="CORS policy error API fetch blocked",
    top_k=10,
    focus_mode="debugging"  # → error_context, root_cause, workarounds gaps
)
content = mcp__jina__parallel_read_url(urls=[...], timeout=60000)
mcp__gigaxity-deep-research__synthesize(
    query="Fix CORS policy errors",
    sources=[...],
    preset="fast"  # → debugging needs speed
)
```

### Pattern 8: Tutorial/Learning (EXPLORATORY)

```
User: "How do I get started with Docker?"

# EXPLORATORY with tutorial focus
discover_result = mcp__gigaxity-deep-research__discover(
    query="Docker getting started tutorial",
    top_k=10,
    focus_mode="tutorial"  # → prerequisites, step_by_step gaps
)
content = mcp__jina__parallel_read_url(urls=[...], timeout=60000)
mcp__gigaxity-deep-research__synthesize(
    query="Docker getting started guide",
    sources=[...],
    preset="tutorial"  # → structured how-to format
)
```

### Pattern 9: Architectural Decision (SYNTHESIS + REASON)

```
User: "Should we use PostgreSQL or MongoDB for our e-commerce app?"

# SYNTHESIS with deep reasoning
ref = mcp__Ref__ref_search_documentation(query="PostgreSQL MongoDB comparison")
exa = mcp__exa__get_code_context_exa(query="e-commerce database choice")
jina = mcp__jina__search_web(query="PostgreSQL vs MongoDB 2026 e-commerce", num=5)

# Use reason for critical architectural decision
mcp__gigaxity-deep-research__reason(
    query="PostgreSQL vs MongoDB for e-commerce: which is better?",
    context="[Summary of gathered sources + app requirements]",
    reasoning_depth="deep"  # → exhaustive analysis for critical decision
)
```

---

## Anti-Patterns to Avoid

### 1. Don't Use EXPLORATORY for Specific Library Queries

```
❌ User: "How do I use React's useEffect?"
   → discover → Jina → synthesize  # Overkill

✅ User: "How do I use React's useEffect?"
   → Ref ref_search_documentation  # DIRECT
```

### 2. Don't Use DIRECT for Comparisons

```
❌ User: "FastAPI vs Flask?"
   → ref_search_documentation  # Won't get comparison

✅ User: "FastAPI vs Flask?"
   → Triple Stack → synthesize(style="comparative")  # SYNTHESIS
```

### 3. Don't Stop After Triple Stack

```
❌ Triple Stack results gathered...
   "Here are the sources I found, let me know if you want me to synthesize"

✅ Triple Stack results gathered...
   → IMMEDIATELY synthesize/reason → Present final answer
```

### 4. Don't Re-Search in Synthesis

```
❌ Triple Stack → synthesize → (synthesize searches again)  # Perplexity did this

✅ Triple Stack → synthesize (uses ONLY provided sources)  # gigaxity-deep-research
```

### 5. Don't Use research Tool for Triple Stack Workflows

```
❌ Triple Stack → gigaxity-deep-research research  # research does its own search

✅ Triple Stack → gigaxity-deep-research synthesize  # synthesize uses provided sources
```

### 6. Don't Cite from Empty-Body URLs (gptr-mcp Reddit/X Quirk)

`mcp__gptr-mcp__quick_search` routes Reddit / X / YouTube queries through OpenAI's web-search retriever (per the `SOCIAL_OPENAI_DOMAINS` config). For anti-scraped domains, the response shape is href-only:

```json
{"href": "https://reddit.com/r/.../comments/.../slug-here/", "body": "", "title": ""}
```

The URL is a CANDIDATE, not evidence. Never infer thread content from the URL slug.

```
❌ quick_search returns {href: ".../granite-docling-hallucinating/", body:"", title:""}
   → cite as "Reddit users report Granite-Docling hallucinations" based on slug
✅ quick_search returns href with empty body
   → ToolSearch(query='select:mcp__jina__read_url')
   → mcp__jina__read_url(url=href)  # fetch the actual content
   → cite from the fetched content only
   → if blocked: mcp__brightdata_fallback__scrape_as_markdown(url=href)
   → if also blocked: drop the claim
```

Other tools with similar shapes: any search retriever that returns "href-only" snippets on access-controlled domains (LinkedIn, paywalled news, members-only forums). When in doubt, check both `body` and `title` — if both are empty strings, it is a candidate, not evidence.

---

## Workflow Selection Heuristics

**Choose DIRECT when:**
- Query contains specific library/framework name
- "How do I [specific thing] in [specific library]?"
- Official documentation would answer it
- Single authoritative source exists

**Choose EXPLORATORY when:**
- Query is about general concepts
- User is learning something new
- "What is...", "Explain...", "How does [general system] work?"
- Latest developments in a field
- Cold start - user doesn't know what they don't know

**Choose SYNTHESIS when:**
- Query contains comparison words (vs, compare, better, best, trade-offs)
- Query asks for recommendations or best practices
- Multiple valid approaches exist
- Consensus or validation needed
- "Which should I use?", "What are best practices?"

---

## Date-Aware Research

**Current year: 2026**

Always add recency context for evolving topics:
- "React hooks tutorial 2026 latest"
- "FastAPI best practices 2026"

**Tool-specific time filters:**
- Jina: `tbs="qdr:y"` (year), `tbs="qdr:m"` (month)
- Exa: Add "2026 latest" to query text

---

## Fallback Chains

**CRITICAL:** When a tool returns ERROR/404/BLOCK on a specific URL, try Brightdata on the SAME URL first (preserve source), then follow the fallback chain.

### Brightdata Fallback (Native MCP)

**Problem:** Full Brightdata MCP has 63+ tools (fills context, causes "No such tool available" errors).
**Solution:** Dedicated minimal MCP server exposing ONLY `scrape` tool.

**When to invoke:** Content shows:
- Empty or minimal content (just domain name)
- "Verify you are human" / CAPTCHA prompts
- "Subscribe to continue reading" / paywall messages
- 403 Forbidden / Access Denied errors
- Cloudflare challenge pages
- "Please enable JavaScript" messages

**Usage (native MCP call):**
```
mcp__brightdata_fallback__scrape_as_markdown(url="BLOCKED_URL")
```

**Why native MCP:**
- Single tool exposed (scrape) - zero context overhead
- Direct MCP call like other global tools
- Calls Brightdata Web Unlocker API directly
- Bypasses CAPTCHA, paywalls, Cloudflare

### Documentation Lookup

```
mcp__Ref__ref_search_documentation(query)
  ON FAIL → mcp__exa__get_code_context_exa(query)
  ON FAIL → mcp__jina__search_web(query)
```

### URL Reading

```
mcp__jina__read_url(url)
  ON ERROR/404/BLOCK → mcp__brightdata_fallback__scrape_as_markdown(url)
  ON FAIL → mcp__Ref__ref_read_url(url)
  ON FAIL → WebFetch(url)  # built-in
```

### Documentation URL Reading

```
mcp__Ref__ref_read_url(url)
  ON ERROR/404/BLOCK → WebFetch(url)  # built-in
  ON FAIL → mcp__brightdata_fallback__scrape_as_markdown(url)
  ON FAIL → mcp__jina__read_url(url)
```

### Code Search

```
mcp__exa__get_code_context_exa(query)
  ON FAIL → Task tool (git clone + native tools)
```

### General Web Search

```
mcp__jina__search_web(query)             # 63 tokens/call — primary
  ON FAIL → mcp__exa__web_search_exa(query)
  ON FAIL → WebSearch(query)              # built-in
```

### Parallel Multi-Query Web Search

```
mcp__jina__parallel_search_web(searches=[3-5 query variants])  # 107 tokens for 3 queries
  # Exa has no parallel mode — no Exa fallback at the parallel tier
  ON FAIL → Sequential mcp__exa__web_search_exa calls
```

### GitHub Issues/PRs/Discussions

```
mcp__jina__read_url(github_issue_url)
  ON ERROR/404 → mcp__brightdata_fallback__scrape_as_markdown(url)
  ON FAIL → mcp__exa__web_search_exa("site:github.com [topic]")  # find alternatives
```

### Academic Papers

```
mcp__jina__search_arxiv(query)
  # No fallback - Jina is primary for academic
```

### Repository Discovery

```
mcp__exa__web_search_advanced_exa(query, category="github")
  ON FAIL → mcp__exa__web_search_exa(query)
  ON FAIL → mcp__jina__search_web(query + " site:github.com")
```

### Image Search

```
mcp__jina__search_images(query, return_url=True)
  # No fallback - Jina is primary for images
  # ALWAYS use return_url=True (base64 causes API Error 400)
```

### Synthesis Fallback

```
mcp__gigaxity-deep-research__synthesize(query, sources)
  ON FAIL → mcp__gigaxity-deep-research__ask(query, context=summary_of_sources)
  ON FAIL → Present raw sources to user with brief summary
```

### Discovery Fallback

```
mcp__gigaxity-deep-research__discover(query)
  ON FAIL → mcp__exa__web_search_exa(query) + mcp__jina__search_web(query)
           → Score URLs manually → mcp__jina__parallel_read_url
           → mcp__gigaxity-deep-research__synthesize
```

### Fallback Decision Logic

```
Tool returns ERROR/404/BLOCK on specific URL?
  YES → Try Brightdata on SAME URL first (preserve source)
      → Then follow "ON FAIL →" chain (find alternative sources)

Tool succeeds but returns NO RESULTS?
  YES → Follow "ON FAIL →" chain IMMEDIATELY
      → Do NOT retry same tool

Tool completely fails (timeout, connection error)?
  YES → Follow "ON FAIL →" chain
      → Do NOT use tools outside the chain
```

---

## Performance Notes

- **DIRECT:** ~100-500 tokens, <10 seconds
- **EXPLORATORY:** ~2000-5000 tokens, 1-2 min
- **SYNTHESIS:** ~5000-10000 tokens, 3-5 min

**Jina parallel operations:**
- Always use `timeout=60000` (default 30000 insufficient)
- Limit to 3-5 URLs per parallel call
- Use `return_url=True` for images/screenshots

**gigaxity-deep-research connector fan-out (load-bearing):**
- `mcp__gigaxity-deep-research__search` / `discover` / `research` fan out to up to 3 backends in parallel via RRF fusion: **SearXNG** (always available — no key), **Tavily** (gated on `RESEARCH_TAVILY_API_KEY`), **LinkUp** (gated on `RESEARCH_LINKUP_API_KEY`).
- Connectors with missing keys are **silently dropped at init** (`SearchAggregator.__init__` filters on `is_configured()`). No error, no warning.
- Health check: `mcp__gigaxity-deep-research__search` returns a trailer line `*N results from ['searxng', 'tavily', 'linkup'] (configured: ['searxng', 'tavily', 'linkup'])*`. If `configured:` shows only `['searxng']`, the other two are unconfigured at init. If `from` is shorter than `configured`, the configured connectors errored or returned empty for this query.
- `research` mirrors the same `from [...] (configured: [...])` shape; `discover` surfaces only the `configured:` line (the Explorer wraps the aggregator and does not expose per-connector raw results).
- Healthy steady state (3-way fusion) requires both `RESEARCH_TAVILY_API_KEY` and `RESEARCH_LINKUP_API_KEY` in the MCP `env` block (`~/.claude.json` under `gigaxity-deep-research.env`). MCP subprocess must be restarted after env changes — restart the full Claude Code session.
- Searxng-only state is functional but lower-coverage — `discover` landscapes and `synthesize` outputs derived from gigaxity's own search will be less diverse.

**Exa MCP transport (HTTP vs stdio — load-bearing):**
- The active config uses the **HTTP transport** at `https://mcp.exa.ai/mcp?tools=...` because the stdio binary silently ignores `ENABLED_TOOLS` and caps at 3 default tools (`web_search_exa`, `get_code_context_exa`, `crawling_exa`). HTTP transport honors the `tools=` URL query param and exposes all 4 active tools including `web_search_advanced_exa`.
- **Rate-limit behavior differs from stdio.** HTTP transport is served from Exa's Vercel edge and has its own quota/throttle profile. If you hit 429s or truncated results:
  1. Reduce `numResults` and drop optional content fields (`enableHighlights`, `enableSummary`, `subpages`) — simpler requests clear higher quota tiers.
  2. Retry with `type: "instant"` on `web_search_advanced_exa` (fastest path).
  3. The underlying API supports a `livecrawl` toggle (`"preferred"` / `"fallback"`), but **MCP 3.2.0 does not expose it** — `web_search_exa` has no `livecrawl` param, and `web_search_advanced_exa` only exposes `livecrawlTimeout` (number). To use the toggle, call `api.exa.ai/search` directly (outside the MCP) or wait for an MCP version that surfaces it.
- If `web_search_advanced_exa` is persistently unavailable, revert the `~/.claude.json` Exa entry to stdio with `ENABLED_TOOLS="web_search_exa,get_code_context_exa,crawling_exa"` (3 tools only) and route all category/date/domain queries through Jina `search_web` + manual filtering. Lossy fallback — prefer fixing HTTP first.

---

## Use Case Priority Matrix

Rationale: Jina is rotatable-for-free (10M trial tier via Camoufox key rotation); Exa costs a Google account lockout per rotation. Default to Jina for high-frequency calls, reserve Exa for what only Exa does well.

| Task | PRIMARY | Secondary | NEVER |
|------|---------|-----------|-------|
| **Mid-task factual lookup** | exa_answer (1-2s, 94% SimpleQA) | gigaxity-deep-research ask | Synthesis tools |
| **API docs** | Ref | Exa get_code_context | — |
| **Code examples / patterns** | Exa get_code_context | Jina search_web `site:github.com` | Ref |
| **Repository docs** | Ref | Jina read_url | — |
| **GitHub repo discovery** | Exa advanced (category="github") | Jina search_web `site:github.com` | — |
| **GitHub issues/PRs** | Jina read_url | Brightdata fallback | Ref |
| **General web (single query)** | Jina search_web (63 tokens) | Exa web_search_exa | Ref |
| **Parallel multi-query web** | Jina parallel_search_web (107 tokens/3 queries) | Sequential Exa web_search_exa | Ref |
| **Advanced filtered web** (category / date / domain / text) | Exa web_search_advanced_exa | — | — |
| **Company research** | Exa advanced (category="company") | Jina search_web | Ref |
| **People / OSINT (attribute search)** | Exa advanced (category="people") | Jina search_web `site:linkedin.com` | Ref |
| **Financial reports (SEC, earnings)** | Exa advanced (category="financial report") | Exa advanced (category="pdf") | — |
| **News / current events (date-bounded)** | Exa advanced (category="news" + startPublishedDate) | Jina search_web | Ref |
| **Social (tweets)** | gptr-mcp quick_search (site:x.com) | Jina search_web | Exa (no tweet category) |
| **Academic (arXiv)** | Jina search_arxiv / parallel_search_arxiv | Exa advanced (category="research paper") | — |
| **Academic (SSRN — econ/law/finance)** | Jina search_ssrn / parallel_search_ssrn | — | — |
| **BibTeX citations** | Jina search_bibtex | — | — |
| **PDFs / whitepapers** | Exa advanced (category="pdf") | Jina search_web | — |
| **PDF layout extraction (figures/tables)** | Jina extract_pdf | — | — |
| **Images** | Jina search_images (`return_url=True`) | — | All others |
| **Screenshots** | Jina capture_screenshot_url | — | All others |
| **URL content extraction (single)** | Jina read_url (0 tokens) | Exa crawling_exa | — |
| **URL content extraction (bulk 3-5)** | Jina parallel_read_url | Exa crawling_exa with urls array | — |
| **URL subpage crawl** | Exa crawling_exa with subpages/subpageTarget | — | Jina (no subpage mode) |
| **URL freshness / credibility check** | Jina guess_datetime_url | — | — |
| **Fact-check exact claim** | Exa advanced (`includeText=[claim]` + `additionalQueries=["X true","X false"]`) | — | — |
| **Geo-targeted search** | Exa advanced (`userLocation=<ISO code>`) | — | — |
| **Rerank Triple Stack union** | Jina sort_by_relevance (0 tokens) | — | — |
| **Dedup snippets before synthesis** | Jina deduplicate_strings (0 tokens) | — | — |
| **Text classification** | Jina classify_text | — | — |
| **Time-aware session primer** | Jina primer | — | — |
| **Citations** | gigaxity-deep-research synthesize | Jina search_bibtex | Single tools |
| **Cold-start discovery** | gigaxity-deep-research discover | — | Single tool only (`type="deep"` not in MCP 3.2.0) |
| **Cross-validation** | Triple Stack → synthesize | — | Single tool |
| **Synthesis** | gigaxity-deep-research synthesize | gigaxity-deep-research reason | Skip Triple Stack |
| **Quick answer** | exa_answer | gigaxity-deep-research ask | Synthesis tools |
| **Deep reasoning** | gigaxity-deep-research reason | gigaxity-deep-research synthesize | discover |
| **Blocked URLs** | Brightdata fallback | Jina/WebFetch | — |
| **Deep multi-hop async research** | gigaxity-deep-research `discover` → Jina `parallel_read_url` → `synthesize` | — | Exa `type="deep"` (not exposed in MCP 3.2.0) |

**AVOID entirely:** `mcp__jina__expand_query` (12k tokens/call). Rewrite query variants in the prompt.

---

## Token Burn Rate (Jina, 10M trial key, stabilized probe 2026-04-18)

Use this to budget calls per rotation. Full Jina tier with Camoufox rotation ≈ effectively unlimited; Exa burns Exa credits per call (treat as precious).

| Tool | Cost/call | Notes |
|------|-----------|-------|
| `read_url` | **0** | Free Reader tier — lean on this for single-URL extraction |
| `primer` | **0** | Free — session time/timezone context |
| `sort_by_relevance` | **0** | Free reranker — insert before synthesis |
| `deduplicate_strings` | **0** | Free dedup — insert before synthesis |
| `deduplicate_images` | **0** (est.) | Free CLIP dedup |
| `classify_text` | **0** (est.) | Free embeddings classifier |
| `guess_datetime_url` | **0** (est.) | Free metadata probe |
| `search_web` | **63** | Dirt cheap — primary general search |
| `parallel_search_web` | **107 / 3 queries = 36/query** | Winner for SYNTHESIS gather |
| `search_arxiv` | **343** | Cheap — academic primary |
| `search_ssrn` | ~343 (est.) | Cheap — social science primary |
| `search_bibtex` | ~343 (est.) | Cheap — citation workflows |
| `search_images` | ~100 (est.) | Cheap |
| `capture_screenshot_url` | content-proportional (img ~13KB b64) | Use sparingly |
| `parallel_read_url` | **17,033** | Content-proportional — use for SYNTHESIS deep reads only |
| `extract_pdf` | content-proportional | Use for specific PDFs, not bulk |
| ~~`expand_query`~~ | **12,000** | ⚠️ AVOID — manually rewrite query variants |

**Budget math (10M trial key):**
- Pure `search_web`: ~158,000 calls before depletion
- Full SYNTHESIS query (1× parallel_search + 1× parallel_read of 3 URLs + free rerank + free dedup): ~17,140 tokens → **~584 full synthesis queries per trial key**
- With weekly rotation: effectively unlimited

## High-Value Follow-On Chains

Sequences below encode the "surface → read → synthesize" pattern with tool specialization at each stage. Pattern numbers are additive to the existing Pattern 1-9 in the Common Patterns section.

### Pattern 10: OSINT on a Person

```
User: "Research [person name], background and current work"

# Step 1: surface profiles with attribute-aware neural search
profiles = mcp__exa__web_search_advanced_exa(
    query="[name] [role] [domain]",
    category="people",
    numResults=5
)

# Step 2: deep-read top profile(s)
profile_content = mcp__jina__parallel_read_url(
    urls=[top_3_profile_urls],
    timeout=60000
)

# Step 3: third-party mentions (what OTHERS say — excludes self-authored)
mentions = mcp__exa__web_search_advanced_exa(
    query="[name]",
    includeText=["[name]"],
    excludeDomains=["linkedin.com", person_home_domain],
    numResults=10
)

# Step 4: academic output (if applicable)
papers = mcp__jina__parallel_search_arxiv(searches=[
    {"query": "author:[name]"},
    {"query": "[name] [subfield]"},
])

# Step 5: verify site activity (credibility signal)
freshness = mcp__jina__guess_datetime_url(url=person_home_url)

# Step 6: synthesize
mcp__gigaxity-deep-research__synthesize(
    query="[name] profile and current work",
    sources=[...all above...],
    preset="comprehensive"
)
```

### Pattern 11: Company Deep-Dive

```
User: "Full profile on [company]: what they do, financials, recent news"

# Step 1-3 in parallel
company = mcp__exa__web_search_advanced_exa(query="[company]", category="company", numResults=5)
financials = mcp__exa__web_search_advanced_exa(query="[company]", category="financial report", numResults=3)
news = mcp__exa__web_search_advanced_exa(
    query="[company]",
    category="news",
    startPublishedDate="<6 months ago>",
    numResults=10
)

# Step 4: bulk-read top URLs across the three
top_urls = mcp__jina__sort_by_relevance(
    query="[company] strategy financials recent moves",
    documents=[...all URLs from 1-3...]
)[:5]
content = mcp__jina__parallel_read_url(urls=top_urls, timeout=60000)

# Step 5: visual check on marketing site
screenshot = mcp__jina__capture_screenshot_url(url=company_home_url)

# Step 6: synthesize
mcp__gigaxity-deep-research__synthesize(
    query="[company] deep-dive",
    sources=[...],
    preset="comprehensive"
)
```

### Pattern 12: Academic Literature Review

```
User: "Lit review on [topic] — recent work + foundational papers"

# Step 1: parallel academic search across 3-5 angles
arxiv = mcp__jina__parallel_search_arxiv(searches=[
    {"query": "[topic] recent"},
    {"query": "[topic] foundational"},
    {"query": "[topic] survey"},
])
ssrn = mcp__jina__search_ssrn(query="[topic]", num=5)  # if econ/law/finance
non_arxiv = mcp__exa__web_search_advanced_exa(query="[topic]", category="research paper", numResults=5)

# Step 2: canonical citations
bibtex = mcp__jina__search_bibtex(query="[topic]", num=10)

# Step 3: extract figures/tables from top 3 papers
paper_details = [mcp__jina__extract_pdf(url=u) for u in top_3_paper_pdf_urls]

# Step 4: synthesize as formal lit review
mcp__gigaxity-deep-research__synthesize(
    query="Literature review: [topic]",
    sources=[...],
    style="comparative",
    preset="academic"
)
```

### Pattern 13: News Event Timeline

```
User: "What happened with [event] — timeline, key sources"

# Step 1: primary coverage, tight date window
coverage = mcp__exa__web_search_advanced_exa(
    query="[event]",
    category="news",
    startPublishedDate=event_start,
    endPublishedDate=event_end,
    numResults=20
)

# Step 2: verify each source's actual publication date
freshness = [mcp__jina__guess_datetime_url(url=u) for u in coverage_urls]
# Filter to sources whose inferred date matches claimed publish date

# Step 3: rerank by relevance to event name
ranked = mcp__jina__sort_by_relevance(query="[event] timeline", documents=verified_urls)

# Step 4: deep-read top 5
timeline_content = mcp__jina__parallel_read_url(urls=ranked[:5], timeout=60000)

# Step 5: synthesize chronologically
mcp__gigaxity-deep-research__synthesize(
    query="[event] timeline",
    sources=[...],
    style="chronological",
    preset="comprehensive"
)
```

### Pattern 14: Fact-Check a Specific Claim

```
User: "Is [specific claim] true?"

# Step 1: find sources asserting the exact claim text
affirming = mcp__exa__web_search_advanced_exa(
    query="[claim topic]",
    includeText=["[exact claim phrase]"],
    numResults=10
)

# Step 2: query variations covering both directions in one call
both_sides = mcp__exa__web_search_advanced_exa(
    query="[claim topic]",
    additionalQueries=["[claim] is true", "[claim] is false", "[claim] debunked"],
    numResults=10
)

# Step 3: free dedup + rerank
deduped = mcp__jina__deduplicate_strings(strings=[...all snippets...])
ranked = mcp__jina__sort_by_relevance(query="[claim] verification", documents=deduped)

# Step 4: synthesize with contradiction detection
mcp__gigaxity-deep-research__synthesize(
    query="Is [claim] true?",
    sources=[...],
    preset="contracrow"  # contradiction-aware
)
```

### Pattern 15: PDF-Heavy Research (Whitepapers / Regulatory Filings)

```
User: "Summarize findings across these whitepapers on [topic]"

# Step 1: surface PDF sources
pdfs = mcp__exa__web_search_advanced_exa(query="[topic]", category="pdf", numResults=10)

# Step 2: full-text via Jina (text content)
text = mcp__jina__parallel_read_url(urls=pdf_urls[:5], timeout=60000)

# Step 3: structured extraction (figures, tables, equations) from top 2-3
structured = [mcp__jina__extract_pdf(url=u) for u in pdf_urls[:3]]

# Step 4: synthesize
mcp__gigaxity-deep-research__synthesize(
    query="[topic] findings across whitepapers",
    sources=[...text + structured...],
    preset="academic"
)
```

### Pattern 16: Competitor / Market Scan

```
User: "Competitive landscape for [category] in [market]"

# Step 1: identify competitors (geo-targeted, exclude own domain)
competitors = mcp__exa__web_search_advanced_exa(
    query="[category] [market]",
    category="company",
    userLocation=<ISO country code>,
    excludeDomains=["<our-domain>"],
    numResults=15
)

# Step 2: recent news per competitor (parallel — 3-5 at a time)
competitor_news = [
    mcp__exa__web_search_advanced_exa(
        query=name,
        category="news",
        startPublishedDate="<3 months ago>",
        numResults=3
    )
    for name in top_competitor_names[:5]
]

# Step 3: rerank union, deep-read top 5
all_urls = [...all competitor + news URLs...]
ranked = mcp__jina__sort_by_relevance(query="[category] competitive moves", documents=all_urls)
content = mcp__jina__parallel_read_url(urls=ranked[:5], timeout=60000)

# Step 4: synthesize
mcp__gigaxity-deep-research__synthesize(
    query="Competitive landscape: [category] in [market]",
    sources=[...],
    style="comparative",
    preset="comprehensive"
)
```

## Integration with Global CLAUDE.md

This skill integrates with existing global MCP configuration:

**Global MCPs (Direct Access):**
- Ref, Exa, Jina (Triple Stack)
- gigaxity-deep-research (synthesis engine)

**Tool Naming:**
- `mcp__gigaxity-deep-research__discover`
- `mcp__gigaxity-deep-research__synthesize`
- `mcp__gigaxity-deep-research__reason`
- `mcp__gigaxity-deep-research__ask`
- `mcp__gigaxity-deep-research__search`

**Replaces deprecated:**
- `mcp__perplexity__perplexity_search`
- `mcp__perplexity__perplexity_ask`
- `mcp__perplexity__perplexity_research`
- `mcp__perplexity__perplexity_reason`
