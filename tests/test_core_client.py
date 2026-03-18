"""Tests for CORE API client."""

import httpx

from scholar_mcp import core_client


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
        return _response(200, {"results": []}, url)

    monkeypatch.setattr(core_client.httpx, "get", fake_get)
    monkeypatch.setattr(core_client.time, "sleep", sleeps.append)

    result = core_client._get(f"{core_client.BASE_URL}/search/works/", params={"q": "test"})

    assert result == {"results": []}
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
                "results": [
                    {
                        "id": 1,
                        "title": "First Paper",
                        "authors": [{"name": "Author One"}],
                        "abstract": "Paper abstract",
                        "publishedDate": "2024-01-15T00:00:00",
                        "journals": [{"title": "Journal A"}],
                        "citationCount": 12,
                        "downloadUrl": "https://example.com/first.pdf",
                        "fieldOfStudy": ["Computer Science"],
                        "doi": "10.1000/first",
                    },
                    {
                        "id": 2,
                        "title": "Second Paper",
                        "authors": ["Author Two"],
                        "publishedDate": "2023-05-20T00:00:00",
                        "citationCount": 2,
                    },
                ]
            },
            url,
        )

    monkeypatch.setattr(core_client.httpx, "get", fake_get)

    results = core_client.search_papers("deep learning", limit=3)

    assert captured["url"] == f"{core_client.BASE_URL}/search/works/"
    assert captured["params"] == {"q": "deep learning", "limit": 6, "sort": "relevance"}
    assert len(results) == 2
    assert results[0]["source"] == "core"
    assert results[0]["paper_id"] == "core_1"
    assert results[0]["title"] == "First Paper"
    assert results[0]["authors"] == ["Author One"]


def test_search_papers_quality_sort(monkeypatch):
    """Results with PDFs and citations should come first."""

    def fake_get(url, params=None, headers=None, timeout=None):
        return _response(
            200,
            {
                "results": [
                    {"id": 2, "title": "Lower Ranked", "citationCount": 50},
                    {
                        "id": 1,
                        "title": "Best Ranked",
                        "citationCount": 10,
                        "downloadUrl": "https://example.com/best.pdf",
                    },
                    {
                        "id": 3,
                        "title": "Citations Only",
                        "citationCount": 80,
                    },
                ]
            },
            url,
        )

    monkeypatch.setattr(core_client.httpx, "get", fake_get)

    results = core_client.search_papers("machine learning", limit=10)

    assert [paper["paper_id"] for paper in results] == ["core_1", "core_3", "core_2"]
    assert results[0]["open_access_url"] == "https://example.com/best.pdf"


def test_get_pdf_url_by_doi(monkeypatch):
    captured = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        captured["params"] = params
        return _response(
            200,
            {"results": [{"downloadUrl": "https://example.com/attention.pdf"}]},
            url,
        )

    monkeypatch.setattr(core_client.httpx, "get", fake_get)

    url = core_client.get_pdf_url(doi="10.48550/arXiv.1706.03762")

    assert captured["params"]["q"] == 'doi:"10.48550/arXiv.1706.03762"'
    assert url == "https://example.com/attention.pdf"


def test_get_pdf_url_by_title(monkeypatch):
    captured = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        captured["params"] = params
        return _response(
            200,
            {"results": [{"sourceFulltextUrls": ["https://example.com/title.pdf"]}]},
            url,
        )

    monkeypatch.setattr(core_client.httpx, "get", fake_get)

    url = core_client.get_pdf_url(title="Attention Is All You Need")

    assert captured["params"]["q"] == 'title:("Attention Is All You Need")'
    assert url == "https://example.com/title.pdf"


def test_format_paper_handles_missing_fields():
    minimal = {"id": 12345, "title": "Test Paper"}
    result = core_client.format_paper(minimal)
    assert result["paper_id"] == "core_12345"
    assert result["title"] == "Test Paper"
    assert result["authors"] == []
    assert result["source"] == "core"


def test_output_format_matches_s2(monkeypatch):
    """CORE results should have the same keys as S2 results."""

    def fake_get(url, params=None, headers=None, timeout=None):
        return _response(
            200,
            {
                "results": [
                    {
                        "id": 1,
                        "title": "Neural Network Paper",
                        "authors": [{"name": "Author"}],
                        "publishedDate": "2024-01-01T00:00:00",
                    }
                ]
            },
            url,
        )

    monkeypatch.setattr(core_client.httpx, "get", fake_get)

    results = core_client.search_papers("neural network", limit=1)

    expected_keys = {
        "paper_id", "title", "authors", "abstract", "year", "venue",
        "citation_count", "influential_citations", "is_open_access",
        "open_access_url", "fields_of_study", "publication_date",
        "tldr", "external_ids", "url", "source",
    }
    assert expected_keys == set(results[0].keys())
