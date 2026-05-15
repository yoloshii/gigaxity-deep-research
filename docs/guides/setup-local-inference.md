# Setting up local inference (self-hosted Tongyi DeepResearch)

> **You are reading this on the `local-inference` branch.** The defaults shipped here already point at a local OpenAI-compatible inference server (`RESEARCH_LLM_API_BASE=http://localhost:8000/v1`) and the LLM client is generic — no OpenRouter-specific code paths. To run against OpenRouter or another hosted endpoint, either override the env vars or check out the [`main` branch](https://github.com/yoloshii/gigaxity-deep-research/tree/main).

This guide covers when to choose local inference, how to host Tongyi 30B (or another reasoning model), and how to wire it back to the synthesis pipeline.

## When to use local inference

- **On-prem requirement** — data must not leave your network
- **Cost predictability** — your usage volume makes per-call pricing more expensive than amortized GPU hosting
- **Latency floor** — round-trip to a hosted service adds 100–500 ms; local hosting can drop that to single-digit ms
- **GPU you already have** — repurposing existing infra
- **Custom fine-tune** — running your own variant of Tongyi/DeepSeek/Qwen

Otherwise, the hosted-OpenRouter path on the `main` branch is simpler — no model server to manage, no GPU prerequisites.

## Hardware requirements

Tongyi DeepResearch 30B (A3B variant) needs:

- ~60 GB VRAM at FP16
- ~30 GB VRAM at INT8
- ~18-19 GB VRAM at Q4_K_M GGUF — the **recommended** path for consumer GPUs
- ~16 GB VRAM at INT4 AWQ or GPTQ — slightly tighter, slightly faster than Q4_K_M GGUF on the same hardware

Single-GPU friendly options:
- 1× A100 80 GB (FP16, comfortable headroom)
- 1× H100 80 GB
- 1× RTX 6000 Ada 48 GB (INT8)
- **1× RTX 3090 / 4090 / 5090 24 GB (Q4_K_M GGUF — fits comfortably with 5+ GB headroom for KV cache)**
- 2× RTX 4090 24 GB (with tensor parallelism, INT8)

Multi-GPU:
- 2× A100 40 GB (FP16, tensor-parallel)
- 4× RTX 3090 24 GB (FP16, tensor-parallel)

### Quant format support per server

Not every server loads every quant format. As of May 2026:

| Server | GGUF | AWQ | GPTQ | safetensors FP16 |
|---|---|---|---|---|
| llama.cpp | ✅ Native (canonical GGUF runtime) | ❌ | ❌ | ❌ |
| vLLM | ✅ since [PR #5191](https://github.com/vllm-project/vllm/pull/5191) — covers most modern architectures, verify yours via [vLLM's GGUF docs](https://docs.vllm.ai/en/latest/api/vllm/model_executor/model_loader/gguf_loader) | ✅ | ✅ | ✅ |
| SGLang | ❌ — tracked at [issue #1937](https://github.com/sgl-project/sglang/issues/1937), not merged as of May 2026 | ✅ | ✅ | ✅ |

Implications:

- **GGUF route (Q4_K_M and friends, on llama.cpp or vLLM):** the recommended path for 24 GB consumer GPUs. Q4_K_M is the sweet spot for this footprint — small quality drop versus FP16 for synthesis with citation binding.
- **SGLang users:** swap the model path to an AWQ or GPTQ build — search HuggingFace for `Tongyi-DeepResearch-30B-A3B-AWQ` (or `-GPTQ`) instead of pulling a `.gguf`. See the [SGLang section](#host-the-model-with-sglang) below.
- **Not locked to GGUF:** AWQ and GPTQ at INT4 land in roughly the same 16-19 GB VRAM footprint as Q4_K_M GGUF and run on both vLLM and SGLang. If your stack is already on vLLM/SGLang and you don't want a second runtime, AWQ is the natural alternative.

### Recommended quant for 24 GB consumer GPUs

For an RTX 3090 / 4090 / 5090 (24 GB) or an Apple Silicon machine with 32 GB+ unified memory, use the Q4_K_M GGUF quant. It loads in about 18.5 GB on disk and consumes around 18.9 GB VRAM at runtime, leaving headroom for the KV cache during long-context synthesis runs. Quality drop versus FP16 is small for synthesis tasks — the reasoning behavior of Tongyi DeepResearch holds up well at Q4_K_M in practice.

Browse the available GGUF quants: [https://huggingface.co/models?other=base_model:quantized:Alibaba-NLP/Tongyi-DeepResearch-30B-A3B](https://huggingface.co/models?other=base_model:quantized:Alibaba-NLP/Tongyi-DeepResearch-30B-A3B). Most quanters publish the full Q3/Q4/Q5/Q6/Q8 ladder — pick Q4_K_M unless you have a specific reason to go higher (Q5_K_M ≈ 21.6 GB; tighter on a 24 GB GPU) or lower (Q3 quants below 16 GB; see the threshold note below).

### When to stop quanting and rent inference instead

Quality degrades non-linearly as you go below Q4_K_M. The practical threshold for *this* pipeline (multi-hop research synthesis with citation binding, contradiction detection, and outline-guided generation) sits at:

| Quant | Disk size | Verdict for synthesis work |
|---|---|---|
| Q5_K_M / Q6_K / Q8_0 | 22–32 GB | Indistinguishable from FP16 in practice; pick if VRAM allows |
| **Q4_K_M (recommended)** | **~18.5 GB** | **Negligible drop vs FP16. Citation accuracy + multi-hop reasoning intact.** |
| Q4_K_S | ~17.6 GB | Still fine. Useful when Q4_K_M won't fit alongside KV cache. |
| IQ4_XS / IQ4_NL | ~16–17 GB | Imatrix quants — comparable to Q4_K_S in practice, slightly tighter. |
| Q3_K_M | ~14.6 GB | **Borderline.** Citation IDs and inline `[sx_xxx]` markers occasionally drift; multi-hop chains shorten. Acceptable for `ask` and quick `research` calls; noticeable on `synthesize` with 5+ sources. |
| Q3_K_S / IQ3_M | ~13–14 GB | **Stop here.** Citation accuracy slips, contradiction detection misses cross-source disagreements, the `<thinking>` block becomes shorter and shallower. |
| Q2_K and below | < 12 GB | Not viable for research synthesis. Reasoning collapses on cross-source comparisons. |

**Rule of thumb:** if your hardware can't comfortably hold **Q4_K_M plus 5+ GB of KV cache** (so ~24 GB VRAM minimum, or ~32 GB unified memory on Apple Silicon), the math usually flips against local inference for this pipeline. At Q3_K_M and below, you're paying the latency and ops cost of self-hosting for output that's measurably worse than what OpenRouter returns for $0.01–0.05 per synthesis. Either:

- **Stay on a 24 GB+ GPU at Q4_K_M** — best quality-per-dollar at moderate-to-heavy usage
- **Use a smaller, less-quanted model** — Tongyi 7B at Q5_K_M, DeepSeek-R1-Distill-Qwen-14B at Q4_K_M, or Qwen3-14B at Q4_K_M all beat Tongyi 30B at Q3_K_S on reasoning benchmarks despite being smaller, because the quant penalty above ~Q4_K_M is small but the penalty below it is steep
- **Rent hosted inference** — switch `RESEARCH_LLM_API_BASE` to OpenRouter (or check out the `main` branch). Pay-per-call beats running degraded Q3 locally for any real research workload

If you have less than 16 GB VRAM, drop down a model tier (Tongyi 7B, DeepSeek-R1-Distill-Qwen-14B, Qwen-QwQ-32B at INT4) — the synthesis pipeline is model-agnostic.

## The `local-inference` branch

```bash
cd gigaxity-deep-research
git checkout local-inference
pip install -e .
```

This branch differs from `main` in two places:

- `src/llm_client.py` exposes a generic `LLMClient` against any OpenAI-compatible endpoint (no OpenRouter-flavored helpers, no `X-OpenRouter-Api-Key` alias).
- `src/config.py` defaults to `RESEARCH_LLM_API_BASE=http://localhost:8000/v1` and `RESEARCH_LLM_MODEL=Alibaba-NLP/Tongyi-DeepResearch-30B-A3B`.

Everything else (search, fusion, synthesis, citations) is identical to `main`. The per-request key override that exists on both branches is named `api_key` (MCP tool parameter) / `X-LLM-Api-Key` (REST header) on this branch — a generic name that fits whatever endpoint you point at.

If your local server runs on the default port, the only env var you need to set is `RESEARCH_LLM_API_KEY` (any non-empty placeholder works for unauthenticated local servers):

```bash
RESEARCH_LLM_API_KEY=local-anything python run_mcp.py
```

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

> **Note on quant format:** SGLang doesn't load GGUF as of May 2026 ([#1937](https://github.com/sgl-project/sglang/issues/1937)). The launch command below loads the FP16 HuggingFace model. For a quantized SGLang deployment that fits in 24 GB VRAM, swap the `--model-path` to an AWQ or GPTQ build (e.g. `Alibaba-NLP/Tongyi-DeepResearch-30B-A3B-AWQ`) and add `--quantization awq`.

```bash
pip install "sglang[all]"

python -m sglang.launch_server \
  --model-path Alibaba-NLP/Tongyi-DeepResearch-30B-A3B \
  --host 0.0.0.0 \
  --port 8000
```

## Lower hardware bar (24 GB consumer GPU or Apple Silicon) — GGUF + llama.cpp

For modest GPUs (24 GB) or Apple Silicon, pull a quantized GGUF build from [`mradermacher/Tongyi-DeepResearch-30B-A3B-GGUF`](https://huggingface.co/mradermacher/Tongyi-DeepResearch-30B-A3B-GGUF) — the most reliable static quant ladder for this model (Q2_K through Q8_0; Q4_K_M ≈ 18.7 GB, flagged "fast, recommended" by the quanter). Imatrix variants are at [`mradermacher/Tongyi-DeepResearch-30B-A3B-i1-GGUF`](https://huggingface.co/mradermacher/Tongyi-DeepResearch-30B-A3B-i1-GGUF). We explicitly avoid [`bartowski/Alibaba-NLP_Tongyi-DeepResearch-30B-A3B-GGUF`](https://huggingface.co/bartowski/Alibaba-NLP_Tongyi-DeepResearch-30B-A3B-GGUF) because of an [open repetition issue](https://huggingface.co/bartowski/Alibaba-NLP_Tongyi-DeepResearch-30B-A3B-GGUF/discussions/2) reported on this exact model.

llama.cpp's `llama-server` is the canonical GGUF runtime. The simplest path uses its HF-shortcut (no manual download — llama.cpp fetches and caches automatically):

```bash
llama-server -hf mradermacher/Tongyi-DeepResearch-30B-A3B-GGUF:Q4_K_M \
  --host 0.0.0.0 --port 8080 -ngl 999 -c 32768
```

Or download the GGUF first and point at it locally:

```bash
huggingface-cli download mradermacher/Tongyi-DeepResearch-30B-A3B-GGUF \
  Tongyi-DeepResearch-30B-A3B.Q4_K_M.gguf \
  --local-dir ~/models

./llama-server -m ~/models/Tongyi-DeepResearch-30B-A3B.Q4_K_M.gguf \
  --host 0.0.0.0 --port 8080 -ngl 999 -c 32768
```

`llama-server` exposes an OpenAI-compatible endpoint at `http://localhost:8080/v1`. vLLM with `--quantization gguf` loads the same file at `http://localhost:8000/v1`; LM Studio and Jan are GUI alternatives that also serve OpenAI-compatible endpoints.

Then set `RESEARCH_LLM_API_BASE=http://localhost:8080/v1` and use whatever model alias `llama-server` reports.

## Configure the orchestrator

The branch defaults already match a vLLM/SGLang server on `localhost:8000`. Override only what you need:

```bash
# vLLM / SGLang on the default port — only RESEARCH_LLM_API_KEY needs setting
RESEARCH_LLM_API_KEY=local-anything   # placeholder string — see note below

# llama.cpp's llama-server (different default port + the alias llama-server reports)
RESEARCH_LLM_API_BASE=http://localhost:8080/v1
RESEARCH_LLM_API_KEY=local-anything
RESEARCH_LLM_MODEL=Tongyi-DeepResearch-30B-A3B

# Hosted endpoint (e.g. OpenRouter) from this branch
RESEARCH_LLM_API_BASE=https://openrouter.ai/api/v1
RESEARCH_LLM_API_KEY=sk-or-v1-your-key
RESEARCH_LLM_MODEL=alibaba/tongyi-deepresearch-30b-a3b
```

`RESEARCH_LLM_API_KEY` must be **non-empty** because every entrypoint calls `settings.require_llm_key()` and fails fast on an empty key — without it, `llm_configured` on `/api/v1/health` would always read `true` and stop being a useful readiness signal. For local servers that do not enforce auth, set the variable to any placeholder string (`local-anything`, `na`, etc.). For hosted endpoints or local servers configured with bearer tokens, set this to the actual token value.

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
