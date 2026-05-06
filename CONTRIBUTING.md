# Contributing to Gigaxity Deep Research

Gigaxity Deep Research is an MCP server (and REST API) that wraps Alibaba's Tongyi DeepResearch 30B model — running on a self-hosted OpenAI-compatible server on this branch (or via OpenRouter on `main`) — and exposes `discover`, `synthesize`, `reason`, and `ask` primitives to Claude Code and other MCP-compatible agents. Thanks for your interest in contributing.

## Prerequisites

- Python >= 3.11
- An OpenAI-compatible LLM endpoint (a local vLLM/SGLang/llama.cpp server, or a hosted service such as OpenRouter) for live LLM tests
- A SearXNG instance — self-hosted (https://docs.searxng.org/) or third-party — for live search tests
- Optional: Docker + Docker Compose if you want to run the server in a container

## Development Setup

```bash
# Clone and install
git clone https://github.com/yoloshii/gigaxity-deep-research.git
cd gigaxity-deep-research

# Install in editable mode with dev extras
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Configure
cp .env.example .env
# Edit .env: set RESEARCH_LLM_API_KEY (OpenRouter), RESEARCH_SEARXNG_HOST, optional Tavily/LinkUp keys

# Run as REST server (FastAPI/uvicorn)
uvicorn src.main:app --reload

# Run as MCP server (stdio for Claude Code)
python run_mcp.py
```

## Running Tests

```bash
# All tests
pytest

# Specific test file
pytest tests/test_synthesis.py

# With coverage
pytest --cov=src --cov-report=term-missing
```

Live LLM and live search tests are skipped automatically when the relevant API keys are absent. To run them, set `RESEARCH_LLM_API_KEY` (and optionally `RESEARCH_TAVILY_API_KEY`, `RESEARCH_LINKUP_API_KEY`) in your shell or a `.env` file.

## Project Structure

```
src/
  api/            # FastAPI REST routes and pydantic schemas
  discovery/      # Query routing, expansion, decomposition, focus modes
  synthesis/      # Quality gate, contradiction detection, presets, engine
  connectors/     # SearXNG, Tavily, LinkUp clients (created at import)
  config.py       # Pydantic settings (RESEARCH_* env vars)
  main.py         # FastAPI app entry
  mcp_server.py   # FastMCP server entry (discover/synthesize/reason/ask tools)
  llm_client.py   # OpenRouter client
  llm_utils.py    # Shared LLM helpers (content extraction, retry)
run_mcp.py        # MCP stdio runner (used by Claude Code)
tests/            # pytest suite (unit + e2e)
docs/             # User-facing documentation
skills/           # Bundled research-workflow skill (optional drop-in for Claude Code users)
```

## Making Changes

1. **Fork and branch** — create a feature branch from `main`:
   ```bash
   git checkout -b feature/your-change
   ```

2. **Write tests first** — add or update tests in `tests/` for your change. Tests should assert correct behavior, not current behavior. If a test fails, fix the source code, not the assertion.

3. **Make your changes** — keep commits focused. One logical change per commit.

4. **Run the full test suite** before submitting:
   ```bash
   pytest
   ```

5. **Manual smoke test** the MCP entry point if you touched `mcp_server.py`, `run_mcp.py`, or `llm_client.py`:
   ```bash
   python run_mcp.py < /dev/null  # should boot without crashing
   ```

## Pull Request Guidelines

- **One concern per PR.** Bug fix, feature, or refactor — pick one.
- **Describe what and why** in the PR description. Link related issues.
- **Keep diffs small.** Large PRs are hard to review and slow to merge. If your change is big, break it into stacked PRs.
- **No unrelated formatting changes.** Don't reformat files you didn't meaningfully change.
- **Tests must pass.** PRs with failing tests won't be merged.

## Code Style

- Follow the existing `src/` patterns. The codebase prefers `pydantic` models for any structured data crossing a function boundary.
- Type-annotate new public functions. Internal helpers can be looser.
- Async-first for I/O. Use `httpx.AsyncClient`, `asyncio.gather`, etc.
- Prefer early returns over deep nesting.
- Errors fail fast and loud. Don't silently swallow exceptions in connectors or LLM calls — log them, propagate, or convert to a structured error response.

## Reporting Bugs

Use the [bug report template](https://github.com/yoloshii/gigaxity-deep-research/issues/new?template=bug-report.yml). Include:
- Steps to reproduce
- Expected vs actual behavior
- Your environment (OS, Python version, OpenRouter model, SearXNG host type)

## Requesting Features

Use the [feature request template](https://github.com/yoloshii/gigaxity-deep-research/issues/new?template=feature-request.yml). Describe the problem you're solving, not just the solution you want.

## License

By contributing, you agree that your contributions will be licensed under the [MIT License](LICENSE).
