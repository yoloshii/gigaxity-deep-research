# Focus modes

Focus modes tune the discovery and search-aggregation layers toward a specific domain. They bias which connectors run, which engines those connectors hit, and how results get re-ranked before fusion. Where presets shape the *output*, focus modes shape the *input*.

## Available modes

| Mode | Source bias | Engine selection | Recency weight |
|---|---|---|---|
| `general` (default) | Mixed | All engines, balanced | Moderate |
| `academic` | `.edu`, `.gov`, peer-reviewed, arXiv, SSRN | Google Scholar, arxiv, ssrn | Low — older sources are fine |
| `documentation` | Official docs, reference sites | Google with `site:docs.*` boost, Bing | Moderate |
| `comparison` | Comparison sites, blog post round-ups | All engines, balanced | Moderate |
| `debugging` | Stack Overflow, GitHub issues, forum posts | Google, DuckDuckGo | High — recent posts are more likely current |
| `tutorial` | Blog posts, video transcripts, official guides | All engines, balanced | Moderate |
| `news` | News outlets, press releases | Google News, Bing News | Very high — date-bounded |

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

`src/discovery/focus_modes.py` holds the per-mode configuration. A focus mode is a dataclass with:

- `name`
- `connector_weights` (dict of connector → weight)
- `searxng_engines` (override default engines)
- `recency_weight` (multiplier on RRF fusion for recent results)
- `domain_boosts` (list of `(domain_pattern, boost_factor)`)
- `domain_penalties` (list of `(domain_pattern, penalty_factor)`)

When a request specifies a focus mode, the discovery layer:

1. Picks the connectors with non-zero weight.
2. Overrides each connector's engine selection.
3. Adjusts the post-fusion ranking using `recency_weight` × `domain_boosts/penalties`.

The synthesis layer also reads the focus mode to adjust prompt templates — e.g. `academic` uses a more formal system prompt with explicit citation-format instructions.

## Combining focus modes and presets

See the preset-mode crosswalk in [presets.md](presets.md). The two axes are orthogonal: pick the preset based on the *answer shape you want* (fast vs comprehensive vs comparison), and the focus mode based on *where the answer lives* (academic vs forum vs official docs).

## Adding a new focus mode

1. Add a new entry to `src/discovery/focus_modes.py` following the dataclass pattern.
2. Add a corresponding system-prompt template under `src/synthesis/prompts/focus_modes/<name>.md` if the synthesis stage should behave differently for this mode.
3. The mode is auto-exposed at `/api/v1/focus-modes` and via the `focus_mode` argument to `discover`, `synthesize`, and `reason`.
