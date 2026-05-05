# REST API reference

Gigaxity Deep Research exposes the same orchestration over HTTP via FastAPI. The REST surface mirrors the six MCP tools (`search`, `research`, `ask`, `discover`, `synthesize`, `reason`) and adds enhanced synthesis variants (`/synthesize/enhanced`, `/synthesize/p1`) plus reflection endpoints for presets and focus modes.

Base URL: `http://<RESEARCH_HOST>:<RESEARCH_PORT>` (defaults to `http://127.0.0.1:8000`). Bind to `0.0.0.0` only behind an authenticated reverse proxy — the REST surface spends the env-configured LLM key on every unauthenticated caller that reaches it.

All POST endpoints accept the optional header `X-LLM-Api-Key: <key>` for per-request key override (multi-tenant). Bodies that include an `api_key` field set the same override. Header and body values both win over the env-configured `RESEARCH_LLM_API_KEY`.

The interactive OpenAPI schema lives at `/docs` and is the source of truth — this page summarizes it.

---

## GET /api/v1/health

Health + connector status.

**Response (`HealthResponse`):**

```json
{
  "status": "healthy",
  "connectors": ["searxng", "tavily"],
  "llm_configured": true
}
```

`connectors` is a list of active connector names. `llm_configured` is true when `RESEARCH_LLM_API_KEY` is set; the env-configured base URL alone is not sufficient.

---

## POST /api/v1/search

Multi-source search only — no LLM call.

**Request (`SearchRequest`):**

| Field | Type | Default | Notes |
|---|---|---|---|
| `query` | str | required | Search query |
| `top_k` | int | `10` | Per-connector result count |
| `connectors` | list[str] \| null | null | Restrict to specific connectors (default: all configured) |

**Response (`SearchResponse`):**

```json
{
  "query": "...",
  "sources": [
    {
      "id": "...",
      "title": "...",
      "url": "...",
      "content": "...",
      "score": 0.92,
      "connector": "searxng",
      "metadata": {}
    }
  ],
  "connectors_used": ["searxng", "tavily"],
  "total_results": 17
}
```

---

## POST /api/v1/research

Combined search + synthesis. The server fetches sources internally — caller does not pre-fetch.

**Request (`ResearchRequest`):**

| Field | Type | Default | Notes |
|---|---|---|---|
| `query` | str | required | Research query |
| `top_k` | int | `10` | Per-connector result count (1–50) |
| `connectors` | list[str] \| null | null | Optional connector restriction |
| `reasoning_effort` | str | `"medium"` | `"low"` / `"medium"` / `"high"` |
| `preset` | str \| null | null | Optional P1 preset: `comprehensive`, `fast`, `contracrow`, `academic`, `tutorial` |
| `focus_mode` | str \| null | null | Optional focus mode (same enum as `discover`) |
| `api_key` | str \| null | null | Per-request key override |

**Response (`ResearchResponse`):**

```json
{
  "query": "...",
  "content": "...synthesis with [1], [2] citations...",
  "citations": [{"id": "1", "title": "...", "url": "..."}],
  "sources": [{"id": "...", "title": "...", "url": "...", "content": "...", "score": 0.9, "connector": "searxng"}],
  "connectors_used": ["searxng", "tavily"],
  "model": "Alibaba-NLP/Tongyi-DeepResearch-30B-A3B-Thinking",
  "usage": {"prompt_tokens": 1234, "completion_tokens": 567},
  "preset_used": "fast",
  "focus_mode_used": null,
  "quality_gate": null,
  "contradictions": [],
  "rcs_summaries": null
}
```

The `preset_used`, `focus_mode_used`, `quality_gate`, `contradictions`, and `rcs_summaries` fields populate only when the corresponding P1 features are enabled.

---

## POST /api/v1/ask

Quick conversational answer. **Direct LLM call, no search hop.** Mirrors the stdio `ask` shape.

