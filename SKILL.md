---
name: gigaxity-deep-research
description: Deep research MCP server wrapping Tongyi DeepResearch 30B over any OpenAI-compatible chat-completions endpoint (self-hosted vLLM/SGLang/llama.cpp on the local-inference branch, OpenRouter on main). Use when an agent needs cross-source synthesis with citations, exploratory expansion of an unfamiliar topic, chain-of-thought reasoning over evidence, or fast conversational lookups grounded in live web search. Exposes six MCP tools — two primitives (search, research) plus four deep-research tools (discover, synthesize, reason, ask) — with matching REST endpoints for each.
version: 1.0.0
---

# Gigaxity Deep Research

A skill-format reference for any agent (Claude Code, Codex, Cursor, Hermes, plain-MCP) calling this server. For human-readable installation instructions, see [README.md](README.md). For harness-agnostic routing logic and the pasteable instruction block (drop into your harness's global `CLAUDE.md` / `AGENTS.md` or a standalone agent's system prompt), see [CLAUDE.md](CLAUDE.md).

## Quick Start

The MCP server exposes **six tools** — two primitives plus four deep-research tools. Pick one based on query type, call it, return the result to the user verbatim.

```
# Primitives — raw and combined behavior in one call
search(query)                           # Multi-source aggregation, no LLM
research(query)                         # Search + synthesis with citations, single call

# Deep-research tools — discrete steps, drive each independently
ask(query)                              # Fast conversational answer (direct LLM, no search)
discover(query)                         # Exploratory expansion + gap detection
synthesize(query, sources)              # Citation-aware fusion of pre-gathered content
reason(query, sources)                  # Synthesize + explicit chain-of-thought depth control
```

## Workflow

### Phase 1: Classify the query

```
Query class?
├── "what is X right now / when was X / latest version"
│     → Phase 2A (ask)
│
├── "tell me about X" (cold start, no prior context)
│     → Phase 2B (discover → read → synthesize)
│
├── "compare X vs Y" or "best practice for X"
│     → Phase 2C (parallel search → synthesize)
│
└── "why did X happen" or "explain reasoning behind X"
      → Phase 2D (reason)
```

### Phase 2A: ASK (fast factual)

```
result = mcp__gigaxity-deep-research__ask(query="<query>")
return result
```

Token budget: ~500–1500. Latency: ~2–5 s.

### Phase 2B: DISCOVER → READ → SYNTHESIZE (exploratory)

```
discovered = mcp__gigaxity-deep-research__discover(
    query="<query>",
    focus_mode="general",  # or academic, documentation, comparison, debugging, tutorial, news
    identify_gaps=True
)

# discovered.sources is a ranked list. Pick top 3-5 URLs.
top_urls = [s.url for s in discovered.sources[:5]]

# Read full content (use a parallel reader if your environment has one)
contents = parallel_read(top_urls)  # e.g. mcp__jina__parallel_read_url

# Fold into a citation-backed synthesis
result = mcp__gigaxity-deep-research__synthesize(
    query="<query>",
    sources=contents,
    preset="comprehensive"  # or fast, tutorial, academic, contracrow
)
return result
```

Token budget: ~5000–10000. Latency: ~10–20 s.

### Phase 2C: SYNTHESIZE (cross-source comparison)

```
# Gather in parallel from multiple search providers
results = await asyncio.gather(
    docs_search(query),       # e.g. Ref MCP
    code_search(query),       # e.g. Exa get_code_context
    web_search(query),        # e.g. Jina search_web
)

# Optional free middleware: rerank + dedup
ranked = jina_sort_by_relevance(query, results)
deduped = jina_deduplicate_strings(ranked)

result = mcp__gigaxity-deep-research__synthesize(
    query="<query>",
    sources=deduped,
    preset="contracrow"  # surfaces disagreements rather than averaging
)
return result
```

Token budget: ~5000–10000. Latency: ~10–20 s.

### Phase 2D: REASON (CoT over evidence)

```
# Sources-aware: chain-of-thought synthesis over the pre-gathered evidence.
result = mcp__gigaxity-deep-research__reason(
    query="<query>",
    sources=evidence_list,
)
return result  # markdown text — the synthesized answer (the CoT is
               # consumed by the prompt and not echoed back; if the model
               # fails to emit the expected tags, the full raw response is
               # returned as a fallback)
```

Token budget: ~5000–15000. Latency: ~15–30 s.

If you do not have pre-gathered sources, drop the `sources` argument and use depth-controlled CoT instead:

```
result = mcp__gigaxity-deep-research__reason(
    query="<query>",
    context="<optional background>",
    reasoning_depth="deep"        # shallow / moderate / deep
)
```

## API Reference

### MCP tools (stdio)

All six tools return **markdown strings**, not JSON. Every tool also accepts an optional `api_key: str | None = None` for per-request LLM key override (omitted from the signatures below for brevity).

```
mcp__gigaxity-deep-research__search(
    query: str,
    top_k: int = 10
) -> str                      # markdown ranked results, no LLM call

mcp__gigaxity-deep-research__research(
    query: str,
    top_k: int = 10,
    reasoning_effort: Literal["low", "medium", "high"] = "medium"
) -> str                      # markdown: search + synthesis + citations

mcp__gigaxity-deep-research__ask(
    query: str,
    context: str = ""
) -> str                      # the LLM's response text, direct call (no search)

mcp__gigaxity-deep-research__discover(
    query: str,
    top_k: int = 10,
    identify_gaps: bool = True,
    focus_mode: Literal["general","academic","documentation","comparison","debugging","tutorial","news"] = "general"
) -> str                      # markdown: knowledge landscape + gaps + sources

mcp__gigaxity-deep-research__synthesize(
    query: str,
    sources: list[dict],      # {title, content, url?, origin?, source_type?}
    style: Literal["comprehensive","concise","comparative","academic","tutorial"] = "comprehensive",
    preset: Literal["comprehensive","fast","contracrow","academic","tutorial"] | None = None
) -> str                      # markdown: synthesis + citations (+ contradictions if preset enables)

mcp__gigaxity-deep-research__reason(
    query: str,
    context: str = "",
    sources: list[dict] | None = None,
    style: Literal["comprehensive","concise","comparative","academic","tutorial"] = "comprehensive",
    reasoning_depth: Literal["shallow", "moderate", "deep"] = "moderate"
) -> str                      # markdown: CoT response. If `sources` is provided,
                              # synthesizes over them; otherwise depth-controlled CoT
                              # over the model's own knowledge plus optional `context`.
```

