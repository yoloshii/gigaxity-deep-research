# Companions

Bundled companion services that pair with the parent `gigaxity-deep-research` MCP server. Each is independently installable and runs as its own MCP server (or, in SearXNG's case, as a backing service).

| Directory | What it is | When to install |
|---|---|---|
| [`exa-answer/`](exa-answer/) | Tiny MCP wrapping Exa's `/answer` endpoint for 1–2 s factual lookups | Always — the routing skill expects it for `QUICK FACTUAL` queries |
| [`brightdata-fallback/`](brightdata-fallback/) | Tiny MCP wrapping Brightdata Web Unlocker — last resort for blocked URLs | Optional — install if you hit CAPTCHA/paywall/Cloudflare on URL reads |
| [`searxng/`](searxng/) | Docker compose + tuned settings for a local SearXNG instance | Required (or point at any other SearXNG) — the parent's primary search source |
| [`gptr-mcp/`](gptr-mcp/) | Install glue for [`gptr-mcp`](https://github.com/assafelovic/gptr-mcp) (GPT Researcher MCP) — clones upstream, sets up venv, ships an env template tuned for social-first research (Reddit, X, YouTube) | Recommended — covers community-knowledge queries that the rest of the stack misses |

## Install order

```
1. searxng           ── docker compose up -d           (~30 s)
2. parent server     ── covered in main README          (~2 min)
3. exa-answer        ── pip install + register MCP      (~1 min)
4. brightdata-fallback ── optional; pip install + register (~1 min)
5. gptr-mcp          ── ./install.sh + register MCP     (~3 min, clones upstream)
```

After all five are running, follow [`../docs/guides/triple-stack-setup.md`](../docs/guides/triple-stack-setup.md) to register the Ref and Jina MCPs (which are both fully hosted — no install) for the complete deep research stack (seven MCPs total: Ref + Exa + Exa Answer + Jina + gigaxity-deep-research + Brightdata fallback + gptr-mcp).

## Why companions, not separate repos?

These four are tightly coupled to how the parent server expects its environment to look:

- The parent's primary search connector requires SearXNG with the JSON API enabled — bundling the working compose file saves users from the most-common setup pitfall
- The two minimal MCP wrappers (~60–140 lines each) are implementation glue rather than standalone projects — they exist to make the routing skill's tool calls land somewhere
- One-clone setup is a usability win for the most common case (single developer, single machine)

If you want to pull any of them out into its own repo for separate distribution, the directories are self-contained — they have their own `README.md`, `requirements.txt`, and (for SearXNG) `docker-compose.yml`. No edits to the parent repo are needed.

## Not bundled

These companions are deliberately **not** bundled because they're either fully hosted (no install) or major standalone projects:

- **Ref** — fully hosted at `https://api.ref.tools`. Sign up at https://ref.tools, paste the API key into your `~/.claude.json`, done.
- **Exa** main MCP — fully hosted at `https://mcp.exa.ai`. Same key as `exa-answer`.
- **Jina** — fully hosted at `https://mcp.jina.ai`. Free 10M tier signup at https://jina.ai.

Configuration for these three is documented in [`../docs/guides/triple-stack-setup.md`](../docs/guides/triple-stack-setup.md).
