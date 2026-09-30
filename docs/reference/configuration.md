# Configuration reference

Every environment variable Gigaxity Deep Research reads, what it controls, and what to set it to. All variables are prefixed `RESEARCH_` to avoid collisions in shared shells.

Variables can be set in `.env` (read at startup), in the MCP `env` block of `~/.claude.json` (overrides `.env`), or in the shell environment (overrides both).

## LLM configuration

| Variable | Default | Required? | Notes |
|---|---|---|---|
| `RESEARCH_LLM_API_BASE` | `http://localhost:8000/v1` | No | Any OpenAI-compatible base URL. Defaults match a local vLLM/SGLang server; for llama.cpp's `llama-server` set `http://localhost:8080/v1`; for hosted services set the provider's `/v1` URL. |
| `RESEARCH_LLM_API_KEY` | *(empty)* | **Yes** | Bearer token for the LLM endpoint. For local servers without auth, set any non-empty placeholder. |
| `RESEARCH_LLM_MODEL` | `Qwen/Qwen3-30B-A3B-Thinking-2507` | No | Any model the endpoint serves |
| `RESEARCH_LLM_TEMPERATURE` | `0.85` | No | 0.0–1.0; lower = more deterministic |
| `RESEARCH_LLM_TOP_P` | `0.95` | No | Nucleus sampling parameter |
| `RESEARCH_LLM_MAX_TOKENS` | `16384` | No | Max output length per call |
| `RESEARCH_LLM_TIMEOUT` | `120` | No | Per-request timeout in seconds. This is an httpx **operation** timeout (read/write/pool; connect is a separate 10 s), **not** a wall-clock bound on the call — httpx reapplies the read timeout to every individual network read, so a slowly-trickling response can outlast it. |
| `RESEARCH_LLM_MAX_RETRIES` | `2` | No | Retries the OpenAI SDK performs per request, i.e. `(value + 1)` attempts. This is the SDK's own default, so leaving it unset changes nothing. The SDK retries read-timeouts exactly as it retries 429s, so this **multiplies** `RESEARCH_LLM_TIMEOUT`: one stalled upstream connection costs roughly `(retries + 1) x timeout` of silent retrying, which is how a chain outlives an MCP client's abort deadline while the server never gets to report a failure. Lowering it shortens that stall **and gives up a recovery attempt** — a request whose first attempts time out and whose last succeeds fails at `0`. |
| `RESEARCH_LLM_WALL_CLOCK_CAP` | `0` (disabled) | No | Absolute wall-clock ceiling in seconds for **one** model call, including the SDK's internal retry chain. A **server-wide resource policy**: it applies uniformly to stdio MCP, HTTP MCP, REST and library calls **made through the project's client wrapper**, because a caller-dependent ceiling would mean identical work timing out on one surface and not another. It does *not* reach a client injected into `SynthesisEngine` — such a caller owns its own timeouts. It is *not* derived from `RESEARCH_LLM_TIMEOUT x attempts` — that product is not a wall-clock bound — so a positive value may deliberately interrupt a retry the SDK would otherwise finish. **Disabled by default** because the right ceiling depends on the endpoint: a hosted model's generation time is bounded, a slow local server's is not, and shipping a ceiling that kills healthy local generation is a worse failure than having none. `480` is a reasonable starting profile for a hosted endpoint. Progress notifications are emitted regardless of this setting, so idle-timeout survival does not depend on it. |
| `RESEARCH_PROGRESS_HEARTBEAT_INTERVAL` | `30` | No | Seconds between "still running" progress notifications while a model call is in flight. Entry and settlement notifications cannot subdivide a single completion, and a non-streaming call is silent for its whole duration, so this is what keeps a slow generation from reading as an idle connection. A call shorter than one interval emits none. Lower it if your client's idle window is tight. |
| `RESEARCH_PROGRESS_SEND_TIMEOUT` | `10` | No | Seconds a single progress notification may take before reporting is disabled for the rest of the request. The reporter holds its serialization lock across the send, so an unbounded send to a transport that *hangs* rather than errors would block every concurrent call — turning the reporting machinery into the stall it exists to prevent. |
| `RESEARCH_RCS_CONCURRENCY` | `4` | No | Max concurrent RCS contextual-summary calls in the synthesis pipeline. Per-source summaries are independent LLM calls; higher values cut wall-clock latency over many sources. Tune up (16–32) for hosted endpoints that accept more parallelism; values <1 are floored to 1 (serial). |
| `RESEARCH_FAIL_OPEN_MIN_SOURCE_SCORE` | `0.3` | No | Pre-synthesis relevance-gate fail-open floor. On a REJECT (or PARTIAL-with-zero-good) decision, if at least one source scores ≥ this, `synthesize` fails open — it synthesizes over the set-aside sources, opens the answer with a `low source relevance (fail-open)` caveat, and marks the result non-cacheable, instead of returning `## Source quality insufficient`. Below the floor (no source clears it) the gate still refuses. Default `0.3` equals the REJECT threshold; raise to refuse more aggressively, lower to fail open over weaker corpora. |

