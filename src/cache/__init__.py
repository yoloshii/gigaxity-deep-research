"""
Hot cache for session-length research caching.

Ephemeral /tmp cache - zero persistence, zero cleanup logic.
Files auto-deleted on reboot. No database, no complexity.
"""

import hashlib
import json
import time
from dataclasses import dataclass, asdict
from functools import wraps
from pathlib import Path
from typing import Optional, Callable, Any


@dataclass
class CacheEntry:
    """Cached result with TTL."""
    result: Any
    created_at: float
    ttl: int


class HotCache:
    """
    Dead-simple /tmp cache for session-length research caching.

    Design principles:
    - Files auto-deleted on reboot (OS handles cleanup)
    - No database, no embeddings, just JSON files
    - Hash-based keys with tier namespacing
    - ~40 lines of actual logic
    """

    # TTL defaults by tier (seconds)
    DEFAULT_TTLS = {
        "synthesis": 3600,      # 1h - main results
        "discover": 3600,       # 1h - discovery results
        "reason": 3600,         # 1h - reasoning results
        "research": 1800,       # 30m - full pipeline results
        "search": 1800,         # 30m - raw search results
        "url": 7200,            # 2h - URL content
        "ask": 1800,            # 30m - quick answers
    }

    def __init__(self, namespace: str = "research"):
        self.cache_dir = Path(f"/tmp/{namespace}_cache")
        self.cache_dir.mkdir(exist_ok=True)
        self._hits = 0
        self._misses = 0

    def _key(self, query: str, tier: str = "", extra: str = "") -> str:
        """Normalize query + tier + extra params to cache key."""
        normalized = f"{tier}:{query.lower().strip()}:{extra}"
        return hashlib.sha256(normalized.encode()).hexdigest()[:16]

    def _path(self, key: str) -> Path:
        return self.cache_dir / f"{key}.json"

    def get(self, query: str, tier: str = "", extra: str = "") -> Optional[Any]:
        """Get cached result if fresh."""
        path = self._path(self._key(query, tier, extra))
        if not path.exists():
            self._misses += 1
            return None

        try:
            data = json.loads(path.read_text())
            age = time.time() - data["created_at"]
            if age < data["ttl"]:
                self._hits += 1
                return data["result"]
            # Expired - remove
            path.unlink(missing_ok=True)
        except (json.JSONDecodeError, KeyError, TypeError):
            path.unlink(missing_ok=True)

        self._misses += 1
        return None

    def set(
        self,
        query: str,
        result: Any,
        tier: str = "",
        extra: str = "",
        ttl: Optional[int] = None,
    ):
        """Cache result with TTL."""
        if ttl is None:
            ttl = self.DEFAULT_TTLS.get(tier, 3600)

        path = self._path(self._key(query, tier, extra))
        entry = CacheEntry(result=result, created_at=time.time(), ttl=ttl)

        try:
            path.write_text(json.dumps(asdict(entry)))
        except (TypeError, OSError):
            pass  # Skip non-serializable results silently

    def get_url(self, url: str) -> Optional[str]:
        """URL content cache (L2)."""
        cached = self.get(url, tier="url")
        return cached.get("content") if cached else None

    def set_url(self, url: str, content: str, ttl: int = 7200):
        """Cache URL content."""
        self.set(url, {"content": content}, tier="url", ttl=ttl)

    def stats(self) -> dict:
        """Cache statistics."""
        total = self._hits + self._misses
        return {
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": self._hits / total if total > 0 else 0,
            "cache_dir": str(self.cache_dir),
            "entries": len(list(self.cache_dir.glob("*.json"))),
        }

    def clear(self):
        """Clear all cache entries."""
        for f in self.cache_dir.glob("*.json"):
            f.unlink(missing_ok=True)
        self._hits = 0
        self._misses = 0


# Global instance
cache = HotCache()


def cached(tier: str = "", ttl: Optional[int] = None, key_params: list[str] = None):
    """
    Decorator for caching async tool results.

    Args:
        tier: Cache tier (synthesis, discover, reason, etc.)
        ttl: Override default TTL
        key_params: Additional kwargs to include in cache key

    Usage:
        @cached(tier="synthesis")
        async def _tool_synthesize(args: dict):
            ...
    """
    def decorator(fn: Callable):
        @wraps(fn)
        async def wrapper(args: dict, *a, **kw):
            query = args.get("query", "")
            if not query:
                return await fn(args, *a, **kw)

            # Build extra key from specified params
            extra_parts = []
            if key_params:
                for param in key_params:
                    if param in args:
                        extra_parts.append(f"{param}={args[param]}")
            extra = ":".join(extra_parts)

            # Check cache
            cached_result = cache.get(query, tier=tier, extra=extra)
            if cached_result is not None:
                # Return cached TextContent with cache indicator
                from mcp.types import TextContent
                text = cached_result
                if isinstance(text, str):
                    text = f"*[cached]*\n\n{text}"
                return [TextContent(type="text", text=text)]

            # Execute function
            result = await fn(args, *a, **kw)

            # Cache the text content
            if result and len(result) > 0:
                text_content = result[0].text if hasattr(result[0], 'text') else str(result[0])
                cache.set(query, text_content, tier=tier, extra=extra, ttl=ttl)

            return result
        return wrapper
    return decorator
