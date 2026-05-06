# MCP server configurations — sanitized reference

Single canonical source for all seven MCP server JSON blocks needed to mirror the deep research stack in your global `~/.claude.json`. Replace each `YOUR_*_PLACEHOLDER` with your own credential.

For installation walkthrough, see [`../guides/triple-stack-setup.md`](../guides/triple-stack-setup.md) and [`../guides/setup-companions.md`](../guides/setup-companions.md). For when each MCP fires in the routing logic, see [`../concepts/fallback-chains.md`](../concepts/fallback-chains.md).

## Required base config

All seven entries go inside the `"mcpServers"` object of your global `~/.claude.json`. Restart Claude Code after editing.

```json
{
  "mcpServers": {
    "Ref": { /* ... */ },
    "exa": { /* ... */ },
    "exa-answer": { /* ... */ },
    "jina": { /* ... */ },
    "gigaxity-deep-research": { /* ... */ },
    "brightdata_fallback": { /* ... */ },
    "gptr-mcp": { /* ... */ }
  }
}
```

The aliases shown (`Ref`, `exa`, `exa-answer`, `jina`, `gigaxity-deep-research`, `brightdata_fallback`, `gptr-mcp`) are what the bundled `research-workflow` skill expects. Changing aliases means rewriting the `mcp__<alias>__<tool>` references in [`CLAUDE.md`](../../CLAUDE.md) and the skill itself.

---

## 1. Ref

HTTP transport. Fully hosted — no install. Sign up at https://ref.tools.

```json
"Ref": {
  "type": "http",
  "url": "https://api.ref.tools/mcp?apiKey=YOUR_REF_API_KEY_PLACEHOLDER"
}
```

**Tools exposed:** `ref_search_documentation`, `ref_read_url`.

**Use for:** library and API documentation lookup. First hop for any "how does library X work" question.

## 2. Exa

HTTP transport. Fully hosted — no install. Sign up at https://exa.ai. Use HTTP transport (not stdio) to expose all four tools — stdio caps at three default tools and ignores `ENABLED_TOOLS`.

```json
"exa": {
  "type": "http",
  "url": "https://mcp.exa.ai/mcp?exaApiKey=YOUR_EXA_API_KEY_PLACEHOLDER&tools=web_search_exa,web_search_advanced_exa,get_code_context_exa,crawling_exa"
}
```

**Tools exposed:** `web_search_exa`, `web_search_advanced_exa`, `get_code_context_exa`, `crawling_exa`.

**Use for:**
- `get_code_context_exa` — code examples and patterns from a curated code index
- `web_search_advanced_exa` — category-filtered search (`company`, `people`, `financial report`, `news`, `github`, `pdf`), date-bounded queries, domain-targeted, highlights
- `web_search_exa` — semantic web search
- `crawling_exa` — URL crawling with subpage support (Jina has no subpage mode)

## 3. Exa Answer

Bundled in this repo at [`companions/exa-answer/`](../../companions/exa-answer/). Install per [setup-companions.md](../guides/setup-companions.md). Same Exa key as the main `exa` MCP.

```json
"exa-answer": {
  "type": "stdio",
  "command": "/absolute/path/to/gigaxity-deep-research/companions/exa-answer/.venv/bin/python",
  "args": ["/absolute/path/to/gigaxity-deep-research/companions/exa-answer/mcp_server.py"],
  "env": {
    "EXA_API_KEY": "YOUR_EXA_API_KEY_PLACEHOLDER"
  }
}
```

**Tools exposed:** `exa_answer`, `exa_answer_detailed`.

**Use for:** speed-critical factual lookups. 1–2 s round-trip, 94% SimpleQA accuracy. Routes here for `QUICK FACTUAL` workflow class.

## 4. Jina

HTTP transport with bearer token. Fully hosted — no install. Sign up at https://jina.ai (free 10M tier).

