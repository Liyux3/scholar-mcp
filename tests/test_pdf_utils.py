"""Tests for PDF download and structured paper reading."""

import httpx
import pytest
import os
import tempfile
from urllib.parse import urlparse

from scholar_mcp import pdf_utils


def test_title_only_papers_get_distinct_stable_filenames():
    first = pdf_utils._pdf_filename({"title": "A Study of Retrieval"})
    second = pdf_utils._pdf_filename({"title": "A Study of Ranking"})

    assert first != second
    assert first.endswith(".pdf")
    assert "unknown" not in first


@pytest.mark.parametrize("identifier", ["CON", "aux", "COM1", "LPT9", "NUL.record"])
def test_pdf_identifiers_are_portable_on_windows(identifier):
    assert pdf_utils._pdf_filename({"paper_id": identifier}) == f"paper-{identifier}.pdf"


@pytest.mark.parametrize("prefix", [b"", b"\xef\xbb\xbf\n"])
def test_existing_pdf_is_reused(monkeypatch, tmp_path, prefix):
    paper = {"title": "Cached", "external_ids": {"DOI": "10.1/cache"}}
    path = tmp_path / pdf_utils._pdf_filename(paper)
    path.write_bytes(prefix + b"%PDF-1.7 cached")
    monkeypatch.setattr(
        pdf_utils,
        "_try_download",
        lambda *args, **kwargs: pytest.fail("cache should avoid the network"),
    )

    result = pdf_utils.download_paper(paper, str(tmp_path))

    assert result["success"] is True
    assert result["source"] == "cache"
    assert result["file_path"] == str(path)