### Common model values

vLLM / SGLang / llama.cpp (local — default on this branch):
- `Qwen/Qwen3-30B-A3B-Thinking-2507` (HF model ID; `llama-server` exposes whatever alias it derives from the GGUF)

Hosted endpoints (OpenRouter and similar):
- `qwen/qwen3-30b-a3b-thinking-2507` — OpenRouter slug, reasoning-tuned for research
- `deepseek/deepseek-r1` — reasoning model, similar capability profile
- `qwen/qwen-qwq-32b-preview` — Qwen reasoning variant
- `anthropic/claude-3.5-sonnet` — non-reasoning, but very strong synthesis

## Search configuration

| Variable | Default | Required? | Notes |
|---|---|---|---|
| `RESEARCH_SEARXNG_HOST` | `http://localhost:8888` | **Yes** | URL of your SearXNG instance |
| `RESEARCH_SEARXNG_ENGINES` | *(empty)* | No | Leave empty so the instance's own settings choose the engines for each category. A non-empty comma-separated list is sent as `engines=` on the base `general` lane, which **overrides the instance's `disabled:` flags** and pins that lane to exactly those engines. Vertical lanes and `vertical_search` never send it — SearXNG would add the named engines to the category. Set it only to pin or debug specific engines. (Before v0.14.0 the default was `brave,duckduckgo,startpage,mojeek,wikipedia`; set that value to restore the old behaviour.) |
| `RESEARCH_SEARXNG_CATEGORIES` | `general` | No | Categories for the base SearXNG lane |
| `RESEARCH_SEARXNG_VERTICAL_ROUTING` | `true` | No | Add a second SearXNG list from one category — `science`, `it` or `videos` — when the focus mode asks for it or, with no focus mode, a keyword heuristic matches — and only when the instance's `/config` shows an enabled engine in that category. It fuses beside the base lane as `searxng:<category>`. `false` keeps the base lane only. See [Vertical lanes](#vertical-lanes) |
| `RESEARCH_SEARXNG_LANGUAGE` | `en` | No | ISO 639-1 code |
| `RESEARCH_SEARXNG_SAFESEARCH` | `0` | No | 0=off, 1=moderate, 2=strict |

### SearXNG host options

