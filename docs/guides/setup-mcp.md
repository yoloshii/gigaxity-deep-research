# Setting up Gigaxity Deep Research as an MCP server for Claude Code

> **You are reading this on the `local-inference` branch.** The example config below shows the branch default — a self-hosted OpenAI-compatible LLM server. For LLM-server setup walkthroughs (vLLM, SGLang, llama.cpp, Ollama) and the recommended Q4_K_M GGUF quant on 24 GB consumer GPUs, see [`setup-local-inference.md`](setup-local-inference.md). To use OpenRouter (or another hosted endpoint) instead, override the `env` block per the OpenRouter callout below the MCP block.

This guide walks through registering the server with Claude Code as an MCP stdio server, the recommended setup for individual developers.

## What you'll get

After this setup, six tools become available to Claude Code — two primitives plus four deep-research tools:

- `mcp__gigaxity-deep-research__search` — raw multi-source aggregation, no LLM call
- `mcp__gigaxity-deep-research__research` — combined search + synthesis in a single call
- `mcp__gigaxity-deep-research__ask` — fast conversational answer (direct LLM, no search hop)
- `mcp__gigaxity-deep-research__discover` — exploratory expansion + gap detection
- `mcp__gigaxity-deep-research__synthesize` — citation-aware multi-source synthesis over pre-gathered content
- `mcp__gigaxity-deep-research__reason` — chain-of-thought reasoning over the LLM's own knowledge with depth control

## Prerequisites

- Python 3.11+
- A local OpenAI-compatible LLM server (vLLM / SGLang / llama.cpp / Ollama) reachable at `http://localhost:8000/v1` — see [`setup-local-inference.md`](setup-local-inference.md) for the setup walkthrough. The recommended Q4_K_M GGUF quant runs on llama.cpp, Ollama, or vLLM; SGLang requires AWQ or GPTQ instead.
- A SearXNG instance — see [Setting up SearXNG](#setting-up-searxng) below if you don't have one
- Claude Code installed

## Install the server

```bash
git clone https://github.com/yoloshii/gigaxity-deep-research.git
cd gigaxity-deep-research

python -m venv .venv
source .venv/bin/activate
pip install -e .
```

Use a virtualenv (not system Python) — Claude Code launches the MCP subprocess and needs a stable, isolated runtime.

## Configure environment

```bash
cp .env.example .env
```

Edit `.env`:

```bash
RESEARCH_LLM_API_KEY=local-anything           # any non-empty placeholder works for local servers without auth
RESEARCH_SEARXNG_HOST=http://localhost:8888
```

That's the minimum. The rest of the variables have working defaults — including `RESEARCH_LLM_API_BASE=http://localhost:8000/v1` and `RESEARCH_LLM_MODEL=Alibaba-NLP/Tongyi-DeepResearch-30B-A3B`.

## Smoke test

```bash
python run_mcp.py < /dev/null
```

The process should start, log "FastMCP server running over stdio," and wait. `Ctrl+C` to exit. If it crashes, see [Troubleshooting](../troubleshooting.md).

## Register with Claude Code

Open `~/.claude.json` (or wherever your global Claude Code config lives). Find the `mcpServers` object and add:

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

Use **absolute paths**. For local servers without auth, `local-anything` (or any non-empty placeholder) is fine for `RESEARCH_LLM_API_KEY`. Omit the `env` block entirely to rely on the `.env` file the venv reads at startup.

**Want OpenRouter (or another hosted endpoint) instead?** Override the three LLM variables:

```json
"RESEARCH_LLM_API_BASE": "https://openrouter.ai/api/v1",
"RESEARCH_LLM_API_KEY": "sk-or-v1-your-real-key",
"RESEARCH_LLM_MODEL": "alibaba/tongyi-deepresearch-30b-a3b"
```

For Ollama, use `http://localhost:11434/v1` and the model tag you registered (e.g. `tongyi-deepresearch:30b-q4`).

## Restart Claude Code

After restart, the six tools should appear under the alias `gigaxity-deep-research`. Confirm by typing `/mcp` in Claude Code — you should see the alias listed with a green dot.

## Install the bundled skill (recommended)

The skill teaches the agent when to call which tool across the full Triple Stack. Without it, the agent has to figure out the routing itself.

```bash
mkdir -p ~/.claude/skills
ln -s "$(pwd)/skills/research-workflow" ~/.claude/skills/research-workflow
```

Symlinking lets you edit the skill in the repo and have changes picked up without redeploying.

## Try it

Ask Claude Code something outside its training cutoff or something requiring fresh sources:

- "How do vLLM and SGLang differ in throughput for 30B reasoning models in 2026?"
- "Compare the 2026 versions of FastAPI and Litestar."
- "What's the latest CVE on `httpx`?"

If the agent calls one of the six tools and returns a citation-backed answer, the install is working.

## Setting up SearXNG

If you don't have a SearXNG instance, the simplest path is Docker:

```bash
docker run -d \
  --name searxng \
  -p 8888:8080 \
  -v ./searxng:/etc/searxng \
  -e BASE_URL=http://localhost:8888 \
  searxng/searxng:latest
```

Then in `.env`:

```bash
RESEARCH_SEARXNG_HOST=http://localhost:8888
```

Verify: `curl http://localhost:8888/healthz` should return `OK`.

For a public instance, pick one from https://searx.space/ that explicitly advertises JSON API support and set `RESEARCH_SEARXNG_HOST` to its URL.

## Multi-tenant deployments

If multiple developers share one server, each request can include an `X-LLM-Api-Key` header that overrides the env-configured key. The MCP surface accepts this via the optional `api_key` parameter on every tool call. The REST surface accepts it as a header.

This means the server holds an "owner" key (used for any request that didn't specify one) and each user's per-request key gets billed to their own LLM endpoint account.

## What's next

- [Triple Stack setup](triple-stack-setup.md) — wire up the other five companion MCPs for the full deep research workflow
- [Configuration reference](../reference/configuration.md) — every env var explained
- [MCP tool reference](../reference/mcp-tools.md) — full input/output reference for all six tools
