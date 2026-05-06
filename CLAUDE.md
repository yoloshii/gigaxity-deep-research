# Gigaxity Deep Research — Agent Reference

This is the agent reference for Gigaxity Deep Research, an open-source deep research MCP server for Claude Code, Hermes, Cursor, and other MCP-compatible agents — `local-inference` branch. Tongyi DeepResearch 30B (or any OpenAI-compatible chat-completions model) runs on a self-hosted server (vLLM, SGLang, llama.cpp, Ollama). The Triple Stack search MCPs (Ref, Exa, Jina) handle web/docs/code retrieval, and the bundled `research-workflow` skill routes queries to the right tool per query class.

This file is loaded by Claude Code (`CLAUDE.md`) and other MCP-compatible agents (`AGENTS.md` is byte-identical). It documents how to operate the six MCP tools this server exposes (two primitives plus four deep-research tools) and how to plug them into the broader deep research stack.

If you maintain a global `~/.claude/CLAUDE.md`, copy the **instruction block** at the bottom of this file into it. That single block makes Claude Code automatically route research queries through this MCP plus the six companion MCPs in the Triple Stack.

---

## Tool surface

The MCP server exposes **two primitives** plus **four deep-research tools** — six tools total. Pick a primitive when you want raw or combined behavior in one call; pick a deep-research tool when you want to drive discovery, synthesis, or reasoning as a discrete step.

**Primitives**

| Tool | Use for | Token cost (typical) |
|---|---|---|
| `mcp__gigaxity-deep-research__search` | Raw multi-source aggregation (SearXNG + Tavily + LinkUp + RRF). No LLM call. | 0 LLM tokens; search-API quotas only |
| `mcp__gigaxity-deep-research__research` | Combined search + synthesis with citations in a single call. The simple pipeline. | ~3000–8000 |

**Deep-research tools**

| Tool | Use for | Token cost (typical) |
|---|---|---|
| `mcp__gigaxity-deep-research__ask` | Quick conversational answer; speed > depth (direct LLM, no search hop) | ~500–1500 |
| `mcp__gigaxity-deep-research__discover` | Cold-start exploration; surfaces explicit/implicit/related/contrasting angles + gap detection | ~2000–5000 |
| `mcp__gigaxity-deep-research__synthesize` | Citation-aware fusion of pre-gathered content; CRAG quality gate, contradiction surfacing | ~5000–10000 |
| `mcp__gigaxity-deep-research__reason` | Deep synthesis with explicit chain-of-thought depth control over pre-gathered content | ~5000–15000 |

All six tools accept an optional `api_key` parameter for per-request LLM key override (multi-tenant deployments). REST callers can use the `X-LLM-Api-Key` header for the same purpose.

---

## When to call which tool

```
Query class?
├── "what is X right now / latest version" (single fact, speed-critical)
│     → ask
│
├── "tell me about X" (cold start, no prior context, want breadth)
│     → discover
│
├── "compare X vs Y" or "best practice for X" (cross-source synthesis, citations matter)
│     → synthesize
│
└── "why did X happen" or "explain the reasoning behind X" (CoT reasoning matters)
      → reason
```

For the full classification tree across the **entire** Triple Stack (when to use Ref, Exa, Jina, Brightdata fallback alongside this MCP), see the bundled [`skills/research-workflow/SKILL.md`](skills/research-workflow/SKILL.md).

---

## Environment variables

All variables are prefixed `RESEARCH_`. Set in `.env` (gitignored) or pass via the MCP `env` config block.

