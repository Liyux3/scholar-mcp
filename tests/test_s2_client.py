"""Tests for Semantic Scholar API client."""

import httpx

from scholar_mcp import s2_client


def _response(status_code: int, payload: dict, url: str) -> httpx.Response:
    request = httpx.Request("GET", url)
    return httpx.Response(status_code, json=payload, request=request)


def test_get_retries_rate_limit_before_success(monkeypatch):
    calls = {"count": 0}
    sleeps = []

    def fake_get(url, params=None, headers=None, timeout=None):
        calls["count"] += 1
        if calls["count"] == 1:
            return _response(429, {"error": "rate limited"}, url)
        return _response(200, {"data": []}, url)

    monkeypatch.setattr(s2_client.httpx, "get", fake_get)
    monkeypatch.setattr(s2_client.time, "sleep", sleeps.append)

    result = s2_client._get(f"{s2_client.BASE_URL}/paper/search", params={"query": "test"})

    assert result == {"data": []}
    assert calls["count"] == 2
    assert sleeps == [2]


def test_search_papers(monkeypatch):
    captured = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        return _response(
            200,
            {
                "data": [
                    {
                        "paperId": "p1",
                        "title": "Attention is All you Need",
                        "authors": [{"name": "Ashish Vaswani"}, {"name": "Noam Shazeer"}],
                        "year": 2017,
                        "citationCount": 12345,
                        "influentialCitationCount": 1200,
                        "isOpenAccess": True,
                        "openAccessPdf": {"url": "https://example.com/paper.pdf"},
                        "fieldsOfStudy": ["Computer Science"],
                        "publicationDate": "2017-06-12",
                        "externalIds": {"ArXiv": "1706.03762"},
                        "tldr": {"text": "Transformers replace recurrence."},
                    }
                ]
            },
            url,
        )

    monkeypatch.setattr(s2_client.httpx, "get", fake_get)

    results = s2_client.search_papers("attention is all you need", limit=3)

    assert captured["url"] == f"{s2_client.BASE_URL}/paper/search"
    assert captured["params"]["query"] == "attention is all you need"
    assert captured["params"]["limit"] == 3
    assert captured["params"]["fields"] == s2_client.SEARCH_FIELDS
    assert len(results) == 1
    assert results[0]["paper_id"] == "p1"
    assert results[0]["authors"] == ["Ashish Vaswani", "Noam Shazeer"]
    assert results[0]["source"] == "semantic_scholar"


def test_search_papers_with_filters(monkeypatch):
    captured = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        captured["params"] = params
        return _response(200, {"data": []}, url)

    monkeypatch.setattr(s2_client.httpx, "get", fake_get)

    results = s2_client.search_papers(
        "neural network",
        limit=5,
        year="2023",
        venue=["NeurIPS", "ICML"],
        fields_of_study=["Computer Science", "Mathematics"],
        min_citations=10,
        open_access_only=True,
    )

    assert results == []
    assert captured["params"]["year"] == "2023"
    assert captured["params"]["venue"] == "NeurIPS,ICML"
    assert captured["params"]["fieldsOfStudy"] == "Computer Science,Mathematics"
    assert captured["params"]["minCitationCount"] == 10
    assert captured["params"]["openAccessPdf"] == ""


def test_get_paper_by_s2_id(monkeypatch):
    def fake_get(url, params=None, headers=None, timeout=None):
        return _response(
            200,
            {
                "paperId": "204e3073870fae3d05bcbc2f6a8e263d9b72e776",
                "title": "Attention is All you Need",
                "year": 2017,
                "citationCount": 999,
                "publicationVenue": {"type": "conference", "url": "https://example.com/venue"},
                "publicationTypes": ["JournalArticle"],
                "referenceCount": 48,
                "citationStyles": {"bibtex": "@inproceedings{attention}"},
            },
            url,
        )

    monkeypatch.setattr(s2_client.httpx, "get", fake_get)

    paper = s2_client.get_paper("204e3073870fae3d05bcbc2f6a8e263d9b72e776")

    assert paper["title"] == "Attention is All you Need"
    assert paper["year"] == 2017
    assert paper["citation_count"] == 999
    assert paper["bibtex"] == "@inproceedings{attention}"
    assert paper["venue_type"] == "conference"


