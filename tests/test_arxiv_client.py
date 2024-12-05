"""Tests for arXiv fallback client."""

from scholar_mcp import arxiv_client


def test_search_papers():
    results = arxiv_client.search_papers("transformer", max_results=3)
    assert len(results) > 0
    paper = results[0]
    assert paper["source"] == "arxiv"
    assert paper["is_open_access"] is True
    assert "arxiv.org/pdf/" in paper["open_access_url"]


def test_get_pdf_url():
    url = arxiv_client.get_pdf_url("1706.03762")
    assert url == "https://arxiv.org/pdf/1706.03762.pdf"


def test_output_format_matches_s2():
    """arXiv results should have the same keys as S2 results."""
    results = arxiv_client.search_papers("BERT", max_results=1)
    assert len(results) > 0
    paper = results[0]
    expected_keys = {
        "paper_id", "title", "authors", "abstract", "year", "venue",
        "citation_count", "influential_citations", "is_open_access",
        "open_access_url", "fields_of_study", "publication_date",
        "tldr", "external_ids", "url", "source",
    }
    assert expected_keys == set(paper.keys())
