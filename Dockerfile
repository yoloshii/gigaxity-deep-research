# syntax=docker/dockerfile:1

FROM python:3.12-slim AS base

# Install uv for fast dependency management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Copy dependency files (pyproject declares readme = "README.md", so the
# build backend needs it present at install time)
COPY pyproject.toml README.md ./

# Install dependencies
RUN uv pip install --system --no-cache -e .

# Copy application code
COPY src ./src

# Create non-root user, and pre-create the cache directory it owns.
#
# docker-compose.yml mounts a named volume at /tmp/research_cache. Docker seeds
# a *fresh* named volume from whatever the image has at that path — contents and
# ownership both — so creating the directory here as `researcher` is what stops
# the volume being created root-owned and silently unwritable to the runtime
# user. Without this the cache never writes a single entry and every request is
# recomputed, with no error surfaced anywhere (see docs/troubleshooting.md).
#
# This fixes NEW volumes only. An existing root-owned volume keeps its
# ownership; remove it (or chown it to uid 1000) once — troubleshooting.md
# has both commands.
RUN useradd -m -u 1000 researcher \
    && mkdir -p /tmp/research_cache \
    && chown researcher:researcher /tmp/research_cache
USER researcher

# Expose port
EXPOSE 8000

# Health check (python-based, no curl needed in slim image)
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/v1/health')" || exit 1

# Run application
CMD ["python", "-m", "uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
