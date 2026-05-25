"""Tests for search_papers in server.py (v0.7 pipeline)."""

import yaml
from scholar_mcp import server, sources


class FakeSourceResult:
    def __init__(self, source, status, results, latency_ms=100, error=None):
        self.source = source
        self.status = status
        self.results = results
        self.latency_ms = latency_ms
        self.error = error


def _paper(title, doi=None, source="s2", cites=0, year=2024):
    return {
        "paper_id": doi or title.lower().replace(" ", "_"),
        "title": title,
        "authors": [],
        "abstract": f"Abstract for {title}",
        "year": year,
        "venue": "arXiv",
        "citation_count": cites,
        "influential_citations": 0,
        "is_open_access": True,
        "open_access_url": "",
        "fields_of_study": ["Computer Science"],
        "publication_date": f"{year}-01-01",
        "tldr": None,
        "external_ids": {"DOI": doi} if doi else {},
        "url": "",
        "source": source,
    }


def test_search_merges_sources(monkeypatch):
    def fake_parallel_search(query, limit=50, **kwargs):
        return [
            FakeSourceResult("semantic_scholar", "ok", [_paper("Hybrid Attention Model", doi="10.1/a", source="s2", cites=100)]),
            FakeSourceResult("arxiv", "ok", [_paper("Samba: Hybrid State Space", doi="10.1/b", source="arxiv")]),
            FakeSourceResult("openalex", "ok", []),
        ]
    monkeypatch.setattr(sources, "parallel_search", fake_parallel_search)
    monkeypatch.setattr(server.relevance, "rerank", lambda q, papers, top_n=50, intent="": papers)

    result = yaml.safe_load(server.search_papers("hybrid attention transformer"))
    assert "_meta" in result
    assert "semantic_scholar" in result["_meta"]["sources_used"]
    assert "arxiv" in result["_meta"]["sources_used"]
    assert len(result["results"]) == 2


def test_search_falls_back_when_primary_empty(monkeypatch):
    def fake_parallel_search(query, limit=50, **kwargs):
        return [
            FakeSourceResult("semantic_scholar", "error", [], error="rate limited"),
            FakeSourceResult("arxiv", "empty", []),
            FakeSourceResult("crossref", "ok", [_paper("Transformer Paper", doi="10.1/c", source="crossref", cites=50)]),
        ]
    monkeypatch.setattr(sources, "parallel_search", fake_parallel_search)
    monkeypatch.setattr(server.relevance, "rerank", lambda q, papers, top_n=50, intent="": papers)

    result = yaml.safe_load(server.search_papers("transformer attention"))
    assert "crossref" in result["_meta"]["sources_used"]


def test_search_returns_error_on_no_results(monkeypatch):
    def fake_parallel_search(query, limit=50, **kwargs):
        return [
            FakeSourceResult("semantic_scholar", "error", [], error="down"),
            FakeSourceResult("arxiv", "empty", []),
            FakeSourceResult("openalex", "empty", []),
        ]
    monkeypatch.setattr(sources, "parallel_search", fake_parallel_search)

    result = yaml.safe_load(server.search_papers("xyznonexistent"))
    assert "error" in result
    assert "_meta" in result


def test_search_deduplicates(monkeypatch):
    same_doi = "10.5555/test"
    def fake_parallel_search(query, limit=50, **kwargs):
        return [
            FakeSourceResult("semantic_scholar", "ok", [_paper("Attention Is All You Need", doi=same_doi, source="s2", cites=1000, year=2017)]),
            FakeSourceResult("arxiv", "ok", [_paper("Attention Is All You Need", doi=same_doi, source="arxiv", year=2017)]),
            FakeSourceResult("openalex", "ok", []),
        ]
    monkeypatch.setattr(sources, "parallel_search", fake_parallel_search)
    monkeypatch.setattr(server.relevance, "rerank", lambda q, papers, top_n=50, intent="": papers)

    result = yaml.safe_load(server.search_papers("attention is all you need"))
    assert len(result["results"]) == 1
