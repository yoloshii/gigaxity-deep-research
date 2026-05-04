# Gigaxity Deep Research — Open-source deep research MCP server for Claude Code, Hermes, and Cursor

**Open-source deep research MCP server for Claude Code, Hermes, Cursor, and any MCP-compatible agent.** [Tongyi DeepResearch 30B](https://huggingface.co/Alibaba-NLP/Tongyi-DeepResearch-30B-A3B-Thinking) via [OpenRouter](https://openrouter.ai/) plus multi-source web synthesis with citations.

Gigaxity Deep Research wraps Alibaba's Tongyi DeepResearch 30B, a model purpose-built for agentic research, and exposes it as six MCP tools — two primitives (`search`, `research`) plus four deep-research tools (`ask`, `discover`, `synthesize`, `reason`) — with a matching FastAPI REST surface. The synthesis layer pulls from a "Triple Stack" of complementary search MCPs ([Ref](https://ref.tools), [Exa](https://exa.ai), [Jina](https://jina.ai)) alongside [SearXNG](https://github.com/searxng/searxng), [Tavily](https://tavily.com), and [LinkUp](https://linkup.so) connectors, then merges results via reciprocal rank fusion with citation binding and contradiction detection on top. A bundled [GPT Researcher](https://github.com/assafelovic/gptr-mcp) (`gptr-mcp`) companion adds Reddit, X, and YouTube as social-first sources.

If you want to run the synthesis model on your own hardware, the `local-inference` branch swaps OpenRouter for any OpenAI-compatible endpoint (Ollama, llama.cpp, vLLM, SGLang). The search-MCP layer is priced separately by each provider. See [`docs/guides/free-tier-strategy.md`](docs/guides/free-tier-strategy.md) for what their free tiers cover and how to wire them up.

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
- **Multi-tenant**: accepts a per-request `X-OpenRouter-Api-Key` header so multiple users can share one server instance and bill their own OpenRouter accounts.
- **MCP and REST**: the same orchestration logic powers both surfaces.

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
# Edit .env: set RESEARCH_LLM_API_KEY (https://openrouter.ai/keys)
#            and RESEARCH_SEARXNG_HOST (or leave default)
```

Add to `~/.claude.json` under `mcpServers`:

```json
"gigaxity-deep-research": {
  "type": "stdio",
  "command": "/path/to/gigaxity-deep-research/.venv/bin/python",
  "args": ["/path/to/gigaxity-deep-research/run_mcp.py"],
  "env": {
    "RESEARCH_LLM_API_BASE": "https://openrouter.ai/api/v1",
    "RESEARCH_LLM_API_KEY": "YOUR_OPENROUTER_API_KEY",
    "RESEARCH_LLM_MODEL": "alibaba/tongyi-deepresearch-30b-a3b"
  }
}
```

Restart Claude Code. The six tools (`search`, `research`, `ask`, `discover`, `synthesize`, `reason`) become callable as `mcp__gigaxity-deep-research__<tool>`.

To unlock the full deep research workflow described in [`CLAUDE.md`](CLAUDE.md), install the bundled `research-workflow` skill from [`skills/research-workflow/`](skills/research-workflow/) and the companion services in [`companions/`](companions/). The companions cover SearXNG (docker compose), an Exa Answer wrapper, and a Brightdata fallback wrapper. Step-by-step in [`docs/guides/setup-companions.md`](docs/guides/setup-companions.md). For the two fully hosted MCPs (Ref, Jina) plus the main Exa MCP, see [`docs/guides/triple-stack-setup.md`](docs/guides/triple-stack-setup.md).

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

Each endpoint accepts an optional `X-OpenRouter-Api-Key` header that overrides the env-configured key for that request. Multi-tenant deployments use it to bill each user separately.

Full REST reference: [`docs/reference/rest-api.md`](docs/reference/rest-api.md).

## Modes

| Mode | Branch | LLM backend | When to use |
|---|---|---|---|
| **OpenRouter (default)** | `main` | Hosted Tongyi DeepResearch 30B via OpenRouter | Single-machine setup, no GPU, fastest path to working |
| **Local inference** *(env-override today; code-level swap pending — see Roadmap)* | `local-inference` *(placeholder branch, currently mirrors `main`)* | Self-hosted Tongyi/DeepSeek/Qwen via vLLM, SGLang, or any OpenAI-compatible server | On-prem requirement, GPU available, no usage-based cost. Works on either branch today by setting `RESEARCH_LLM_API_BASE` to any OpenAI-compatible endpoint. |
| **REST API (any backend)** | both | Either, plus optional remote model server | Distributed compute — orchestrator and model on different machines |

The `local-inference` branch currently mirrors `main` and serves as a placeholder for the planned client-and-default swap. Once the swap lands, that branch will ship with `RESEARCH_LLM_API_BASE` defaulted to `http://localhost:8000/v1` and the OpenRouter client replaced by a generic OpenAI-compatible client. Until then, point `RESEARCH_LLM_API_BASE` at your local endpoint on either branch — search, fusion, synthesis, and citations behave identically.

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
| :white_check_mark: | OpenRouter mode | Default, shipped on `main` |
| :white_check_mark: | MCP + REST surfaces | Both stable, share orchestration logic |
| :white_check_mark: | search · research · ask · discover · synthesize · reason | All six tools wired and tested |
| :white_check_mark: | Multi-tenant via per-request key | `X-OpenRouter-Api-Key` header passthrough |
| :construction: | Local inference branch | Bring-your-own Tongyi/DeepSeek/Qwen, parity with OpenRouter mode. Placeholder branch exists; client-and-default swap pending. |
| :construction: | Self-hosted Tongyi guide | vLLM and SGLang reference deployments |
| :memo: | Streaming responses | SSE for `synthesize` / `reason` long-running calls |
| :memo: | Pluggable rerankers | Optional Jina or Cohere rerank stage between fusion and synthesis |

:white_check_mark: Shipped&ensp; :construction: Planned&ensp; :memo: Exploring

## Requirements

- Python 3.11+
- An OpenRouter API key (https://openrouter.ai/keys) for default mode
- A SearXNG instance, self-hosted (https://docs.searxng.org/) or third-party, as the primary search source
- Optional: Tavily API key, LinkUp API key for fallback search
- Optional: Docker + Docker Compose for REST mode

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup, test commands, and PR guidelines. Bug reports and feature requests use the templates under [`.github/ISSUE_TEMPLATE/`](.github/ISSUE_TEMPLATE/).

## Security

See [SECURITY.md](SECURITY.md) for the threat model and the private vulnerability reporting flow.

## License

[MIT](LICENSE). Copyright (c) 2026 Yoloshii.
