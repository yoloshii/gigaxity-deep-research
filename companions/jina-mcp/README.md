# jina-mcp

Self-hosted MCP server for Jina AI — the search and read layer of the Triple Stack.

Replaces the hosted `https://mcp.jina.ai/v1` endpoint. Same 19 tool names, so any
routing rules you already have keep working. Runs over stdio, no worker deploy.

## Why this exists

The hosted Jina MCP routes its entire search family through `svip.jina.ai`, a
paid-only lane. If your key holds trial credits — including the free 10M tier
every new signup gets — that lane answers:

```
HTTP 500  {"error":"Not enough credits"}
```

The hosted server returns only `response.statusText` and discards the body, so
what reaches your agent is a bare `Internal Server Error`. A billing condition
wearing an outage costume. Since ~1 August 2026 this has taken out `search_web`,
`parallel_search_web`, `search_arxiv`, `search_ssrn`, and `search_images` for
every trial-tier user, tracked upstream as
[jina-ai/MCP#32](https://github.com/jina-ai/MCP/issues/32) and
[jina-ai/reader#1258](https://github.com/jina-ai/reader/issues/1258).

Rotating your key does not fix it. A fresh key is another trial key, and the lane
refuses trial credits by design.

This server takes a different route for every affected tool.

## What changed

**Web search uses `s.jina.ai`**, which accepts trial credits. Its validator caps
`num` at 20 while the hosted server declares a default of 30, so we never send
`num` at all — we take the default page and slice client-side. The cap cannot be
tripped by any caller.

**Academic search leaves Jina entirely.** The hosted server scoped arXiv and SSRN
with a `domain:` body param that only `svip` understands; `s.jina.ai` accepts it
and silently ignores it, returning general web results. So:

| | Backend | Cost | Key |
|---|---|---|---|
| `search_arxiv` | [arXiv export API](https://info.arxiv.org/help/api/) | free | none |
| `search_ssrn` | [OpenAlex](https://openalex.org) → SSRN source | free | none |
| `search_bibtex` | DBLP → Semantic Scholar | free | none |

This is an upgrade, not a workaround. `search_arxiv` now takes native arXiv field
syntax the Jina wrapper never exposed:

```
cat:cs.CL AND abs:"retrieval augmented"     # category + abstract
au:Vaswani                                   # author
```

plus `sort="date"` for newest-first, which matters for literature reviews.

**Every error reports the response body.** Discarding it is the single bug that
made the original outage take days to diagnose. Failures here name what the
server said and what to do about it — `show_api_key` will even tell you whether a
failure is genuine exhaustion or an unfunded lane.

## Install

```bash
cd companions/jina-mcp
pip install -r requirements.txt
```

Register in `~/.claude.json`:

```json
{
  "mcpServers": {
    "jina": {
      "type": "stdio",
      "command": "python3",
      "args": ["/absolute/path/to/companions/jina-mcp/mcp_server.py"],
      "env": { "JINA_API_KEY": "jina_your_key_here" }
    }
  }
}
```

Replacing a hosted `jina` entry? Delete the old `"type": "http"` block. Keeping
the server name `jina` means tool paths stay `mcp__jina__*` and nothing
downstream needs editing.

Verify:

```bash
JINA_API_KEY=jina_… python3 mcp_server.py   # should start silently on stdio
```

Then call `primer` from your agent — it prints the active search lane and
backends. `show_api_key` prints your wallet balances.

## Tools

**Reading** — `read_url` · `parallel_read_url` · `capture_screenshot_url` · `extract_pdf`

**Search** — `search_web` · `parallel_search_web` · `search_arxiv` ·
`parallel_search_arxiv` · `search_ssrn` · `parallel_search_ssrn` ·
`search_bibtex` · `search_images` · `search_jina_blog`

**Ranking** — `sort_by_relevance` · `deduplicate_strings` · `classify_text`

**Utility** — `guess_datetime_url` · `primer` · `show_api_key`

The `parallel_*` variants fan out concurrently (default 5 at a time) and are
cheaper than looping their single-shot equivalents.

## Two tools need more than a trial key

`search_images` has no free-lane equivalent — `s.jina.ai` returns 503 for
`type: "images"`. It stays pointed at `svip` and returns an explanatory error
unless you have paid balance.

`search_jina_blog` needs `JINA_GHOST_KEY`, a Ghost Content API key. Without it,
that one tool says so and nothing else is affected.

## Past upstream incident — site-restricted search (resolved)

From ~1 August 2026, site-restricted search failed regardless of client:
`s.jina.ai` proxied both the `X-Site` header and the `site:` operator to `svip`,
which returned `TypeError: fetch failed`. **Retested 3 August 2026 — working
again**, and general search quality recovered in the same window. Tracked at
[jina-ai/reader#1258](https://github.com/jina-ai/reader/issues/1258).

The `site` argument on `search_web` is wired and usable. If it starts returning
500s again, that is the same incident recurring rather than a client fault — no
key rotation will help. Exa's `web_search_advanced_exa` with `includeDomains=[...]`
remains the better choice when you need more than one domain, since it is a real
filter rather than a query-string hint; simply dropping the restriction is not a
substitute.

## Configuration

Every setting is optional except `JINA_API_KEY`. See [`env.example`](env.example).

The one worth knowing is `JINA_SEARCH_ENDPOINT`. It defaults to `standard`
(`s.jina.ai`). Set it to `vip` only if you hold paid balance — on a trial key it
reproduces exactly the failure this server exists to route around.
