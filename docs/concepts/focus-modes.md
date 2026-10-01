# Focus modes

Focus modes tune the discovery and search layers toward a specific domain. They pick which SearXNG category lane runs beside the general web, whether discovery expands the query, and which knowledge gaps discovery highlights. Where presets shape the *output*, focus modes shape the *input*.

## Available modes

| Mode | Use for | SearXNG vertical lane | Query expansion | Gap categories highlighted |
|---|---|---|---|---|
| `general` | Broad technical questions and general research | none — general lane only | yes | documentation, examples, alternatives, gotchas |
| `academic` | Research papers, scientific studies, citations | `science` — arXiv, Google Scholar, Semantic Scholar, OpenAlex, PubMed, Crossref | yes | methodology, limitations, replications, critiques, citations |
| `documentation` | Official docs, API references, library guides | `it` — Stack Overflow, Ask Ubuntu, Super User, GitHub, Hacker News | no | api_reference, examples, migration, changelog, configuration |
| `comparison` | X vs Y evaluations, choosing between options | keyword heuristic | yes | criteria, tradeoffs, edge_cases, benchmarks, community_preference |
| `debugging` | Error messages, bug investigation, troubleshooting | `it` | yes | error_context, similar_issues, root_cause, workarounds, fixes |
| `tutorial` | How-to guides, step-by-step learning | `videos` — YouTube | no | prerequisites, step_by_step, common_mistakes, next_steps |
| `news` | Recent events, announcements, updates | keyword heuristic | yes | announcement, reaction, impact, timeline |

Engines listed are those of the bundled [SearXNG companion](../../companions/searxng/README.md); on another instance the lane runs whatever engines that instance puts in the category. With no focus mode, the lane follows a keyword heuristic: `science` for paper / arXiv / peer-reviewed / DOI queries, `it` for error / traceback / crash / "not working" queries, `videos` for video / tutorial / walkthrough / how-to queries — otherwise no lane. Discovery-side behaviour (expansion, gap categories) treats no focus mode as `general`. A lane runs only when the instance's `/config` shows an enabled engine in its category, and it keeps only results one of those engines returned.

## When to use which

```
Question type?
├── "What is X?" / "How do I use Y?"
│     → documentation OR tutorial
│
├── "X vs Y" / "Best of"
│     → comparison
│
├── "Why is X erroring with Z?"
│     → debugging
│
├── "Latest research on X"
│     → academic
│
├── "What happened with X this week"
│     → news
│
└── (default / unsure)
      → general
```

## How it works under the hood

Two modules carry the per-mode behaviour:

- `src/search/verticals.py` maps each focus mode to a SearXNG vertical lane (`FOCUS_TO_VERTICAL`) and holds the keyword heuristic. The search aggregator adds that lane as a second SearXNG list, which fuses beside the base `general` list as `searxng:<category>`. `RESEARCH_SEARXNG_VERTICAL_ROUTING=false` turns lanes off.
- `src/discovery/focus_modes.py` holds a dataclass per mode. Its `search_expansion` flag decides whether the MCP `discover` tool expands the query (REST `/discover` takes `expand_searches` from the request instead), and `gap_categories` marks the matching knowledge gaps in the MCP tool's output. The dataclass also declares `priority_engines`, `metadata_boost` and `time_filter`, which nothing applies yet — engine choice comes from the lane's SearXNG category, never from an `engines=` list.

The mode applies to searches; the synthesis prompts do not change with it.

## Combining focus modes and presets

See the preset-mode crosswalk in [presets.md](presets.md). The two axes are orthogonal: pick the preset based on the *answer shape you want* (fast vs comprehensive vs comparison), and the focus mode based on *where the answer lives* (academic vs forum vs official docs).

## Adding a new focus mode

1. Add a new entry to `src/discovery/focus_modes.py` following the dataclass pattern.
2. Add a corresponding system-prompt template under `src/synthesis/prompts/focus_modes/<name>.md` if the synthesis stage should behave differently for this mode.
3. The mode is auto-exposed at `/api/v1/focus-modes` and via the `focus_mode` argument to `discover`, `synthesize`, and `reason`.