def test_relative_download_returns_a_path_that_survives_cwd_changes(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(pdf_utils.httpx, "Client", lambda **kwargs: _StreamClient([b"%PDF-1.7\ncomplete"], **kwargs))
    paper = {"title": "Persistent Path", "paper_id": "stable", "open_access_url": "https://example.test/paper.pdf"}
    result = pdf_utils.download_paper(paper, "downloads")
    destination = tmp_path / "downloads" / "stable.pdf"
    assert result["success"] is True
    assert result["file_path"] == str(destination.resolve())
    monkeypatch.chdir(tmp_path.parent)
    with open(result["file_path"], "rb") as stored:
        assert stored.read() == b"%PDF-1.7\ncomplete"


class _StreamResponse:
    def __init__(self, chunks):
        self.chunks = chunks

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def raise_for_status(self):
        return None

    def iter_bytes(self, chunk_size):
        del chunk_size
        for chunk in self.chunks:
            if isinstance(chunk, Exception):
                raise chunk
            yield chunk


class _StreamClient:
    def __init__(self, chunks, **kwargs):
        del kwargs
        self.chunks = chunks

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def stream(self, method, url, headers):
        del method, url, headers
        return _StreamResponse(self.chunks)


def test_download_streams_and_atomically_publishes(monkeypatch, tmp_path):
    chunks = [b"%PDF-1.7\n", b"first chunk", b"second chunk"]
    monkeypatch.setattr(
        pdf_utils.httpx,
        "Client",
        lambda **kwargs: _StreamClient(chunks, **kwargs),
    )

    result = pdf_utils._try_download("https://example.test/paper", str(tmp_path), "paper.pdf")

    assert result == str(tmp_path / "paper.pdf")
    assert (tmp_path / "paper.pdf").read_bytes() == b"".join(chunks)
    assert list(tmp_path.glob("*.part")) == []
    assert list(tmp_path.glob(".*.part")) == []


def test_interrupted_download_never_publishes_partial_pdf(monkeypatch, tmp_path):
    chunks = [b"%PDF-1.7\npartial", OSError("connection lost")]
    monkeypatch.setattr(
        pdf_utils.httpx,
        "Client",
        lambda **kwargs: _StreamClient(chunks, **kwargs),
    )

    result = pdf_utils._try_download("https://example.test/paper", str(tmp_path), "paper.pdf")

    assert result is None
    assert not (tmp_path / "paper.pdf").exists()
    assert list(tmp_path.glob(".*.part")) == []


def test_non_pdf_download_is_discarded(monkeypatch, tmp_path):
    monkeypatch.setattr(
        pdf_utils.httpx,
        "Client",
        lambda **kwargs: _StreamClient([b"<html>not a paper</html>"], **kwargs),
    )

    result = pdf_utils._try_download("https://example.test/paper", str(tmp_path), "paper.pdf")

    assert result is None
    assert not (tmp_path / "paper.pdf").exists()
    assert list(tmp_path.glob(".*.part")) == []


@pytest.mark.integration
def test_download_from_arxiv():
    paper_info = {
        "paper_id": "1706.03762",
        "open_access_url": None,
        "external_ids": {"ArXiv": "1706.03762"},
        "url": "https://www.semanticscholar.org/paper/test",
    }
    with tempfile.TemporaryDirectory() as tmpdir:
        result = pdf_utils.download_paper(paper_info, tmpdir)
        assert result["success"] is True
        assert result["source"] == "arxiv"
        assert os.path.exists(result["file_path"])


def test_download_nonexistent_paper(monkeypatch):
    paper_info = {
        "paper_id": "nonexistent",
        "open_access_url": None,
        "external_ids": {},
        "url": "",
    }
    monkeypatch.setattr(pdf_utils.sources, "resolve_pdf_candidates", lambda paper: [])
    with tempfile.TemporaryDirectory() as tmpdir:
        result = pdf_utils.download_paper(paper_info, tmpdir)
        assert result["success"] is False


def test_registered_repository_candidate_uses_shared_download_path(monkeypatch, tmp_path):
    paper_info = {
        "paper_id": "paper",
        "title": "A paper",
        "open_access_url": None,
        "external_ids": {},
        "url": "",
    }
    monkeypatch.setattr(
        pdf_utils.sources,
        "resolve_pdf_candidates",
        lambda paper: [("hal", "https://example.test/paper.pdf")],
    )
    monkeypatch.setattr(
        pdf_utils,
        "_prioritize_pdf_candidates",
        lambda candidates: candidates,
    )
    monkeypatch.setattr(
        pdf_utils,
        "_try_download",
        lambda url, save_path, filename: str(tmp_path / filename),
    )

    result = pdf_utils.download_paper(paper_info, str(tmp_path))

    assert result["success"] is True
    assert result["source"] == "hal"


def test_repository_probes_move_confirmed_pdfs_forward_without_dropping_fallbacks(monkeypatch):
    candidates = [
        ("openaire", "https://example.test/landing"),
        ("hal", "https://example.test/paper.pdf"),
        ("zenodo", "https://example.test/archive.pdf"),
    ]
    monkeypatch.setattr(
        pdf_utils,
        "_probe_pdf",
        lambda url: url.endswith(".pdf"),
    )

    ordered = pdf_utils._prioritize_pdf_candidates(candidates)

    assert ordered == [candidates[1], candidates[2], candidates[0]]


PROXY_ENV = ("LIBRARY_PROXY_PREFIX", "LIBRARY_PROXY_BASE",
             "LIBRARY_PROXY_COOKIES", "LIBRARY_PROXY_COOKIE")
PREFIX = "https://eproxy.example.edu/login?url="
COOKIE_FILE = (
    "# Netscape HTTP Cookie File\n"
    "#HttpOnly_.eproxy.example.edu\tTRUE\t/\tTRUE\t0\tezproxy\tSECRETSESSION\n"
    ".eproxy.example.edu\tTRUE\t/\tTRUE\t1\tstale\tEXPIREDVALUE\n"
    ".unrelated.example.org\tTRUE\t/\tTRUE\t0\tprivate\tOTHERSITE\n"
)


def _route_requests(monkeypatch, handler):
    """Serve the proxy route from a handler instead of the network."""
    real_client = httpx.Client
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(pdf_utils.httpx, "Client",
                        lambda **kwargs: real_client(transport=transport, **kwargs))


@pytest.fixture
def proxy_env(monkeypatch, tmp_path):
    """No proxy settings from the developer's own environment or data directory."""
    for name in PROXY_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(pdf_utils.config, "DATA_DIR", tmp_path / "data")
    return monkeypatch


@pytest.fixture
def configured_proxy(proxy_env, tmp_path):
    cookies = tmp_path / "cookies.txt"
    cookies.write_text(COOKIE_FILE, encoding="utf-8")
    proxy_env.setenv("LIBRARY_PROXY_PREFIX", PREFIX)
    proxy_env.setenv("LIBRARY_PROXY_COOKIES", str(cookies))
    return cookies


class TestLibraryProxy:
    """The institutional proxy is the last automated step before Sci-Hub and
    the least reliable one: sessions expire, publishers serve interstitials
    instead of files, and many papers are not covered at all. Failures must be
    quiet and fast rather than slowing every download.
    """

    @pytest.fixture(autouse=True)
    def legacy_proxy(self, proxy_env):
        proxy_env.setenv("LIBRARY_PROXY_BASE", "https://library.example")

    def test_disabled_without_an_institution(self, monkeypatch):
        monkeypatch.delenv("LIBRARY_PROXY_BASE", raising=False)
        monkeypatch.setattr(pdf_utils, "_library_cookie", lambda: "session=abc")
        _route_requests(monkeypatch, lambda request: pytest.fail("should not make a request"))
        assert pdf_utils._try_ezproxy("10.1234/abc", "/tmp", "x.pdf") is None

    def test_disabled_without_a_cookie(self, monkeypatch):
        monkeypatch.setattr(pdf_utils, "_library_cookie", lambda: "")
        _route_requests(monkeypatch, lambda request: pytest.fail("should not make a request"))
        assert pdf_utils._try_ezproxy("10.1234/abc", "/tmp", "x.pdf") is None

    def test_rejects_non_pdf_responses(self, monkeypatch, tmp_path):
        """A landing page returns 200 with HTML. Saving that would produce a
        file that looks like a paper and is not one.
        """
        monkeypatch.setattr(pdf_utils, "_library_cookie", lambda: "session=abc")

        def handler(request):
            if request.url.path == "/login":
                return httpx.Response(302, headers={
                    "location": "https://pub-example-org.library.example/abstract"})
            return httpx.Response(200, headers={"content-type": "text/html"},
                                  content=b"<html>Abstract</html>")

        _route_requests(monkeypatch, handler)
        notes = []
        assert pdf_utils._try_ezproxy("10.1234/abc", str(tmp_path), "x.pdf", notes) is None
        assert not (tmp_path / "x.pdf").exists()
        assert "did not return a PDF" in " ".join(notes)

    def test_saves_a_real_pdf_with_the_legacy_cookie(self, monkeypatch, tmp_path):
        monkeypatch.setattr(pdf_utils, "_library_cookie", lambda: "session=abc; other=1")
        seen = []

        def handler(request):
            seen.append((str(request.url), request.headers.get("cookie")))
            return httpx.Response(200, headers={"content-type": "application/pdf"},
                                  content=b"%PDF-1.4 fake")

        _route_requests(monkeypatch, handler)
        path = pdf_utils._try_ezproxy("10.1234/abc", str(tmp_path), "x.pdf")
        assert path is not None
        assert (tmp_path / "x.pdf").read_bytes().startswith(b"%PDF")
        assert len(seen) == 1
        assert seen[0][0] == "https://library.example/login?url=https://doi.org/10.1234/abc"
        assert set(seen[0][1].split("; ")) == {"session=abc", "other=1"}

    def test_legacy_cookie_stays_on_the_proxy_domain(self, monkeypatch, tmp_path):
        """The old raw header went to every redirect hop, including publishers."""
        monkeypatch.setattr(pdf_utils, "_library_cookie", lambda: "session=abc")
        cookies = {}

        def handler(request):
            cookies[request.url.host] = request.headers.get("cookie")
            if request.url.host == "library.example":
                return httpx.Response(302, headers={"location": "https://doi.org/10.1234/abc"})
            return httpx.Response(200, headers={"content-type": "application/pdf"},
                                  content=b"%PDF-1.4 fake")

        _route_requests(monkeypatch, handler)
        assert pdf_utils._try_ezproxy("10.1234/abc", str(tmp_path), "x.pdf")
        assert cookies == {"library.example": "session=abc", "doi.org": None}

    def test_network_errors_are_swallowed(self, monkeypatch, tmp_path):
        monkeypatch.setattr(pdf_utils, "_library_cookie", lambda: "session=abc")

        def boom(request):
            raise httpx.ConnectError("proxy unreachable", request=request)

        _route_requests(monkeypatch, boom)
        assert pdf_utils._try_ezproxy("10.1234/abc", str(tmp_path), "x.pdf") is None

    def test_cookie_is_read_from_env_first(self, monkeypatch):
        monkeypatch.setenv("LIBRARY_PROXY_COOKIE", "  session=fromenv  ")
        assert pdf_utils._library_cookie() == "session=fromenv"

    def test_cookie_file_uses_configured_data_directory(self, monkeypatch, tmp_path):
        monkeypatch.delenv("LIBRARY_PROXY_COOKIE", raising=False)
        monkeypatch.setattr(pdf_utils.config, "DATA_DIR", tmp_path)
        (tmp_path / "library_cookie.txt").write_text(" session=fromfile\n", encoding="utf-8")
        assert pdf_utils._library_cookie() == "session=fromfile"


class TestLibraryProxyRoute:
    """Netscape cookie export, publisher resolution and the place of the route
    in the download chain."""

    PAPER = {"title": "Proxy paper", "external_ids": {"DOI": "10.1126/science.abc123"}}

    @pytest.fixture
    def chain(self, monkeypatch):
        """Everything before the proxy finds nothing; Sci-Hub is recorded."""
        monkeypatch.setattr(pdf_utils.sources, "resolve_pdf_candidates", lambda info: [])
        monkeypatch.setattr(pdf_utils, "_try_unpaywall", lambda doi: None)
        monkeypatch.setattr(pdf_utils.config, "SCIHUB_ENABLED", True)
        calls = []
        monkeypatch.setattr(pdf_utils, "_try_scihub",
                            lambda doi, save_path, filename: calls.append(doi))
        return calls

    def test_configured_session_downloads_a_pdf_through_the_proxy(
            self, configured_proxy, monkeypatch, tmp_path, chain):
        requests = []

        def handler(request):
            requests.append((str(request.url), request.headers.get("cookie")))
            if request.url.host == "eproxy.example.edu":
                # EZproxy hands the session to the rewritten publisher hostname.
                return httpx.Response(302, headers={
                    "location": "https://www-science-org.eproxy.example.edu/doi/pdf/10.1126/science.abc123"})
            return httpx.Response(200, headers={"content-type": "application/pdf"},
                                  content=b"%PDF-1.7 proxied paper")

        _route_requests(monkeypatch, handler)
        result = pdf_utils.download_paper(self.PAPER, str(tmp_path))

        assert result["success"] and result["source"] == "library_proxy"
        assert (tmp_path / "10.1126_science.abc123.pdf").read_bytes() == b"%PDF-1.7 proxied paper"
        assert requests[0][0] == (PREFIX + "https://www.science.org/doi/pdf/10.1126/science.abc123")
        # The session cookie follows the redirect to the rewritten hostname;
        # cookies the file held for other sites, or expired, are never sent.
        assert requests[1][1] == "ezproxy=SECRETSESSION"
        assert all("OTHERSITE" not in (cookie or "") and "EXPIRED" not in (cookie or "")
                   for _, cookie in requests)
        assert chain == [], "Sci-Hub must not run once the proxy delivered the PDF"

    def test_html_without_a_pdf_pointer_is_not_saved(self, configured_proxy, monkeypatch, tmp_path):
        _route_requests(monkeypatch, lambda request: httpx.Response(
            200, headers={"content-type": "application/pdf"}, content=b"<html>paywall</html>"))
        notes = []
        assert pdf_utils._try_ezproxy("10.1234/abc", str(tmp_path), "x.pdf", notes) is None
        assert list(tmp_path.glob("*.pdf")) == []

    def test_landing_page_pointer_is_followed_through_the_proxy(
            self, configured_proxy, monkeypatch, tmp_path):
        requests = []

        def handler(request):
            requests.append(str(request.url))
            if request.url.host == "eproxy.example.edu":
                # EZproxy rewrites the target host and keeps the path and query.
                target = urlparse(str(request.url).split("login?url=", 1)[1])
                return httpx.Response(302, headers={"location": (
                    f"https://pub-example-org.eproxy.example.edu{target.path}"
                    + (f"?{target.query}" if target.query else ""))})
            if request.url.path == "/10.1234/abc":
                return httpx.Response(200, headers={"content-type": "text/html"}, content=(
                    b'<html><head><meta name="citation_pdf_url" '
                    b'content="https://pub.example.org/article/1.pdf?a=1&amp;b=2"></head></html>'))
            return httpx.Response(200, headers={"content-type": "application/pdf"},
                                  content=b"%PDF-1.5 pointer")

        _route_requests(monkeypatch, handler)
        path = pdf_utils._try_ezproxy("10.1234/abc", str(tmp_path), "x.pdf")

        assert path and (tmp_path / "x.pdf").read_bytes() == b"%PDF-1.5 pointer"
        assert requests[0] == PREFIX + "https://doi.org/10.1234/abc"
        # The pointer names the publisher's own host, so it goes through the prefix.
        assert PREFIX + "https://pub.example.org/article/1.pdf?a=1&b=2" in requests

    def test_expired_session_reports_re_export_and_chain_continues(
            self, configured_proxy, monkeypatch, tmp_path, chain):
        _route_requests(monkeypatch, lambda request: httpx.Response(
            200, headers={"content-type": "text/html"},
            content=b'<html><form action="/login"><input type="password" name="pass"></form></html>'))

        result = pdf_utils.download_paper(self.PAPER, str(tmp_path))

        assert chain == ["10.1126/science.abc123"], "Sci-Hub still runs after an expired session"
        assert not result["success"]
        assert "expired" in result["message"] and "export cookies" in result["message"]
        assert (PREFIX + "https://doi.org/10.1126/science.abc123") in result["message"]
        assert "SECRETSESSION" not in result["message"]

    def test_expired_session_message_survives_a_later_source_succeeding(
            self, configured_proxy, monkeypatch, tmp_path, chain):
        _route_requests(monkeypatch, lambda request: httpx.Response(
            302, headers={"location": "https://eproxy.example.edu/login?url=x"})
            if request.url.path != "/login" else httpx.Response(
                200, headers={"content-type": "text/html"}, content=b"<html>Sign in</html>"))
        monkeypatch.setattr(pdf_utils, "_try_scihub", lambda doi, save_path, filename: "/tmp/a.pdf")

        result = pdf_utils.download_paper(self.PAPER, str(tmp_path))

        assert result["source"] == "scihub"
        assert "export cookies" in result["message"]

    def test_not_configured_skips_the_route(self, proxy_env, monkeypatch, tmp_path, chain):
        _route_requests(monkeypatch, lambda request: pytest.fail("should not make a request"))

        result = pdf_utils.download_paper(self.PAPER, str(tmp_path))

        assert chain == ["10.1126/science.abc123"]
        assert not result["success"]
        assert "proxy" not in result["message"].lower()

    def test_prefix_without_cookies_skips_requests_but_offers_a_link(
            self, proxy_env, monkeypatch, tmp_path, chain):
        proxy_env.setenv("LIBRARY_PROXY_PREFIX", PREFIX)
        _route_requests(monkeypatch, lambda request: pytest.fail("should not make a request"))

        result = pdf_utils.download_paper(self.PAPER, str(tmp_path))

        assert not result["success"]
        assert "LIBRARY_PROXY_COOKIES" in result["message"]
        assert result["message"].endswith(
            f"Library proxy link: {PREFIX}https://doi.org/10.1126/science.abc123")

    def test_malformed_prefix_is_reported_not_used(self, proxy_env, monkeypatch, tmp_path, chain):
        proxy_env.setenv("LIBRARY_PROXY_PREFIX", "eproxy.example.edu/login?url=")
        _route_requests(monkeypatch, lambda request: pytest.fail("should not make a request"))
        notes = []
        assert pdf_utils._try_ezproxy("10.1/x", str(tmp_path), "x.pdf", notes) is None
        assert "not an http(s) URL" in notes[0]

    def test_cookie_file_without_usable_cookies_asks_for_a_fresh_export(
            self, proxy_env, monkeypatch, tmp_path):
        stale = tmp_path / "stale.txt"
        stale.write_text(".eproxy.example.edu\tTRUE\t/\tTRUE\t1\tezproxy\tOLDVALUE\n")
        proxy_env.setenv("LIBRARY_PROXY_PREFIX", PREFIX)
        proxy_env.setenv("LIBRARY_PROXY_COOKIES", str(stale))
        _route_requests(monkeypatch, lambda request: pytest.fail("should not make a request"))
        notes = []

        assert pdf_utils._try_ezproxy("10.1/x", str(tmp_path), "x.pdf", notes) is None
        assert "expired" in notes[0] and "export cookies.txt again" in notes[0]
        assert "OLDVALUE" not in notes[0]

    def test_missing_cookie_file_is_reported(self, proxy_env, monkeypatch, tmp_path):
        proxy_env.setenv("LIBRARY_PROXY_PREFIX", PREFIX)
        proxy_env.setenv("LIBRARY_PROXY_COOKIES", str(tmp_path / "absent.txt"))
        notes = []
        assert pdf_utils._try_ezproxy("10.1/x", str(tmp_path), "x.pdf", notes) is None
        assert "could not be read" in notes[0]

    def test_cookie_export_keeps_httponly_lines_and_only_proxy_cookies(self, tmp_path):
        path = tmp_path / "cookies.txt"
        path.write_text(COOKIE_FILE, encoding="utf-8")

        jar, expired = pdf_utils._load_cookie_file(path, "eproxy.example.edu")

        assert {cookie.name for cookie in jar.jar} == {"ezproxy"}
        assert expired == 1
        assert all(cookie.secure for cookie in jar.jar)

    @pytest.mark.parametrize("destination", [
        "http://eproxy.example.edu/article", "https://other.example.edu/article",
    ])
    def test_session_is_not_sent_after_insecure_or_external_redirect(
            self, configured_proxy, monkeypatch, tmp_path, destination):
        cookies = tmp_path / "cookies.txt"
        cookies.write_text(COOKIE_FILE.replace(".eproxy.example.edu", ".example.edu"))
        observed = []

        def handler(request):
            observed.append(request.headers.get("cookie"))
            if len(observed) == 1:
                return httpx.Response(302, headers={"location": destination})
            return httpx.Response(200, content=b"%PDF-1.4 fixture")

        _route_requests(monkeypatch, handler)
        assert pdf_utils._try_ezproxy("10.1234/abc", str(tmp_path), "x.pdf")
        assert observed == ["ezproxy=SECRETSESSION", None]

    def test_interrupted_proxy_stream_never_publishes_a_partial_pdf(
            self, configured_proxy, monkeypatch, tmp_path):
        class Interrupted(httpx.SyncByteStream):
            def __iter__(self):
                yield b"%PDF-1.4" + b" " * pdf_utils.DOWNLOAD_CHUNK_SIZE
                raise httpx.ReadError("interrupted fixture")

        _route_requests(monkeypatch, lambda request: httpx.Response(200, stream=Interrupted()))
        assert pdf_utils._try_ezproxy("10.1234/abc", str(tmp_path), "x.pdf") is None
        assert not (tmp_path / "x.pdf").exists()
        assert not list(tmp_path.glob("*.part"))

    def test_login_handoff_page_follows_its_scoped_continue_link(
            self, configured_proxy, monkeypatch, tmp_path):
        target = "https://publisher.eproxy.example.edu/article.pdf"

        def handler(request):
            if request.url.host == "eproxy.example.edu":
                return httpx.Response(200, text=f'<p>Wait or click <a href="{target}">here</a> to continue</p>')
            assert str(request.url) == target
            return httpx.Response(200, content=b"%PDF-1.4 fixture")

        _route_requests(monkeypatch, handler)
        notes = []
        assert pdf_utils._try_ezproxy("10.1234/abc", str(tmp_path), "x.pdf", notes)
        assert notes == []

    @pytest.mark.parametrize("doi,expected", [
        ("10.1126/science.abc123", "https://www.science.org/doi/pdf/10.1126/science.abc123"),
        ("10.1038/s41586-020-2649-2", "https://www.nature.com/articles/s41586-020-2649-2.pdf"),
        ("10.1007/s00000-020-0001-1", "https://link.springer.com/content/pdf/10.1007/s00000-020-0001-1.pdf"),
        ("10.1145/3292500.3330701", "https://dl.acm.org/doi/pdf/10.1145/3292500.3330701"),
        ("10.1002/adma.202000001", "https://onlinelibrary.wiley.com/doi/pdfdirect/10.1002/adma.202000001"),
    ])
    def test_publisher_pdf_patterns(self, doi, expected):
        assert pdf_utils._publisher_pdf_urls(doi) == [expected]

    def test_publishers_without_a_doi_pattern_use_the_landing_page(self):
        assert pdf_utils._publisher_pdf_urls("10.1109/TPAMI.2020.2978333") == []
        assert pdf_utils._publisher_pdf_urls("10.1016/j.cell.2020.01.001") == []
        ieee = pdf_utils._landing_pdf_url(
            "https://ieeexplore-ieee-org.eproxy.example.edu/document/9021877/", "<html></html>")
        assert ieee == ("https://ieeexplore-ieee-org.eproxy.example.edu"
                        "/stampPDF/getPDF.jsp?tp=&arnumber=9021877")
        elsevier = pdf_utils._landing_pdf_url(
            "https://www-sciencedirect-com.eproxy.example.edu/science/article/pii/S0092867420300001",
            "<html></html>")
        assert elsevier.endswith("/science/article/pii/S0092867420300001/pdfft"
                                 "?isDTMRedir=true&download=true")