**Request (`AskRequest`):**

| Field | Type | Default | Notes |
|---|---|---|---|
| `query` | str | required | The question |
| `context` | str | `""` | Optional system-context string fed to the LLM |
| `api_key` | str \| null | null | Per-request key override |

**Response (`AskResponse`):**

```json
{
  "query": "...",
  "content": "...the LLM's answer...",
  "citations": [],
  "sources": [],
  "model": "Alibaba-NLP/Tongyi-DeepResearch-30B-A3B-Thinking"
}
```

`citations` and `sources` are always empty because `ask` does not search.

---

## POST /api/v1/discover

Exploratory expansion + knowledge-gap detection.

**Request (`DiscoverRequest`):**

| Field | Type | Default | Notes |
|---|---|---|---|
| `query` | str | required | Topic to explore |
| `top_k` | int | `10` | Per-connector result count |
| `identify_gaps` | bool | `true` | Run gap-detection LLM call |
| `focus_mode` | str | `"general"` | `general`, `academic`, `documentation`, `comparison`, `debugging`, `tutorial`, `news` |
| `connectors` | list[str] \| null | null | Optional connector restriction |
| `api_key` | str \| null | null | Per-request key override |

**Response (`DiscoverResponse`):**

```json
{
  "query": "...",
  "landscape": {
    "explicit_topics": ["..."],
    "implicit_topics": ["..."],
    "related_concepts": ["..."],
    "contrasting_perspectives": ["..."]
  },
  "knowledge_gaps": [
    {"gap": "...", "importance": "high", "category": "...", "description": "..."}
  ],
  "sources": [
    {
      "source": {"id": "...", "title": "...", "url": "...", "content": "...", "score": 0.9, "connector": "searxng"},
      "gap_coverage": ["gap_a", "gap_b"]
    }
  ],
  "synthesis_preview": "...",
  "recommended_deep_dives": ["https://..."],
  "connectors_used": ["searxng", "tavily"]
}
```

---

## POST /api/v1/synthesize

Citation-aware synthesis over caller-provided sources. Does not search.

**Request (`SynthesizeRequest`):**

| Field | Type | Default | Notes |
|---|---|---|---|
| `query` | str | required | Original research query |
| `sources` | list[PreGatheredSource] | required | Pre-fetched sources from Ref/Exa/Jina or your own reader |
| `style` | str | `"comprehensive"` | `comprehensive`, `concise`, `comparative`, `tutorial`, `academic` |
| `max_tokens` | int | `3000` | Output cap (500–16384) |
| `api_key` | str \| null | null | Per-request key override |

`PreGatheredSource` shape: `{"origin": "ref|exa|jina|...", "url": "...", "title": "...", "content": "...", "source_type": "article", "metadata": {}}`.

**Response (`SynthesizeResponse`):**

```json
{
  "query": "...",
  "content": "...synthesis with [1], [2]...",
  "citations": [{"id": "1", "title": "...", "url": "..."}],
  "source_attribution": [{"origin": "ref", "contribution": 0.42}],
  "confidence": 0.83,
  "style_used": "comprehensive",
  "word_count": 612,
  "model": "Alibaba-NLP/Tongyi-DeepResearch-30B-A3B-Thinking",
  "usage": {"prompt_tokens": 1500, "completion_tokens": 612}
}
```

---

## POST /api/v1/synthesize/enhanced

Adds the P0 reliability stack on top of `/synthesize`: source quality gate (CRAG), contradiction detection (PaperQA2), and citation verification.

**Request (`SynthesizeRequestEnhanced`):** extends `SynthesizeRequest` with `run_quality_gate: bool`, `detect_contradictions: bool`, `verify_citations: bool`.

**Response (`SynthesizeResponseEnhanced`):** extends `SynthesizeResponse` with `quality_gate`, `contradictions`, and `verified_claims` blocks.

---

## POST /api/v1/synthesize/p1

