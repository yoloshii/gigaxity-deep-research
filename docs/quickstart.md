# Quickstart for Gigaxity Deep Research MCP

> **You are reading this on the `local-inference` branch.** This quickstart shows the branch default — a self-hosted OpenAI-compatible LLM server. Need OpenRouter (or any other hosted endpoint) instead? Either check out the [`main` branch](https://github.com/yoloshii/gigaxity-deep-research/tree/main) for an OpenRouter-default config, or stay here and use the OpenRouter override block at the bottom of [step 4](#4-register-with-claude-code). For LLM-server setup (vLLM, SGLang, llama.cpp, Ollama) and the recommended Q4_K_M GGUF quant on 24 GB consumer GPUs, see [`setup-local-inference.md`](guides/setup-local-inference.md).

A five-minute install that gets the six MCP tools (`search`, `research`, `ask`, `discover`, `synthesize`, `reason`) registered with Claude Code, calling Tongyi DeepResearch 30B on a self-hosted OpenAI-compatible server, and resolving real queries.

## Prerequisites

- Python 3.11 or newer
- A local OpenAI-compatible LLM server (vLLM, SGLang, llama.cpp, or Ollama) reachable at `http://localhost:8000/v1` — see [`setup-local-inference.md`](guides/setup-local-inference.md) for the setup walkthrough plus the recommended Q4_K_M GGUF quant
- A SearXNG instance — easiest path is [Docker self-host](https://docs.searxng.org/admin/installation-docker.html); a public instance also works as long as it exposes the JSON API

## 1. Clone and install

```bash
git clone https://github.com/yoloshii/gigaxity-deep-research.git
cd gigaxity-deep-research

python -m venv .venv
source .venv/bin/activate
pip install -e .
```

## 2. Configure

```bash
cp .env.example .env
```

Edit `.env`:

```bash
RESEARCH_LLM_API_KEY=local-anything               # any non-empty placeholder works for local servers without auth
RESEARCH_SEARXNG_HOST=http://localhost:8888       # or your SearXNG URL
```

The other variables have working defaults; leave them alone unless you have a specific reason to change them.

## 3. Smoke-test the MCP entry point

```bash
python run_mcp.py < /dev/null
```

If it boots without crashing and waits for stdin, the MCP server is healthy. Press `Ctrl+C` to exit.

If you see an `ImportError`, install the missing extras: `pip install -e ".[dev]"`.

## 4. Register with Claude Code

Open `~/.claude.json`, find the `mcpServers` object, and add:

```json
"gigaxity-deep-research": {
  "type": "stdio",
  "command": "/absolute/path/to/gigaxity-deep-research/.venv/bin/python",
  "args": ["/absolute/path/to/gigaxity-deep-research/run_mcp.py"],
  "env": {
    "RESEARCH_LLM_API_BASE": "http://localhost:8000/v1",
    "RESEARCH_LLM_API_KEY": "local-anything",
    "RESEARCH_LLM_MODEL": "Alibaba-NLP/Tongyi-DeepResearch-30B-A3B-Thinking"
  }
}
```

Use **absolute paths**. For local servers without auth, `local-anything` (or any non-empty placeholder) is fine for the API key. Omit the `env` block entirely to rely on `.env`.

**Want OpenRouter (or another hosted endpoint) instead?** Override the three LLM variables in the `env` block above:

```json
"RESEARCH_LLM_API_BASE": "https://openrouter.ai/api/v1",
"RESEARCH_LLM_API_KEY": "sk-or-v1-your-real-key",
"RESEARCH_LLM_MODEL": "alibaba/tongyi-deepresearch-30b-a3b"
```

## 5. Restart Claude Code

After restart, the six tools should appear under the alias `gigaxity-deep-research`. Confirm with `/mcp` in Claude Code — you should see six tools registered (`search`, `research`, `ask`, `discover`, `synthesize`, `reason`).

## 6. Try it

Ask Claude Code something the model wouldn't know off the top — a recent library version, a 2026 news event, a comparison between two new frameworks. If the agent calls `mcp__gigaxity-deep-research__discover` or `synthesize` and returns a citation-backed answer, you're done.

If it doesn't trigger automatically, install the bundled skill so the routing logic kicks in:

```bash
mkdir -p ~/.claude/skills
ln -s "$(pwd)/skills/research-workflow" ~/.claude/skills/research-workflow
```

## What's next

- [Triple Stack setup](guides/triple-stack-setup.md) — wire up the other six companion MCPs (Ref, Exa, Exa Answer, Jina, Brightdata fallback, gptr-mcp)
- [Free-tier strategy](guides/free-tier-strategy.md) — configuring the search MCPs against each provider's free tier
- [Configuration reference](reference/configuration.md) — every `RESEARCH_*` env var explained
- [Troubleshooting](troubleshooting.md) — common boot and runtime errors
