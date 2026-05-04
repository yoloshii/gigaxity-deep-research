# Synthesis presets

Presets bundle a set of synthesis-stage choices (number of LLM calls, outline generation on/off, contradiction surfacing on/off, output structure) into a single named configuration. Pass `preset="<name>"` to `synthesize` or `reason`, or leave it unset to use the default.

## Available presets

| Preset | LLM calls | Latency | Best for |
|---|---|---|---|
| `fast` (default) | 1 | ~2–5 s | Quick answers, single-source synthesis, low-stakes lookups |
| `tutorial` | 1 | ~5–10 s | Step-by-step explanations with structural outline |
| `comprehensive` | 2–3 | ~15–30 s | Multi-pass synthesis with quality gate and contradiction detection |
| `contracrow` | 2 | ~10–20 s | Comparison queries — surfaces disagreements rather than averaging |
| `academic` | 2 | ~15–25 s | Citation-heavy, formal structure, peer-reviewed source bias |

## When to use which

```
Goal?
├── Fastest possible answer
│     → fast
│
├── Step-by-step instructional output
│     → tutorial
│
├── Compare two or more options, surface disagreements
│     → contracrow
│
├── Formal write-up with heavy citations
│     → academic
│
└── Best possible answer, latency not critical
      → comprehensive
```

## Preset details

### `fast`

Single LLM call. Source content fed directly to the model with minimal preprocessing. No outline, no quality gate, no contradiction detection.

Use when: you trust the sources, the question is well-formed, you need a quick answer.

### `tutorial`

Single LLM call, but the prompt instructs the model to produce structural output (numbered steps, code-block snippets, prerequisites/followups). Outline-guided.

Use when: the user is learning something step-by-step (setup guides, how-to questions, walkthroughs).

### `comprehensive`

Multi-pass:
1. Quality gate filters sources below a CRAG threshold.
2. Contradiction detector pairwise-compares remaining sources.
3. Optional outline pass.
4. Synthesis pass folds quality-gated content + contradictions into a coherent answer.

Use when: the question is high-stakes (security, architectural, financial), or sources are mixed quality and you need filtering, or contradictions matter.

### `contracrow`

Two-pass:
1. First pass synthesizes per-position answers (one per "side" of the comparison).
2. Second pass surfaces disagreements explicitly as a structured contradiction list, then offers a synthesis that highlights rather than hides the disagreement.

Use when: comparing tools, libraries, approaches, or any question where the answer is "it depends and here's why."

### `academic`

Two-pass:
1. Outline pass generates a paper-style structure (intro, related work, claims, evidence, limitations).
2. Synthesis pass fills the outline with citation-bound prose.

Use when: producing literature reviews, formal write-ups, or any output that goes downstream into a citing document.

## Combining presets and focus modes

Presets control *output structure*. Focus modes (see [focus-modes.md](focus-modes.md)) control *source selection bias*. They compose freely:

| Preset | Focus mode | Use case |
|---|---|---|
| `comprehensive` | `academic` | Lit review on a research question |
| `contracrow` | `comparison` | Tool/library comparison |
| `tutorial` | `documentation` | Library setup guide |
| `fast` | `news` | Quick fact-check on a recent event |
| `comprehensive` | `debugging` | Root-cause analysis from forum posts and issue trackers |

## Adding a new preset

Edit `src/synthesis/presets.py`. A preset is a dataclass with fields:

- `name`
- `llm_call_count` (1, 2, or 3)
- `enable_quality_gate` (bool)
- `enable_contradiction_detection` (bool)
- `outline_strategy` (`none` / `simple` / `academic` / `tutorial`)
- `system_prompt_template` (path to template file under `src/synthesis/prompts/`)

After adding the preset, expose it in `/api/v1/presets` (auto via list reflection) and document it here.