Adds the P1 stack: presets (which switch on quality-gate / RCS / contradictions / outline-guided synthesis as a bundle), Recursive Context Summarization, and outline-guided generation.

**Request (`SynthesizeRequestP1`):** extends `SynthesizeRequest` with `preset: Literal["comprehensive","fast","contracrow","academic","tutorial"]` plus per-feature toggles.

**Response (`SynthesizeResponseP1`):** extends `SynthesizeResponse` with `preset_used`, `quality_gate`, `contradictions`, `rcs_summaries`, `outline`, and `critique` blocks (populated by whatever the preset enables).

---

## POST /api/v1/reason

Deep reasoning over pre-gathered sources with a fixed chain-of-thought prompt. There is no `style` parameter — `reason` is intentionally about reasoning shape, not prose register. Use `/api/v1/synthesize` if you want style variants.

**Request (`ReasonRequest`):**

| Field | Type | Default | Notes |
|---|---|---|---|
| `query` | str | required | Question |
| `sources` | list[PreGatheredSource] | required | Pre-fetched sources |
| `api_key` | str \| null | null | Per-request key override |

**Response (`ReasonResponse`):**

```json
{
  "query": "...",
  "content": "...the synthesized answer (the chain-of-thought is consumed by the prompt and not echoed back; if the model fails to emit the expected `<synthesis>` block, the full raw response is returned in `content`)...",
  "reasoning": null,
  "citations": [{"id": "1", "title": "...", "url": "..."}],
  "source_attribution": [{"origin": "ref", "contribution": 0.5}],
  "confidence": 0.81,
  "word_count": 920,
  "model": "Alibaba-NLP/Tongyi-DeepResearch-30B-A3B-Thinking"
}
```

`reasoning` is reserved for prompt configurations that emit a separable CoT trace and is `null` on the default reasoning path. The default path consumes the chain-of-thought inside the prompt and returns only the synthesized answer in `content` — the trace is *not* echoed back. If the model fails to emit the expected `<synthesis>` block, the full raw response is returned in `content` as a fallback.

---

## GET /api/v1/presets

Lists synthesis presets and their per-stage configuration.

**Response (`PresetListResponse`):** `{"presets": [{"name": "comprehensive", "description": "...", "max_tokens": 4000, "run_quality_gate": true, "detect_contradictions": true, "use_rcs": false, "use_outline": false, "verify_citations": true}, ...]}`.

There are five presets: `comprehensive`, `fast`, `contracrow`, `academic`, `tutorial`.

## GET /api/v1/focus-modes

Lists focus modes and their gap-detection categories.

**Response (`FocusModeListResponse`):** `{"focus_modes": [{"name": "general", "value": "general", "description": "...", "gap_categories": [...]}, ...]}`.

There are seven focus modes: `general`, `academic`, `documentation`, `comparison`, `debugging`, `tutorial`, `news`.

---

## Error responses

Standard FastAPI / OpenAPI status codes:

| Status | Meaning |
|---|---|
| 200 | Success |
| 400 | Validation error |
| 401 | LLM endpoint auth failed (forwarded from upstream) |
| 429 | Rate limited (forwarded from upstream) |
| 500 | Internal error |
| 503 | No search connectors configured |
| 504 | LLM timeout |

Error body shape (FastAPI default):

```json
{"detail": "human-readable explanation or validation error array"}
```

Stricter typed error envelopes are not yet produced — clients should branch on status codes plus the `detail` content.

---

## Authentication

The server has no built-in authentication. For anything beyond `localhost`, put it behind a reverse proxy (nginx, Caddy) that enforces auth and TLS. See [`setup-rest.md`](../guides/setup-rest.md) for an example.

The per-request `X-LLM-Api-Key` header is forwarded to the configured LLM endpoint — it does **not** authenticate the caller to this server. Anyone who can hit `/api/v1/*` will spend whichever LLM key is in scope (header, body, or env).
