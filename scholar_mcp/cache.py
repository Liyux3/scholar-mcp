"""Simple in-memory TTL cache for API responses.

Avoids redundant API calls when agent searches the same query multiple times
in a workflow loop. Cache is per-process (resets on server restart).
"""

import time
from functools import wraps

_cache: dict[str, tuple[float, any]] = {}
DEFAULT_TTL = 300  # 5 minutes


def cached(ttl: int = DEFAULT_TTL):
    """Decorator that caches function results by args for ttl seconds."""
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            key = f"{fn.__name__}:{args}:{sorted(kwargs.items())}"
            now = time.time()
            if key in _cache:
                expires, value = _cache[key]
                if now < expires:
                    return value
            result = fn(*args, **kwargs)
            if result is not None and result != [] and result != {}:
                _cache[key] = (now + ttl, result)
            if len(_cache) > 500:
                _evict()
            return result
        return wrapper
    return decorator


def _evict():
    """Remove expired entries."""
    now = time.time()
    expired = [k for k, (exp, _) in _cache.items() if now >= exp]
    for k in expired:
        del _cache[k]


def clear():
    """Clear all cached entries."""
    _cache.clear()
