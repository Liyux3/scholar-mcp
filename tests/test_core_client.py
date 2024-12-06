"""Tests for CORE API client."""

from scholar_mcp import core_client


def test_search_papers():
    results = core_client.search_papers("deep learning", limit=3)
    assert len(results) > 0
    paper = results[0]
    assert paper["source"] == "core"
    assert "paper_id" in paper
    assert paper["paper_id"].startswith("core_")
    assert "title" in paper


def test_search_papers_quality_sort():
    """Results with PDFs and citations should come first."""
    results = core_client.search_papers("machine learning", limit=10)
    if len(results) >= 2:
        # First result should have PDF if any do
        has_pdf = [r for r in results if r["open_access_url"]]
        if has_pdf:
            assert results[0]["open_access_url"] is not None


def test_get_pdf_url_by_doi():
    # Well-known DOI for "Attention Is All You Need"
    url = core_client.get_pdf_url(doi="10.48550/arXiv.1706.03762")
    # May or may not find it, but should not crash
    assert url is None or url.startswith("http")


def test_get_pdf_url_by_title():
    url = core_client.get_pdf_url(title="Attention Is All You Need")
    assert url is None or url.startswith("http")


def test_format_paper_handles_missing_fields():
    minimal = {"id": 12345, "title": "Test Paper"}
    result = core_client.format_paper(minimal)
    assert result["paper_id"] == "core_12345"
    assert result["title"] == "Test Paper"
    assert result["authors"] == []
    assert result["source"] == "core"


def test_output_format_matches_s2():
    """CORE results should have the same keys as S2 results."""
    results = core_client.search_papers("neural network", limit=1)
    if results:
        expected_keys = {
            "paper_id", "title", "authors", "abstract", "year", "venue",
            "citation_count", "influential_citations", "is_open_access",
            "open_access_url", "fields_of_study", "publication_date",
            "tldr", "external_ids", "url", "source",
        }
        assert expected_keys == set(results[0].keys())
