# Brightdata Fallback MCP

A minimal MCP server that exposes only `scrape_as_markdown` against Brightdata's [Web Unlocker API](https://brightdata.com/products/web-unlocker). Designed as the **last hop** in a research-workflow URL-reading fallback chain — the tool the agent reaches for when ordinary fetchers fail on a URL (CAPTCHA, paywall, Cloudflare challenge, 403).

## Why a separate minimal wrapper?

Brightdata's full MCP exposes ~63 tools, which is enough to flood an LLM context window and cause "no such tool available" errors in long sessions. This wrapper exposes one tool only, keeping the agent's context budget intact while preserving the high-success-rate scraping capability where it matters.

## Setup

```bash
cd companions/brightdata-fallback

python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Set the two required environment variables. Either copy the bundled `env.example`:

```bash
cp env.example .env
# then edit .env and fill in BRIGHTDATA_API_TOKEN and BRIGHTDATA_ZONE
```

Or export them directly:

```bash
export BRIGHTDATA_API_TOKEN="your-brightdata-api-token-placeholder"
export BRIGHTDATA_ZONE="your-web-unlocker-zone-name"
```

The server fails fast at startup if either variable is missing. There is no default value, and your account credentials are not bundled.

## Register with Claude Code

Add to `~/.claude.json` under `mcpServers`:

```json
"brightdata_fallback": {
  "type": "stdio",
  "command": "/absolute/path/to/companions/brightdata-fallback/.venv/bin/python",
  "args": ["/absolute/path/to/companions/brightdata-fallback/mcp_server.py"],
  "cwd": "/absolute/path/to/companions/brightdata-fallback",
  "env": {
    "BRIGHTDATA_API_TOKEN": "your-brightdata-api-token-placeholder",
    "BRIGHTDATA_ZONE": "your-web-unlocker-zone-name"
  }
}
```

After restart, `mcp__brightdata_fallback__scrape_as_markdown` becomes callable.

## Where it fits in the research workflow

The `research-workflow` skill bundled at [`../../skills/research-workflow/SKILL.md`](../../skills/research-workflow/SKILL.md) wires this as the last fallback in the URL-reading chain:

```
1. Jina read_url (free reader tier, 0 tokens)
2. Ref ref_read_url (for documentation URLs)
3. Brightdata scrape_as_markdown (when 1 and 2 fail with empty/CAPTCHA/paywall/403)
```

Costs apply only when the chain reaches step 3 — Brightdata bills per Web Unlocker request. The agent only reaches this step on URLs the cheaper tools couldn't crack.

## Cost notes

Brightdata Web Unlocker is paid. Pricing varies by plan; check https://brightdata.com/pricing/web-unlocker. Typical use in a research workflow is 5–20 requests per session for blocked-URL exceptions, not bulk scraping.

If you don't have a Brightdata account, the rest of the research stack still works — the routing skill degrades gracefully when this MCP isn't registered. URLs that would have routed here just return their original error.

## License

MIT (same as the parent repo).
