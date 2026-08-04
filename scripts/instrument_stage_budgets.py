#!/usr/bin/env python
"""Stage-budget instrumentation harness (lenient-parsed-callsites rev 7, Q2).

Design Q2 gated every non-outline budget change on instrumentation. The
first run of this harness (2026-08-04, glm-5.2) showed critique/landscape/
gaps/expansion/preview truncating at 100% and scoring at 33% of calls on
their flat budgets, so all stages now use derive_effective_budget(base,
model) — bases unchanged, non-reasoning models unaffected. The harness runs each stage's production prompt at its production budget
against the configured model over a small fixture corpus and reports, per
call: finish_reason, token usage, extraction outcome under the stage's
production mode, stage-grammar parse outcome, latency, and the aggregate
fallback frequency per stage.

Live-LLM tool — costs tokens; never runs in the test suite.

Usage:
    .venv/bin/python scripts/instrument_stage_budgets.py --stage all --runs 2
    .venv/bin/python scripts/instrument_stage_budgets.py --stage critique
    .venv/bin/python scripts/instrument_stage_budgets.py --stage outline --model glm-5.2
"""

import argparse
import asyncio
import sys
import time
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import settings
from src.discovery.expansion import QueryExpander, parse_expansion_records
from src.discovery.explorer import (
    KNOWLEDGE_GAP_PROMPT,
    LANDSCAPE_EXPANSION_PROMPT,
    SOURCE_SCORING_PROMPT,
    parse_gap_records,
    parse_landscape_records,
    parse_scoring_records,
)
from src.llm_client import get_llm_client
from src.llm_utils import (
    ExtractionMode,
    derive_effective_budget,
    extract_llm_output,
)
from src.synthesis.outline import (
    OutlineGuidedSynthesizer,
    parse_critique_records,
    parse_outline_records,
)


# ---------------------------------------------------------------------------
# Fixture corpus — compact, representative, self-contained.
# ---------------------------------------------------------------------------

QUERIES = [
    "Compare FastAPI and Flask for production APIs",
    "How do vector databases index embeddings?",
    "Rust async runtime tradeoffs tokio vs async-std",
]

SOURCE_SUMMARY = """[1] FastAPI docs (jina): FastAPI is a modern async Python web framework built on Starlette and Pydantic, with automatic OpenAPI generation and dependency injection...
[2] Flask docs (jina): Flask is a lightweight WSGI framework with a large extension ecosystem, synchronous by default, minimal core...
[3] Benchmark blog (exa): Under load, async frameworks sustain higher concurrency for IO-bound endpoints; WSGI servers scale via workers..."""

SOURCES_NUMBERED = """SOURCE 1:
Title: FastAPI documentation
URL: https://fastapi.tiangolo.com
Snippet: FastAPI is a modern async Python web framework built on Starlette and Pydantic with automatic OpenAPI generation...
---
SOURCE 2:
Title: Flask documentation
URL: https://flask.palletsprojects.com
Snippet: Flask is a lightweight WSGI framework with a large extension ecosystem, synchronous by default...
---
SOURCE 3:
Title: Async framework benchmarks 2026
URL: https://example.com/benchmarks
Snippet: Under IO-bound load async frameworks sustain higher concurrency; WSGI scales via worker processes...
---"""

GAPS_NUMBERED = """1. deployment story: production deployment differences (importance: high)
2. ecosystem maturity: extension and middleware availability (importance: medium)"""

DRAFT = """## Overview

FastAPI and Flask are both mature Python web frameworks [1][2]. FastAPI is async-first and generates OpenAPI schemas automatically [1], while Flask is synchronous by default with a large extension ecosystem [2].

## Performance

Under IO-bound load, async frameworks sustain higher concurrency [3]."""


@dataclass
class CallRecord:
    stage: str
    query: str
    budget: int
    finish_reason: str
    prompt_tokens: int
    completion_tokens: int
    extraction: str      # ok | blank_reasoning_only | blank_truncated | blank_empty
    parse: str           # accepted | rejected | n/a
    fallback: bool
    latency_s: float


async def _measure(client, model, stage, query, prompt, budget, parser) -> CallRecord:
    started = time.perf_counter()
    response = await client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=budget,
        temperature=0.7,
    )
    latency = time.perf_counter() - started
    choice = response.choices[0] if getattr(response, "choices", None) else None
    usage = getattr(response, "usage", None)
    output = extract_llm_output(choice, ExtractionMode.PARSE_REQUIRED)

    if output.text:
        extraction = "ok"
    elif output.truncated:
        extraction = "blank_truncated"
    elif output.reasoning_only:
        extraction = "blank_reasoning_only"
    else:
        extraction = "blank_empty"

    if parser is None:
        parse = "n/a"
        fallback = not output.text.strip() or output.truncated
    else:
        parsed = parser(output.text)
        parse = "accepted" if parsed is not None else "rejected"
        fallback = parsed is None

    return CallRecord(
        stage=stage,
        query=query[:38],
        budget=budget,
        finish_reason=str(getattr(choice, "finish_reason", None)),
        prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
        completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
        extraction=extraction,
        parse=parse,
        fallback=fallback,
        latency_s=latency,
    )


