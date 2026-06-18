# Free-tier strategy for the deep research MCP stack

Each of the search MCPs in the stack — Jina, Exa, and Context7 — exposes a free or trial tier with specific usage limits. This guide documents what each tier covers, where the boundaries are, and how the bundled `research-workflow` routing avoids unnecessary spend by sending each query to the cheapest tool that can answer it.

It also covers OpenRouter's pay-as-you-go pricing for Qwen3-30B-A3B-Thinking and the local-inference fallback for users who want to run the synthesis model on their own hardware.

## Jina AI — free reader plus token-budgeted search

[Jina AI](https://jina.ai) ships an MCP server with `read_url`, `parallel_read_url`, `search_web`, `parallel_search_web`, `search_arxiv`, `extract_pdf`, `sort_by_relevance` (reranker), `deduplicate_strings`, and several other tools. The free tier includes a generous token allowance before key rotation; for a developer doing multiple research syntheses per day, that's hundreds of full sessions before the limit is reached.

Token cost per call (current, approximate):

| Tool | Cost |
|---|---|
| `read_url` | 0 (free reader) |
| `sort_by_relevance` | 0 |
| `deduplicate_strings` | 0 |
| `search_web` | ~63 tokens |
| `parallel_search_web` | ~107 tokens for 3 queries |
| `parallel_read_url` | ~17,000 tokens (content-proportional) |
| `search_arxiv` | ~343 tokens |
| `extract_pdf` | content-proportional |

**Avoid:** `expand_query` — 12,000 tokens per call. Rewrite query variants in the prompt instead. The bundled `research-workflow` skill already generates variants in-prompt at zero token cost.

## Exa — generous free trial credits, then paid

[Exa](https://exa.ai) ships an MCP server with `web_search_exa`, `web_search_advanced_exa` (category-filtered: `company`, `people`, `financial report`, `news`, `github`, `pdf`), `get_code_context_exa`, and `crawling_exa`. The free trial covers most exploratory usage; paid plans pick up only after the trial credits are exhausted.

Trial credits are tied to the signup account. A fresh Google account allocation buys another round of credits when the previous one runs out — useful for stretching free-tier usage across multiple research bursts. Reserve Exa for what only Exa does well (curated code index, category-filtered web, subpage crawling) so each credit goes further.

The companion `exa-answer` wrapper bundled in `companions/exa-answer/` exposes a separate `/answer` endpoint optimized for 1–2 second factual lookups. It uses the same Exa key as the main `exa` MCP.

The `research-workflow` skill routes only category-filtered or code-context queries to Exa — broader queries go to Jina (free reader) first. That keeps Exa credits available for the queries Jina can't answer well.

## Context7 — free tier for library docs

[Context7](https://context7.com) is the documentation-lookup MCP for canonical library and API docs. It's a two-step tool — `resolve-library-id` maps a library name to a Context7 ID, then `query-docs` returns up-to-date, version-aware docs. The free tier covers regular documentation lookups; paid plans raise the limits (current pricing is on the Context7 site). It returns canonical library docs that broader web search doesn't always surface cleanly.

The recommended setup includes Context7. If Context7 isn't installed (or its free tier is exhausted), the fallback chain routes documentation queries to `mcp__exa__get_code_context_exa` (Exa trial) or `mcp__jina__search_web` (Jina free tier); quality drops on edge cases but the synthesis still works.

**Swapping the docs MCP:** the skill is wired to Context7's `resolve-library-id` → `query-docs` tool names. To use a different docs MCP (e.g. [Ref](https://ref.tools)), edit the routing references in `skills/research-workflow/SKILL.md` and `CLAUDE.md`.

## Brightdata Web Unlocker — monthly free-tier limit, then paid

[Brightdata Web Unlocker](https://brightdata.com) is the last hop in the URL-reading fallback chain — it fires only when Jina's free reader can't fetch a URL (CAPTCHA, paywall, Cloudflare challenge, 403). In practice that's ~5–15% of URL fetches in a research workflow, so the monthly free-tier allowance typically covers most usage; paid charges only kick in if you push past the monthly limit on blocked-URL recovery.

If you skip Brightdata entirely, the routing skill degrades gracefully — URLs that would have routed to Brightdata just propagate their original error. SYNTHESIS workflows tolerate this because they pull from many sources; single-URL queries on blocked sites will simply fail. For a stack that treats blocked URLs as recoverable, register Brightdata and let the free tier handle most months.

## Qwen3-30B-A3B-Thinking via OpenRouter — pay-as-you-go

The synthesis stage runs against [Qwen3-30B-A3B-Thinking](https://huggingface.co/Qwen/Qwen3-30B-A3B-Thinking-2507) — a reasoning-tuned 30B-A3B MoE model (~3B active params). [OpenRouter](https://openrouter.ai/) hosts it on pay-as-you-go billing. Current rates are at `https://openrouter.ai/qwen/qwen3-30b-a3b-thinking-2507`.

A typical multi-source synthesis runs 5,000–10,000 input tokens (the gathered source content) plus 2,000–4,000 output tokens (the synthesized answer with citations). The `fast` preset is a single LLM call; `comprehensive` runs 2–3 calls and costs proportionally more.

### Per-request key override (multi-tenant)

Both the MCP and REST surfaces accept a per-request `api_key` parameter (or `X-LLM-Api-Key` HTTP header). One server instance can serve multiple tenants who bring their own keys for whichever LLM endpoint is configured. See [`docs/reference/configuration.md`](../reference/configuration.md) for the env-vs-header precedence rules.

## Local inference — zero ongoing inference cost

For users with GPU capacity, the `local-inference` branch swaps OpenRouter for any OpenAI-compatible inference server (vLLM, SGLang, or llama.cpp). Qwen3-30B-A3B-Thinking at 4-bit quantization fits in roughly 24 GB of VRAM, so a single RTX 3090 / 4090 / 5090 or an Apple Silicon machine with 32 GB+ unified memory can run it locally. Smaller variants (DeepSeek-R1 distilled, Qwen3, Llama 3.3) work too — anything OpenAI-compatible plugs in.

See [`docs/guides/setup-local-inference.md`](setup-local-inference.md) for the branch swap and inference-server setup.

## Routing decisions that minimize spend

The `research-workflow` skill encodes the routing logic that keeps each tool used for what it's actually good at:

- **Quick factual lookups (single answer, speed-critical)** → `mcp__exa-answer__exa_answer` (1–2 s, citation-backed)
- **Library / API documentation** → `mcp__context7__resolve-library-id` → `mcp__context7__query-docs` first; fall back to `mcp__exa__get_code_context_exa` if Context7 isn't installed
- **Code examples / patterns** → `mcp__exa__get_code_context_exa` (curated code index)
- **General web search** → `mcp__jina__search_web` (~63 tokens) before any paid alternative
- **Multi-query parallel web** → `mcp__jina__parallel_search_web` (~107 tokens for 3 queries — better unit economics than three sequential calls)
- **Bulk URL reading** → `mcp__jina__parallel_read_url` first; substitute `mcp__brightdata_fallback__scrape_as_markdown` only for blocked URLs
- **Reranking / dedup before synthesis** → `mcp__jina__sort_by_relevance` and `mcp__jina__deduplicate_strings` (both 0 tokens)
- **Synthesis with citations** → `mcp__gigaxity-deep-research__synthesize` (one OpenRouter call per session)

This routing is what the `research-workflow` skill enforces by default. Override it only when a specific query has unusual requirements.

## Configuration walkthrough

Sanitized JSON configs for all seven MCPs in the stack are in [`docs/reference/mcp-configs.md`](../reference/mcp-configs.md). Sign-up links:

- OpenRouter: [openrouter.ai/keys](https://openrouter.ai/keys)
- Jina: [jina.ai](https://jina.ai)
- Exa (one key for both `exa` and `exa-answer`): [exa.ai](https://exa.ai)
- Context7: [context7.com](https://context7.com)
- Brightdata Web Unlocker (optional, blocked-URL fallback): [brightdata.com](https://brightdata.com)
- Tavily (optional, free additional parallel connector for the built-in aggregator): [tavily.com](https://tavily.com)

The same configs work for any MCP-compatible client (Claude Code, Cursor, Hermes, Windsurf).