- **Self-host via Docker** — see [setup-mcp.md](../guides/setup-mcp.md#setting-up-searxng)
- **Public instance** — pick from https://searx.space/ (verify JSON API enabled)
- **Local network** — point at any reachable SearXNG with JSON API enabled

### Vertical lanes

`search`, `research` and `discover` (MCP and REST) query SearXNG's `general` category on every call, and add at most ONE vertical lane:

| Focus mode | Lane |
|---|---|
| `academic` | `science` |
| `debugging`, `documentation` | `it` |
| `tutorial` | `videos` |
| `general` | none — the general lane only |
| `comparison`, `news`, or none given | keyword heuristic: paper / arXiv / peer-reviewed / DOI → `science`; error / traceback / crash / "not working" → `it`; video / tutorial / walkthrough / how-to → `videos`; otherwise none |

`vertical_search` (MCP tool and `POST /api/v1/vertical-search`) queries one category directly: `videos`, `science`, `it`, `docs`, `packages` or `general`. `science`, `it` and `videos` are stock SearXNG categories. `docs` and `packages` are defined by the bundled [`companions/searxng/settings.yml.example`](../../companions/searxng/settings.yml.example). SearXNG answers a category it does not define from its default categories, without an error, and labels results with each engine's primary category — so the server reads the instance's `/config` (cached for ten minutes) to learn which categories have an enabled engine. A lane runs only for a category confirmed there; `vertical_search` skips a missing category and says so, and when `/config` cannot be read it queries anyway and flags the result as unverified. A malformed `/config` counts as unreadable, never as "category missing". The read runs beside the other connectors' searches, once per host however many searches ask at the same time, so a slow `/config` delays only the lane.

A confirmed category is still not proof that a request gets it: a locked `categories` preference on the instance, or a `!bang` in the query, selects other engines. So a lane and `vertical_search` keep only results that an engine of the category returned (per `/config`) and drop the rest; `vertical_search` reports how many.

The lane is the same SearXNG instance as the base list, so a URL both return is kept in the base list only — it is not boosted as if two providers had agreed. A dropped result leaves its rank empty, so the lane results after it keep their own ranks in the fusion.

Every search a tool runs costs one request per configured connector. `discover` runs several (the original query, expansions and gap fills), so with a Parallel key set one discovery bills several Parallel requests; `RESEARCH_PARALLEL_MAX_RESULTS` caps results per request, not requests. Restrict a REST discovery with its `connectors` field.

## Optional additional search connectors

| Variable | Default | Required? | Notes |
|---|---|---|---|
| `RESEARCH_TAVILY_API_KEY` | *(empty)* | No | Tavily — additional parallel source (https://tavily.com) |
| `RESEARCH_TAVILY_SEARCH_DEPTH` | `advanced` | No | `basic` or `advanced` |
| `RESEARCH_LINKUP_API_KEY` | *(empty)* | No | LinkUp — additional parallel source (https://linkup.so) |
| `RESEARCH_BRAVE_API_KEY` | *(empty)* | No | Brave Search — additional parallel source (https://brave.com/search/api/). Official Brave index over a keyed API, so unlike SearXNG's scraped engines it cannot be served a CAPTCHA under automated load. Free tier ~1,000 queries/month, recurring |
| `RESEARCH_BRAVE_COUNTRY` | *(empty)* | No | Optional ISO country code for geo-targeting, e.g. `us` |
| `RESEARCH_BRAVE_SAFESEARCH` | `off` | No | `off`, `moderate`, or `strict` |
| `RESEARCH_LINKUP_DEPTH` | `standard` | No | `standard` or `deep` |
| `RESEARCH_PARALLEL_API_KEY` | *(empty)* | No | Parallel Search — additional parallel source (https://platform.parallel.ai). Parallel's own web index over a keyed API, returning LLM-oriented excerpts; like Brave it cannot be served a CAPTCHA |
| `RESEARCH_PARALLEL_MODE` | `fast` | No | `turbo` or `fast` ($1 per 1,000 requests as of 2026-09), `basic` or `advanced` ($5 per 1,000). Always sent — the API bills `advanced` when `mode` is omitted. An unknown value falls back to `fast` |
| `RESEARCH_PARALLEL_MAX_RESULTS` | `10` | No | Results per request (≥ 1). Ten are in the base price; each extra result bills separately |
| `RESEARCH_PARALLEL_EXCERPT_CHARS` | `1500` | No | Excerpt characters per result (≥ 1) |

When the corresponding API key is empty, the connector is disabled. When set, the connector runs in parallel with SearXNG and contributes to RRF fusion — it is **not** a failover-on-error fallback.

A Parallel `402` (no credit) pauses the connector for an hour and a `429` for a minute, so a fused search does not pay a failing round trip on every call; the other connectors carry on.

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

### Local inference with llama.cpp's `llama-server`

```bash
RESEARCH_LLM_API_BASE=http://localhost:8080/v1
RESEARCH_LLM_API_KEY=local-anything
RESEARCH_LLM_MODEL=Qwen3-30B-A3B-Thinking-2507   # alias llama-server reports
RESEARCH_SEARXNG_HOST=http://localhost:8888
```

### Local inference (vLLM on a different machine)

```bash
RESEARCH_LLM_API_BASE=http://192.0.2.50:8000/v1   # example LAN IP (RFC 5737 TEST-NET-1)
RESEARCH_LLM_API_KEY=local-anything
RESEARCH_LLM_MODEL=Qwen/Qwen3-30B-A3B-Thinking-2507
RESEARCH_SEARXNG_HOST=http://192.0.2.10:8888   # example SearXNG on yet another machine (RFC 5737)
RESEARCH_HOST=127.0.0.1
RESEARCH_PORT=8001    # if running the orchestrator's REST mode on the same host
```

### Hosted endpoint (OpenRouter from this branch)

```bash
RESEARCH_LLM_API_BASE=https://openrouter.ai/api/v1
RESEARCH_LLM_API_KEY=sk-or-v1-your-key-placeholder
RESEARCH_LLM_MODEL=qwen/qwen3-30b-a3b-thinking-2507
RESEARCH_SEARXNG_HOST=http://localhost:8888
```

### Hosted endpoint + Tavily as the search source (no SearXNG)

```bash
RESEARCH_LLM_API_BASE=https://openrouter.ai/api/v1
RESEARCH_LLM_API_KEY=sk-or-v1-your-key-placeholder
RESEARCH_LLM_MODEL=qwen/qwen3-30b-a3b-thinking-2507
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
- `RESEARCH_LLM_MAX_RETRIES` < 0 → `ValidationError`
- `RESEARCH_PROGRESS_HEARTBEAT_INTERVAL` < 1 → `ValidationError`
- `RESEARCH_PROGRESS_SEND_TIMEOUT` < 1 → `ValidationError`
- `RESEARCH_SEARXNG_SAFESEARCH` not in {0, 1, 2} → `ValidationError`
- `RESEARCH_PARALLEL_MAX_RESULTS` or `RESEARCH_PARALLEL_EXCERPT_CHARS` < 1 → `ValidationError`

Empty optional keys are accepted as "disabled."
