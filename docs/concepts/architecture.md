# Gigaxity Deep Research architecture

How the pipeline is laid out from request to response, and why each stage exists. Targeted at contributors and operators who want to understand or extend the synthesis flow.

## Two surfaces, one pipeline

The server exposes its capabilities over two surfaces:

- **MCP stdio** (`run_mcp.py` → `src/mcp_server.py`) — a [FastMCP](https://github.com/jlowin/fastmcp) server that Claude Code launches as a subprocess.
- **REST** (`src/main.py` → `src/api/routes.py`) — a [FastAPI](https://fastapi.tiangolo.com/) app that runs under uvicorn.

Both surfaces invoke the same orchestration layer underneath. Choosing one over the other is a deployment decision, not a feature decision.

## Pipeline stages

```
1. Discovery layer        →  classify, expand, decompose
2. Search aggregator      →  SearXNG + Tavily + LinkUp (parallel)
3. RRF fusion             →  rank-merge across providers
4. (optional) Read URLs   →  client fetches full content for top sources
5. Synthesis layer        →  quality gate + contradiction + outline + RCS
6. LLM call               →  OpenRouter or local OpenAI-compatible endpoint
7. Citation binding       →  map claims back to source URLs
8. Response               →  structured JSON to caller
```

### Stage 1 — discovery

`src/discovery/` contains the routing logic that decides which connectors and which expansions to run.

- **Routing** classifies the query (factual lookup, comparison, exploratory, debugging) and selects connectors accordingly.
- **Expansion** generates HyDE-style variants — alternate phrasings of the query that surface different result clusters.
- **Decomposition** splits multi-aspect queries into sub-queries that each get their own search-and-synthesis pass.
- **Focus modes** bias the connector selection and ranking weights toward a specific domain (academic, debugging, news, etc.).

### Stage 2 — search aggregator

`src/connectors/` holds one client per provider:

- `searxng.py` — primary, talks JSON to a self-hosted or third-party SearXNG instance
- `tavily.py` — optional fallback via `tavily-python`
- `linkup.py` — optional fallback via `linkup-sdk`

Connectors run in parallel via `asyncio.gather`. If a connector fails (timeout, 5xx, missing API key), the aggregator logs and continues with whatever returned. Empty results from all connectors propagate as an empty `sources` array — the caller decides whether to retry or surface the failure.

### Stage 3 — RRF fusion

Reciprocal Rank Fusion combines per-connector ranked lists into a single union list. The constant `k` (default 60) controls how aggressively top results from one connector dominate.

```
score(d) = Σᵢ 1 / (k + rank_i(d))
```

Where `rank_i(d)` is the rank of document `d` in connector `i`'s list. Documents that appear in multiple connectors' top results get a multiplicative boost without any one connector being able to fully dictate the final order.

### Stage 4 — content extraction (caller's responsibility for `synthesize`)

`synthesize` and `reason` accept a `sources` argument that already includes full text. The caller is expected to have fetched the content (often via `mcp__jina__parallel_read_url` or equivalent). The server doesn't fetch content during `synthesize` because:

- It lets the caller choose the URL-reading strategy (free Jina reader vs. Brightdata for blocked URLs vs. headless browser for JS-heavy pages)
- It keeps the synthesis step deterministic across reruns

`discover` returns ranked URLs without content. `research` (REST only, combined endpoint) fetches content server-side using the connectors' built-in extractors.

### Stage 5 — synthesis layer

`src/synthesis/` holds the LLM-side preprocessing:

- **Quality gate** — CRAG-style scoring rejects sources below a quality threshold before they enter the LLM context. Saves tokens and improves answer reliability.
- **Contradiction detector** — pairwise checks across sources surface disagreements rather than averaging them out. Surfaced contradictions appear in the response payload as a separate field.
- **Outline-guided generation** — for `tutorial`, `comprehensive`, and `academic` presets, the LLM first generates a structural outline, then fills it in. SciRAG-style.
- **Recursive Context Summarization (RCS)** — when source content overflows the LLM context window, RCS summarizes per-source first and feeds the summaries instead of full text.

### Stage 6 — LLM call

`src/llm_client.py` holds the OpenRouter client (or, on the `local-inference` branch, a generic OpenAI-compatible client). The call sends the assembled prompt (system instructions + sources + query) and receives the model's completion.

The LLM is OpenAI-compatible only — no streaming-tool-call gymnastics. This keeps the client tiny (~100 lines) and lets you swap backends by changing two env vars.

### Stage 7 — citation binding

After the LLM returns text, `src/synthesis/citations.py` (VeriCite-style) walks the output and binds inline citation markers (`[1]`, `[2]`, …) to the source URLs they came from. The bound mapping ships in the response payload alongside the prose answer.

### Stage 8 — response

The final JSON looks roughly like:

```json
{
  "answer": "...",
  "citations": [
    {"index": 1, "claim": "...", "source_url": "..."},
    ...
  ],
  "contradictions": [
    {"sources": ["url_a", "url_b"], "claim_a": "...", "claim_b": "..."}
  ],
  "metadata": {
    "preset": "comprehensive",
    "focus_mode": "general",
    "model": "Alibaba-NLP/Tongyi-DeepResearch-30B-A3B-Thinking",
    "latency_ms": 14823
  }
}
```

## Branches

| Branch | LLM client | Default `RESEARCH_LLM_API_BASE` |
|---|---|---|
| `main` | OpenRouter-flavored | `https://openrouter.ai/api/v1` |
| `local-inference` | Generic OpenAI-compatible | `http://localhost:8000/v1` |

Everything outside `src/llm_client.py` is shared between branches. Adding a new backend means subclassing the LLM client, not touching the synthesis pipeline.

## Extending

| Add | Where |
|---|---|
| New search connector | `src/connectors/<name>.py` + register in aggregator |
| New focus mode | `src/discovery/focus_modes.py` |
| New synthesis preset | `src/synthesis/presets.py` |
| New MCP tool | `src/mcp_server.py` |
| New REST endpoint | `src/api/routes.py` + `src/api/schemas.py` |
| Different LLM backend | Subclass `src/llm_client.py` |
