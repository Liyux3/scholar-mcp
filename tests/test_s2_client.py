"""Tests for Semantic Scholar API client."""

import pytest
from scholar_mcp import s2_client


def test_search_papers():
    results = s2_client.search_papers("attention is all you need", limit=3)
    assert len(results) > 0
    paper = results[0]
    assert "paper_id" in paper
    assert "title" in paper
    assert "authors" in paper
    assert paper["source"] == "semantic_scholar"


def test_search_papers_with_filters():
    results = s2_client.search_papers(
        "neural network", limit=5, year="2023", min_citations=10
    )
    assert len(results) > 0
    for paper in results:
        assert paper["citation_count"] >= 10


def test_get_paper_by_s2_id():
    # Attention Is All You Need
    paper = s2_client.get_paper("204e3073870fae3d05bcbc2f6a8e263d9b72e776")
    assert paper["title"] == "Attention is All you Need"
    assert paper["year"] == 2017
    assert "bibtex" in paper
    assert paper["citation_count"] > 0


def test_get_paper_by_arxiv_id():
    paper = s2_client.get_paper("ArXiv:1706.03762")
    assert "attention" in paper["title"].lower()


def test_get_paper_by_doi():
    # BERT paper (published in NAACL, has a proper DOI)
    paper = s2_client.get_paper("DOI:10.18653/v1/N19-1423")
    assert paper["year"] == 2019


def test_get_citations():
    results = s2_client.get_citations(
        "204e3073870fae3d05bcbc2f6a8e263d9b72e776", limit=5
    )
    assert len(results) > 0
    assert all("paper_id" in p for p in results)


def test_get_references():
    results = s2_client.get_references(
        "204e3073870fae3d05bcbc2f6a8e263d9b72e776", limit=5
    )
    assert len(results) > 0


def test_get_recommendations():
    results = s2_client.get_recommendations(
        "204e3073870fae3d05bcbc2f6a8e263d9b72e776", limit=5
    )
    assert isinstance(results, list)


def test_search_authors():
    results = s2_client.search_authors("Yoshua Bengio", limit=3)
    assert len(results) > 0
    author = results[0]
    assert "author_id" in author
    assert "name" in author
    assert "h_index" in author


def test_format_paper_handles_missing_fields():
    """format_paper should not crash on sparse data."""
    minimal = {"paperId": "abc123", "title": "Test"}
    result = s2_client.format_paper(minimal)
    assert result["paper_id"] == "abc123"
    assert result["title"] == "Test"
    assert result["authors"] == []
    assert result["citation_count"] == 0
    assert result["open_access_url"] is None
