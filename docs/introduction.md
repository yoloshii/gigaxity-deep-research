# Introduction to Gigaxity Deep Research

> **You are reading this on the `local-inference` branch.** The default LLM endpoint is a self-hosted OpenAI-compatible server (vLLM, SGLang, llama.cpp, Ollama). The hosted-OpenRouter framing in the rest of this page applies on `main`; this branch swaps the default to local inference but the synthesis pipeline is identical.

Gigaxity Deep Research is an MCP server that gives Claude Code (and other MCP-compatible agents) a deep research capability — multi-source search, citation-aware synthesis, contradiction detection, and chain-of-thought reasoning — backed by [Tongyi DeepResearch 30B](https://huggingface.co/Alibaba-NLP/Tongyi-DeepResearch-30B-A3B-Thinking) running locally on this branch (or hosted on [OpenRouter](https://openrouter.ai/) when configured for that). It exposes six tools — two primitives (`search`, `research`) plus four deep-research tools (`ask`, `discover`, `synthesize`, `reason`) — over both an MCP stdio surface and a FastAPI REST API.

This page covers what the project is, the problems it solves, and where it sits in the broader seven-MCP deep research stack.

## What problem does it solve?

Modern coding agents hit two recurring walls:

1. **Knowledge cutoff.** The model was trained months or years ago. Library versions, API surfaces, vulnerabilities, recent papers — all outside the cutoff.
2. **Generic web search isn't enough.** A single search engine returns ranked links, but the agent still has to read each one, reconcile contradictions, and synthesize an answer. That work is repetitive, expensive in context, and easy to get wrong.

Gigaxity Deep Research handles the second wall by running the search-read-synthesize loop server-side against a reasoning-tuned model. The agent calls one tool with a question, and gets back a citation-backed answer plus flagged contradictions and detected gaps. The first wall (knowledge cutoff) gets handled implicitly: the synthesis is grounded in live web sources rather than the LLM's pretrained corpus.

## What does the pipeline look like?

```
Query → Discovery layer (route, expand, decompose, focus)
      → Search aggregator (SearXNG required; Tavily, LinkUp optional — all configured connectors run in parallel)
      → RRF fusion (rank-merge across providers)
      → Synthesis layer (CRAG quality gate, contradiction detection, outline-guided generation)
      → OpenAI-compatible LLM (Tongyi 30B by default)
      → Citation-bound answer
```

The same pipeline serves both MCP and REST surfaces. The MCP surface is what Claude Code talks to over stdio; the REST surface is for distributed-compute setups where the orchestrator and the model server live on different machines.

## Where does it fit?

This server is one of seven MCPs in the **deep research stack** — a configuration that turns Claude Code into a deep-research-first environment. The middle three (`Ref` + `exa` + `jina`) form the **Triple Stack** — the search/docs/code trio that does most of the heavy retrieval.

| MCP | Role | Relationship to this server |
|---|---|---|
| `Ref` | Library and API documentation (Triple Stack) | Used before this server when the answer is in official docs |
| `exa` | Code-context search, advanced web (Triple Stack) | Used in parallel with this server's discovery layer for cross-validation |
| `jina` | Free-tier web/arxiv/ssrn search (Triple Stack) | Provides the URL-reading layer that feeds this server's `synthesize` |
| `exa-answer` | Speed-critical 1–2 s factual lookups | Substitutes for `ask` when latency is the only thing that matters |
| **`gigaxity-deep-research`** | This server — multi-source synthesis with Tongyi 30B | The core synthesis engine |
| `brightdata_fallback` | Last-resort scraper for blocked URLs | Handles the long tail of CAPTCHA/paywall/Cloudflare pages |
| `gptr-mcp` | Social-first research — community knowledge from Reddit, X, YouTube | Surfaces lived-experience content the rest of the stack misses |

The bundled [`research-workflow` skill](../skills/research-workflow/SKILL.md) and the pasteable instruction block in [`CLAUDE.md`](../CLAUDE.md) wire all seven together with classification logic. You can use this server alone — it's self-contained — but the full experience comes from the stack.

## What models does it work with?

Default on this branch is `Alibaba-NLP/Tongyi-DeepResearch-30B-A3B-Thinking` running on a local OpenAI-compatible server (vLLM, SGLang, llama.cpp, Ollama), chosen for its reasoning-tuned multi-hop research behavior. The synthesis pipeline is model-agnostic, so any OpenAI-compatible chat-completions model works:

- DeepSeek-R1 (and any reasoning variant)
- Qwen-QwQ
- Llama-3.x (locally hosted or via a hosted endpoint)
- Anthropic / OpenAI / Gemini via OpenRouter or another aggregator
- A self-hosted model exposed over an OpenAI-compatible endpoint (vLLM, SGLang, Ollama, llama.cpp)

Switch models by setting `RESEARCH_LLM_MODEL` in `.env` or the MCP `env` block. No code changes needed.

## What are the modes?

| Mode | Branch | When to use |
|---|---|---|
| OpenRouter | `main` | Default on `main`. Single machine, no GPU, fastest path. |
| Local inference | `local-inference` | Default on this branch. Self-hosted model, on-prem, no usage cost. |
| REST distributed | both | Orchestrator and model on different machines |

See [docs/guides/setup-mcp.md](guides/setup-mcp.md), [docs/guides/setup-rest.md](guides/setup-rest.md), and [docs/guides/setup-local-inference.md](guides/setup-local-inference.md) for setup per mode.

## Where to next

- [Quickstart](quickstart.md) — five-minute MCP install
- [Architecture](concepts/architecture.md) — how the pipeline works under the hood
- [Triple Stack setup](guides/triple-stack-setup.md) — wire up the full seven-MCP deep research stack (Triple Stack + four)
