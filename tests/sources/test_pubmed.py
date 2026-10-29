"""Tests for PubMed E-utilities client."""

import httpx

from scholar_mcp.sources import pubmed


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

    monkeypatch.setattr(httpx, "get", fake_get)

    results = pubmed.search_papers("CRISPR gene editing", max_results=3)

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
    result = pubmed.format_paper(data, "12345")
    assert result["paper_id"] == "pmid_12345"
    assert result["title"] == "Test Paper"
    assert result["authors"] == ["Smith J"]
    assert result["year"] == 2023
    assert result["venue"] == "Nature"
    assert result["external_ids"]["DOI"] == "10.1234/test"
    assert result["external_ids"]["PMID"] == "12345"
    assert result["source"] == "pubmed"


def test_ncbi_key_is_sent_and_paced_consistently_for_both_requests(monkeypatch):
    from scholar_mcp import config
    monkeypatch.setattr(config, "NCBI_API_KEY", "fixture-key")
    calls, gates = [], []
    monkeypatch.setattr(pubmed.pacing, "wait", lambda provider, key=None: gates.append((provider, key)))

    def get(url, params=None, **kwargs):
        calls.append(params)
        payload = {"esearchresult": {"idlist": ["1"]}} if url.endswith("esearch.fcgi") else {
            "result": {"1": {"title": "A paper", "authors": []}}}
        return _response(200, payload, url)

    monkeypatch.setattr(httpx, "get", get)
    assert len(pubmed.search_papers("question", 1)) == 1
    assert all(params["api_key"] == "fixture-key" for params in calls)
    assert gates == [("ncbi", "fixture-key"), ("ncbi", "fixture-key")]
