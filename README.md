# Gigaxity Deep Research — Open-source deep research MCP server for Claude Code, Hermes, and Cursor

**Open-source deep research MCP server for Claude Code, Hermes, Cursor, and any MCP-compatible agent — local-inference branch.** [Tongyi DeepResearch 30B](https://huggingface.co/Alibaba-NLP/Tongyi-DeepResearch-30B-A3B-Thinking) (or any OpenAI-compatible chat-completions model) running on your own hardware via [vLLM](https://github.com/vllm-project/vllm), [SGLang](https://github.com/sgl-project/sglang), [Ollama](https://ollama.ai/), or [llama.cpp](https://github.com/ggerganov/llama.cpp), plus multi-source web synthesis with citations.

Gigaxity Deep Research wraps Alibaba's Tongyi DeepResearch 30B, a model purpose-built for agentic research, and exposes it as six MCP tools — two primitives (`search`, `research`) plus four deep-research tools (`ask`, `discover`, `synthesize`, `reason`) — with a matching FastAPI REST surface. The synthesis layer pulls from a "Triple Stack" of complementary search MCPs ([Ref](https://ref.tools), [Exa](https://exa.ai), [Jina](https://jina.ai)) alongside [SearXNG](https://github.com/searxng/searxng), [Tavily](https://tavily.com), and [LinkUp](https://linkup.so) connectors, then merges results via reciprocal rank fusion with citation binding and contradiction detection on top. A bundled [`gptr-mcp`](https://github.com/assafelovic/gptr-mcp) companion — the MCP shim around [GPT Researcher](https://github.com/assafelovic/gpt-researcher) — adds Reddit, X, and YouTube as social-first sources.

This branch defaults to a local OpenAI-compatible inference server on `http://localhost:8000/v1`. If you'd rather use a hosted model (OpenRouter and friends), check out the [`main` branch](https://github.com/yoloshii/gigaxity-deep-research/tree/main) — it ships with OpenRouter as the default and is the simpler path when you don't have GPU capacity. The search-MCP layer is priced separately by each provider. See [`docs/guides/free-tier-strategy.md`](docs/guides/free-tier-strategy.md) for what their free tiers cover and how to wire them up.

Python on FastAPI. MIT License. Runs as an MCP stdio server, FastAPI REST API, or both. Drop-in instructions for the full deep research stack live in [`CLAUDE.md`](CLAUDE.md) so any agent can mirror the configuration.

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](pyproject.toml)
[![MCP](https://img.shields.io/badge/MCP-stdio-purple.svg)](https://modelcontextprotocol.io/)

---

## What it does

Tongyi DeepResearch 30B is a reasoning-tuned model that Alibaba designed for multi-hop web research. Gigaxity Deep Research wires it to a multi-source search layer (SearXNG, Tavily, LinkUp), an RRF fusion stage, and a synthesis engine with citation binding, then exposes the whole pipeline as six MCP tools — two primitives (`search`, `research`) plus four deep-research tools (`ask`, `discover`, `synthesize`, `reason`) — that Claude Code or any MCP-compatible agent can call.

So when an agent hits a question outside its training cutoff, it doesn't hallucinate or shell out to a generic search tool. It calls `discover` to widen the source set, reads the top hits, and calls `synthesize` to fold the evidence into a citation-backed answer. Round-trip is typically 5–15 seconds against a hosted reasoning model.

## Features

### Tools (MCP and REST)

The MCP server exposes **two primitives** plus **four deep-research tools** — six tools total. The primitives give you raw search and the simple combined pipeline; the deep-research tools split discovery, synthesis, and reasoning so each step can be driven independently.

**Primitives**

| Tool | Purpose |
|---|---|
| `search` | Raw multi-source aggregation across SearXNG, Tavily, and LinkUp with RRF fusion. No LLM call. |
| `research` | Combined pipeline: multi-source search plus LLM synthesis with citations, in one call. |

**Deep-research tools**

| Tool | Purpose |
|---|---|
| `discover` | Exploratory expansion — surfaces explicit, implicit, related, and contrasting angles, then flags knowledge gaps |
| `synthesize` | Citation-aware synthesis over pre-gathered content; CRAG-style quality gate, contradiction surfacing, outline-guided generation |
| `reason` | Deep synthesis with optional CoT depth control over pre-gathered content |
| `ask` | Fast conversational answer (direct LLM call, no search hop) |

### Pipeline

- **Multi-source search**: parallel queries across SearXNG, Tavily, and LinkUp with graceful degradation if any source is unavailable.
- **RRF fusion**: Reciprocal Rank Fusion combines and re-ranks results across providers.
- **Adaptive routing**: query classification picks the right combination of connectors per query.
- **Query expansion**: HyDE-style variant generation for broader coverage.
- **Query decomposition**: multi-aspect breakdown for complex queries.
- **Quality gate**: CRAG-style filtering keeps low-quality sources out of synthesis.
- **Contradiction detection**: PaperQA2-style disagreement surfacing flags conflicting claims rather than averaging them out.
- **Citation binding**: VeriCite-style claim-to-evidence mapping in the final answer.
- **Outline-guided synthesis**: SciRAG-style structured generation for tutorial and academic presets.
- **Focus modes**: `general`, `academic`, `documentation`, `comparison`, `debugging`, `tutorial`, `news`.

### Compatibility

- **Reasoning models**: works with Tongyi DeepResearch, DeepSeek-R1, Qwen-QwQ, and any other OpenAI-compatible chat-completions model.
- **Multi-tenant**: accepts a per-request `X-LLM-Api-Key` header so multiple users can share one server instance and bring their own LLM endpoint keys.
- **MCP and REST**: the same orchestration logic powers both surfaces.

## What the full install includes

The Quick Starts below cover the orchestrator MCP — one of seven in the full stack. The complete deep research workflow (automatic per-query routing across the whole stack) comprises four parts:

1. **Seven MCPs.** This repo's orchestrator (`gigaxity-deep-research`) plus the **Triple Stack** (`Ref` + `exa` + `jina` — search/docs/code trio) plus three more (`exa-answer`, `brightdata_fallback`, `gptr-mcp`).
2. **Companion projects and dependencies.** [SearXNG](https://github.com/searxng/searxng) (primary search source, bundled at [`companions/searxng/`](companions/searxng/)) and [GPT Researcher](https://github.com/assafelovic/gpt-researcher) (transitive dependency of `gptr-mcp`); plus the minimal MCP wrappers bundled at [`companions/exa-answer/`](companions/exa-answer/) and [`companions/brightdata-fallback/`](companions/brightdata-fallback/).
3. **The pasteable instruction block** in [`CLAUDE.md`](CLAUDE.md#instruction-block--paste-into-your-global-claudemd) — drop into your global `~/.claude/CLAUDE.md` (or `AGENTS.md`) so the agent fires the research workflow on external-knowledge queries and routes each query class to the right MCP.
4. **The bundled [`research-workflow`](skills/research-workflow/SKILL.md) skill** — the deep reference for the routing classifier (token costs per tool, presets, fallback chains).

Walk the [Setup roadmap](#setup-roadmap) below for a stage-by-stage path through all four.

## Quick start: MCP for Claude Code

For individual setups, run as an MCP stdio server and register it in your global `~/.claude.json`.

```bash
# Clone and install
git clone https://github.com/yoloshii/gigaxity-deep-research.git
cd gigaxity-deep-research

python -m venv .venv
source .venv/bin/activate
pip install -e .

# Configure
cp .env.example .env
# Edit .env: defaults point at http://localhost:8000/v1 — start vLLM, SGLang,
#            llama.cpp, or Ollama there before booting the orchestrator.
#            For Ollama, set RESEARCH_LLM_API_BASE=http://localhost:11434/v1.
#            RESEARCH_LLM_API_KEY must be non-empty (any placeholder works for
#            local servers without auth).
```

Stand up a local model server. The fastest GPU-friendly path is vLLM:

```bash
# In a separate terminal (needs ~24-60 GB VRAM at INT4-FP16)
pip install vllm
python -m vllm.entrypoints.openai.api_server \
  --model Alibaba-NLP/Tongyi-DeepResearch-30B-A3B-Thinking \
  --host 0.0.0.0 --port 8000
```

For lighter setups, swap in any smaller OpenAI-compatible model (Ollama runs on a 24 GB GPU or even CPU). See [`docs/guides/setup-local-inference.md`](docs/guides/setup-local-inference.md) for hardware tradeoffs and per-server commands.

Add to `~/.claude.json` under `mcpServers`:

```json
"gigaxity-deep-research": {
  "type": "stdio",
  "command": "/path/to/gigaxity-deep-research/.venv/bin/python",
  "args": ["/path/to/gigaxity-deep-research/run_mcp.py"],
  "env": {
    "RESEARCH_LLM_API_BASE": "http://localhost:8000/v1",
    "RESEARCH_LLM_API_KEY": "local-anything",
    "RESEARCH_LLM_MODEL": "Alibaba-NLP/Tongyi-DeepResearch-30B-A3B-Thinking"
  }
}
```

Restart Claude Code. The six tools (`search`, `research`, `ask`, `discover`, `synthesize`, `reason`) become callable as `mcp__gigaxity-deep-research__<tool>`.

The MCP alone gives you raw access to the six tools. Most of the deep research value — automatic per-query tool routing across the full seven-MCP stack, the social-first layer via `gptr-mcp`, the routing skill, and the global agent-instruction block — comes from the rest of the staircase. Walk it in [Setup roadmap](#setup-roadmap) below.

## Quick start: REST API for distributed compute

When the model lives on a different machine from the orchestrator (e.g. you self-host Tongyi on a GPU box and want the rest of the pipeline on a CPU-only edge node), run it as a REST API.

```bash
docker compose up -d
curl http://localhost:8000/api/v1/health
```

REST endpoints:

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/v1/health` | GET | Health check, lists active connectors |
| `/api/v1/discover` | POST | Exploratory expansion |
| `/api/v1/synthesize` | POST | Citation-aware synthesis |
| `/api/v1/reason` | POST | Chain-of-thought reasoning |
| `/api/v1/ask` | POST | Quick answer |
| `/api/v1/research` | POST | Combined search + synthesis |
| `/api/v1/search` | POST | Multi-source search only (no LLM) |
| `/api/v1/presets` | GET | List synthesis presets |
| `/api/v1/focus-modes` | GET | List focus modes |

Each endpoint accepts an optional `X-LLM-Api-Key` header that overrides the env-configured key for that request. Multi-tenant deployments use it to bill each user separately when the configured `RESEARCH_LLM_API_BASE` is a paid hosted endpoint.

Full REST reference: [`docs/reference/rest-api.md`](docs/reference/rest-api.md).

## Setup roadmap

The Quick Starts above get the orchestrator MCP running against a model and a search source. The full deep research workflow — automatic tool routing across the seven-MCP stack, social-first research via `gptr-mcp`, the routing skill that classifies queries, plus the agent-instruction block that wires it all into Claude Code — needs the rest of the staircase below.

Each stage has a verify step, so you can stop at any point and know the layer below is solid. Stages 1–4 give you a working orchestrator. Stages 5–7 turn it into the full deep research stack.

| # | Stage | What you do | Verify | Time | Doc |
|---|---|---|---|---|---|
| 1 | Core install | Clone repo, create venv, `pip install -e .` | `python -c "from src.main import app"` exits 0 | 2 min | [Quickstart](docs/quickstart.md) |
| 2 | Primary search source | Stand up SearXNG (bundled compose file under [`companions/searxng/`](companions/searxng/)) | `curl http://localhost:8888/healthz` returns 200 | 5 min | [setup-companions.md](docs/guides/setup-companions.md) |
| 3 | LLM endpoint | Start a local model (Ollama / vLLM / SGLang / llama.cpp) **or** point env vars at a hosted endpoint such as OpenRouter | `curl $RESEARCH_LLM_API_BASE/models` returns a model list | 5–30 min | [setup-local-inference.md](docs/guides/setup-local-inference.md) |
| 4 | Wire gigaxity into Claude Code | `cp .env.example .env`, edit env vars, register the stdio MCP block in `~/.claude.json`, restart Claude Code | `/mcp` shows `gigaxity-deep-research` with a green dot; `mcp__gigaxity-deep-research__research` returns a synthesis with citations | 5 min | [setup-mcp.md](docs/guides/setup-mcp.md) |
| 5 | Companion MCPs (Triple Stack) | Register Ref, Exa, Exa Answer, Jina, Brightdata fallback, and gptr-mcp in `~/.claude.json` | `/mcp` shows all seven MCPs registered with green dots | 10–15 min | [triple-stack-setup.md](docs/guides/triple-stack-setup.md) |
| 6 | Routing skill + agent instructions | Symlink [`skills/research-workflow/`](skills/research-workflow/) into your skills dir; paste the instruction block from [`CLAUDE.md`](CLAUDE.md#instruction-block--paste-into-your-global-claudemd) into your global `~/.claude/CLAUDE.md` | A research query triggers the `research-workflow` skill instead of the agent's built-in WebSearch | 3 min | [skill SKILL.md](skills/research-workflow/SKILL.md) |
| 7 | Full-stack smoke | Run one query of each routing class and confirm the right MCP fires | See the smoke matrix below | 5 min | (below) |

### Smoke matrix

Run each query in Claude Code (or the agent of your choice) after Stage 7 and confirm the routing matches:

| Query | Should route to | What you should see |
|---|---|---|
| "What's the latest stable version of Bun?" | `exa-answer` | 1–2 s factual answer with citations |
| "What does the OpenAI Python SDK's `client.beta` namespace cover?" | `Ref` (`ref_search_documentation`) | Library/API documentation chunks |
| "Show me a code example using `httpx.AsyncClient` with retries" | `exa` (`get_code_context_exa`) | Curated code-context snippets |
| "Find recent papers on CRAG quality gates" | `jina` (`search_arxiv`) | arXiv search hits |
| "Compare FastAPI vs Litestar for production APIs in 2026" | `gigaxity-deep-research` (`synthesize`) | Citation-backed comparative synthesis |
| "What do people on Reddit say about Bun vs Node for production?" | `gptr-mcp` (`quick_search`) | Reddit / X / YouTube community sentiment |

If a query routes somewhere unexpected, the most common cause is the global instruction block from Stage 6 not being pasted into your global `CLAUDE.md` / `AGENTS.md`. Without it, the agent has to discover the routing logic on its own per session.

### Common pitfalls

- **Stage 2 is required, not optional.** SearXNG is the primary search source — Tavily and LinkUp are fallbacks, not replacements. Skipping it leaves the synthesis layer with nothing to fuse.
- **Verify Stage 4 before adding companions.** A failing `research` call after Stage 5 is hard to debug because the failure could be any of seven MCPs misfiring; confirm the orchestrator alone works first.
- **Stage 6 is what makes the agent route automatically.** Without the skill plus the instruction block, the seven MCPs are visible but the agent treats them as raw tools, not a stack.
- **`local-inference` branch defaults to `http://localhost:8000/v1`; `main` defaults to OpenRouter.** Stage 3's verify command is the same either way, but the env var values differ — match them to your branch.

## Modes

| Mode | Branch | LLM backend | When to use |
|---|---|---|---|
| **OpenRouter (hosted)** | [`main`](https://github.com/yoloshii/gigaxity-deep-research/tree/main) | Hosted Tongyi DeepResearch 30B via OpenRouter | Single-machine setup, no GPU, fastest path to working |
| **Local inference (default on this branch)** | `local-inference` | Self-hosted Tongyi/DeepSeek/Qwen via vLLM, SGLang, llama.cpp, or Ollama | On-prem requirement, GPU available, no usage-based cost |
| **REST API (any backend)** | both | Either, plus optional remote model server | Distributed compute — orchestrator and model on different machines |

This branch ships with `RESEARCH_LLM_API_BASE` defaulted to `http://localhost:8000/v1` and a generic OpenAI-compatible LLM client. Search, fusion, synthesis, and citations behave identically across both branches; the only divergence is which inference endpoint the synthesis layer talks to by default. To run against a hosted model from this branch, just override the env vars (or check out `main` for a config that's already wired for OpenRouter).

## Architecture

```
┌────────────────────── Gigaxity Deep Research ──────────────────────┐
│                                                                    │
│  MCP stdio (run_mcp.py) ──┐               ┌── REST (FastAPI)       │
│                            ▼               ▼                         │
│                       ┌─────────────────────────┐                   │
│                       │      Discovery layer     │                   │
│                       │  routing · expansion ·   │                   │
│                       │  decomposition · focus   │                   │
│                       └────────────┬────────────┘                   │
│                                    ▼                                 │
│  ┌──────── Search aggregator (parallel, fail-graceful) ─────────┐   │
│  │   SearXNG     ·     Tavily     ·     LinkUp                  │   │
│  │              ↓ all results unioned ↓                         │   │
│  │                      RRF fusion                              │   │
│  └─────────────────────────┬────────────────────────────────────┘   │
│                            ▼                                         │
│                       ┌─────────────────────────┐                   │
│                       │     Synthesis layer      │                   │
│                       │  CRAG quality gate ·     │                   │
│                       │  contradiction detector ·│                   │
│                       │  outline guide · RCS     │                   │
│                       └────────────┬────────────┘                   │
│                                    ▼                                 │
│              OpenAI-compatible LLM (OpenRouter / local)              │
│                          Tongyi 30B et al.                           │
└────────────────────────────────────────────────────────────────────┘
```

## The bigger stack

Gigaxity Deep Research is the synthesis MCP in a seven-MCP deep research stack for Claude Code. The other six handle search, URL reading, and social discovery:

| MCP | Role |
|---|---|
| **Ref** | Library and API documentation lookup |
| **Exa** | Code-context search, advanced web search, crawling |
| **Exa Answer** | Speed-critical factual lookups (1–2 s) |
| **Jina** | Free-tier web/arxiv/ssrn search, parallel reads, screenshots |
| **gigaxity-deep-research** *(this repo)* | Multi-source search + synthesis with Tongyi 30B |
| **Brightdata fallback** | Last-resort scraper for blocked URLs (CAPTCHA, paywall, Cloudflare) |
| **gptr-mcp** | Social-first research — community knowledge from Reddit, X/Twitter, YouTube |

The bundled [`research-workflow`](skills/research-workflow/) skill plus the instruction block in [`CLAUDE.md`](CLAUDE.md) wire all seven together with a query classifier (quick factual, direct, exploratory, synthesis, social-first), so the agent picks the right tools per query class on its own. Drop the instruction block into your own global `CLAUDE.md` or `AGENTS.md` to mirror the setup. All seven sanitized MCP server configs live in [`docs/reference/mcp-configs.md`](docs/reference/mcp-configs.md) for one-stop copy-paste.

## Documentation

- [Introduction](docs/introduction.md): what this is, why it exists, where it fits
- [Quickstart](docs/quickstart.md): five-minute MCP install
- [Concepts: architecture](docs/concepts/architecture.md): how the pipeline works
- [Concepts: presets](docs/concepts/presets.md): `fast`, `tutorial`, `academic`, `comprehensive`, `contracrow`
- [Concepts: focus modes](docs/concepts/focus-modes.md)
- [Concepts: fallback chains](docs/concepts/fallback-chains.md): how Brightdata, Jina, and the rest chain on URL/search/synthesis failures
- [Guide: MCP setup for Claude Code](docs/guides/setup-mcp.md)
- [Guide: REST API setup for distributed compute](docs/guides/setup-rest.md)
- [Guide: Local inference (Tongyi self-host)](docs/guides/setup-local-inference.md)
- [Guide: Bundled companions setup (SearXNG, Exa Answer, Brightdata)](docs/guides/setup-companions.md)
- [Guide: Triple Stack — full deep research setup](docs/guides/triple-stack-setup.md)
- [Guide: Free-tier strategy](docs/guides/free-tier-strategy.md): configuring the search MCPs against each provider's free tier
- [Reference: MCP tools](docs/reference/mcp-tools.md): input/output reference for the six stdio MCP tools this server exposes
- [Reference: MCP configs](docs/reference/mcp-configs.md): sanitized JSON blocks for all seven MCPs in the stack, in one place
- [Reference: REST API](docs/reference/rest-api.md)
- [Reference: Configuration](docs/reference/configuration.md): `RESEARCH_*` env vars for this server
- [Troubleshooting](docs/troubleshooting.md)

## Research foundations

The pipeline implements techniques from the recent literature:

| Feature | Research basis |
|---|---|
| Quality gate | CRAG (arXiv:2401.15884) |
| Contradiction detection | PaperQA2 (arXiv:2409.13740) |
| Query expansion | HyDE (arXiv:2212.10496) |
| Query decomposition | Multi-hop retrieval (arXiv:2507.00355) |
| Outline-guided synthesis | SciRAG (arXiv:2511.14362) |

## Roadmap

| Status | Feature | Description |
|---|---|---|
| :white_check_mark: | OpenRouter mode | Default on `main` |
| :white_check_mark: | Local inference mode | Default on this branch — generic OpenAI-compatible client, ships with vLLM/SGLang/Ollama-friendly defaults |
| :white_check_mark: | MCP + REST surfaces | Both stable, share orchestration logic |
| :white_check_mark: | search · research · ask · discover · synthesize · reason | All six tools wired and tested |
| :white_check_mark: | Multi-tenant via per-request key | `X-LLM-Api-Key` header passthrough |
| :construction: | Self-hosted Tongyi guide | vLLM and SGLang reference deployments — see [setup-local-inference.md](docs/guides/setup-local-inference.md) |
| :memo: | Streaming responses | SSE for `synthesize` / `reason` long-running calls |
| :memo: | Pluggable rerankers | Optional Jina or Cohere rerank stage between fusion and synthesis |

:white_check_mark: Shipped&ensp; :construction: Planned&ensp; :memo: Exploring

## Requirements

- Python 3.11+
- A local OpenAI-compatible inference server: vLLM, SGLang, llama.cpp, or Ollama (or override `RESEARCH_LLM_API_BASE` to point at a hosted endpoint such as OpenRouter)
- A GPU sized for your chosen model — Tongyi DeepResearch 30B fits in a 24 GB consumer GPU (RTX 3090 / 4090 / 5090) at the **Q4_K_M GGUF** quant (~18.5 GB on disk, ~18.9 GB VRAM at runtime; loads on llama.cpp, Ollama, or vLLM — SGLang doesn't load GGUF as of May 2026, use an AWQ or GPTQ build instead). See [`setup-local-inference.md`](docs/guides/setup-local-inference.md#quant-format-support-per-server) for the per-server format matrix and recommended quants. Smaller models (Qwen, Llama 3.x, DeepSeek-R1 distilled) run on lighter hardware.
- A SearXNG instance, self-hosted (https://docs.searxng.org/) or third-party, as the primary search source
- Optional: Tavily API key, LinkUp API key for fallback search
- Optional: Docker + Docker Compose for REST mode

## License

[MIT](LICENSE). Copyright (c) 2026 Yoloshii.
