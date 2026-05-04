# Quickstart for Gigaxity Deep Research MCP

A five-minute install that gets the six MCP tools (`search`, `research`, `ask`, `discover`, `synthesize`, `reason`) registered with Claude Code, calling Tongyi DeepResearch 30B on OpenRouter, and resolving real queries.

## Prerequisites

- Python 3.11 or newer
- An OpenRouter API key — get one at https://openrouter.ai/keys
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
RESEARCH_LLM_API_KEY=sk-or-v1-your-openrouter-key-here
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
    "RESEARCH_LLM_API_BASE": "https://openrouter.ai/api/v1",
    "RESEARCH_LLM_API_KEY": "YOUR_OPENROUTER_API_KEY",
    "RESEARCH_LLM_MODEL": "alibaba/tongyi-deepresearch-30b-a3b"
  }
}
```

Use **absolute paths**. Replace `YOUR_OPENROUTER_API_KEY` with your actual key (or omit the `env` block and rely on `.env`).

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
