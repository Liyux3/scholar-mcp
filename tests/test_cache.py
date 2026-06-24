"""Tests for API response cache."""

import time
from scholar_mcp import cache
from scholar_mcp.cache import cached, clear


def test_cache_hit():
    call_count = 0

    @cached(ttl=10)
    def fn(x):
        nonlocal call_count
        call_count += 1
        return x * 2

    assert fn(5) == 10
    assert fn(5) == 10
    assert call_count == 1


def test_cache_different_args():
    call_count = 0

    @cached(ttl=10)
    def fn(x):
        nonlocal call_count
        call_count += 1
        return x

    fn("a")
    fn("b")
    assert call_count == 2


def test_cache_expiry():
    @cached(ttl=1)
    def fn():
        return time.time()

    t1 = fn()
    time.sleep(1.1)
    t2 = fn()
    assert t2 > t1


def test_cache_clear():
    @cached(ttl=60)
    def fn():
        return time.time()

    t1 = fn()
    clear()
    t2 = fn()
    assert t2 > t1


class TestCacheBound:
    """MAX_ENTRIES was not enforced: _evict only removed expired entries, so a
    session issuing distinct queries faster than the 5 minute TTL grew the
    cache without limit. Each entry holds up to 100 papers with abstracts,
    around 28 KB, in a long-lived MCP server process.
    """

    def setup_method(self):
        cache.clear()

    def teardown_method(self):
        cache.clear()

    def test_bounded_when_nothing_expires(self):
        @cache.cached(ttl=3600)
        def fn(i):
            return [i]

        for i in range(cache.MAX_ENTRIES * 3):
            fn(i)
        assert len(cache._cache) <= cache.MAX_ENTRIES

    def test_evicts_oldest_first(self):
        @cache.cached(ttl=3600)
        def fn(i):
            return [i]

        for i in range(cache.MAX_ENTRIES + 50):
            fn(i)
        keys = set(cache._cache)
        assert not any(f"fn:({i},)" in k for k in keys for i in range(10)), \
            "oldest entries should have been evicted"
        assert any(str(cache.MAX_ENTRIES + 49) in k for k in keys), \
            "most recent entry should survive"

    def test_expired_entries_go_first(self):
        """Eviction should prefer dropping dead entries over live ones."""
        import time as _t
        cache._cache["stale"] = (_t.time() - 1, ["old"])
        for i in range(cache.MAX_ENTRIES):
            cache._cache[f"live-{i}"] = (_t.time() + 3600, [i])
        cache._evict()
        assert "stale" not in cache._cache
        assert len(cache._cache) <= cache.MAX_ENTRIES