```json
"jina": {
  "type": "http",
  "url": "https://mcp.jina.ai/v1",
  "headers": {
    "Authorization": "Bearer YOUR_JINA_API_KEY_PLACEHOLDER"
  }
}
```

**Tools exposed:**
- `read_url` (0 tokens — free reader tier)
- `parallel_read_url` (~17k tokens, content-proportional)
- `search_web` (~63 tokens)
- `parallel_search_web` (~107 tokens for 3 queries — better unit economics than 3 sequential calls)
- `search_arxiv` / `parallel_search_arxiv` (~343 tokens)
- `search_ssrn` / `parallel_search_ssrn` (econ, law, finance papers)
- `search_bibtex` (citation entries)
- `search_images`, `capture_screenshot_url`
- `extract_pdf` (PDF layout extraction — figures, tables, equations)
- `guess_datetime_url` (URL freshness inference for credibility checks)
- `sort_by_relevance` (free reranker, 0 tokens)
- `deduplicate_strings`, `deduplicate_images` (free dedup, 0 tokens)
- `classify_text` (free embeddings classifier)
- `primer` (session timezone/time context, 0 tokens)

**AVOID:** `expand_query` — 12,000 tokens per call. Rewrite query variants in the prompt instead.

## 5. gigaxity-deep-research (this repo)

```json
"gigaxity-deep-research": {
  "type": "stdio",
  "command": "/absolute/path/to/gigaxity-deep-research/.venv/bin/python",
  "args": ["/absolute/path/to/gigaxity-deep-research/run_mcp.py"],
  "env": {
    "RESEARCH_LLM_API_BASE": "http://localhost:8000/v1",
    "RESEARCH_LLM_API_KEY": "local-anything",
    "RESEARCH_LLM_MODEL": "Alibaba-NLP/Tongyi-DeepResearch-30B-A3B",
    "RESEARCH_SEARXNG_HOST": "http://localhost:8888"
  }
}
```

**Tools exposed:** `ask`, `discover`, `synthesize`, `reason`.

**Use for:** the full multi-source synthesis pipeline against Tongyi DeepResearch 30B. See [mcp-tools.md](mcp-tools.md) for input/output schemas.

**Critical rule:** `synthesize` does NOT re-search the web. Pass it pre-gathered sources (from `discover` or from your own URL reads). Pairing `synthesize` with a fresh search-and-read is the caller's job.

## 6. Brightdata fallback

Bundled in this repo at [`companions/brightdata-fallback/`](../../companions/brightdata-fallback/). Optional but recommended. Sign up at https://brightdata.com → Web Unlocker zone.

```json
"brightdata_fallback": {
  "type": "stdio",
  "command": "/absolute/path/to/gigaxity-deep-research/companions/brightdata-fallback/.venv/bin/python",
  "args": ["/absolute/path/to/gigaxity-deep-research/companions/brightdata-fallback/mcp_server.py"],
  "cwd": "/absolute/path/to/gigaxity-deep-research/companions/brightdata-fallback",
  "env": {
    "BRIGHTDATA_API_TOKEN": "YOUR_BRIGHTDATA_API_TOKEN_PLACEHOLDER",
    "BRIGHTDATA_ZONE": "YOUR_WEB_UNLOCKER_ZONE_NAME_PLACEHOLDER"
  }
}
```

**Tools exposed:** `scrape_as_markdown`.

**Use for:** last hop in the URL-reading fallback chain. Fires on CAPTCHA / paywall / Cloudflare / 403 — typically 5–15% of URL fetches.

## 7. gptr-mcp

Bundled in this repo at [`companions/gptr-mcp/`](../../companions/gptr-mcp/). Install glue clones upstream `assafelovic/gptr-mcp` into a sibling directory.

