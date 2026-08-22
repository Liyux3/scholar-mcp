"""Tests for Crossref API client."""

from scholar_mcp import crossref_client


def test_format_paper_basic():
    item = {
        "DOI": "10.1038/nature12373",
        "title": ["Crystal structure of Cas9"],
        "author": [
            {"given": "Martin", "family": "Jinek"},
            {"given": "Feng", "family": "Jiang"},
        ],
        "published-print": {"date-parts": [[2014, 2, 13]]},
        "container-title": ["Nature"],
        "is-referenced-by-count": 5000,
        "URL": "https://doi.org/10.1038/nature12373",
    }
    result = crossref_client.format_paper(item)
    assert result is not None
    assert result["title"] == "Crystal structure of Cas9"
    assert result["authors"] == ["Martin Jinek", "Feng Jiang"]
    assert result["year"] == 2014
    assert result["venue"] == "Nature"
    assert result["citation_count"] == 5000
    assert result["source"] == "crossref"
    assert result["external_ids"]["DOI"] == "10.1038/nature12373"


def test_format_paper_with_jats_abstract():
    item = {
        "DOI": "10.1234/test",
        "title": ["Test Paper"],
        "author": [],
        "published-online": {"date-parts": [[2024]]},
        "abstract": "<jats:p>This is a <jats:italic>test</jats:italic> abstract.</jats:p>",
    }
    result = crossref_client.format_paper(item)
    assert "<jats" not in result["abstract"]
    assert "test" in result["abstract"]


def test_format_paper_no_title():
    item = {"DOI": "10.1234/x", "author": []}
    result = crossref_client.format_paper(item)
    assert result is None


def test_format_paper_preserves_retraction_updates():
    item = {
        "DOI": "10.1234/retracted",
        "title": ["A Retracted Paper"],
        "update-to": [{
            "DOI": "10.1234/notice",
            "type": "retraction",
            "label": "Retraction",
            "source": "retraction-watch",
            "updated": {"date-time": "2026-01-01T00:00:00Z"},
        }],
    }
    result = crossref_client.format_paper(item)
    assert result["updates"][0]["type"] == "retraction"
    assert result["updates"][0]["source"] == "retraction-watch"


def test_output_format_matches_s2():
    item = {
        "DOI": "10.1234/test",
        "title": ["Format Test"],
        "author": [{"given": "A", "family": "B"}],
        "published-print": {"date-parts": [[2024, 1, 1]]},
        "container-title": ["Journal"],
        "is-referenced-by-count": 10,
    }
    result = crossref_client.format_paper(item)
    expected_keys = {
        "paper_id", "title", "authors", "abstract", "year", "venue",
        "citation_count", "influential_citations", "is_open_access",
        "open_access_url", "fields_of_study", "publication_date",
        "tldr", "external_ids", "url", "source",
    }
    assert expected_keys == set(result.keys())