| Variable | Default | Purpose |
|---|---|---|
| `RESEARCH_LLM_API_BASE` | `http://localhost:8000/v1` | LLM endpoint. For Ollama set `http://localhost:11434/v1`; for hosted services point at their `/v1` URL. |
| `RESEARCH_LLM_API_KEY` | *(empty — required, set any non-empty placeholder for local servers without auth)* | LLM API key |
| `RESEARCH_LLM_MODEL` | `Alibaba-NLP/Tongyi-DeepResearch-30B-A3B-Thinking` | Any OpenAI-compatible chat-completions model |
| `RESEARCH_LLM_TEMPERATURE` | `0.85` | |
| `RESEARCH_LLM_TOP_P` | `0.95` | |
| `RESEARCH_LLM_MAX_TOKENS` | `16384` | |
| `RESEARCH_LLM_TIMEOUT` | `120` | Seconds |
| `RESEARCH_SEARXNG_HOST` | `http://localhost:8888` | Primary search source — required |
| `RESEARCH_SEARXNG_ENGINES` | `brave,bing,duckduckgo,startpage,mojeek,wikipedia` | Matches the bundled SearXNG `settings.yml.example` enabled list |
| `RESEARCH_TAVILY_API_KEY` | *(empty)* | Optional fallback search |
| `RESEARCH_LINKUP_API_KEY` | *(empty)* | Optional fallback search |
| `RESEARCH_DEFAULT_TOP_K` | `10` | Results per source |
| `RESEARCH_RRF_K` | `60` | RRF fusion constant |
| `RESEARCH_HOST` | `127.0.0.1` | REST mode only. Default loopback; bind `0.0.0.0` only behind an authenticated reverse proxy. |
| `RESEARCH_PORT` | `8000` | REST mode only |

**Critical:** `RESEARCH_LLM_API_KEY` and `RESEARCH_SEARXNG_HOST` are the only two values you must set. Everything else has a working default.

---

## Anti-patterns

```
❌ Use ask() for cross-source comparisons
✅ Use synthesize() — it runs the quality gate + contradiction detector

❌ Use discover() when you already have the URLs you want analyzed
✅ Use synthesize() with the URLs already in the prompt

❌ Use reason() for "what is X" lookups
✅ Use ask() — reason() burns tokens on a CoT you don't need

❌ Pass the same LLM API key in every request body
✅ Set RESEARCH_LLM_API_KEY in env, override per-request only when multi-tenant

❌ Skip SearXNG and rely on Tavily/LinkUp alone
✅ SearXNG is primary; Tavily and LinkUp are fallback. Set up SearXNG first.

❌ Run REST mode bound to 0.0.0.0 on a shared/exposed machine
✅ Bind to 127.0.0.1 unless behind an authenticated reverse proxy
```

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `RESEARCH_LLM_API_KEY` missing on startup | env var not set | Set in `.env` or MCP `env` block; for local servers without auth, set any non-empty placeholder |
| `ConnectionError` / `APIConnectionError` on first call | Local LLM server not running | Start vLLM/SGLang/Ollama on the configured `RESEARCH_LLM_API_BASE`; `curl <base>/models` should return 200 |
| 401 from LLM endpoint | Invalid bearer token | Match `RESEARCH_LLM_API_KEY` to what your server expects (real key for hosted; placeholder for unauthenticated local) |
| Empty results from `discover` / `synthesize` | SearXNG host unreachable | `curl $RESEARCH_SEARXNG_HOST/healthz` — should return 200 |
| `model not found` from local server | Model not loaded | vLLM/SGLang load 30B in 30-120 s; check the model-server logs and use the exact slug they registered |
| MCP server boots but Claude Code shows no tools | stdio path / venv mismatch | Confirm `command` in `~/.claude.json` points at the venv's Python (not system Python) |
| Out-of-memory at model startup | Model larger than VRAM | Switch to a quantized variant (AWQ, INT4) or smaller model |
| Latency > 30 s on `synthesize` | Quality gate enabled with many sources | Lower `RESEARCH_DEFAULT_TOP_K` to 5; switch preset to `fast` |
| Per-request `X-LLM-Api-Key` header ignored | Header name typo | Exact header is `X-LLM-Api-Key` (case-insensitive in HTTP, but exact spelling in alias) |

---

## Architecture quick-reference

| Layer | Path | Notes |
|---|---|---|
| MCP entry | `run_mcp.py` → `src/mcp_server.py` | FastMCP, stdio transport |
| REST entry | `src/main.py` → `src/api/routes.py` | FastAPI, uvicorn |
| LLM client | `src/llm_client.py` | Generic OpenAI-compatible client on this branch; OpenRouter-flavored on `main` |
| Discovery | `src/discovery/` | Routing, expansion, decomposition, focus modes |
| Synthesis | `src/synthesis/` | Quality gate, contradictions, presets, outline, RCS |
| Connectors | `src/connectors/` | SearXNG, Tavily, LinkUp |
| Config | `src/config.py` | All `RESEARCH_*` env vars; pydantic settings |

---