def _stage_calls(model: str):
    """Yield (stage, query, prompt, budget, parser) per fixture item.

    Budgets are the PRODUCTION values — this harness measures the current
    configuration; it does not propose one.
    """
    for query in QUERIES:
        yield (
            "outline",
            query,
            OutlineGuidedSynthesizer.OUTLINE_PROMPT.format(
                query=query, source_summary=SOURCE_SUMMARY, style="comprehensive"
            ),
            derive_effective_budget(300, model),
            parse_outline_records,
        )
        yield (
            "critique",
            query,
            OutlineGuidedSynthesizer.CRITIQUE_PROMPT.format(
                query=query, draft=DRAFT, source_summary=SOURCE_SUMMARY
            ),
            derive_effective_budget(500, model),
            parse_critique_records,
        )
        yield (
            "landscape",
            query,
            LANDSCAPE_EXPANSION_PROMPT.format(query=query),
            derive_effective_budget(500, model),
            parse_landscape_records,
        )
        yield (
            "gaps",
            query,
            KNOWLEDGE_GAP_PROMPT.format(query=query, sources=SOURCE_SUMMARY),
            derive_effective_budget(800, model),
            parse_gap_records,
        )
        yield (
            "scoring",
            query,
            SOURCE_SCORING_PROMPT.format(
                query=query, gaps=GAPS_NUMBERED, sources=SOURCES_NUMBERED
            ),
            derive_effective_budget(1500, model),
            lambda text: parse_scoring_records(text, num_targets=3, num_gaps=2),
        )
        yield (
            "expansion",
            query,
            QueryExpander.EXPANSION_PROMPT.format(query=query, n=3),
            derive_effective_budget(500, model),
            lambda text, q=query: parse_expansion_records(text, 3, q),
        )
        # Preview is FINAL_ANSWER and never parsed (parser=None): "fallback"
        # here means the text was blank or truncated → "preview unavailable".
        yield (
            "preview",
            query,
            f"""Based on these top sources, provide a 2-3 sentence overview that answers or frames the query. This is a preview, not a full synthesis.

Query: {query}

Sources:
{SOURCE_SUMMARY}

Brief overview:""",
            derive_effective_budget(200, model),
            None,
        )


async def run(stage_filter: str, runs: int, model: str) -> int:
    client = get_llm_client()
    records: list[CallRecord] = []

    calls = [
        c for c in _stage_calls(model)
        if stage_filter == "all" or c[0] == stage_filter
    ]
    total = len(calls) * runs
    print(f"model={model}  calls={total}  (stages={stage_filter}, runs={runs})\n")

    n = 0
    for _ in range(runs):
        for stage, query, prompt, budget, parser in calls:
            n += 1
            try:
                record = await _measure(
                    client, model, stage, query, prompt, budget, parser
                )
            except Exception as exc:
                print(f"[{n}/{total}] {stage:<10} TRANSPORT ERROR: {type(exc).__name__}: {exc}")
                continue
            records.append(record)
            print(
                f"[{n}/{total}] {record.stage:<10} budget={record.budget:<6}"
                f" finish={record.finish_reason:<7} out_tok={record.completion_tokens:<6}"
                f" extract={record.extraction:<20} parse={record.parse:<9}"
                f" fallback={'Y' if record.fallback else 'n'}"
                f" {record.latency_s:5.1f}s  {record.query}"
            )

    if not records:
        print("no successful calls — nothing to summarize")
        return 1

    print("\n== per-stage summary ==")
    print(f"{'stage':<10} {'calls':>5} {'budget':>7} {'fallback%':>9} "
          f"{'trunc%':>7} {'avg_out_tok':>11} {'avg_latency':>11}")
    for stage in ("outline", "critique", "landscape", "gaps", "scoring", "expansion", "preview"):
        rs = [r for r in records if r.stage == stage]
        if not rs:
            continue
        fallback_pct = 100 * sum(r.fallback for r in rs) / len(rs)
        trunc_pct = 100 * sum(r.finish_reason == "length" for r in rs) / len(rs)
        avg_out = sum(r.completion_tokens for r in rs) / len(rs)
        avg_lat = sum(r.latency_s for r in rs) / len(rs)
        print(f"{stage:<10} {len(rs):>5} {rs[0].budget:>7} {fallback_pct:>8.0f}% "
              f"{trunc_pct:>6.0f}% {avg_out:>11.0f} {avg_lat:>10.1f}s")

    print(
        "\nDecision rule (design Q2): a stage earns a budget change only when"
        " this harness shows truncation/fallback at its production budget —"
        " the outline stage's n=1 measurement does not generalize."
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--stage",
        default="all",
        choices=["all", "outline", "critique", "landscape", "gaps", "scoring", "expansion", "preview"],
    )
    parser.add_argument("--runs", type=int, default=1, help="repetitions per fixture")
    parser.add_argument("--model", default=None, help="override settings.llm_model")
    args = parser.parse_args()

    if not settings.llm_api_key:
        print("RESEARCH_LLM_API_KEY not configured — cannot run live instrumentation")
        return 1

    model = args.model or settings.llm_model
    return asyncio.run(run(args.stage, args.runs, model))


if __name__ == "__main__":
    raise SystemExit(main())
