"""Tests for OpenAlex API client."""

import httpx
from scholar_mcp import openalex_client


def _response(status_code, payload, url):
    return httpx.Response(status_code, json=payload, request=httpx.Request("GET", url))


def test_format_paper_basic():
    work = {
        "id": "https://openalex.org/W2741809807",
        "title": "Attention Is All You Need",
        "authorships": [
            {"author": {"display_name": "Ashish Vaswani", "orcid": None}, "institutions": []},
            {"author": {"display_name": "Noam Shazeer", "orcid": None}, "institutions": []},
        ],
        "publication_year": 2017,
        "doi": "https://doi.org/10.5555/3295222.3295349",
        "cited_by_count": 120000,
        "concepts": [{"display_name": "Computer Science"}, {"display_name": "Transformer"}],
        "open_access": {"is_oa": True, "oa_url": "https://arxiv.org/pdf/1706.03762"},
        "primary_location": {"source": {"display_name": "NeurIPS"}, "landing_page_url": "https://example.com"},
        "publication_date": "2017-06-12",
    }
    result = openalex_client.format_paper(work)
    assert result is not None
    assert result["title"] == "Attention Is All You Need"
    assert result["authors"] == ["Ashish Vaswani", "Noam Shazeer"]
    assert result["year"] == 2017
    assert result["citation_count"] == 120000
    assert result["source"] == "openalex"
    assert result["external_ids"]["DOI"] == "10.5555/3295222.3295349"
    assert result["is_open_access"] is True
    assert "Computer Science" in result["fields_of_study"]


def test_format_paper_with_inverted_abstract():
    work = {
        "id": "https://openalex.org/W123",
        "title": "Test Paper",
        "authorships": [],
        "publication_year": 2024,
        "abstract_inverted_index": {"Hello": [0], "world": [1], "this": [2], "is": [3], "a": [4], "test": [5]},
    }
    result = openalex_client.format_paper(work)
    assert result["abstract"] == "Hello world this is a test"


def test_format_paper_missing_title():
    work = {"id": "W1", "authorships": [], "publication_year": 2024}
    result = openalex_client.format_paper(work)
    assert result is None


def test_search_papers(monkeypatch):
    captured = {}

    def fake_get(url, params=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        return _response(200, {
            "results": [{
                "id": "https://openalex.org/W1",
                "title": "Test Paper",
                "authorships": [{"author": {"display_name": "Author A"}}],
                "publication_year": 2025,
                "cited_by_count": 10,
            }]
        }, url)

    monkeypatch.setattr(openalex_client.httpx, "get", fake_get)

    results = openalex_client.search_papers("test query", limit=5)
    assert len(results) == 1
    assert results[0]["source"] == "openalex"
    assert results[0]["title"] == "Test Paper"
    assert "search" in captured["params"]


def test_output_format_matches_s2():
    work = {
        "id": "https://openalex.org/W1",
        "title": "Format Test",
        "authorships": [{"author": {"display_name": "Author"}}],
        "publication_year": 2024,
        "publication_date": "2024-01-01",
    }
    result = openalex_client.format_paper(work)
    expected_keys = {
        "paper_id", "title", "authors", "abstract", "year", "venue",
        "citation_count", "influential_citations", "is_open_access",
        "open_access_url", "fields_of_study", "publication_date",
        "tldr", "external_ids", "url", "source",
    }
    assert expected_keys == set(result.keys())
