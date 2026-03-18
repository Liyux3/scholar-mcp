"""Tests for PubMed E-utilities client."""

import httpx

from scholar_mcp import pubmed_client


def _response(status_code: int, payload: dict, url: str) -> httpx.Response:
    request = httpx.Request("GET", url)
    return httpx.Response(status_code, json=payload, request=request)


def test_search_papers(monkeypatch):
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append((url, params))
        if url.endswith("/esearch.fcgi"):
            return _response(
                200,
                {"esearchresult": {"idlist": ["12345", "67890"]}},
                url,
            )
        return _response(
            200,
            {
                "result": {
                    "12345": {
                        "title": "CRISPR gene editing.",
                        "authors": [{"name": "Smith J"}],
                        "pubdate": "2023 Jan",
                        "fulljournalname": "Nature",
                        "articleids": [{"idtype": "doi", "value": "10.1234/test"}],
                    },
                    "67890": {
                        "title": "Genome engineering advances.",
                        "authors": [{"name": "Jones A"}],
                        "pubdate": "2022 Feb",
                        "source": "Science",
                        "articleids": [],
                    },
                }
            },
            url,
        )

    monkeypatch.setattr(pubmed_client.httpx, "get", fake_get)

    results = pubmed_client.search_papers("CRISPR gene editing", max_results=3)

    assert calls[0][0].endswith("/esearch.fcgi")
    assert calls[0][1]["term"] == "CRISPR gene editing"
    assert calls[1][0].endswith("/esummary.fcgi")
    assert calls[1][1]["id"] == "12345,67890"
    assert len(results) == 2
    assert results[0]["source"] == "pubmed"
    assert results[0]["paper_id"] == "pmid_12345"
    assert results[0]["title"] == "CRISPR gene editing"


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
    assert result["title"] == "Test Paper"
    assert result["authors"] == ["Smith J"]
    assert result["year"] == 2023
    assert result["venue"] == "Nature"
    assert result["external_ids"]["DOI"] == "10.1234/test"
    assert result["external_ids"]["PMID"] == "12345"
    assert result["source"] == "pubmed"


def test_output_format_matches_s2(monkeypatch):
    """PubMed results should have the same keys as S2 results."""

    def fake_get(url, params=None, timeout=None):
        if url.endswith("/esearch.fcgi"):
            return _response(200, {"esearchresult": {"idlist": ["29860986"]}}, url)
        return _response(
            200,
            {
                "result": {
                    "29860986": {
                        "title": "Cancer immunotherapy.",
                        "authors": [{"name": "Taylor R"}],
                        "pubdate": "2024 Mar 01",
                        "fulljournalname": "Cell",
                        "articleids": [{"idtype": "doi", "value": "10.5555/example"}],
                    }
                }
            },
            url,
        )

    monkeypatch.setattr(pubmed_client.httpx, "get", fake_get)

    results = pubmed_client.search_papers("cancer immunotherapy", max_results=1)

    expected_keys = {
        "paper_id", "title", "authors", "abstract", "year", "venue",
        "citation_count", "influential_citations", "is_open_access",
        "open_access_url", "fields_of_study", "publication_date",
        "tldr", "external_ids", "url", "source",
    }
    assert expected_keys == set(results[0].keys())
