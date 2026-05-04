# Exa Answer MCP

A minimal MCP server that exposes Exa's [`/answer` endpoint](https://docs.exa.ai/reference/answer) as two tools: `exa_answer` (fast, citation-only) and `exa_answer_detailed` (with full source text).

Use this for **mid-task factual lookups** where speed matters more than depth — it returns a direct answer with citations in 1–2 seconds at 94% SimpleQA accuracy.

## Why a separate wrapper?

The Exa main MCP exposes web search and crawling tools but doesn't include `/answer` in the default tool surface. This wrapper plugs that gap with a tiny dedicated server, keeping `/answer` callable without bloating Exa's main MCP surface.

## Two tools, not one

This wrapper exposes **two** tools — `exa_answer` (citation-only, fast) and `exa_answer_detailed` (citations plus full source text). The Brightdata fallback companion ships a single tool by contrast; we shipped two here because `exa_answer_detailed` returns a meaningfully different shape (full source text payload, larger response), and collapsing them into one parameterized tool makes routing logic in research-workflow harder to reason about. If you'd rather keep the agent's tool surface minimal, you can drop `exa_answer_detailed` from `mcp_server.py` — `exa_answer` alone is the common case.

## Setup

```bash
cd companions/exa-answer

python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Set the required environment variable. Either copy the bundled `env.example`:

```bash
cp env.example .env
# then edit .env and fill in EXA_API_KEY
```

Or export it directly:

```bash
export EXA_API_KEY="your-exa-api-key-placeholder"
```

The server fails fast if `EXA_API_KEY` is missing. The same key works for the main `exa` MCP, so register both under one key.

## Register with Claude Code

Add to `~/.claude.json` under `mcpServers`:

```json
"exa-answer": {
  "type": "stdio",
  "command": "/absolute/path/to/companions/exa-answer/.venv/bin/python",
  "args": ["/absolute/path/to/companions/exa-answer/mcp_server.py"],
  "env": {
    "EXA_API_KEY": "your-exa-api-key-placeholder"
  }
}
```

After restart, `mcp__exa-answer__exa_answer` and `mcp__exa-answer__exa_answer_detailed` become callable.

## When to use vs. other research tools

| Use case | Tool |
|---|---|
| "What's the latest version of X?" | `exa_answer` |
| "When was X released?" | `exa_answer` |
| "Find me a definitive 1-sentence answer" | `exa_answer` |
| "Verify a claim with source text" | `exa_answer_detailed` |
| "Explore a topic broadly" | `gigaxity-deep-research.discover` (not this) |
| "Compare X vs Y with citations" | `gigaxity-deep-research.synthesize` (not this) |

The bundled [`research-workflow` skill](../../skills/research-workflow/SKILL.md) routes `QUICK FACTUAL` queries here automatically.

## Cost notes

Exa `/answer` is paid (typically a few cents per call). Free trial credits available at signup. Pricing: https://exa.ai/pricing.

## License

MIT (same as the parent repo).