For the JSON / typed shape, call the matching REST endpoints (`/api/v1/<tool>`) — see `docs/reference/rest-api.md`.

### REST endpoints

Base URL: `http://localhost:8000` (configurable via `RESEARCH_HOST` / `RESEARCH_PORT`).

| Method | Path | Body | Notes |
|---|---|---|---|
| GET | `/api/v1/health` | — | Health + active connectors |
| POST | `/api/v1/search` | `{query, top_k?, connectors?}` | Multi-source search only, no LLM |
| POST | `/api/v1/research` | `{query, top_k?, reasoning_effort?, preset?, focus_mode?}` | Combined search + synthesis |
| POST | `/api/v1/ask` | `{query, context?, api_key?}` | Direct LLM, no search hop |
| POST | `/api/v1/discover` | `{query, focus_mode?, identify_gaps?, top_k?}` | Exploratory expansion + gap detection |
| POST | `/api/v1/synthesize` | `{query, sources, style?, max_tokens?}` | Citation-aware synthesis over pre-gathered content |
| POST | `/api/v1/reason` | `{query, sources, api_key?}` | CoT synthesis over pre-gathered sources (no `style` — see `synthesize` for variants) |
| GET | `/api/v1/presets` | — | List the five synthesis presets |
| GET | `/api/v1/focus-modes` | — | List the seven focus modes |

All POST endpoints accept the optional header `X-LLM-Api-Key: <key>` to override `RESEARCH_LLM_API_KEY` for that request.

## Presets

| Preset | Use for | Latency | LLM calls |
|---|---|---|---|
| `fast` | Quick answers, single-call synthesis | ~2–5 s | 1 |
| `tutorial` | Step-by-step explanations with outline | ~5–10 s | 1 |
| `comprehensive` | Multi-pass synthesis with quality gate | ~15–30 s | 2–3 |
| `contracrow` | Comparison queries — surfaces disagreements | ~10–20 s | 2 |
| `academic` | Citation-heavy, formal structure | ~15–25 s | 2 |

## Focus modes

| Mode | Tunes |
|---|---|
| `general` | Default, balanced |
| `academic` | Prioritizes peer-reviewed and `.edu` sources |
| `documentation` | Prioritizes official docs and reference sites |
| `comparison` | Tunes synthesis to surface differences |
| `debugging` | Prioritizes Stack Overflow, GitHub issues, error-message matches |
| `tutorial` | Prioritizes step-by-step content, blog posts |
| `news` | Date-bounded, recent-first |

## Architecture

| Layer | Path |
|---|---|
| MCP entry | `run_mcp.py` → `src/mcp_server.py` |
| REST entry | `src/main.py` → `src/api/routes.py` |
| LLM client | `src/llm_client.py` (OpenRouter on `main`, generic OpenAI-compat on `local-inference` branch) |
| Discovery | `src/discovery/` |
| Synthesis | `src/synthesis/` |
| Connectors | `src/connectors/` (SearXNG, Tavily, LinkUp) |
| Config | `src/config.py` (pydantic settings, `RESEARCH_*` env vars) |

## Error Handling

| Error | Action |
|---|---|
| `RESEARCH_LLM_API_KEY` missing | Fail fast at startup with clear message |
| 401 from LLM endpoint | Propagate as 401 to caller; do not retry with same key |
| 429 from LLM endpoint | Exponential backoff (3 attempts), then propagate |
| SearXNG host unreachable | Fail open — fall back to Tavily/LinkUp if configured |
| All search sources fail | Return empty `sources` with `error` field — let the agent decide |
| LLM timeout | Honor `RESEARCH_LLM_TIMEOUT`; return partial result if streaming, else 504 |
| Per-request key invalid | 401 to caller; the env-configured key remains usable for other tenants |

## Composable dependencies

This skill works alone for synthesis. For the full deep research workflow, pair with the other six MCPs in the stack:

- `mcp__exa-answer__exa_answer` — quick factual lookups (1–2 s, citation-backed)
- `mcp__Ref__ref_search_documentation` — official library/API docs
- `mcp__exa__get_code_context_exa` — code-context examples
- `mcp__exa__web_search_advanced_exa` — category-filtered search (`company`, `people`, `financial report`, `news`, `github`, `pdf`)
- `mcp__jina__search_web` / `mcp__jina__parallel_read_url` — free-tier web access
- `mcp__jina__extract_pdf` / `mcp__jina__guess_datetime_url` — PDF layout, URL freshness
- `mcp__brightdata_fallback__scrape_as_markdown` — blocked-URL fallback (CAPTCHA / paywall / Cloudflare)
- `mcp__gptr-mcp__quick_search` / `deep_research` — social-first research (Reddit, X, YouTube)

Routing logic across all seven is in [`skills/research-workflow/SKILL.md`](skills/research-workflow/SKILL.md). Sanitized JSON configs for all seven in [`docs/reference/mcp-configs.md`](docs/reference/mcp-configs.md).
