# Bundled skills

This directory ships skills that pair with the Gigaxity Deep Research MCP server. Skills are framework-agnostic instruction files (universal 2026 format) that any MCP-compatible agent can load.

## research-workflow

Routes a query across the six-MCP Triple Stack (`Ref` + `exa` + `exa-answer` + `jina` + `gigaxity-deep-research` + `brightdata_fallback`). Classifies the query into one of four workflows — `QUICK FACTUAL`, `DIRECT`, `EXPLORATORY`, `SYNTHESIS` — and chains the appropriate tools.

### Install for Claude Code (per-user)

```bash
mkdir -p ~/.claude/skills
ln -s "$(pwd)/skills/research-workflow" ~/.claude/skills/research-workflow
```

After symlinking, `/research-workflow` becomes available as a slash command, and the skill auto-triggers based on its `description` field whenever the agent needs external knowledge.

### Install for other agents

The skill format is a single markdown file with YAML frontmatter (`name`, `description`, `version`). Drop it into whatever skill directory your agent reads.

| Agent | Skills directory |
|---|---|
| Claude Code | `~/.claude/skills/` |
| Hermes | `~/.config/hermes/skills/` |
| Cursor | (paste contents into `.cursor/rules/`) |

### Why bundled

The skill encodes the routing logic that turns the six MCPs into a coherent deep-research workflow. Without it, an agent sees six separate tool servers and has to figure out the call sequence on its own. With it, the agent sees one workflow with the right tool selected per query class.

The pasteable instruction block in [`../CLAUDE.md`](../CLAUDE.md) is an abridged version of this skill suitable for inlining into a global `CLAUDE.md`. The skill itself is the deep reference (token costs per tool call, preset/focus-mode catalog, fallback chains for blocked URLs).
