# Configuration reference

Every environment variable Gigaxity Deep Research reads, what it controls, and what to set it to. All variables are prefixed `RESEARCH_` to avoid collisions in shared shells.

Variables can be set in `.env` (read at startup), in the MCP `env` block of `~/.claude.json` (overrides `.env`), or in the shell environment (overrides both).

## LLM configuration

| Variable | Default | Required? | Notes |
|---|---|---|---|
| `RESEARCH_LLM_API_BASE` | `http://localhost:8000/v1` | No | Any OpenAI-compatible base URL. Defaults match a local vLLM/SGLang server; for Ollama set `http://localhost:11434/v1`; for hosted services set the provider's `/v1` URL. |
| `RESEARCH_LLM_API_KEY` | *(empty)* | **Yes** | Bearer token for the LLM endpoint. For local servers without auth, set any non-empty placeholder. |
| `RESEARCH_LLM_MODEL` | `Alibaba-NLP/Tongyi-DeepResearch-30B-A3B-Thinking` | No | Any model the endpoint serves |
| `RESEARCH_LLM_TEMPERATURE` | `0.85` | No | 0.0–1.0; lower = more deterministic |
| `RESEARCH_LLM_TOP_P` | `0.95` | No | Nucleus sampling parameter |
| `RESEARCH_LLM_MAX_TOKENS` | `16384` | No | Max output length per call |
| `RESEARCH_LLM_TIMEOUT` | `120` | No | Seconds before LLM call times out |

### Common model values

vLLM / SGLang / Ollama (local — default on this branch):
- `Alibaba-NLP/Tongyi-DeepResearch-30B-A3B-Thinking` (HF model ID)
- `tongyi-deepresearch:30b-q4` (Ollama tag)

Hosted endpoints (OpenRouter and similar):
- `alibaba/tongyi-deepresearch-30b-a3b` — OpenRouter slug, reasoning-tuned for research
- `deepseek/deepseek-r1` — reasoning model, similar capability profile
- `qwen/qwen-qwq-32b-preview` — Qwen reasoning variant
- `anthropic/claude-3.5-sonnet` — non-reasoning, but very strong synthesis

## Search configuration

| Variable | Default | Required? | Notes |
|---|---|---|---|
| `RESEARCH_SEARXNG_HOST` | `http://localhost:8888` | **Yes** | URL of your SearXNG instance |
| `RESEARCH_SEARXNG_ENGINES` | `brave,bing,duckduckgo,startpage,mojeek,wikipedia` | No | Comma-separated SearXNG engine names. Matches the bundled `companions/searxng/settings.yml.example` enabled list. Google is disabled by default in the bundled SearXNG settings (broken on aggregator traffic since Oct 2025). |
| `RESEARCH_SEARXNG_CATEGORIES` | `general` | No | Comma-separated SearXNG categories |
| `RESEARCH_SEARXNG_LANGUAGE` | `en` | No | ISO 639-1 code |
| `RESEARCH_SEARXNG_SAFESEARCH` | `0` | No | 0=off, 1=moderate, 2=strict |

### SearXNG host options

