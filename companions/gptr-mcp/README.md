# GPT Researcher MCP companion

The seventh MCP in the deep research stack. Specializes in **social-first research** — queries that benefit from real-world opinions, community knowledge, or platform-specific discussions (Reddit, X/Twitter, YouTube).

This companion **does not vendor source code** — `gptr-mcp` is an actively maintained standalone project at https://github.com/assafelovic/gptr-mcp (MIT). We bundle only an install script, an environment template tuned for social-first research, and the config block that wires it into the rest of the stack.

## Why social-first research?

Web search and documentation lookup miss two patterns that AI agents need:

1. **Lived-experience knowledge** — "What does it actually feel like to switch from X to Y?" "What unexpected gotchas hit people who deployed this?" Answers live on Reddit, X, and YouTube comments, not in docs.
2. **Recency tied to community sentiment** — "Are people happy with the new release?" "What's the consensus on this design choice?" Search engines surface news; the actual sentiment lives on social platforms.

GPT Researcher's `social_openai` retriever surfaces this kind of content, ranked by reputation signal rather than just SEO authority. Pair it with the rest of the stack and you cover docs (Ref) + code (Exa) + general web (Jina) + real-people-experiences (gptr-mcp).

## Install

The `install.sh` script clones gptr-mcp upstream into a sibling directory and builds a venv. It does not modify the parent repo.

```bash
cd companions/gptr-mcp
./install.sh
```

What it does:

```bash
# Clones upstream into a sibling of the parent repo (NOT into companions/).
# If your parent repo lives at $HOME/Projects/gigaxity-deep-research,
# the source lands at $HOME/Projects/gptr-mcp-source.
git clone https://github.com/assafelovic/gptr-mcp.git ../../../gptr-mcp-source

# Optional: pin a ref instead of tracking main
cd ../../../gptr-mcp-source
git checkout <tag-or-commit>      # e.g. v0.x.y; defaults to main

# Creates a venv next to the source
python -m venv .venv
.venv/bin/pip install -r requirements.txt
```

`./install.sh` accepts `GPTR_MCP_REF=<tag-or-commit>` for the same effect — the default is `main`. You can run those steps yourself if you'd rather not run a script.

## Configure

```bash
cd ../gptr-mcp-source
cp ../gigaxity-deep-research/companions/gptr-mcp/env.example .env
```

Edit `.env` to set:

- `OPENAI_API_KEY` — required for the underlying LLM and the `social_openai` retriever
- `TAVILY_API_KEY` — required for the fallback web retriever (https://tavily.com)

The other variables in `env.example` are pre-tuned for social-first research and can stay as-is.

## Register with Claude Code

Add to `~/.claude.json` under `mcpServers`:

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

After restart, four tools become callable:

- `mcp__gptr-mcp__quick_search` — fast social-first lookup
- `mcp__gptr-mcp__deep_research` — multi-hop social-first research
- `mcp__gptr-mcp__get_research_context` — retrieve prior research session context
- `mcp__gptr-mcp__get_research_sources` — extract sources from prior research

## When to route here

The bundled [`research-workflow` skill](../../skills/research-workflow/SKILL.md) routes to gptr-mcp on these signals:

| Signal | Route |
|---|---|
| Query mentions Reddit, X/Twitter, or YouTube explicitly | `mcp__gptr-mcp__quick_search` |
| Query asks for "real user experiences" / "what people think" / "honest opinions" | `mcp__gptr-mcp__quick_search` |
| Query asks about troubleshooting where official docs are insufficient | `mcp__gptr-mcp__quick_search` (with site filter `site:reddit.com`) |
| Query needs cross-platform community sentiment | `mcp__gptr-mcp__deep_research` |
| Query is generic factual/documentation/comparison | NOT gptr-mcp — use Ref / Exa / Jina / gigaxity instead |

LinkedIn-specific queries don't route here (gptr-mcp's social retriever doesn't include LinkedIn). For LinkedIn, use `mcp__jina__search_web` with `site:linkedin.com`.

## Cost notes

`SOCIAL_OPENAI_MODEL=gpt-4o` and `SMART_LLM=openai:gpt-4o` mean every `quick_search` and `deep_research` call hits OpenAI's GPT-4o. Cost varies with research depth; expect $0.05–$0.50 per `deep_research` call. For cheaper operation, swap to `gpt-4o-mini` everywhere.

## License

MIT. Upstream `gptr-mcp` is also MIT — compatible with this repo's licensing.

## Why not bundle source?

`gptr-mcp` and `gpt-researcher` are substantial active projects (~5K LOC combined, weekly commits). Vendoring would commit us to upstream syncing on every release. Cloning at install time gets the user the latest version without baking in version drift.

The `install.sh` here is the integration glue, not the project itself. Same pattern as SearXNG.
