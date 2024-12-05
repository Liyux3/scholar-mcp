"""Tests for PDF download and extraction utilities."""

import os
import tempfile
from scholar_mcp import pdf_utils


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
