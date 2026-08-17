"""Configuration for the research tool."""

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # SearXNG Configuration
    searxng_host: str = Field(default="http://localhost:8888", description="SearXNG instance URL")
    searxng_engines: str = Field(
        default="brave,duckduckgo,startpage,mojeek,wikipedia",
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

    # Brave Search Configuration
    brave_api_key: str = Field(default="", description="Brave Search API key")
    brave_country: str = Field(default="", description="Optional ISO country code for geo-targeting, e.g. 'us'")
    brave_safesearch: str = Field(default="off", description="Safe search: off, moderate, or strict")

    # LLM Configuration (any OpenAI-compatible endpoint: vLLM, SGLang, llama.cpp, OpenRouter)
    llm_api_base: str = Field(default="http://localhost:8000/v1", description="LLM API base URL (defaults to a local OpenAI-compatible server on port 8000)")
    llm_api_key: str = Field(default="", description="LLM API key; for local servers without auth, set any non-empty placeholder")
    llm_model: str = Field(default="Qwen/Qwen3-30B-A3B-Thinking-2507", description="DeepResearch model (HuggingFace path that vLLM/SGLang load by default)")
    llm_temperature: float = Field(default=0.85, description="Generation temperature")
    llm_top_p: float = Field(default=0.95, description="Top-p sampling")
    llm_max_tokens: int = Field(default=16384, description="Max output tokens")
    llm_reasoning_headroom: int = Field(default=8192, description="Extra output tokens added to the answer-budget base for reasoning models (chain-of-thought overhead); effective budget = base + headroom, capped at llm_max_tokens")
    llm_scoring_headroom: int = Field(default=1536, description="DEPRECATED (no longer read): the quality-gate relevance scorer now sizes its budget with derive_effective_budget(500, model) - the same reasoning-aware helper synthesis and RCS use (min(500 + llm_reasoning_headroom, llm_max_tokens)) - so reasoning-model scoring shares one headroom knob. Field retained to avoid breaking existing RESEARCH_LLM_SCORING_HEADROOM settings; safe to remove in a future release.")
    llm_timeout: int = Field(default=120, description="Per-request LLM timeout in seconds. This is an httpx OPERATION timeout (read/write/pool; connect is a separate 10s), NOT a wall-clock bound on the call: httpx reapplies the read timeout to each individual network read, so a slowly-trickling response can outlast it, and the SDK sleeps between retries outside those windows.")
    llm_max_retries: int = Field(default=2, ge=0, description="Retries the OpenAI SDK performs per request, i.e. (llm_max_retries + 1) attempts. Defaults to the SDK's own value, so behaviour is unchanged unless you set it. The SDK retries read-timeouts the same way it retries 429s, so this multiplies llm_timeout: a stalled upstream connection costs roughly (retries + 1) x llm_timeout of silent retrying, which is what makes a long chain outlive an MCP client's abort deadline. Lowering it shortens that stall and gives up a recovery attempt: a request whose first attempts time out and whose last succeeds will fail at 0.")
    llm_wall_clock_cap: int = Field(default=0, description="Absolute wall-clock ceiling in seconds for ONE model call including the SDK's internal retry chain, enforced with asyncio.timeout around the completion. 0 (the default) disables it. This is a SERVER-WIDE RESOURCE POLICY, not a mirror of any client setting: it applies uniformly to MCP, REST and direct library callers, because making it depend on the caller would mean identical work timing out on one surface and not another. It is NOT derived from llm_timeout x attempts - llm_timeout is a per-operation httpx timeout with no natural wall-clock maximum - so a positive value may deliberately interrupt a retry the SDK would otherwise finish. Disabled by default because the right ceiling depends on the endpoint: a hosted model has a bounded generation time, while a slow local server can legitimately run far longer, and shipping a ceiling that kills healthy local generation is a worse failure than having none. 480 is a reasonable starting profile for a hosted endpoint. Progress notifications are emitted regardless of this setting, so idle-timeout survival does not depend on it.")
    progress_send_timeout: int = Field(default=10, gt=0, description="Seconds a single progress notification may take before reporting is disabled for the rest of the request. The reporter holds its serialization lock across the send, so an unbounded send to a sink that HANGS rather than raises would block every concurrent call - turning the reporting machinery into the stall it exists to prevent. Delivery is local transport work, so 10s is already generous.")
    progress_heartbeat_interval: int = Field(default=30, gt=0, description="Seconds between 'still running' progress notifications emitted while a model call is in flight. Entry and settlement notifications cannot subdivide a single completion, and a non-streaming call is silent for its whole duration, so this is what keeps a slow generation from reading as an idle connection to the client. A call shorter than one interval emits no heartbeat. Lower it if your client's idle window is tight; the cost is one notification per interval per in-flight call.")
    rcs_concurrency: int = Field(default=4, description="Max concurrent RCS contextual-summary calls. Per-source summaries are independent LLM calls; running them serially scales as N x per-call latency over many sources. asyncio.gather preserves source order regardless of concurrency. Tune higher for endpoints that accept more parallelism. Values <1 are floored to 1 (serial).")

    # Synthesis quality gate
    fail_open_min_source_score: float = Field(default=0.3, description="Fail-open floor for the synthesis relevance gate. On a REJECT decision the pipeline synthesizes anyway with a low-relevance caveat (instead of refusing) only when max(source_scores) >= this floor; below it there is no positive evidence to ground a synthesis, so the gate hard-refuses even when the scorer is degraded. Defaults to the reject threshold (0.3): a source at 0.30 is the first 'some evidence exists' band, 0.29 hard-refuses. Applied AFTER QualityGate.evaluate() returns REJECT, so the degraded-scorer rescue and PARTIAL/PROCEED branches are untouched.")

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
                "For local servers without auth (vLLM, SGLang, llama.cpp default), set any non-empty placeholder "
                "such as `local-anything`. For remote services that enforce bearer auth (OpenRouter, hosted "
                "endpoints), use the actual key."
            )


settings = Settings()