```json
"gptr-mcp": {
  "type": "stdio",
  "command": "/absolute/path/to/gptr-mcp-source/.venv/bin/python",
  "args": ["/absolute/path/to/gptr-mcp-source/server.py"],
  "cwd": "/absolute/path/to/gptr-mcp-source",
  "env": {
    "OPENAI_API_KEY": "YOUR_OPENAI_API_KEY_PLACEHOLDER",
    "TAVILY_API_KEY": "YOUR_TAVILY_API_KEY_PLACEHOLDER",
    "RETRIEVER": "social_openai,tavily",
    "SOCIAL_OPENAI_DOMAINS": "reddit.com,x.com,youtube.com",
    "SOCIAL_OPENAI_MODEL": "gpt-4o",
    "FAST_LLM": "openai:gpt-4o-mini",
    "SMART_LLM": "openai:gpt-4o",
    "STRATEGIC_LLM": "openai:gpt-4o-mini"
  }
}
```

**Tools exposed:** `quick_search`, `deep_research`, `get_research_context`, `get_research_sources`.

**Use for:** social-first research surfacing community knowledge from Reddit, X/Twitter, YouTube. Fires when the query asks for real-world opinions, troubleshooting beyond official docs, or community sentiment. LinkedIn is excluded from `SOCIAL_OPENAI_DOMAINS` — for LinkedIn-specific queries, use Jina with `site:linkedin.com`.

---

## Sign-up summary

| MCP | Sign up | Cost |
|---|---|---|
| Ref | https://ref.tools | Free credits, then ~$9/mo Basic |
| Exa (one key for both `exa` and `exa-answer`) | https://exa.ai | Paid; generous free trial credits. A fresh Google account allocation buys another round of free credits if you exhaust the first. |
| Jina | https://jina.ai | Paid; generous free 10M trial tier — hundreds of full pipeline sessions before key rotation |
| Self-hosted LLM (for `gigaxity-deep-research`) | vLLM, SGLang, llama.cpp, Ollama | Hardware cost only — zero ongoing usage charges. Tongyi 30B fits in ~24-60 GB VRAM at INT4-FP16. |
| Brightdata Web Unlocker | [brightdata.com](https://brightdata.com) | Monthly free-tier limit, then paid; only fires on ~5–15% of URL fetches |
| OpenAI (for `gptr-mcp`) | https://platform.openai.com/api-keys | Pay-per-call |
| Tavily (free tier — for `gptr-mcp` fallback) | https://tavily.com | Free tier |

**Recommendation: register all seven.** The routing skill is designed around the full stack — each MCP fills a niche the others don't cover well, and most operations land on Jina's free reader, Exa's free trial credits, Ref's free credits, or Brightdata's monthly free-tier allowance. On this branch the synthesis stage runs on your own hardware (no per-call LLM charges), so the steady-state cost is dominated by the Ref subscription once the starter credits run out. To run synthesis against OpenRouter instead, point `RESEARCH_LLM_API_BASE` at it (or check out the `main` branch which is wired for OpenRouter by default).

**Alternative for docs lookup:** [Context7](https://context7.com) is a drop-in alternative for the documentation-lookup role Ref plays. Same niche (library and API docs), different MCP surface. The bundled `research-workflow` skill is wired to Ref's tool names today — swapping in Context7 means editing the routing references in `skills/research-workflow/SKILL.md` and `CLAUDE.md`. Not yet implemented in this repo.

## What goes where (cheat sheet)

| You want | Look at |
|---|---|
| All seven configs in one place | This file |
| Step-by-step install with verification | [`../guides/triple-stack-setup.md`](../guides/triple-stack-setup.md) |
| Companion install instructions (SearXNG, Exa Answer, Brightdata, gptr-mcp) | [`../guides/setup-companions.md`](../guides/setup-companions.md) |
| Pasteable agent-routing rules for global CLAUDE.md | [`../../CLAUDE.md`](../../CLAUDE.md) instruction block |
| When each tool fires (decision tree) | [`../concepts/fallback-chains.md`](../concepts/fallback-chains.md) |
| `RESEARCH_*` env vars for the parent server | [`configuration.md`](configuration.md) |