## Companion MCPs — full deep research setup

The full deep-research workflow uses seven MCPs. The middle three (`Ref` + `exa` + `jina`) form the **Triple Stack** — the search/docs/code trio. This repo ships the most complex one (`gigaxity-deep-research`); the other six each take 30 seconds to register in `~/.claude.json`.

### Companion MCP configs

```json
"Ref": {
  "type": "http",
  "url": "https://api.ref.tools/mcp?apiKey=YOUR_REF_API_KEY_PLACEHOLDER"
},
"exa": {
  "type": "http",
  "url": "https://mcp.exa.ai/mcp?exaApiKey=YOUR_EXA_API_KEY_PLACEHOLDER&tools=web_search_exa,web_search_advanced_exa,get_code_context_exa,crawling_exa"
},
"exa-answer": {
  "type": "stdio",
  "command": "/absolute/path/to/gigaxity-deep-research/companions/exa-answer/.venv/bin/python",
  "args": ["/absolute/path/to/gigaxity-deep-research/companions/exa-answer/mcp_server.py"],
  "env": { "EXA_API_KEY": "YOUR_EXA_API_KEY" }
},
"jina": {
  "type": "http",
  "url": "https://mcp.jina.ai/v1",
  "headers": { "Authorization": "Bearer YOUR_JINA_API_KEY" }
},
"brightdata_fallback": {
  "type": "stdio",
  "command": "/absolute/path/to/gigaxity-deep-research/companions/brightdata-fallback/.venv/bin/python",
  "args": ["/absolute/path/to/gigaxity-deep-research/companions/brightdata-fallback/mcp_server.py"],
  "cwd": "/absolute/path/to/gigaxity-deep-research/companions/brightdata-fallback",
  "env": {
    "BRIGHTDATA_API_TOKEN": "YOUR_BRIGHTDATA_API_TOKEN",
    "BRIGHTDATA_ZONE": "YOUR_WEB_UNLOCKER_ZONE_NAME"
  }
},
"gptr-mcp": {
  "type": "stdio",
  "command": "/absolute/path/to/gptr-mcp-source/.venv/bin/python",
  "args": ["/absolute/path/to/gptr-mcp-source/server.py"],
  "cwd": "/absolute/path/to/gptr-mcp-source",
  "env": {
    "OPENAI_API_KEY": "YOUR_OPENAI_API_KEY",
    "TAVILY_API_KEY": "YOUR_TAVILY_API_KEY",
    "RETRIEVER": "social_openai,tavily",
    "SOCIAL_OPENAI_DOMAINS": "reddit.com,x.com,youtube.com",
    "SOCIAL_OPENAI_MODEL": "gpt-4o",
    "FAST_LLM": "openai:gpt-4o-mini",
    "SMART_LLM": "openai:gpt-4o",
    "STRATEGIC_LLM": "openai:gpt-4o-mini"
  }
}
```

Sign-up links:
- Ref: https://ref.tools
- Exa (one key for both `exa` and `exa-answer`): https://exa.ai
- Jina (free 10M tier): https://jina.ai
- Brightdata Web Unlocker (paid; optional): https://brightdata.com
- OpenAI (for gptr-mcp): https://platform.openai.com/api-keys
- Tavily (for gptr-mcp fallback retriever): https://tavily.com

`gigaxity-deep-research` on this branch uses a self-hosted OpenAI-compatible endpoint (vLLM, SGLang, llama.cpp, Ollama). No external sign-up needed for the LLM unless you point `RESEARCH_LLM_API_BASE` at a hosted service (e.g. OpenRouter).

