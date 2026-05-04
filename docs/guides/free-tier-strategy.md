# Free-tier strategy for the deep research MCP stack

Each of the search MCPs in the stack — Jina, Exa, and Ref — exposes a free or trial tier with specific usage limits. This guide documents what each tier covers, where the boundaries are, and how the bundled `research-workflow` routing avoids unnecessary spend by sending each query to the cheapest tool that can answer it.

It also covers OpenRouter's pay-as-you-go pricing for Tongyi DeepResearch 30B and the local-inference fallback for users who want to run the synthesis model on their own hardware.

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

## Exa — trial credits, then paid

[Exa](https://exa.ai) ships an MCP server with `web_search_exa`, `web_search_advanced_exa` (category-filtered: `company`, `people`, `financial report`, `news`, `github`, `pdf`), `get_code_context_exa`, and `crawling_exa`. Trial credits cover most exploratory usage; after the trial, individual searches are paid per call.

The companion `exa-answer` wrapper bundled in `companions/exa-answer/` exposes a separate `/answer` endpoint optimized for 1–2 second factual lookups. It uses the same Exa key as the main `exa` MCP.

The `research-workflow` skill routes only category-filtered or code-context queries to Exa — broader queries go to Jina (free reader) first. That keeps Exa credits available for the queries Jina can't answer well.

## Ref — paid, low per-call cost

[Ref](https://ref.tools) is the cheapest source for canonical library and API documentation. Plans start around $9–$15/month depending on tier; current pricing is on the Ref site. Per-call cost works out to roughly $0.0045 on the lower-tier plans.

Ref isn't free, but it's typically cheaper per documentation lookup than burning Exa credits or Jina tokens on lower-quality docs sources. If you want a strict $0/month setup, skip Ref entirely — the fallback chain routes documentation queries to `mcp__exa__get_code_context_exa` (Exa trial) or `mcp__jina__search_web` (Jina free tier). Quality drops on edge cases but the synthesis still works.

## Tongyi DeepResearch 30B via OpenRouter — pay-as-you-go

The synthesis stage runs against [Tongyi DeepResearch 30B](https://huggingface.co/Alibaba-NLP/Tongyi-DeepResearch-30B-A3B-Thinking) — a reasoning-tuned 30B-parameter model purpose-built for agentic multi-hop research. [OpenRouter](https://openrouter.ai/) hosts it on pay-as-you-go billing. Current rates are at `https://openrouter.ai/alibaba/tongyi-deepresearch-30b-a3b`.

A typical multi-source synthesis runs 5,000–10,000 input tokens (the gathered source content) plus 2,000–4,000 output tokens (the synthesized answer with citations). The `fast` preset is a single LLM call; `comprehensive` runs 2–3 calls and costs proportionally more.

### Per-request key override (multi-tenant)

Both the MCP and REST surfaces accept a per-request `openrouter_api_key` parameter (or `X-OpenRouter-Api-Key` HTTP header). One server instance can serve multiple tenants who bring their own OpenRouter keys. See [`docs/reference/configuration.md`](../reference/configuration.md) for the env-vs-header precedence rules.

## Local inference — zero ongoing inference cost

For users with GPU capacity, the `local-inference` branch swaps OpenRouter for any OpenAI-compatible inference server (Ollama, llama.cpp, vLLM, SGLang). Tongyi DeepResearch 30B at 4-bit quantization fits in roughly 24 GB of VRAM, so a single RTX 3090 / 4090 / 5090 or an Apple Silicon machine with 32 GB+ unified memory can run it locally. Smaller variants (DeepSeek-R1 distilled, Qwen3, Llama 3.3) work too — anything OpenAI-compatible plugs in.

See [`docs/guides/setup-local-inference.md`](setup-local-inference.md) for the branch swap and inference-server setup.

## Routing decisions that minimize spend

The `research-workflow` skill encodes the routing logic that keeps each tool used for what it's actually good at:

- **Quick factual lookups (single answer, speed-critical)** → `mcp__exa-answer__exa_answer` (1–2 s, citation-backed)
- **Library / API documentation** → `mcp__Ref__ref_search_documentation` first; fall back to `mcp__exa__get_code_context_exa` if Ref isn't installed
- **Code examples / patterns** → `mcp__exa__get_code_context_exa` (curated code index)
- **General web search** → `mcp__jina__search_web` (~63 tokens) before any paid alternative
- **Multi-query parallel web** → `mcp__jina__parallel_search_web` (~107 tokens for 3 queries — better unit economics than three sequential calls)
- **Bulk URL reading** → `mcp__jina__parallel_read_url` first; substitute `mcp__brightdata_fallback__scrape_as_markdown` only for blocked URLs
- **Reranking / dedup before synthesis** → `mcp__jina__sort_by_relevance` and `mcp__jina__deduplicate_strings` (both 0 tokens)
- **Synthesis with citations** → `mcp__gigaxity-deep-research__synthesize` (one OpenRouter call per session)

This routing is what the `research-workflow` skill enforces by default. Override it only when a specific query has unusual requirements.

## Configuration walkthrough

Sanitized JSON configs for all seven MCPs in the stack are in [`docs/reference/mcp-configs.md`](../reference/mcp-configs.md). Sign-up links:

- OpenRouter: https://openrouter.ai/keys
- Jina: https://jina.ai
- Exa (one key for both `exa` and `exa-answer`): https://exa.ai
- Ref (optional): https://ref.tools
- Brightdata Web Unlocker (optional, blocked-URL fallback): https://brightdata.com
- Tavily (optional, free fallback for built-in connectors): https://tavily.com

The same configs work for any MCP-compatible client (Claude Code, Cursor, Hermes, Windsurf).
