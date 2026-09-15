"""Keep verification tests offline and independent of the user's browser state."""
import pytest
import httpx
from scholar_mcp import cache, relevance, scholar_session


@pytest.fixture(autouse=True)
def isolated_google_state(monkeypatch, tmp_path):
    monkeypatch.setenv("SCHOLAR_GOOGLE_RECOVERY", "off")
    monkeypatch.setattr(scholar_session, "_path", lambda: tmp_path / "sessions/google.json")


@pytest.fixture(autouse=True)
def offline_unit_transport(monkeypatch, request):
    if request.node.get_closest_marker("integration"):
        return
    def forbidden(*_args, **_kwargs):
        pytest.fail("Unit test attempted real HTTP; provide a transport fixture or mark integration")
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)


@pytest.fixture(autouse=True)
def clear_response_cache():
    """Prevent cached values from one test bypassing the next test's stubs."""
    cache._cache.clear()
    yield
    cache._cache.clear()


@pytest.fixture(autouse=True)
def isolated_process_caches(monkeypatch, request):
    if not request.node.get_closest_marker("integration"):
        # Model behavior is supplied explicitly by the tests that exercise it.
        monkeypatch.setattr(relevance, "_load_keybert", lambda: None)
