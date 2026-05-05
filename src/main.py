"""FastAPI application entry point with MCP server support."""

import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi_mcp import FastApiMCP
from .api import router
from .config import settings

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    # Startup
    settings.require_llm_key()
    logger.info("Research Tool starting on %s:%s", settings.host, settings.port)
    logger.info("LLM API: %s", settings.llm_api_base)
    logger.info("SearXNG: %s", settings.searxng_host)
    logger.info("MCP Server: http://%s:%s/mcp", settings.host, settings.port)

    yield

    # Shutdown
    logger.info("Research Tool shutting down")


app = FastAPI(
    title="Research Tool",
    description="Lightweight hybrid research tool with multi-source search and LLM synthesis",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API routes
app.include_router(router, prefix="/api/v1", tags=["research"])

# MCP Server - exposes all API endpoints as MCP tools
# Uses Streamable HTTP transport (recommended, replaces deprecated SSE)
mcp = FastApiMCP(
    app,
    name="Research Tool MCP",
    description="Multi-source search and LLM synthesis research tool",
    # Forward per-user LLM API key to route handlers
    headers=["X-LLM-Api-Key"],
    # Expose exactly the six documented tools — `synthesize/enhanced` and
    # `synthesize/p1` stay reachable as REST endpoints but not as MCP tools,
    # so the HTTP MCP surface matches the stdio MCP surface (and the docs).
    include_operations=[
        "search_api_v1_search_post",
        "research_api_v1_research_post",
        "ask_api_v1_ask_post",
        "discover_api_v1_discover_post",
        "synthesize_api_v1_synthesize_post",
        "reason_api_v1_reason_post",
    ],
)
mcp.mount_http()  # Mounts at /mcp by default


# Root endpoint
@app.get("/")
async def root():
    """Root endpoint with API info."""
    return {
        "name": "Research Tool",
        "version": "1.0.0",
        "docs": "/docs",
        "mcp": "/mcp",
        "endpoints": {
            # Core endpoints
            "health": "/api/v1/health",
            "search": "/api/v1/search",
            "research": "/api/v1/research",
            # Conversational + deep-research endpoints
            "ask": "/api/v1/ask",
            "discover": "/api/v1/discover",
            "synthesize": "/api/v1/synthesize",
            "reason": "/api/v1/reason",
        },
    }


def run():
    """Run the application with uvicorn."""
    import uvicorn
    uvicorn.run(
        "src.main:app",
        host=settings.host,
        port=settings.port,
        reload=False,
    )


if __name__ == "__main__":
    run()
