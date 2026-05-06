# Installing the bundled companions

Four companion services ship in [`companions/`](../../companions/) and pair with the parent server. This guide walks through installing each — order matters because some depend on others.

## What you're installing

| Companion | What | Required? |
|---|---|---|
| `companions/searxng/` | Local SearXNG instance via Docker | **Required** unless you point at an external SearXNG |
| `companions/exa-answer/` | Tiny MCP wrapping Exa's `/answer` endpoint | Recommended — `QUICK FACTUAL` queries route here |
| `companions/brightdata-fallback/` | Tiny MCP wrapping Brightdata Web Unlocker | Optional — needed only if you hit blocked URLs often |
| `companions/gptr-mcp/` | Install glue for [`gptr-mcp`](https://github.com/assafelovic/gptr-mcp) — clones the upstream MCP shim around [GPT Researcher](https://github.com/assafelovic/gpt-researcher) and ships an env template tuned for social-first research (Reddit, X, YouTube) | Recommended — community-knowledge queries route here |

## Order

```
1. SearXNG               (sets up the search backend)
2. Parent server         (already covered in setup-mcp.md)
3. exa-answer            (Python venv + register MCP)
4. brightdata-fallback   (Python venv + register MCP) — optional
5. gptr-mcp              (./install.sh clones upstream + register MCP)
```

## 1. SearXNG

Spin up a local SearXNG instance with the JSON API enabled.

```bash
cd companions/searxng
cp settings.yml.example settings.yml

# (optional) edit settings.yml — change `secret_key` if exposing beyond localhost
# generate one with: openssl rand -hex 32

docker compose up -d
```

Verify:

```bash
curl http://localhost:8888/healthz
# OK

curl 'http://localhost:8888/search?q=test&format=json' | head
# JSON response (not HTML)
```

If JSON test returns HTML, the `formats: [html, json]` line in `settings.yml` is missing — fix it and `docker compose restart`.

In the parent project's `.env`:

```bash
RESEARCH_SEARXNG_HOST=http://localhost:8888
```

For production hardening (real `secret_key`, rate limiting, reverse proxy), see [`companions/searxng/README.md`](../../companions/searxng/README.md).

## 2. Parent server

Already covered in [setup-mcp.md](setup-mcp.md). Skip if already done.

## 3. exa-answer

Install the minimal Exa `/answer` wrapper:

```bash
cd companions/exa-answer

python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Register with Claude Code in `~/.claude.json`:

```json
"exa-answer": {
  "type": "stdio",
  "command": "/absolute/path/to/gigaxity-deep-research/companions/exa-answer/.venv/bin/python",
  "args": ["/absolute/path/to/gigaxity-deep-research/companions/exa-answer/mcp_server.py"],
  "env": {
    "EXA_API_KEY": "your-exa-api-key-placeholder"
  }
}
```

Sign up at https://exa.ai if you don't have a key. The same key works for the main `exa` MCP — register both under one key.

After Claude Code restart, `mcp__exa-answer__exa_answer` is callable.

Smoke test from the venv:

```bash
EXA_API_KEY=your-exa-api-key-placeholder python mcp_server.py < /dev/null
# Should boot and wait for stdin. Ctrl+C to exit.
```

If it fails immediately with "EXA_API_KEY must be set" — env not picked up; double-check the `env` block.

## 4. brightdata-fallback (optional)

Install the minimal Brightdata Web Unlocker wrapper:

```bash
cd companions/brightdata-fallback

python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Get a Brightdata account + Web Unlocker zone:
- https://brightdata.com → sign up
- Create a Web Unlocker zone in the dashboard (note the zone name, e.g. `web_unlocker1`)
- Generate an API token under Account Settings

Register with Claude Code in `~/.claude.json`:

```json
"brightdata_fallback": {
  "type": "stdio",
  "command": "/absolute/path/to/gigaxity-deep-research/companions/brightdata-fallback/.venv/bin/python",
  "args": ["/absolute/path/to/gigaxity-deep-research/companions/brightdata-fallback/mcp_server.py"],
  "cwd": "/absolute/path/to/gigaxity-deep-research/companions/brightdata-fallback",
  "env": {
    "BRIGHTDATA_API_TOKEN": "your-brightdata-api-token-placeholder",
    "BRIGHTDATA_ZONE": "your-web-unlocker-zone-name-placeholder"
  }
}
```

After Claude Code restart, `mcp__brightdata_fallback__scrape_as_markdown` is callable.

If you skip Brightdata, the routing skill degrades gracefully — URLs that would have routed here just propagate their original error. SYNTHESIS workflows tolerate this because they pull from many sources; single-URL queries on blocked sites will simply fail.

## 5. gptr-mcp

`gptr-mcp` (the seventh MCP in the deep research stack) is a thin shim around [GPT Researcher](https://github.com/assafelovic/gpt-researcher) — the agentic-research library — tuned for social-first sources (Reddit, X/Twitter, YouTube). The bundled install script clones the upstream MCP into a sibling directory rather than vendoring source.

```bash
cd companions/gptr-mcp
./install.sh
```

What `install.sh` does:
- Clones `https://github.com/assafelovic/gptr-mcp.git` into `../../../gptr-mcp-source` (sibling of the parent repo)
- Creates a venv next to the source
- Installs `requirements.txt` (which pulls in `gpt-researcher` as a transitive dep)

Pin a specific upstream commit/tag with `GPTR_MCP_REF=<ref> ./install.sh`. Default is `main`.

Get API keys:
- OpenAI key — required for the underlying LLM and the `social_openai` retriever (https://platform.openai.com/api-keys)
- Tavily key — required for the fallback web retriever (https://tavily.com)

Configure:

```bash
cd ../../../gptr-mcp-source
cp ../gigaxity-deep-research/companions/gptr-mcp/env.example .env
# edit .env to set OPENAI_API_KEY and TAVILY_API_KEY
```

Register with Claude Code in `~/.claude.json`:

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

After Claude Code restart, four tools become callable: `mcp__gptr-mcp__quick_search`, `mcp__gptr-mcp__deep_research`, `mcp__gptr-mcp__get_research_context`, `mcp__gptr-mcp__get_research_sources`.

LinkedIn isn't in `SOCIAL_OPENAI_DOMAINS` — gptr-mcp's social retriever doesn't handle LinkedIn well. For LinkedIn-specific queries, use Jina with `site:linkedin.com`.

If you skip gptr-mcp, the routing skill falls back to Jina with `site:reddit.com` etc. — workable but with less social-aware ranking.

## Verify the full stack

In Claude Code, type `/mcp` — confirm all seven MCPs show green:

```
Ref                             ●  (HTTP)
exa                             ●  (HTTP)
exa-answer                      ●  (stdio, companions/exa-answer)
jina                            ●  (HTTP)
gigaxity-deep-research          ●  (stdio, parent)
brightdata_fallback             ●  (stdio, companions/brightdata-fallback) — optional
gptr-mcp                        ●  (stdio, ../gptr-mcp-source)
```

The middle three (`Ref` + `exa` + `jina`) are the **Triple Stack** — the search/docs/code trio. The other four (`exa-answer`, `gigaxity-deep-research`, `brightdata_fallback`, `gptr-mcp`) layer on top: speed-critical lookups, synthesis, blocked-URL recovery, and social-first research.

If any are red, follow the "Failure modes" table in [triple-stack-setup.md](triple-stack-setup.md).

## Why bundled vs separate repos?

Bundling these three saves users from the most-common setup pitfalls: SearXNG without JSON enabled, missing-wrapper for `/answer`, and locating a Brightdata wrapper template. Each companion is self-contained — `requirements.txt` + a single Python file (or compose file for SearXNG) — so they don't add meaningful weight to the parent repo.

If you want any companion in its own repo, the directories are portable. Copy the directory out, push to its own remote, adjust the parent's docs to point at the new URL. No edits to companion source needed.
