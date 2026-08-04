"""Shared test fixtures."""

import pytest

from scholar_mcp import cache


@pytest.fixture(autouse=True)
def clear_response_cache():
    """Empty the response cache around every test.

    The cache is a module-level dict shared by the whole process, so without
    this a test that exercises a cached function leaves a value behind that
    the next test silently receives instead of calling its own stub. The
    failure looks like the code ignoring a monkeypatch, which is a confusing
    place to start debugging from, and it only appears once a function gains
    an @cached decorator, long after the tests were written.
    """
    cache._cache.clear()
    yield
    cache._cache.clear()
