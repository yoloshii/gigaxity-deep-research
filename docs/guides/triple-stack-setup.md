# Triple Stack — full deep research setup for Claude Code

Six MCP servers. One classification skill. The combination turns Claude Code into a deep-research-first environment that picks the right tool per query class without hand-holding.

This guide assumes you've already set up `gigaxity-deep-research` per [setup-mcp.md](setup-mcp.md). It walks through the other five companion MCPs.

## The seven MCPs

| MCP | Role | Cost |
|---|---|---|
| `Ref` | Library and API documentation lookup | Free credits, then ~$9/mo Basic ([ref.tools](https://ref.tools)) |
| `exa` | Code-context search, advanced web, crawling | Paid; generous free trial credits ([exa.ai](https://exa.ai)). Trial credits reset per signup, so a fresh Google account allocation buys another round if you exhaust them. |
| `exa-answer` | 1–2 s factual lookups (uses Exa `/answer`) | Same key as `exa` |
| `jina` | Free-tier web/arxiv/ssrn search, parallel reads | Paid; generous free 10M trial tier ([jina.ai](https://jina.ai)) — enough for hundreds of full pipeline sessions before key rotation |
| `gigaxity-deep-research` | Multi-source synthesis with Tongyi 30B | Pay-per-call against your OpenRouter key (or zero ongoing cost on the `local-inference` branch) |
| `brightdata_fallback` | Last-resort scraper for blocked URLs | Monthly free-tier limit, then paid ([brightdata.com](https://brightdata.com) Web Unlocker); only fires on ~5–15% of URL fetches |
| `gptr-mcp` | Social-first research via Reddit, X, YouTube — wraps [GPT Researcher](https://github.com/assafelovic/gptr-mcp) | Pay-per-call OpenAI + free-tier Tavily |

**Recommendation: secure all seven keys and run the full stack.** Each MCP fills a distinct niche — Ref is the cheapest source for canonical docs, Exa exposes a curated code index and category-filtered web search, Jina is the workhorse free-tier reader, gigaxity-deep-research drives synthesis, Brightdata recovers blocked URLs, and gptr-mcp surfaces community knowledge. The routing skill orchestrates them so each call lands on the cheapest tool that can answer it; replacing one with a fallback degrades quality rather than just cost.

The routing logic *does* degrade gracefully if an MCP isn't registered, so you can ship without one or two and add them later. But the design intent is the full seven — and most operations land on Jina (free 10M tier), Exa (free trial credits), Ref (free credits before the $9/mo tier), or Brightdata (monthly free-tier limit), so the running cost is far below what the table's "Paid" labels imply. See [free-tier-strategy.md](free-tier-strategy.md) for per-MCP free-tier mechanics and the routing logic that keeps spend predictable.

**Alternative for docs lookup:** [Context7](https://context7.com) covers the same role as Ref — library and API documentation lookup — and ships its own MCP. The bundled `research-workflow` skill is currently wired to Ref's tool names; swapping in Context7 requires a small edit to the routing references in [`../../skills/research-workflow/SKILL.md`](../../skills/research-workflow/SKILL.md) and [`../../CLAUDE.md`](../../CLAUDE.md). Pick whichever you have a key for — the rest of the stack doesn't care.

## Prerequisites

You'll register all five into your global `~/.claude.json` under `mcpServers`. The four paid services need API keys; sign up first.

## Stack 1: Ref

```json
"Ref": {
  "type": "http",
  "url": "https://api.ref.tools/mcp?apiKey=YOUR_REF_API_KEY_PLACEHOLDER"
}
```

Sign up: https://ref.tools (Basic tier ~$9/mo for 2k queries).

What it does: official library/API documentation search. Use as the first hop for any "how does library X work" question. Faster and more accurate than web search for this class of query.

## Stack 2: Exa

```json
"exa": {
  "type": "http",
  "url": "https://mcp.exa.ai/mcp?exaApiKey=YOUR_EXA_API_KEY_PLACEHOLDER&tools=web_search_exa,web_search_advanced_exa,get_code_context_exa,crawling_exa"
}
```

Sign up: https://exa.ai. The `tools=` query parameter selects four tools; you can include or exclude tools by editing the list.

Use HTTP transport (not stdio) — stdio caps at three default tools and ignores `ENABLED_TOOLS` env var.

## Stack 3: Exa Answer

```json
"exa-answer": {
  "type": "stdio",
  "command": "/absolute/path/to/gigaxity-deep-research/companions/exa-answer/.venv/bin/python",
  "args": ["/absolute/path/to/gigaxity-deep-research/companions/exa-answer/mcp_server.py"],
  "env": { "EXA_API_KEY": "YOUR_EXA_API_KEY_PLACEHOLDER" }
}
```

`exa-answer` is a minimal wrapper around Exa's `/answer` endpoint, **bundled in this repo** at [`companions/exa-answer/`](../../companions/exa-answer/). See [setup-companions.md](setup-companions.md) for install steps. The same Exa API key works for both this and the main `exa` MCP.

What it does: 1–2 s factual lookups with citations (94% SimpleQA accuracy). Use when speed is the only thing that matters.

## Stack 4: Jina

```json
"jina": {
  "type": "http",
  "url": "https://mcp.jina.ai/v1",
  "headers": { "Authorization": "Bearer YOUR_JINA_API_KEY" }
}
```

Sign up: https://jina.ai. The free tier is 10M tokens — enough for hundreds of full-pipeline queries.

What it does: web search, URL reading (free reader tier), arxiv, ssrn, parallel reads, classification, reranking, dedup. The workhorse free-tier MCP.

## Stack 5: gigaxity-deep-research (this repo)

Already covered in [setup-mcp.md](setup-mcp.md).

## Stack 7: gptr-mcp (social-first research)

Bundled in this repo with an install script that clones upstream — see [`../../companions/gptr-mcp/`](../../companions/gptr-mcp/) and [setup-companions.md](setup-companions.md) for the full procedure.

```json
"gptr-mcp": {
  "type": "stdio",
  "command": "/absolute/path/to/gptr-mcp-source/.venv/bin/python",
  "args": ["/absolute/path/to/gptr-mcp-source/server.py"],
  "cwd": "/absolute/path/to/gptr-mcp-source",
  "env": {
    "OPENAI_API_KEY": "your-openai-api-key-placeholder",
    "TAVILY_API_KEY": "your-tavily-api-key-placeholder",
    "RETRIEVER": "social_openai,tavily",
    "SOCIAL_OPENAI_DOMAINS": "reddit.com,x.com,youtube.com",
    "SOCIAL_OPENAI_MODEL": "gpt-4o",
    "FAST_LLM": "openai:gpt-4o-mini",
    "SMART_LLM": "openai:gpt-4o",
    "STRATEGIC_LLM": "openai:gpt-4o-mini"
  }
}
```

What it does: surfaces real-world opinions, troubleshooting threads, and community sentiment from Reddit, X/Twitter, and YouTube — content that web search and documentation lookup miss. Routed automatically by the `research-workflow` skill when a query benefits from lived-experience knowledge.

LinkedIn is **not** part of `SOCIAL_OPENAI_DOMAINS` (the upstream retriever doesn't support LinkedIn well). For LinkedIn-specific queries, route to Jina with `site:linkedin.com`.

## Stack 6: Brightdata fallback

```json
"brightdata_fallback": {
  "type": "stdio",
  "command": "/absolute/path/to/gigaxity-deep-research/companions/brightdata-fallback/.venv/bin/python",
  "args": ["/absolute/path/to/gigaxity-deep-research/companions/brightdata-fallback/mcp_server.py"],
  "cwd": "/absolute/path/to/gigaxity-deep-research/companions/brightdata-fallback",
  "env": {
    "BRIGHTDATA_API_TOKEN": "YOUR_BRIGHTDATA_API_TOKEN",
    "BRIGHTDATA_ZONE": "YOUR_WEB_UNLOCKER_ZONE_NAME"
  }
}
```

`brightdata_fallback` is a minimal MCP that exposes only `scrape_as_markdown` against Brightdata's Web Unlocker API. **Bundled in this repo** at [`companions/brightdata-fallback/`](../../companions/brightdata-fallback/). See [setup-companions.md](setup-companions.md) for install steps.

What it does: scrapes URLs that Jina can't (CAPTCHA, paywall, Cloudflare challenge, 403). Used as the last resort in the URL-reading fallback chain — see [concepts/fallback-chains.md](../concepts/fallback-chains.md) for when it fires.

The wrapper requires `BRIGHTDATA_API_TOKEN` and `BRIGHTDATA_ZONE` env vars and fails fast at startup if either is missing — no defaults are bundled.

## Install the routing skill

The bundled [`research-workflow` skill](../../skills/research-workflow/SKILL.md) is what makes the agent route correctly across all six MCPs.

```bash
mkdir -p ~/.claude/skills
ln -s "$(pwd)/skills/research-workflow" ~/.claude/skills/research-workflow
```

After symlinking, the skill auto-triggers on research queries via its `description` field.

## Add the instruction block to global CLAUDE.md

Open the [pasteable instruction block in our CLAUDE.md](../../CLAUDE.md#instruction-block--paste-into-your-global-claudemd) and copy it into your global `~/.claude/CLAUDE.md` (or `AGENTS.md`).

This adds:
- Default behavior: always trigger `research-workflow` for external knowledge queries
- Subagent dispatch template (max 2 in parallel)
- Tool selection matrix per query class
- Brightdata fallback chain

Without this block, the skill works but the agent has to discover the routing on its own per session. With it, the routing becomes default behavior.

## Verify

In Claude Code, type `/mcp` — you should see all six MCPs listed with green dots.

Test each by asking a question that should route to it:

| Query | Expected MCP |
|---|---|
| "What is the OpenAI Python SDK's `client.beta` namespace for?" | `Ref` |
| "Show me a code example using `httpx.AsyncClient` with retries" | `exa` (`get_code_context_exa`) |
| "What's the current Bun version?" | `exa-answer` |
| "Find recent papers on CRAG quality gates" | `jina` (`search_arxiv`) |
| "What do people say about FastAPI vs Litestar in 2026?" | `gigaxity-deep-research` (`synthesize`) |

If routing happens correctly, the stack is wired.

## Failure modes

| Symptom | Cause | Fix |
|---|---|---|
| Agent ignores all MCPs and uses native WebSearch | Skill not installed or not triggered | Confirm `~/.claude/skills/research-workflow/SKILL.md` exists; restart Claude Code |
| Routing always picks the same MCP | Instruction block not in global CLAUDE.md | Paste the block per the section above |
| `Ref` returns empty for queries that should hit official docs | Free tier exhausted | Check usage at ref.tools dashboard |
| Jina calls fail with 401 | Bearer token expired or wrong | Regenerate at https://jina.ai dashboard |
| `brightdata_fallback` errors on every URL | `.env` in `cwd` missing or wrong creds | Re-check Brightdata zone setup |
| `gigaxity-deep-research` calls work but other MCPs don't | One specific MCP misconfigured | Run `/mcp` and check the failing MCP's status |

## Next steps

- [MCP tool reference](../reference/mcp-tools.md) — full schemas for the four `gigaxity-deep-research` tools
- [REST API reference](../reference/rest-api.md) — same surface over HTTP
- [Troubleshooting](../troubleshooting.md) — boot and runtime errors
