# Setting up the REST API for distributed compute

Run Gigaxity Deep Research as a long-lived REST service. This is the right setup when:

- The orchestrator (the thing calling research tools) and the model server live on different machines
- Multiple users or services share one research backend
- You want to put the research engine behind an API gateway or reverse proxy
- You're integrating from a non-MCP environment (a web app, a Slack bot, a CI job)

If you're a single Claude Code user on one machine, use [the MCP setup](setup-mcp.md) instead — it's simpler.

## Prerequisites

- Docker + Docker Compose, OR Python 3.11+ if running natively
- An OpenRouter API key (or another OpenAI-compatible LLM endpoint)
- A SearXNG instance reachable from the server

## Option A: Docker (recommended)

```bash
git clone https://github.com/yoloshii/gigaxity-deep-research.git
cd gigaxity-deep-research

cp .env.example .env
# Edit .env with your keys

docker compose up -d
```

The compose file starts the server bound to `127.0.0.1:8000` by default — loopback only. Override the port mapping in `docker-compose.yml` only after putting the service behind an authenticated reverse proxy. Verify:

```bash
curl http://localhost:8000/api/v1/health
```

Expected response:

```json
{
  "status": "healthy",
  "connectors": ["searxng"],
  "llm_configured": true
}
```

`connectors` lists the active connector names (any of `searxng`, `tavily`, `linkup` whose configuration is complete). `llm_configured` reflects whether `RESEARCH_LLM_API_KEY` is set; the env-configured base URL alone is not sufficient.

## Option B: Native Python

```bash
git clone https://github.com/yoloshii/gigaxity-deep-research.git
cd gigaxity-deep-research

python -m venv .venv
source .venv/bin/activate
pip install -e .

cp .env.example .env
# Edit .env

uvicorn src.main:app --host 127.0.0.1 --port 8000
```

`--host 127.0.0.1` (loopback) is the safe default — the REST surface spends the env-configured OpenRouter key for any caller that reaches it. Bind `--host 0.0.0.0` only behind an authenticated reverse proxy (see the "Reverse proxy and TLS" section below).

Add `--reload` during development for autoreload on source changes.

## Endpoint surface

The REST surface mirrors the six MCP tools (two primitives plus four deep-research tools) and adds enhanced synthesis variants:

| Method | Path | Body | Notes |
|---|---|---|---|
| GET | `/api/v1/health` | — | Health + connector status |
| POST | `/api/v1/search` | `{query, top_k?, connectors?}` | Multi-source search only, no LLM |
| POST | `/api/v1/research` | `{query, top_k?, reasoning_effort?, preset?, focus_mode?}` | Combined search + synthesis |
| POST | `/api/v1/ask` | `{query, context?}` | Quick conversational answer (direct LLM, no search) |
| POST | `/api/v1/discover` | `{query, focus_mode?, identify_gaps?, top_k?}` | Exploratory expansion + gap detection |
| POST | `/api/v1/synthesize` | `{query, sources, style?, max_tokens?}` | Citation-aware synthesis over pre-gathered content |
| POST | `/api/v1/synthesize/enhanced` | `{query, sources, run_quality_gate?, detect_contradictions?, verify_citations?}` | P0-stack synthesis (REST-only — not exposed via the HTTP MCP tool list) |
| POST | `/api/v1/synthesize/p1` | `{query, sources, preset?, ...}` | P1-stack synthesis (REST-only — not exposed via the HTTP MCP tool list) |
| POST | `/api/v1/reason` | `{query, sources}` | CoT synthesis over pre-gathered sources. No `style` — use `/api/v1/synthesize` for prose-style variants. |
| GET | `/api/v1/presets` | — | List the five synthesis presets |
| GET | `/api/v1/focus-modes` | — | List the seven focus modes |

The HTTP MCP transport (mounted at `/mcp`) exposes the same six tools as the stdio MCP. The two enhanced synthesis variants are **REST-only** — callers who need them hit the HTTP endpoint directly.

Full schemas: [reference/rest-api.md](../reference/rest-api.md).

## Multi-tenant via per-request keys

Send `X-OpenRouter-Api-Key: <key>` on any POST. The server forwards that key to OpenRouter for that single request, bypassing `RESEARCH_LLM_API_KEY`.

```bash
curl -X POST http://localhost:8000/api/v1/ask \
  -H "Content-Type: application/json" \
  -H "X-OpenRouter-Api-Key: sk-or-v1-tenant-key-placeholder" \
  -d '{"query": "What is the OpenRouter rate limit?"}'
```

Use this when your front-end already collects user OpenRouter keys and you want each user's calls billed to their own account.

## Distributed compute pattern

When the LLM server lives on a different machine than the orchestrator (e.g. a GPU box with vLLM/SGLang serving Tongyi 30B locally and a CPU-only edge node running everything else):

```
Orchestrator (this server, CPU)  ──HTTP──▶  Model server (GPU, vLLM/SGLang OpenAI-compat)
       ▲
       │ HTTP
       │
   Your app / agent / Claude Code adapter
```

Set `RESEARCH_LLM_API_BASE` to point at the model server's OpenAI-compatible endpoint, and `RESEARCH_LLM_API_KEY` to whatever auth token the model server expects. **It must be non-empty** — every entrypoint calls `settings.require_llm_key()` and fails fast on an empty key. For open endpoints, set it to any placeholder (`local-anything`, `na`, etc.).

Example for vLLM:

```bash
RESEARCH_LLM_API_BASE=http://192.0.2.50:8000/v1
RESEARCH_LLM_API_KEY=local-anything   # placeholder — see note above
RESEARCH_LLM_MODEL=alibaba/tongyi-deepresearch-30b-a3b
```

See [setup-local-inference.md](setup-local-inference.md) for the model-server side.

## Reverse proxy and TLS

The server has no built-in auth or TLS. For anything beyond localhost, put it behind a reverse proxy (nginx, Caddy, Traefik) that terminates TLS and enforces auth.

Example Caddyfile:

```caddy
research.example.com {
  reverse_proxy localhost:8000
  basicauth /api/* {
    user $2a$14$bcrypt-hash-of-password
  }
  # Strip the per-request key from access logs
  log {
    output file /var/log/caddy/research.log
    format filter {
      request>headers>X-OpenRouter-Api-Key delete
    }
  }
}
```

Stripping the per-request OpenRouter key from access logs is important if you accept multi-tenant traffic.

## Security hardening checklist

- [ ] Bind to `127.0.0.1` if behind a reverse proxy on the same host (`RESEARCH_HOST=127.0.0.1`)
- [ ] Reverse proxy enforces TLS and authentication
- [ ] Reverse proxy strips `X-OpenRouter-Api-Key` from access logs
- [ ] `.env` file is `0600` and not in source control (the shipped `.gitignore` already excludes it)
- [ ] Container runs as non-root (the shipped `Dockerfile` does this)
- [ ] OpenRouter key in env, not baked into image layers

## What's next

- [Local inference setup](setup-local-inference.md) — host Tongyi 30B yourself
- [Triple Stack setup](triple-stack-setup.md) — wire the full deep research stack
- [REST API reference](../reference/rest-api.md) — full request/response schemas
