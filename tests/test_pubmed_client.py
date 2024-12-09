"""Tests for PubMed E-utilities client."""

from scholar_mcp import pubmed_client


def test_search_papers():
    results = pubmed_client.search_papers("CRISPR gene editing", max_results=3)
    assert len(results) > 0
    paper = results[0]
    assert paper["source"] == "pubmed"
    assert "paper_id" in paper
    assert paper["paper_id"].startswith("pmid_")
    assert "title" in paper


def test_format_paper_handles_fields():
    data = {
        "title": "Test Paper.",
        "authors": [{"name": "Smith J"}],
        "pubdate": "2023 Jan",
        "fulljournalname": "Nature",
        "articleids": [{"idtype": "doi", "value": "10.1234/test"}],
    }
    result = pubmed_client.format_paper(data, "12345")
    assert result["paper_id"] == "pmid_12345"
    assert result["title"] == "Test Paper"  # trailing dot stripped
    assert result["authors"] == ["Smith J"]
    assert result["year"] == 2023
    assert result["venue"] == "Nature"
    assert result["external_ids"]["DOI"] == "10.1234/test"
    assert result["external_ids"]["PMID"] == "12345"
    assert result["source"] == "pubmed"


def test_output_format_matches_s2():
    """PubMed results should have the same keys as S2 results."""
    results = pubmed_client.search_papers("cancer immunotherapy", max_results=1)
    if results:
        expected_keys = {
            "paper_id", "title", "authors", "abstract", "year", "venue",
            "citation_count", "influential_citations", "is_open_access",
            "open_access_url", "fields_of_study", "publication_date",
            "tldr", "external_ids", "url", "source",
        }
        assert expected_keys == set(results[0].keys())