- **Self-host via Docker** — see [setup-mcp.md](../guides/setup-mcp.md#setting-up-searxng)
- **Public instance** — pick from https://searx.space/ (verify JSON API enabled)
- **Local network** — point at any reachable SearXNG with JSON API enabled

## Optional fallback search

| Variable | Default | Required? | Notes |
|---|---|---|---|
| `RESEARCH_TAVILY_API_KEY` | *(empty)* | No | Tavily fallback (https://tavily.com) |
| `RESEARCH_TAVILY_SEARCH_DEPTH` | `advanced` | No | `basic` or `advanced` |
| `RESEARCH_LINKUP_API_KEY` | *(empty)* | No | LinkUp fallback (https://linkup.so) |
| `RESEARCH_LINKUP_DEPTH` | `standard` | No | `standard` or `deep` |

When the corresponding API key is empty, the connector is disabled. When set, the connector runs in parallel with SearXNG and contributes to RRF fusion.

## Search aggregation

| Variable | Default | Required? | Notes |
|---|---|---|---|
| `RESEARCH_DEFAULT_TOP_K` | `10` | No | Results requested per source |
| `RESEARCH_RRF_K` | `60` | No | RRF fusion constant; higher = less aggressive top-result dominance |

## Server (REST mode only)

These are ignored when running as MCP stdio.

| Variable | Default | Required? | Notes |
|---|---|---|---|
| `RESEARCH_HOST` | `127.0.0.1` | No | Bind address (default loopback) |
| `RESEARCH_PORT` | `8000` | No | Port |

For `RESEARCH_HOST`:
- `127.0.0.1` (default) — loopback only. Pair with an authenticated reverse proxy on the same host if the service needs external reach.
- `0.0.0.0` — all interfaces. Use only behind an authenticated reverse proxy. The REST surface spends the env-configured LLM key for any unauthenticated caller that reaches it.

## Common .env templates

### Minimum viable (local vLLM/SGLang on the default port + local SearXNG)

```bash
RESEARCH_LLM_API_KEY=local-anything    # placeholder; required to be non-empty
RESEARCH_SEARXNG_HOST=http://localhost:8888
```

### Local inference with Ollama

```bash
RESEARCH_LLM_API_BASE=http://localhost:11434/v1
RESEARCH_LLM_API_KEY=local-anything
RESEARCH_LLM_MODEL=tongyi-deepresearch:30b-q4
RESEARCH_SEARXNG_HOST=http://localhost:8888
```

### Local inference (vLLM on a different machine)

```bash
RESEARCH_LLM_API_BASE=http://192.0.2.50:8000/v1   # example LAN IP (RFC 5737 TEST-NET-1)
RESEARCH_LLM_API_KEY=local-anything
RESEARCH_LLM_MODEL=Alibaba-NLP/Tongyi-DeepResearch-30B-A3B-Thinking
RESEARCH_SEARXNG_HOST=http://192.0.2.10:8888   # example SearXNG on yet another machine (RFC 5737)
RESEARCH_HOST=127.0.0.1
RESEARCH_PORT=8001    # if running the orchestrator's REST mode on the same host
```

### Hosted endpoint (OpenRouter from this branch)

```bash
RESEARCH_LLM_API_BASE=https://openrouter.ai/api/v1
RESEARCH_LLM_API_KEY=sk-or-v1-your-key-placeholder
RESEARCH_LLM_MODEL=alibaba/tongyi-deepresearch-30b-a3b
RESEARCH_SEARXNG_HOST=http://localhost:8888
```

### Hosted endpoint + Tavily fallback (no SearXNG)

```bash
RESEARCH_LLM_API_BASE=https://openrouter.ai/api/v1
RESEARCH_LLM_API_KEY=sk-or-v1-your-key-placeholder
RESEARCH_LLM_MODEL=alibaba/tongyi-deepresearch-30b-a3b
RESEARCH_SEARXNG_HOST=http://localhost:8888    # required even if unreachable; aggregator handles failure
RESEARCH_TAVILY_API_KEY=tvly-your-key-placeholder
```

## Precedence

When the same variable appears in multiple places, precedence is:

1. Shell environment (highest)
2. MCP `env` block in `~/.claude.json`
3. `.env` file
4. Defaults in `src/config.py` (lowest)

In practice, this means:

- For **MCP setup**, put values in the MCP `env` block (visible in your Claude Code config)
- For **REST/Docker setup**, put values in `.env` (gitignored)
- For **one-off testing**, set in shell

## Validation

`src/config.py` uses `pydantic-settings` to validate at startup. Bad values fail loudly:

- Numeric variables that aren't parseable → `ValidationError`
- `RESEARCH_LLM_TIMEOUT` < 1 → `ValidationError`
- `RESEARCH_SEARXNG_SAFESEARCH` not in {0, 1, 2} → `ValidationError`

Empty optional keys are accepted as "disabled."
