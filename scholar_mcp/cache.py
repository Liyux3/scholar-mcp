"""Simple in-memory TTL cache for API responses.

Avoids redundant API calls when agent searches the same query multiple times
in a workflow loop. Cache is per-process (resets on server restart).
"""

import time
from functools import wraps
from copy import deepcopy
from concurrent.futures import Future
import threading

_cache: dict[str, tuple[float, any]] = {}
_pending: dict[str, Future] = {}
_guard = threading.RLock()
DEFAULT_TTL = 300  # 5 minutes

# A cached search result holds up to 100 papers with abstracts, roughly 28 KB,
# so the bound is about 14 MB.
MAX_ENTRIES = 500


def cached(ttl: int = DEFAULT_TTL):
    """Decorator that caches function results by args for ttl seconds."""
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            key = f"{fn.__module__}.{fn.__qualname__}:{args}:{sorted(kwargs.items())}"
            with _guard:
                if key in _cache:
                    expires, value = _cache[key]
                    if time.time() < expires:
                        return deepcopy(value)
                pending = _pending.get(key)
                owner = pending is None
                if owner:
                    pending = _pending[key] = Future()
            if not owner:
                return deepcopy(pending.result())
            try:
                result = fn(*args, **kwargs)
                snapshot = deepcopy(result)
                with _guard:
                    if _is_cacheable(result):
                        _cache[key] = (time.time() + ttl, snapshot)
                    if len(_cache) > MAX_ENTRIES:
                        _evict()
                pending.set_result(snapshot)
                return result
            except BaseException as error:
                pending.set_exception(error)
                raise
            finally:
                with _guard:
                    _pending.pop(key, None)
        return wrapper
    return decorator


def _is_cacheable(result) -> bool:
    if result is None:
        return False
    if isinstance(result, list):
        return len(result) > 0
    if isinstance(result, dict):
        return bool(result) and "error" not in result
    return True


def _evict():
    """Drop expired entries, then the oldest survivors if still over budget.

    Expiry alone does not bound the cache: with a 5 minute TTL, a session
    issuing distinct queries faster than they expire grows without limit.
    Falling back to insertion order keeps MAX_ENTRIES a real ceiling. dicts
    preserve insertion order, and re-inserting on refresh is what makes the
    oldest key also the least recently stored.
    """
    now = time.time()
    for k in [k for k, (exp, _) in _cache.items() if now >= exp]:
        del _cache[k]

    overflow = len(_cache) - MAX_ENTRIES
    if overflow > 0:
        for k in list(_cache)[:overflow]:
            del _cache[k]


def clear():
    """Clear all cached entries."""
    with _guard:
        _cache.clear()
