"""Bounded TTL cache with optional durable public source responses.

Avoids redundant API calls when agent searches the same query multiple times
in a workflow loop. Completed provider responses can survive a server restart;
errors and interrupted partial responses are never saved as complete answers.
"""

import time
import hashlib
import json
import os
import sqlite3
from contextlib import closing, contextmanager
from contextvars import ContextVar
from pathlib import Path
from functools import wraps
from copy import deepcopy
from concurrent.futures import Future
import threading

_cache: dict[str, tuple[float, any]] = {}
_pending: dict[str, Future] = {}
_guard = threading.RLock()
DEFAULT_TTL = 300  # 5 minutes

# Bound both the number of memory entries and bytes in the durable store.
MAX_ENTRIES = 500
MAX_DISK_BYTES = 16_000_000
_request_misses: ContextVar[dict | None] = ContextVar('scholar_request_misses', default=None)


class PartialRecords(dict):
    """Completed metadata batches, with interrupted batches still retryable."""
    partial_warning = 'some metadata batches did not complete'


@contextmanager
def request_scope():
    """Share explicit lookup misses for one operation, even across long stages."""
    if _request_misses.get() is not None:
        yield
        return
    token = _request_misses.set({})
    try:
        yield
    finally:
        _request_misses.reset(token)


def _retain_miss(key: str, value, miss_ttl: int) -> None:
    memo = _request_misses.get()
    if (miss_ttl and memo is not None and not getattr(value, 'partial_warning', None)
            and (value is None or isinstance(value, (dict, list)) and not value)):
        with _guard:
            memo[key] = deepcopy(value)


def _path() -> Path:
    from . import config
    return Path(config.DATA_DIR) / "cache.sqlite3"


def _disk(key: str, entry: tuple | None = None):
    """Best-effort atomic storage. A cache problem always leaves the API path usable."""
    path = _path()
    digest = hashlib.sha256(key.encode()).hexdigest()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(descriptor)
        with closing(sqlite3.connect(path, timeout=1)) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS responses (key TEXT PRIMARY KEY, expires REAL, stored REAL, value TEXT)")
            now = time.time()
            if entry is not None:
                expires, value = entry
                payload = json.dumps(value, ensure_ascii=False, allow_nan=False)
                if len(payload.encode()) > 2_000_000:
                    return None
                db.execute("INSERT OR REPLACE INTO responses VALUES (?, ?, ?, ?)", (digest, expires, now, payload))
                db.execute("DELETE FROM responses WHERE expires <= ?", (now,))
                db.execute("DELETE FROM responses WHERE key IN (SELECT key FROM responses ORDER BY stored DESC LIMIT -1 OFFSET ?)",
                           (MAX_ENTRIES,))
                size, oldest = 0, []
                for stored_key, byte_count in db.execute(
                        "SELECT key, length(CAST(value AS BLOB)) FROM responses ORDER BY stored DESC"):
                    size += byte_count
                    if size > MAX_DISK_BYTES:
                        oldest.append((stored_key,))
                db.executemany("DELETE FROM responses WHERE key=?", oldest)
            else:
                row = db.execute("SELECT expires, value FROM responses WHERE key=? AND expires>?", (digest, now)).fetchone()
                if row:
                    return row[0], json.loads(row[1])
    except (OSError, sqlite3.Error, ValueError, TypeError):
        return None


def cached(ttl: int = DEFAULT_TTL, *, persistent: bool = False, namespace: str = "", miss_ttl: int = 0):
    """Decorator that caches function results by args for ttl seconds."""
    def decorator(fn):
        def make_key(args, kwargs):
            return f"{namespace or fn.__module__ + '.' + fn.__qualname__}:{args}:{sorted(kwargs.items())}"

        def read_cached(key):
            memo = _request_misses.get()
            if memo is not None and key in memo:
                return True, deepcopy(memo[key])
            if key in _cache:
                expires, value = _cache[key]
                if time.time() < expires:
                    _retain_miss(key, value, miss_ttl)
                    return True, deepcopy(value)
            return False, None

        def peek(*args, **kwargs):
            """Read an available memory entry without waiting on queued work."""
            with _guard:
                return read_cached(make_key(args, kwargs))

        def store(value, *args, **kwargs):
            """Seed a complete subquery from a batch, using this cache's TTL and miss scope."""
            key = make_key(args, kwargs)
            cacheable = _is_cacheable(value)
            if getattr(value, 'partial_warning', None) or not cacheable and not (miss_ttl and value in (None, {}, [])):
                return
            snapshot = deepcopy(value)
            entry = (time.time() + (ttl if cacheable else miss_ttl), snapshot)
            with _guard:
                _retain_miss(key, snapshot, miss_ttl)
                _cache[key] = entry
                if len(_cache) > MAX_ENTRIES:
                    _evict()
            if persistent and cacheable:
                _disk(key, entry)

        @wraps(fn)
        def wrapper(*args, **kwargs):
            key = make_key(args, kwargs)
            with _guard:
                hit, value = read_cached(key)
                if hit:
                    return value
                pending = _pending.get(key)
                owner = pending is None
                if owner:
                    pending = _pending[key] = Future()
            if not owner:
                result = deepcopy(pending.result())
                _retain_miss(key, result, miss_ttl)
                return result
            try:
                if persistent and (entry := _disk(key)) is not None:
                    with _guard:
                        _cache[key] = entry
                        if len(_cache) > MAX_ENTRIES:
                            _evict()
                    pending.set_result(deepcopy(entry[1]))
                    return deepcopy(entry[1])
                result = fn(*args, **kwargs)
                _retain_miss(key, result, miss_ttl)
                snapshot = deepcopy(result)
                cacheable = _is_cacheable(result)
                expires = time.time() + (ttl if cacheable else miss_ttl)
                if persistent and _is_cacheable(result):
                    _disk(key, (expires, snapshot))
                with _guard:
                    if cacheable or miss_ttl and result in (None, {}, []) and not getattr(result, 'partial_warning', None):
                        _cache[key] = (expires, snapshot)
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
        wrapper.peek = peek
        wrapper.store = store
        return wrapper
    return decorator


def _is_cacheable(result) -> bool:
    if result is None or getattr(result, "partial_warning", None):
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
    """Clear process memory; completed persistent source responses retain their TTL."""
    with _guard:
        _cache.clear()
