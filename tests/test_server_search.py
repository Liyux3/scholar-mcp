"""Tests for the multi-source merge search_papers in server.py."""

import json
import httpx

from scholar_mcp import server, s2_client, arxiv_client, core_client


def _response(status_code, payload, url):
    return httpx.Response(status_code, json=payload, request=httpx.Request("GET", url))


def test_search_merges_s2_and_arxiv(monkeypatch):
    """When both S2 and arXiv return results, they should be merged and deduped."""
    def fake_s2_get(url, params=None, headers=None, timeout=None):
        return _response(200, {
            "data": [{
                "paperId": "s2_1", "title": "Hybrid Attention Model",
                "abstract": "Transformer with linear attention",
                "citationCount": 100, "year": 2025, "venue": "NeurIPS",
                "fieldsOfStudy": ["Computer Science"],
            }]
        }, url)

    monkeypatch.setattr(s2_client.httpx, "get", fake_s2_get)

    captured_arxiv = {"called": False}
    original_arxiv = arxiv_client.search_papers

    def fake_arxiv(query, max_results=10):
        captured_arxiv["called"] = True
        return [{
            "paper_id": "2406.07522", "title": "Samba: Hybrid State Space Model",
            "authors": ["Author A"], "abstract": "Mamba with sliding window attention",
            "year": 2024, "venue": "arXiv", "citation_count": 0,
            "influential_citations": 0, "is_open_access": True,
            "open_access_url": "https://arxiv.org/pdf/2406.07522.pdf",
            "fields_of_study": ["cs.CL"], "publication_date": "2024-06-11",
            "tldr": None, "external_ids": {"ArXiv": "2406.07522"},
            "url": "https://arxiv.org/abs/2406.07522", "source": "arxiv",
        }]

    monkeypatch.setattr(arxiv_client, "search_papers", fake_arxiv)

    result = json.loads(server.search_papers("hybrid attention transformer"))
    assert "_meta" in result
    assert "semantic_scholar" in result["_meta"]["sources_used"]
    assert "arxiv" in result["_meta"]["sources_used"]
    assert captured_arxiv["called"]
    assert len(result["results"]) == 2


def test_search_falls_back_when_primary_empty(monkeypatch):
    """When S2 and arXiv both fail, should fall back to CORE etc."""
    def fail_s2(*args, **kwargs):
        raise Exception("S2 rate limited")

    monkeypatch.setattr(s2_client, "search_papers", fail_s2)
    monkeypatch.setattr(arxiv_client, "search_papers", lambda q, max_results=10: [])

    def fake_core(q, limit=10):
        return [{
            "paper_id": "core_1", "title": "Some Core Paper about Transformers",
            "authors": [], "abstract": "Attention mechanism in transformer models",
            "year": 2024, "venue": "", "citation_count": 5,
            "influential_citations": 0, "is_open_access": False,
            "open_access_url": None, "fields_of_study": [],
            "publication_date": "2024-01-01", "tldr": None,
            "external_ids": {}, "url": "", "source": "core",
        }]

    monkeypatch.setattr(core_client, "search_papers", fake_core)

    result = json.loads(server.search_papers("transformer attention"))
    assert "core" in result["_meta"]["sources_used"]
    assert "semantic_scholar" in result["_meta"]["sources_failed"][0]


def test_search_returns_meta_on_no_results(monkeypatch):
    """When nothing matches, return error with meta info."""
    def fail_s2(*a, **kw):
        raise Exception("down")
    monkeypatch.setattr(s2_client, "search_papers", fail_s2)
    monkeypatch.setattr(arxiv_client, "search_papers", lambda q, max_results=10: [])
    monkeypatch.setattr(core_client, "search_papers", lambda q, limit=10: [])

    from scholar_mcp import pubmed_client, scholar_client as gscholar
    monkeypatch.setattr(pubmed_client, "search_papers", lambda q, max_results=10: [])
    monkeypatch.setattr(gscholar, "search_papers", lambda q, max_results=10: [])

    result = json.loads(server.search_papers("xyznonexistent"))
    assert "error" in result
    assert "_meta" in result
    assert len(result["_meta"]["sources_failed"]) >= 1


def test_search_deduplicates(monkeypatch):
    """Same paper from S2 and arXiv should be deduped."""
    def fake_s2_get(url, params=None, headers=None, timeout=None):
        return _response(200, {
            "data": [{
                "paperId": "abc", "title": "Attention Is All You Need",
                "abstract": "Transformer architecture",
                "externalIds": {"DOI": "10.5555/test"},
                "citationCount": 1000, "year": 2017, "venue": "NeurIPS",
            }]
        }, url)

    monkeypatch.setattr(s2_client.httpx, "get", fake_s2_get)
    monkeypatch.setattr(arxiv_client, "search_papers", lambda q, max_results=10: [{
        "paper_id": "1706.03762", "title": "Attention Is All You Need",
        "authors": [], "abstract": "Transformer architecture",
        "year": 2017, "venue": "arXiv", "citation_count": 0,
        "influential_citations": 0, "is_open_access": True,
        "open_access_url": "", "fields_of_study": [],
        "publication_date": "2017-06-12", "tldr": None,
        "external_ids": {"DOI": "10.5555/test"}, "url": "", "source": "arxiv",
    }])

    result = json.loads(server.search_papers("attention is all you need"))
    assert result["_meta"]["total_before_filter"] == 2
    assert len(result["results"]) == 1
