"""Tests for API response cache."""

import time
from scholar_mcp import cache
from scholar_mcp.cache import cached, clear


def test_partial_metadata_is_retained_but_not_saved_as_a_complete_batch():
    calls = []
    @cache.cached(ttl=3600)
    def records():
        calls.append(True)
        return cache.PartialRecords({'one': {'title': 'Completed record'}}) if len(calls) == 1 else {
            'one': {'title': 'Completed record'}, 'two': {'title': 'Recovered record'}}
    first = records()
    assert first['one']['title'] == 'Completed record' and first.partial_warning
    assert set(records()) == {'one', 'two'} and len(calls) == 2


def test_peek_distinguishes_missing_values_from_cached_misses(monkeypatch):
    now = [1000]
    monkeypatch.setattr(cache.time, 'time', lambda: now[0])
    @cached(ttl=3600, miss_ttl=30)
    def lookup(value):
        return None
    assert lookup.peek('id') == (False, None)
    with cache.request_scope():
        assert lookup('id') is None
        now[0] += 60
        assert lookup.peek('id') == (True, None)
    assert lookup.peek('id') == (False, None)


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
    calls = []
    @cached(ttl=60)
    def fn():
        calls.append(1)
        return len(calls)

    t1 = fn()
    clear()
    t2 = fn()
    assert (t1, t2) == (1, 2)


def test_same_function_name_in_different_sources_has_separate_cache():
    def make(source):
        def search_papers(query):
            return [{"source": source}]
        search_papers.__module__ = source
        return cached()(search_papers)
    assert make("source_a")("same query")[0]["source"] == "source_a"
    assert make("source_b")("same query")[0]["source"] == "source_b"


def test_cached_records_are_not_mutated_by_ranking_or_hydration():
    @cached()
    def papers():
        return [{"title": "Original", "external_ids": {"DOI": "10.1000/x"}}]
    result = papers()
    result[0]["title"] = "Changed"
    result[0]["external_ids"]["DOI"] = "changed"
    assert papers() == [{"title": "Original", "external_ids": {"DOI": "10.1000/x"}}]


def test_concurrent_identical_requests_share_one_fetch():
    from concurrent.futures import ThreadPoolExecutor
    calls = []
    @cached()
    def fetch(query):
        calls.append(query)
        time.sleep(0.05)
        return [{"title": query}]
    with ThreadPoolExecutor(max_workers=4) as pool:
        result = list(pool.map(fetch, ["same"] * 4))
    assert calls == ["same"]
    assert len(result) == 4
    result[0][0]["title"] = "mutated"
    assert result[1][0]["title"] == "same"


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


def test_partial_results_retry_then_cache_the_completed_response():
    class Partial(list):
        partial_warning = "only the first page arrived"

    calls = []

    @cached()
    def fetch():
        calls.append(True)
        return Partial([{"title": "A"}]) if len(calls) == 1 else [{"title": "A"}, {"title": "B"}]

    assert len(fetch()) == 1
    assert len(fetch()) == 2
    assert len(fetch()) == 2 and len(calls) == 2


def test_completed_source_response_survives_a_real_process_restart(tmp_path):
    import json
    import os
    import subprocess
    import sys
    script = '''
import json, sys
from scholar_mcp import cache, config
config.DATA_DIR = sys.argv[1]
@cache.cached(persistent=True, namespace='restart-fixture')
def fetch():
    if sys.argv[2] == 'offline':
        raise RuntimeError('provider unavailable after restart')
    return [{'title': 'Completed before interruption', 'external_ids': {'DOI': '10.1000/x'}}]
print(json.dumps(fetch()))
'''
    first = subprocess.check_output([sys.executable, '-c', script, str(tmp_path), 'online'], text=True)
    restarted = subprocess.check_output([sys.executable, '-c', script, str(tmp_path), 'offline'], text=True)
    assert json.loads(first) == json.loads(restarted)
    if os.name == 'posix':
        assert (tmp_path / 'cache.sqlite3').stat().st_mode & 0o077 == 0


def test_persistent_cache_retries_partial_results_after_restart():
    class Partial(list):
        partial_warning = 'later page timed out'
    calls = []
    @cached(persistent=True)
    def fetch():
        calls.append(True)
        return Partial([{'title': 'A'}]) if len(calls) == 1 else [{'title': 'A'}, {'title': 'B'}]
    assert len(fetch()) == 1
    cache._cache.clear()
    assert len(fetch()) == 2 and len(calls) == 2
    cache._cache.clear()
    assert len(fetch()) == 2 and len(calls) == 2


def test_unwritable_persistent_cache_preserves_the_provider_path(monkeypatch):
    import sqlite3
    monkeypatch.setattr(sqlite3, 'connect', lambda *a, **kw: (_ for _ in ()).throw(sqlite3.OperationalError('disk full')))
    @cached(persistent=True)
    def fetch():
        return [{'title': 'Still available'}]
    assert fetch()[0]['title'] == 'Still available'


def test_explicit_short_miss_cache_prevents_repeating_a_failed_lookup(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(cache.time, 'time', lambda: clock[0])
    calls = []
    @cached(ttl=3600, miss_ttl=30)
    def lookup():
        calls.append(True)
        return None if len(calls) == 1 else {'title': 'Recovered'}
    assert lookup() is None and lookup() is None and len(calls) == 1
    clock[0] += 31
    assert lookup()['title'] == 'Recovered' and len(calls) == 2


def test_persistent_cache_evicts_old_responses_to_bound_storage(monkeypatch):
    monkeypatch.setattr(cache, 'MAX_DISK_BYTES', 20)
    cache._disk('old', (time.time() + 60, 'x' * 12))
    cache._disk('new', (time.time() + 60, 'y' * 12))
    assert cache._disk('old') is None
    assert cache._disk('new')[1] == 'y' * 12


def test_request_scope_retains_explicit_misses_across_stage_delays(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from contextvars import copy_context
    clock, calls = [100.0], []
    monkeypatch.setattr(cache.time, 'time', lambda: clock[0])
    @cached(ttl=300, miss_ttl=30)
    def missing():
        calls.append(True)
        return None
    with cache.request_scope():
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(copy_context().run, missing).result() is None
        clock[0] += 60
        assert missing() is None and len(calls) == 1
    assert missing() is None and len(calls) == 2
