# Setting up local inference (self-hosted Tongyi DeepResearch)

> **Status: env-override path is fully working today on either branch; code-level swap pending (Roadmap → :construction: Local inference branch).** The `local-inference` branch exists as a placeholder and currently mirrors `main` byte-for-byte. The hosting steps below work today against any OpenAI-compatible endpoint by setting `RESEARCH_LLM_API_BASE` on either branch — that path is the supported way to run local inference right now. The dedicated branch with default-config-aware swaps (less env wiring, local-first defaults baked into `src/llm_client.py`) is still in development. Track progress in the [Roadmap section of README.md](../../README.md#roadmap).

The default setup calls Tongyi DeepResearch 30B on OpenRouter — fastest path, no GPU needed, pay-per-call. Once the planned `local-inference` divergence lands, that branch will swap the OpenRouter client for a generic OpenAI-compatible client so the env wiring below collapses to defaults.

This guide covers when to choose local inference, how to host Tongyi 30B (or another reasoning model), and how to wire it back to the synthesis pipeline.

## When to use local inference

- **On-prem requirement** — data must not leave your network
- **Cost predictability** — your usage volume makes per-call pricing more expensive than amortized GPU hosting
- **Latency floor** — round-trip to OpenRouter adds 100–500 ms; local hosting can drop that to single-digit ms
- **GPU you already have** — repurposing existing infra
- **Custom fine-tune** — running your own variant of Tongyi/DeepSeek/Qwen

Otherwise, default OpenRouter mode is simpler.

## Hardware requirements

Tongyi DeepResearch 30B (A3B variant) needs:

- ~60 GB VRAM at FP16
- ~30 GB VRAM at INT8
- ~16 GB VRAM at INT4 (with quality tradeoff)

Single-GPU friendly options:
- 1× A100 80 GB (FP16, comfortable headroom)
- 1× H100 80 GB
- 2× RTX 4090 24 GB (with tensor parallelism, INT8)
- 1× RTX 6000 Ada 48 GB (INT8)

Multi-GPU:
- 2× A100 40 GB (FP16, tensor-parallel)
- 4× RTX 3090 24 GB (FP16, tensor-parallel)

If you have less, drop down a model tier (Tongyi 7B, DeepSeek-R1-Distill-Qwen-14B, Qwen-QwQ-32B at INT4) — the synthesis pipeline is model-agnostic.

## The `local-inference` branch (placeholder today, code swap planned)

```bash
cd gigaxity-deep-research
git checkout local-inference        # exists today, currently identical to main
pip install -e .
```

Right now the branch is a packaging placeholder — it mirrors `main` byte-for-byte so you can pin downstream tooling to it without a code-level divergence. Once the planned divergence lands, the branch will differ from `main` in `src/llm_client.py` (generic OpenAI-compatible client instead of OpenRouter-flavored) and the default `RESEARCH_LLM_API_BASE`. Everything else (search, fusion, synthesis, citations) will stay identical.

**Today**, run the same setup against either branch by pointing the LLM client at your local endpoint. `RESEARCH_LLM_API_KEY` must be non-empty (see the note in the Configure section below); set any placeholder string when your model server doesn't enforce auth:

```bash
RESEARCH_LLM_API_BASE=http://localhost:8000/v1 \
RESEARCH_LLM_API_KEY=local-anything \
RESEARCH_LLM_MODEL=alibaba/Tongyi-DeepResearch-30B-A3B \
python run_mcp.py
```

OpenRouter-specific behavior (per-request `X-OpenRouter-Api-Key` header passthrough) is harmless against most OpenAI-compatible servers.

## Host the model with vLLM

vLLM is the highest-throughput option for OpenAI-compatible serving.

```bash
pip install vllm

# Single-GPU FP16
python -m vllm.entrypoints.openai.api_server \
  --model Alibaba-NLP/Tongyi-DeepResearch-30B-A3B \
  --host 0.0.0.0 \
  --port 8000 \
  --max-model-len 32768

# Multi-GPU tensor-parallel
python -m vllm.entrypoints.openai.api_server \
  --model Alibaba-NLP/Tongyi-DeepResearch-30B-A3B \
  --tensor-parallel-size 2 \
  --host 0.0.0.0 \
  --port 8000

# Quantized (INT4)
python -m vllm.entrypoints.openai.api_server \
  --model Alibaba-NLP/Tongyi-DeepResearch-30B-A3B-AWQ \
  --quantization awq \
  --host 0.0.0.0 \
  --port 8000
```

vLLM exposes `/v1/chat/completions` at the OpenAI-compatible path.

## Host the model with SGLang

SGLang is faster for multi-turn / structured generation workloads and has built-in support for reasoning models.

```bash
pip install "sglang[all]"

python -m sglang.launch_server \
  --model-path Alibaba-NLP/Tongyi-DeepResearch-30B-A3B \
  --host 0.0.0.0 \
  --port 8000
```

## Host the model with Ollama (lower hardware bar)

For modest GPUs (24 GB) or CPU-only experiments, Ollama works with quantized GGUF builds:

```bash
ollama pull tongyi-deepresearch:30b-q4
ollama serve
```

Ollama's OpenAI-compatible endpoint is at `http://localhost:11434/v1`.

## Configure the orchestrator

In `.env`:

```bash
# vLLM / SGLang
RESEARCH_LLM_API_BASE=http://localhost:8000/v1
RESEARCH_LLM_API_KEY=local-anything   # placeholder string — see note below
RESEARCH_LLM_MODEL=Alibaba-NLP/Tongyi-DeepResearch-30B-A3B

# Ollama
RESEARCH_LLM_API_BASE=http://localhost:11434/v1
RESEARCH_LLM_API_KEY=local-anything   # placeholder string — see note below
RESEARCH_LLM_MODEL=tongyi-deepresearch:30b-q4
```

`RESEARCH_LLM_API_KEY` must be **non-empty** because every entrypoint calls `settings.require_llm_key()` and fails fast on an empty key — this is the OpenRouter-mode safety check that prevents the server from coming up without a configured key. For local servers that do not enforce auth, set the variable to any placeholder string (`local-anything`, `na`, etc.). If your model server uses bearer tokens, set this to the actual token value.

## Smoke test

```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Alibaba-NLP/Tongyi-DeepResearch-30B-A3B",
    "messages": [{"role":"user","content":"hello"}],
    "max_tokens": 64
  }'
```

If you get a JSON response with `choices[0].message.content`, the model server is healthy.

Then start the orchestrator:

```bash
python run_mcp.py < /dev/null   # MCP mode
# or
uvicorn src.main:app --port 8001  # REST mode (port 8001 to avoid clashing with the model server on 8000)
```

## Distributed setup

When the model server is on a different machine than the orchestrator:

```
[GPU box] → vLLM / SGLang on 192.0.2.50:8000
                          ▲
                          │
                          │ HTTPS or HTTP over private network
                          │
[Edge / app server] → Gigaxity Deep Research orchestrator
                       RESEARCH_LLM_API_BASE=http://192.0.2.50:8000/v1
```

If crossing a public network, terminate TLS on the model server and use a bearer token. Don't expose vLLM/SGLang directly to the internet without auth — there's no rate-limiting, no token accounting, and no auth middleware in the default OpenAI-compatible servers.

## Switching models on the fly

`RESEARCH_LLM_MODEL` is read at request time, not startup. To switch from Tongyi to DeepSeek-R1, change the env var and restart the orchestrator. The model server has to be hosting the requested model, of course.

For multi-model serving, run multiple model servers on different ports and have multiple orchestrator instances pointed at different `RESEARCH_LLM_API_BASE` values, registered under different MCP aliases.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `ConnectionError` on first call | Orchestrator can't reach model server | Verify `RESEARCH_LLM_API_BASE`; `curl <base>/models` from the orchestrator host |
| 401 from model server | Bearer token mismatch | Set `RESEARCH_LLM_API_KEY` to match what the server expects |
| Empty completions | Model not loaded yet | vLLM/SGLang takes 30–120 s to load 30B; wait or check the model-server logs |
| Out-of-memory at startup | Model larger than VRAM | Switch to a quantized variant (AWQ, INT4) or smaller model |
| Slow first request | Cold-start prompt eval | Send a warmup request after model load before traffic |

## What's next

- [REST API setup](setup-rest.md) — for distributed deployments
- [Configuration reference](../reference/configuration.md) — full env var list
