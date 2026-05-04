# Fallback chains in the deep research workflow

The seven-MCP deep research stack uses multiple sources because no single one handles every URL or query type well. The `research-workflow` skill encodes a set of fallback chains — ordered tool sequences where each step is tried only if the previous step failed or returned unusable content.

This page documents the five chains the skill uses, when each fires, and where Brightdata and gptr-mcp fit.

## Chain 1 — URL reading

Triggers when an agent has a specific URL and needs its content as text.

```
Step 1:  mcp__jina__read_url(url)                            (0 tokens, free reader)
            │
            │ on empty / 404 / CAPTCHA / paywall / 403 / Cloudflare
            ▼
Step 2:  mcp__brightdata_fallback__scrape_as_markdown(url)   (paid, ~$0.01/req)
            │
            │ on persistent failure
            ▼
Step 3:  mcp__exa__crawling_exa(url)                         (paid, fallback only)
```

**Key rule:** when a URL fetcher returns an error, retry on the **same URL** before falling back to a different source URL. The chain is per-URL, not per-query.

**Special cases:**
- **GitHub issues / PRs / discussions** — start at Jina (it handles GitHub's anti-scraping well)
- **PDF files** — skip the chain, use a PDF-specific reader (the skill includes a `pdf_reader` reference)
- **Documentation hosts** — try `mcp__Ref__ref_read_url` first if the URL matches a known docs domain, fall back to Jina

## Chain 2 — Documentation lookup

Triggers when the agent needs official library/API documentation.

```
Step 1:  mcp__Ref__ref_search_documentation(query)
            │
            │ on no useful match
            ▼
Step 2:  mcp__exa__get_code_context_exa(query)
            │
            │ on no useful match
            ▼
Step 3:  mcp__jina__search_web(query, num=5)
```

Use Ref first — it's the cheapest, fastest source for canonical library docs. Exa's `get_code_context_exa` falls in next because it indexes code-adjacent docs (GitHub READMEs, Stack Overflow). Jina's web search is the broad-net last resort.

## Chain 3 — Web search

Triggers when the agent needs general web content (not docs, not a specific URL).

```
Step 1:  mcp__jina__search_web(query)                        (63 tokens, primary)
            │
            │ on weak results
            ▼
Step 2:  mcp__exa__web_search_exa(query)                     (paid, semantic search)
            │
            │ on advanced needs (date/category/domain filtering)
            ▼
Step 3:  mcp__exa__web_search_advanced_exa(query, ...)       (paid, with filters)
```

For **multi-query coverage** in one call, use `mcp__jina__parallel_search_web([q1, q2, q3])` (107 tokens for 3 queries — better unit economics than three separate calls).

## Chain 4 — Synthesis-grade research

Triggers when the agent needs cross-source synthesis with citations and contradiction detection.

```
Step 1:  Gather sources in parallel:
           - mcp__Ref__ref_search_documentation(query)
           - mcp__exa__get_code_context_exa(query)
           - mcp__jina__parallel_search_web([variants])
            │
            ▼
Step 2:  (optional, free) Rerank + dedup:
           - mcp__jina__sort_by_relevance(query, results)    (0 tokens)
           - mcp__jina__deduplicate_strings(results)         (0 tokens)
            │
            ▼
Step 3:  Read top URLs in bulk:
           - mcp__jina__parallel_read_url(top_urls)
            │
            │ on blocked URLs
            ▼
Step 3a: Substitute Brightdata for failed URLs:
           - mcp__brightdata_fallback__scrape_as_markdown(url)
            │
            ▼
Step 4:  Synthesize:
           - mcp__gigaxity-deep-research__synthesize(query, sources, preset)
```

This is the heaviest chain — typical token cost is 5–10 k. Reserve for queries where citations and cross-source validation matter.

## When does Brightdata actually fire?

In practice, Brightdata fires on roughly 5–15% of URL fetches in research workflows — the long tail where the cheaper tools fail. Common triggers:

| Pattern | Why Jina fails | Why Brightdata works |
|---|---|---|
| News paywalls (NYT, FT, WSJ) | Returns paywall HTML or empty | Web Unlocker rotates IPs and handles cookie walls |
| Cloudflare-protected sites | 403 / "Verify you are human" page | Web Unlocker solves the challenge |
| LinkedIn / X / Reddit | Login walls | Web Unlocker presents authenticated-looking session |
| Heavy JS sites that don't render server-side | Empty body / spinner | Web Unlocker runs a real browser |

If you don't have a Brightdata account, the skill degrades to "URL unreachable, skip and continue with the URLs that did work." That's usually fine for SYNTHESIS workflows because they pull from many sources — losing 10–15% to blocked URLs is rarely fatal.

## Chain 5 — Social-first research

Triggers when the agent needs lived-experience knowledge or community sentiment — questions whose best answers live on Reddit, X/Twitter, or YouTube rather than in docs or curated web pages.

```
Step 1:  mcp__gptr-mcp__quick_search(query)              (single-call social search)
            │
            │ on weak results / single-platform coverage
            ▼
Step 2:  mcp__gptr-mcp__deep_research(query)             (multi-hop, cross-platform)
            │
            │ on LinkedIn-specific signal (gptr doesn't index LinkedIn)
            ▼
Step 3:  mcp__jina__search_web("query site:linkedin.com")
```

**Triggers for this chain:**
- "What do people actually think of X?"
- "Real-world experiences with X"
- "Why does X happen — what are people saying?"
- "Honest opinions on X"
- Troubleshooting where official docs are insufficient
- Community-validated comparisons ("Reddit prefers X or Y?")

**When NOT to use:** factual lookups, documentation queries, code patterns, comparison-by-spec — those go to QUICK FACTUAL / DIRECT / SYNTHESIS chains.

## When does the skill skip the chains entirely?

Three escape hatches:

1. **User explicitly names a tool**: "use Jina to..." → skill defers to the explicit choice
2. **Local codebase query**: "find functions that call X in src/" → use native Grep/Read, not the chains
3. **QUICK FACTUAL workflow**: "what's the latest version of X" → goes straight to `mcp__exa-answer__exa_answer`, which returns a citation-backed answer in 1–2 s without any chain

## Token budget cheat sheet

| Tool | Cost per call |
|---|---|
| `mcp__jina__read_url` | 0 (free reader tier) |
| `mcp__jina__sort_by_relevance` | 0 |
| `mcp__jina__deduplicate_strings` | 0 |
| `mcp__jina__search_web` | ~63 tokens |
| `mcp__jina__parallel_search_web` | ~107 tokens / 3 queries (~36 each) |
| `mcp__jina__search_arxiv` | ~343 |
| `mcp__jina__parallel_read_url` | ~17,000 (content-proportional) |
| `mcp__exa-answer__exa_answer` | flat per-call, paid |
| `mcp__exa__web_search_exa` | flat per-call, paid |
| `mcp__exa__crawling_exa` | flat per-call, paid |
| `mcp__brightdata_fallback__scrape_as_markdown` | ~$0.01/req, paid |
| `mcp__gigaxity-deep-research__ask` | ~500–1500 tokens |
| `mcp__gigaxity-deep-research__synthesize` | ~5000–10000 tokens |

For a free-tier-heavy workflow that uses Jina for everything except synthesis, a full deep-research session typically costs ~17 k Jina tokens + one OpenRouter synthesis call. With the Jina free 10M trial tier, that's hundreds of full sessions before a key rotation.

## Chain failure observability

When a chain fails completely (every step returns errors), the skill is supposed to surface the failure to the agent rather than silently substitute a worse answer. If you see the agent silently fall back to its training-data answer after a research call returns nothing, that's a skill bug — file an issue.
