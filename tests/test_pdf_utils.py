"""Tests for PDF download and extraction utilities."""

import httpx
import pytest
import os
import tempfile
from pypdf import PdfWriter
from scholar_mcp import pdf_utils


def test_title_only_papers_get_distinct_stable_filenames():
    first = pdf_utils._pdf_filename({"title": "A Study of Retrieval"})
    second = pdf_utils._pdf_filename({"title": "A Study of Ranking"})

    assert first != second
    assert first.endswith(".pdf")
    assert "unknown" not in first


def test_existing_pdf_is_reused(monkeypatch, tmp_path):
    paper = {"title": "Cached", "external_ids": {"DOI": "10.1/cache"}}
    path = tmp_path / pdf_utils._pdf_filename(paper)
    path.write_bytes(b"%PDF-1.7 cached")
    monkeypatch.setattr(
        pdf_utils,
        "_try_download",
        lambda *args, **kwargs: pytest.fail("cache should avoid the network"),
    )

    result = pdf_utils.download_paper(paper, str(tmp_path))

    assert result["success"] is True
    assert result["source"] == "cache"
    assert result["file_path"] == str(path)


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


@pytest.mark.integration
def test_extract_text():
    """Download a known paper and extract text."""
    paper_info = {
        "paper_id": "1706.03762",
        "open_access_url": None,
        "external_ids": {"ArXiv": "1706.03762"},
        "url": "",
    }
    with tempfile.TemporaryDirectory() as tmpdir:
        dl = pdf_utils.download_paper(paper_info, tmpdir)
        if dl["success"]:
            result = pdf_utils.extract_text(dl["file_path"], pages="1")
            assert len(result["content"]) > 100
            assert result["pages"] == "1"
            content = result["content"].lower()
            assert "attention" in content or "transformer" in content


def test_extract_text_returns_reusable_page_ranges(tmp_path):
    path = tmp_path / "paper.pdf"
    writer = PdfWriter()
    for _ in range(24):
        writer.add_blank_page(width=612, height=792)
    with path.open("wb") as stream:
        writer.write(stream)

    result = pdf_utils.extract_text(str(path))

    assert result["pages"] == "1-10"
    assert result["total_pages"] == 24
    assert result["next_pages"] == "11-20"


class TestLibraryProxy:
    """The institutional proxy is the last automated step before Sci-Hub and
    the least reliable one: sessions expire, publishers serve interstitials
    instead of files, and many papers are not covered at all. Failures must be
    quiet and fast rather than slowing every download.
    """

    def test_disabled_without_a_cookie(self, monkeypatch):
        from scholar_mcp import pdf_utils
        monkeypatch.delenv("LIBRARY_PROXY_COOKIE", raising=False)
        monkeypatch.setattr(pdf_utils, "_library_cookie", lambda: "")
        monkeypatch.setattr(pdf_utils.httpx, "get",
                            lambda *a, **kw: pytest.fail("should not make a request"))
        assert pdf_utils._try_ezproxy("10.1234/abc", "/tmp", "x.pdf") is None

    def test_rejects_non_pdf_responses(self, monkeypatch, tmp_path):
        """A login form or landing page returns 200 with HTML. Saving that
        would produce a file that looks like a paper and is not one.
        """
        from scholar_mcp import pdf_utils
        monkeypatch.setattr(pdf_utils, "_library_cookie", lambda: "session=abc")

        class FakeResponse:
            status_code = 200
            headers = {"content-type": "text/html; charset=utf-8"}
            content = b"<html>Sign in</html>"

        monkeypatch.setattr(pdf_utils.httpx, "get", lambda *a, **kw: FakeResponse())
        assert pdf_utils._try_ezproxy("10.1234/abc", str(tmp_path), "x.pdf") is None
        assert not (tmp_path / "x.pdf").exists()

    def test_saves_a_real_pdf(self, monkeypatch, tmp_path):
        from scholar_mcp import pdf_utils
        monkeypatch.setattr(pdf_utils, "_library_cookie", lambda: "session=abc")

        class FakeResponse:
            status_code = 200
            headers = {"content-type": "application/pdf"}
            content = b"%PDF-1.4 fake"

        monkeypatch.setattr(pdf_utils.httpx, "get", lambda *a, **kw: FakeResponse())
        path = pdf_utils._try_ezproxy("10.1234/abc", str(tmp_path), "x.pdf")
        assert path is not None
        assert (tmp_path / "x.pdf").read_bytes().startswith(b"%PDF")

    def test_network_errors_are_swallowed(self, monkeypatch, tmp_path):
        from scholar_mcp import pdf_utils
        monkeypatch.setattr(pdf_utils, "_library_cookie", lambda: "session=abc")

        def boom(*a, **kw):
            raise httpx.ConnectError("proxy unreachable")

        monkeypatch.setattr(pdf_utils.httpx, "get", boom)
        assert pdf_utils._try_ezproxy("10.1234/abc", str(tmp_path), "x.pdf") is None

    def test_cookie_is_read_from_env_first(self, monkeypatch):
        from scholar_mcp import pdf_utils
        monkeypatch.setenv("LIBRARY_PROXY_COOKIE", "  session=fromenv  ")
        assert pdf_utils._library_cookie() == "session=fromenv"
