"""Configuration for the research tool."""

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # SearXNG Configuration
    searxng_host: str = Field(default="http://localhost:8888", description="SearXNG instance URL")
    searxng_engines: str = Field(
        default="brave,bing,duckduckgo,startpage,mojeek,wikipedia",
        description="Comma-separated search engines (matches the bundled SearXNG settings.yml.example default-enabled list)",
    )
    searxng_categories: str = Field(default="general", description="Search categories")
    searxng_language: str = Field(default="en", description="Search language")
    searxng_safesearch: int = Field(default=0, description="Safe search level (0=off, 1=moderate, 2=strict)")

    # Tavily Configuration
    tavily_api_key: str = Field(default="", description="Tavily API key")
    tavily_search_depth: str = Field(default="advanced", description="Search depth: basic or advanced")

    # LinkUp Configuration
    linkup_api_key: str = Field(default="", description="LinkUp API key")
    linkup_depth: str = Field(default="standard", description="Search depth: standard or deep")

    # LLM Configuration (any OpenAI-compatible endpoint: vLLM, SGLang, Ollama, llama.cpp, OpenRouter)
    llm_api_base: str = Field(default="http://localhost:8000/v1", description="LLM API base URL (defaults to a local OpenAI-compatible server on port 8000)")
    llm_api_key: str = Field(default="", description="LLM API key; for local servers without auth, set any non-empty placeholder")
    llm_model: str = Field(default="Alibaba-NLP/Tongyi-DeepResearch-30B-A3B-Thinking", description="DeepResearch model (HuggingFace path that vLLM/SGLang load by default)")
    llm_temperature: float = Field(default=0.85, description="Generation temperature")
    llm_top_p: float = Field(default=0.95, description="Top-p sampling")
    llm_max_tokens: int = Field(default=16384, description="Max output tokens")
    llm_timeout: int = Field(default=120, description="LLM request timeout in seconds")

    # Search Configuration
    default_top_k: int = Field(default=10, description="Default number of results per source")
    rrf_k: int = Field(default=60, description="RRF fusion constant")

    # Server Configuration
    host: str = Field(default="127.0.0.1", description="Server host (default loopback; bind 0.0.0.0 only behind an authenticated reverse proxy)")
    port: int = Field(default=8000, description="Server port")

    model_config = SettingsConfigDict(
        env_prefix="RESEARCH_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    def require_llm_key(self) -> None:
        """Fail fast if the LLM key is missing. Call from MCP / REST entrypoints."""
        if not self.llm_api_key:
            raise RuntimeError(
                "RESEARCH_LLM_API_KEY is not set. "
                "Set it in .env (in the project root), export it, or pass it via the MCP `env` block. "
                "For local servers without auth (vLLM, SGLang, Ollama default), set any non-empty placeholder "
                "such as `local-anything`. For remote services that enforce bearer auth (OpenRouter, hosted "
                "endpoints), use the actual key."
            )


settings = Settings()
