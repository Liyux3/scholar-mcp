"""Tests for PDF download and extraction utilities."""

import httpx
import pytest
import os
import tempfile
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


def test_download_nonexistent_paper():
    paper_info = {
        "paper_id": "nonexistent",
        "open_access_url": None,
        "external_ids": {},
        "url": "",
    }
    with tempfile.TemporaryDirectory() as tmpdir:
        result = pdf_utils.download_paper(paper_info, tmpdir)
        assert result["success"] is False


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
            text = pdf_utils.extract_text(dl["file_path"], max_pages=1)
            assert len(text) > 100
            assert "attention" in text.lower() or "transformer" in text.lower()


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
