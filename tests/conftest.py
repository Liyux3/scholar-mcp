"""Keep verification tests offline and independent of the user's browser state."""
import httpx
import pytest

from scholar_mcp import (
    access,
    cache,
    config,
    knowledge_base,
    library_connectors,
    relevance,
    scholar_session,
    vault,
)
from scholar_mcp.sources import dblp, google_scholar, pacing


@pytest.fixture(autouse=True)
def isolated_google_state(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(relevance, "_local_score_cache", {})
    monkeypatch.setattr(access, "_host_until", {})
    # Provider fixtures patch httpx.get; production uses its shared keep-alive client.
    monkeypatch.setattr(access, "_native_get", lambda url, **kw: httpx.get(url, **kw))
    monkeypatch.setenv("SCHOLAR_GOOGLE_RECOVERY", "off")
    monkeypatch.setenv("SCHOLAR_USAGE_LOG", "off")
    monkeypatch.delenv("SCHOLAR_ACCESS_COMMAND", raising=False)
    monkeypatch.setattr(scholar_session, "_path", lambda: tmp_path / "sessions/google.json")
    monkeypatch.setattr(library_connectors, "_saved_credentials", lambda _: {})
    monkeypatch.setattr(knowledge_base, "DEFAULT_KB_DIR", str(tmp_path / "kb"))
    # Each test gets fresh pacing slots, never the machine's shared ones, and
    # reservations are kept but never slept on.
    monkeypatch.setattr(pacing, "state_dir", lambda: tmp_path / "pace")
    monkeypatch.setattr(pacing, "_sleep", lambda seconds: None)
    monkeypatch.setattr(vault, "DEFAULT_VAULT_DIR", tmp_path / "vault")


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
        monkeypatch.setattr(relevance, "_flashrank_rankers", {})
        monkeypatch.setattr(relevance, "_flashrank_scores", _no_local_model)
        monkeypatch.setattr(relevance, "_gpu_device", "")


def _no_local_model(*args, **kwargs):
    """The real ranking model never loads in unit tests; tests that need scores stub them."""
    raise OSError("no local ranking model in unit tests")


@pytest.fixture(autouse=True)
def isolated_source_sessions(monkeypatch):
    import atexit
    for source in (dblp, google_scholar):
        monkeypatch.setattr(source, "_client", None)
    yield
    for source in (dblp, google_scholar):
        if source._client is not None:
            atexit.unregister(source._client.close)
            source._client.close()
