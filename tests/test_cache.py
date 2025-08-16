"""Tests for API response cache."""

import time
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