def test_get_paper_by_arxiv_id(monkeypatch):
    captured = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        captured["url"] = url
        return _response(200, {"paperId": "p1", "title": "Attention is All you Need"}, url)

    monkeypatch.setattr(s2_client.httpx, "get", fake_get)

    paper = s2_client.get_paper("ArXiv:1706.03762")

    assert captured["url"].endswith("/paper/ArXiv:1706.03762")
    assert "attention" in paper["title"].lower()


def test_get_paper_by_doi(monkeypatch):
    captured = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        captured["url"] = url
        return _response(200, {"paperId": "p2", "title": "BERT", "year": 2019}, url)

    monkeypatch.setattr(s2_client.httpx, "get", fake_get)

    paper = s2_client.get_paper("DOI:10.18653/v1/N19-1423")

    assert captured["url"].endswith("/paper/DOI:10.18653/v1/N19-1423")
    assert paper["year"] == 2019


def test_get_citations(monkeypatch):
    def fake_get(url, params=None, headers=None, timeout=None):
        return _response(
            200,
            {
                "data": [
                    {"citingPaper": {"paperId": "c1", "title": "Citation 1"}},
                    {"citingPaper": {"paperId": "c2", "title": "Citation 2"}},
                    {"citingPaper": {"title": "Missing ID should be skipped"}},
                ]
            },
            url,
        )

    monkeypatch.setattr(s2_client.httpx, "get", fake_get)

    results = s2_client.get_citations("paper-id", limit=5)

    assert [paper["paper_id"] for paper in results] == ["c1", "c2"]


def test_get_references(monkeypatch):
    def fake_get(url, params=None, headers=None, timeout=None):
        return _response(
            200,
            {
                "data": [
                    {"citedPaper": {"paperId": "r1", "title": "Reference 1"}},
                    {"citedPaper": {"paperId": "r2", "title": "Reference 2"}},
                ]
            },
            url,
        )

    monkeypatch.setattr(s2_client.httpx, "get", fake_get)

    results = s2_client.get_references("paper-id", limit=5)

    assert [paper["paper_id"] for paper in results] == ["r1", "r2"]


def test_get_recommendations(monkeypatch):
    def fake_get(url, params=None, headers=None, timeout=None):
        return _response(
            200,
            {"recommendedPapers": [{"paperId": "rec1", "title": "Recommended Paper"}]},
            url,
        )

    monkeypatch.setattr(s2_client.httpx, "get", fake_get)

    results = s2_client.get_recommendations("paper-id", limit=5)

    assert len(results) == 1
    assert results[0]["paper_id"] == "rec1"


def test_search_authors(monkeypatch):
    def fake_get(url, params=None, headers=None, timeout=None):
        return _response(
            200,
            {
                "data": [
                    {
                        "authorId": "a1",
                        "name": "Yoshua Bengio",
                        "affiliations": ["Mila"],
                        "paperCount": 1000,
                        "citationCount": 500000,
                        "hIndex": 250,
                    }
                ]
            },
            url,
        )

    monkeypatch.setattr(s2_client.httpx, "get", fake_get)

    results = s2_client.search_authors("Yoshua Bengio", limit=3)

    assert len(results) == 1
    assert results[0]["author_id"] == "a1"
    assert results[0]["name"] == "Yoshua Bengio"
    assert results[0]["h_index"] == 250


def test_format_paper_handles_missing_fields():
    """format_paper should not crash on sparse data."""
    minimal = {"paperId": "abc123", "title": "Test"}
    result = s2_client.format_paper(minimal)
    assert result["paper_id"] == "abc123"
    assert result["title"] == "Test"
    assert result["authors"] == []
    assert result["citation_count"] == 0
    assert result["open_access_url"] is None