`exa-answer` and `brightdata_fallback` are minimal wrappers **bundled in this repo** under [`companions/`](companions/). `gptr-mcp` is the [upstream MCP shim](https://github.com/assafelovic/gptr-mcp) around [GPT Researcher](https://github.com/assafelovic/gpt-researcher) — `companions/gptr-mcp/install.sh` clones it into a sibling directory rather than vendoring source. Install per [`docs/guides/setup-companions.md`](docs/guides/setup-companions.md).

### Bundled skill

[`skills/research-workflow/SKILL.md`](skills/research-workflow/SKILL.md) is a vendored copy of the universal-format skill that drives the classification logic across all seven MCPs. Symlink or copy it into your skills directory:

```bash
# Claude Code (per-user skills)
mkdir -p ~/.claude/skills
ln -s /path/to/gigaxity-deep-research/skills/research-workflow ~/.claude/skills/research-workflow
```

---

## Instruction block — paste into your global CLAUDE.md

Drop the block below into your global `~/.claude/CLAUDE.md` (or `~/.claude/AGENTS.md`). It tells Claude Code when to trigger the `research-workflow` skill, how to route between MCPs, and what the standard subagent dispatch looks like.

````markdown
## Research Skill Trigger (DEFAULT BEHAVIOR)

**MANDATORY**: Execute `research-workflow` skill for ANY external knowledge query.

**DEFAULT = TRIGGER SKILL:**
- Questions about facts, concepts, technologies, events
- Documentation, APIs, libraries, frameworks
- Comparisons, best practices, recommendations
- Current/recent information (dates, versions, news)
- "What is...", "How to...", "Explain...", "Compare...", "Find..."
- Ambiguous queries that MIGHT need external info

**Execution:**
Use Skill tool with skill="research-workflow"

**ONLY skip when ALL conditions met:**
1. User explicitly names a specific tool: "use Jina to..."
2. OR user provides a specific URL to read directly
3. OR query is about LOCAL codebase files (use native Read/Grep/Glob)
4. OR query is purely conversational with NO external knowledge need

**When in doubt → TRIGGER THE SKILL.**

**DEFAULT: Use Task tool with subagent for research** (unless user says "don't use subagent").

---

## Research Subagent Spawning (RECOMMENDED)

If your client supports parallel subagents (e.g. Claude Code's `Task` tool), dispatching research as a subagent keeps the main thread's context clean. Optional — skip this section if your client has no subagent primitive.

**Parallelism limit:** Max 2 research subagents in parallel. For 3+ research topics, queue: run 2, wait for completion, run next batch.

❌ Spawn 3+ research subagents in a single Agent tool message
✅ Spawn at most 2, wait for completion, spawn next batch

**Subagent prompt should include:**
1. All relevant conversation context (prior decisions, constraints, what's established/ruled out)
2. Research-workflow instructions (the body below, or reference to skills/research-workflow/SKILL.md)

```
Task tool:
  subagent_type: "general-purpose"
  prompt: |
    Research query: [QUERY]

    ## Context
    [All relevant conversation context that affects research scope, constraints, or expected outcomes]

    ## Research Workflow Instructions

    Classify query and execute appropriate workflow:

    **QUICK FACTUAL** (mid-task lookup, speed-critical, single answer):
    → mcp__exa-answer__exa_answer(query) — 1-2s, 94% accuracy
    → Fallback: mcp__exa__web_search_advanced_exa with highlights

    **DIRECT** (specific library/API, single source sufficient):
    → mcp__Ref__ref_search_documentation OR mcp__exa__get_code_context_exa

    **EXPLORATORY** (general concept, cold-start, learning):
    → mcp__gigaxity-deep-research__discover(query, focus_mode, identify_gaps=True)
    → Score URLs from result, select top 3-5
    → mcp__jina__parallel_read_url(urls, timeout=60000)
    → mcp__gigaxity-deep-research__synthesize(query, sources, preset)

    **SYNTHESIS** (comparison, best practices, consensus, cross-validation):
    → Execute in parallel:
      - mcp__Ref__ref_search_documentation(query)
      - mcp__exa__get_code_context_exa(query)                             # code-specific
      - mcp__jina__parallel_search_web(searches=[3-5 query variants])     # free-tier, ~107 tokens for 3 queries
    → Optional free middleware before synthesis:
      - mcp__jina__sort_by_relevance(query, documents) — 0 tokens
      - mcp__jina__deduplicate_strings(strings) — 0 tokens
    → mcp__gigaxity-deep-research__synthesize(query, sources, preset)

    **SOCIAL-FIRST** (community sentiment, real user experiences, Reddit/X/YouTube):
    → mcp__gptr-mcp__quick_search(query) — single-call social-first lookup
    → mcp__gptr-mcp__deep_research(query) — multi-hop social-first research with cross-platform sentiment
    → For LinkedIn-specific queries: mcp__jina__search_web "site:linkedin.com"
    → Combine with SYNTHESIS when comparing community sentiment to documentation/spec

    Execute research now and return full synthesis.
```

**Post-subagent:** Output the COMPLETE result to user. NEVER truncate or summarize.

---

## Tool Selection Matrix (Triple Stack)

| Need | Primary | Fallback |
|---|---|---|
| Quick factual answer (1-2 s) | mcp__exa-answer__exa_answer | mcp__exa__web_search_advanced_exa |
| Library / API documentation | mcp__Ref__ref_search_documentation | mcp__exa__get_code_context_exa |
| Code examples / patterns | mcp__exa__get_code_context_exa | mcp__jina__search_web "site:github.com" |
| General web (single query) | mcp__jina__search_web (~63 tokens) | mcp__exa__web_search_exa |
| Parallel multi-query web (3-5 variants) | mcp__jina__parallel_search_web (~107 / 3) | sequential mcp__exa__web_search_exa |
| Advanced web (date-bounded, highlights, domain filters) | mcp__exa__web_search_advanced_exa | mcp__jina__search_web with manual filtering |
| Company info / company research | mcp__exa__web_search_advanced_exa with `category="company"` | mcp__jina__search_web |
| People / OSINT / attribute-based | mcp__exa__web_search_advanced_exa with `category="people"` | mcp__jina__search_web |
| Financial reports (SEC, earnings) | mcp__exa__web_search_advanced_exa with `category="financial report"` | — |
| News (date-bounded) | mcp__exa__web_search_advanced_exa with `category="news"` | mcp__jina__search_web with `site:` |
| GitHub repo discovery | mcp__exa__web_search_advanced_exa with `category="github"` | mcp__jina__search_web "site:github.com" |
| PDFs / whitepapers (search) | mcp__exa__web_search_advanced_exa with `category="pdf"` | — |
| URL subpage crawl (multiple pages from one site) | mcp__exa__crawling_exa with `subpages` / `subpageTarget` | — (Jina has no subpage mode) |
| URL → markdown (single) | mcp__jina__read_url (0 tokens) | mcp__brightdata_fallback__scrape_as_markdown |
| URL → markdown (bulk 3-5) | mcp__jina__parallel_read_url | per-URL fallback to Brightdata for blocked ones |
| URL freshness / credibility check | mcp__jina__guess_datetime_url (free) | — |
| Academic (arXiv) — single query | mcp__jina__search_arxiv | — |
| Academic (arXiv) — multi-query parallel | mcp__jina__parallel_search_arxiv | — |
| Academic (SSRN — econ/law/finance) | mcp__jina__search_ssrn / parallel_search_ssrn | — |
| BibTeX citations | mcp__jina__search_bibtex | — |
| PDF layout extraction (figures, tables, equations) | mcp__jina__extract_pdf | — |
| Images | mcp__jina__search_images | — |
| Screenshots | mcp__jina__capture_screenshot_url | — |
| Free reranker (before synthesis) | mcp__jina__sort_by_relevance (0 tokens) | — |
| Free dedup (before synthesis) | mcp__jina__deduplicate_strings (0 tokens) | — |
| Synthesis with citations | mcp__gigaxity-deep-research__synthesize | — |
| CoT reasoning over evidence | mcp__gigaxity-deep-research__reason | — |
| Exploratory expansion + gap detection | mcp__gigaxity-deep-research__discover | — |
| Quick conversational LLM answer | mcp__gigaxity-deep-research__ask | mcp__exa-answer__exa_answer |
| Social-first research (Reddit / X / YouTube — "what do people think") | mcp__gptr-mcp__quick_search | mcp__jina__search_web with site: filter |
| Deep social research (multi-hop community sentiment) | mcp__gptr-mcp__deep_research | — |

**AVOID:** `mcp__jina__expand_query` — 12,000 tokens/call. Rewrite query variants in the prompt instead.

**Exa MCP 3.2.0 caveat:** the `type="deep"` parameter previously documented for `web_search_exa` does NOT exist in the current MCP. The deprecated `deep_researcher_start` / `deep_researcher_check` have no MCP replacement either. For deep multi-hop research, use the chain `mcp__gigaxity-deep-research__discover` → `mcp__jina__parallel_read_url` → `mcp__gigaxity-deep-research__synthesize`.

---

## Specific URL → Tool Mapping

When the user supplies a specific URL, route by URL type:

| URL pattern | Primary | On error/block |
|---|---|---|
| GitHub issues / PRs / discussions | mcp__jina__read_url | mcp__brightdata_fallback__scrape_as_markdown |
| Documentation / API references | mcp__Ref__ref_read_url | mcp__jina__read_url → Brightdata |
| General articles / blogs | mcp__jina__read_url | mcp__brightdata_fallback__scrape_as_markdown |
| Paywalled / Cloudflare / CAPTCHA | mcp__brightdata_fallback__scrape_as_markdown (direct) | mcp__exa__crawling_exa |
| PDF files | dedicated PDF reader (e.g. pdf_reader MCP if installed) | mcp__jina__extract_pdf |
| URL subpage crawl (multiple pages from one site) | mcp__exa__crawling_exa with subpages | — |
| Reddit / X / YouTube discussion or community sentiment | mcp__gptr-mcp__quick_search | mcp__jina__search_web with `site:reddit.com` etc. |
| LinkedIn (gptr-mcp's social retriever excludes LinkedIn) | mcp__jina__search_web with `site:linkedin.com` | mcp__exa__web_search_advanced_exa |

---

## Brightdata Fallback Chain (URL Reading)

Triggered automatically when an upstream URL fetcher fails. The chain is **per-URL**, not per-query — retry on the SAME URL before falling back to a different source.

```
Step 1:  mcp__jina__read_url(url)                            (0 tokens, free reader)
            │
            │ on empty / 404 / CAPTCHA / paywall / 403 / Cloudflare
            ▼
Step 2:  mcp__brightdata_fallback__scrape_as_markdown(url)   (paid, ~$0.01/req)
            │
            │ on persistent failure
            ▼
Step 3:  mcp__exa__crawling_exa(url)                         (paid, last resort)
```

**When Brightdata fires (typical 5-15% of URL fetches):**
- News paywalls (NYT, FT, WSJ) — login walls or paywall HTML
- Cloudflare-protected sites — "Verify you are human" challenges
- LinkedIn / X / Reddit — auth gates
- Heavy-JS sites that don't render server-side — empty bodies / spinners

**If Brightdata isn't installed:** the chain skips Step 2 and goes straight to Step 3 (or fails out). Most SYNTHESIS workflows tolerate 10-15% URL loss because they pull from many sources — but for single-URL queries, install Brightdata or expect occasional gaps.

---

## CRITICAL Rules

```
❌ Use native WebSearch tool                                ✅ Use the Triple Stack (Ref + Exa + Jina)
❌ Stop after gathering sources                             ✅ ALWAYS synthesize via gigaxity-deep-research
❌ Truncate or summarize the synthesis output               ✅ Return the COMPLETE result verbatim
❌ Spawn subagent without conversation context              ✅ Include prior decisions, constraints, ruled-out approaches
❌ Use ask() for cross-source comparisons                   ✅ Use synthesize() — runs quality gate + contradictions
❌ Use discover() when URLs are already chosen              ✅ Use synthesize() with the URLs already in the prompt
❌ Use reason() for "what is X" lookups                     ✅ Use ask() — reason() burns tokens on a CoT you don't need
❌ Pass the same LLM API key in every request body       ✅ Set RESEARCH_LLM_API_KEY in env, override per-request only when multi-tenant
❌ Call synthesize() expecting it to fetch sources          ✅ synthesize NEVER re-searches — pass it pre-gathered sources from discover() or your own URL reads
❌ Use mcp__jina__expand_query                              ✅ Rewrite query variants in the prompt — expand_query burns 12k tokens/call
❌ Pass type="deep" to web_search_exa                       ✅ MCP 3.2.0 has no deep type — use discover→jina→synthesize chain instead
```
````

End of pasteable block.

---

## Notes on the pasteable block

- Adjust subagent parallelism (`Max 2`) to match your model and quota tolerance.
- The block assumes the five companion MCPs are registered under the exact aliases shown (`Ref`, `exa`, `exa-answer`, `jina`, `brightdata_fallback`). The `mcp__<alias>__<tool>` names in the block are derived from those aliases — change the block if you register them under different names.
- The bundled skill in `skills/research-workflow/` contains the deep version of the routing matrix (token costs, presets, focus modes, per-tool capabilities). The block above is the abridged trigger logic; the skill is the full reference.
