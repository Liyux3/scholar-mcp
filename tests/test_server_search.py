"""Tests for the multi-source merge search_papers in server.py."""

import pytest
import yaml

from scholar_mcp import server


def test_search_papers_merges_structured_authors_from_sources(monkeypatch):
    from scholar_mcp.sources import SourceResult

    common = {"title": "Learning from a large dataset", "year": 2024,
              "abstract": "A complete abstract.", "external_ids": {"DOI": "10.1234/test"}}
    monkeypatch.setattr(server.sources, "parallel_search", lambda *a, **kw: [
        SourceResult("first", "ok", [{**common, "authors": ["A Author"]}], 0),
        SourceResult("second", "ok", [{**common, "authors": [
            {"name": "Alice Author", "authorId": "1"}, {"name": "Bob Writer"},
        ]}], 0),
    ])
    monkeypatch.setattr(server.metadata, "hydrate", lambda *a, **kw: None)
    monkeypatch.setattr(server.s2_client, "is_healthy", lambda: False)
    monkeypatch.setattr(server.relevance, "rerank", lambda query, papers, **kw: papers)
    monkeypatch.setattr(server.relevance, "rank_final", lambda papers: papers)
    monkeypatch.setattr(server.expansion, "expand", lambda *a, **kw: {})

    result = yaml.safe_load(server.search_papers("learning dataset"))

    assert len(result["results"]) == 1
    assert result["results"][0]["authors"] == ["Alice Author", "Bob Writer"]


def test_strict_type_and_open_access_filters_apply_to_every_source(monkeypatch):
    papers = [
        {"title": "Allowed review", "publication_types": ["review"], "is_open_access": True},
        {"title": "Paywalled review", "publication_types": ["Review"], "is_open_access": False},
        {"title": "Wrong type", "publication_types": ["Conference"], "is_open_access": True},
        {"title": "Unknown type", "is_open_access": True},
    ]
    monkeypatch.setattr(server, "_pipeline", lambda *a, **kw: (papers, []))
    result = yaml.safe_load(server.search_papers("topic", paper_types="Review", open_access_only=True))
    assert [p["title"] for p in result["results"]] == ["Allowed review"]


def test_type_filter_uses_adapter_aliases_and_preserves_duplicates_evidence(monkeypatch):
    papers = [
        {"title": "Same paper", "abstract": "Known abstract", "external_ids": {"DOI": "10.1000/x"}, "publication_types": ["journal-article"]},
        {"title": "Same paper", "external_ids": {"DOI": "10.1000/x"}, "publication_types": ["Review"]},
    ]
    monkeypatch.setattr(server, "_pipeline", lambda *a, **kw: (papers, []))
    result = yaml.safe_load(server.search_papers("topic", paper_types="JournalArticle"))
    assert len(result["results"]) == 1


def test_summary_completion_only_looks_up_the_displayed_shortlist(monkeypatch):
    papers = [{"title": "Visible paper", "external_ids": {"ArXiv": "2506.15442"}},
              {"title": "Discarded paper", "external_ids": {"ArXiv": "2501.12202"}}]
    monkeypatch.setattr(server, "_pipeline", lambda *a, **kw: (papers, []))
    monkeypatch.setattr(server.s2_client, "is_healthy", lambda: False)
    looked_up = []
    from scholar_mcp import arxiv_client
    def get_paper(identifier):
        looked_up.append(identifier)
        return {"title": "Visible paper", "abstract": "Original abstract.", "source": "arxiv"}
    monkeypatch.setattr(arxiv_client, "get_paper", get_paper)
    result = yaml.safe_load(server.search_papers("query", limit=1))
    assert looked_up == ["ArXiv:2506.15442"]
    assert result["results"][0]["abstract"] == "Original abstract."


def test_summary_lookup_failure_keeps_paper_and_explicit_null(monkeypatch):
    paper = {"title": "Visible paper", "external_ids": {"ArXiv": "2506.15442"}}
    monkeypatch.setattr(server, "_pipeline", lambda *a, **kw: ([paper], []))
    monkeypatch.setattr(server.s2_client, "is_healthy", lambda: False)
    from scholar_mcp import arxiv_client
    def unavailable(*args):
        raise TimeoutError("archive unavailable")
    monkeypatch.setattr(arxiv_client, "get_paper", unavailable)
    result = yaml.safe_load(server.search_papers("query", limit=1))
    assert result["results"][0]["title"] == "Visible paper"
    assert result["results"][0]["abstract"] is None


def test_search_date_sort_handles_mixed_source_types(monkeypatch):
    """Date sorting must compare one normalized type across source schemas.

    Some clients provide an ISO ``publication_date`` string while others only
    provide ``year`` as an int (or occasionally a numeric string).  Comparing
    those raw values raises ``TypeError`` on Python 3.
    """
    papers = [
        {"title": "Unknown", "year": None, "publication_date": None},
        {"title": "Year integer", "year": 2026, "publication_date": None},
        {"title": "Full date", "year": 2026,
         "publication_date": "2026-08-14"},
        {"title": "Older full date", "year": 2025,
         "publication_date": "2025-12-31"},
        {"title": "Year string", "year": "2027", "publication_date": None},
    ]
    monkeypatch.setattr(
        server,
        "_pipeline",
        lambda *args, **kwargs: (papers, []),
    )

    result = yaml.safe_load(server.search_papers("robot learning", sort="date"))

    assert [paper["title"] for paper in result["results"]] == [
        "Year string",
        "Full date",
        "Year integer",
        "Older full date",
        "Unknown",
    ]


def test_year_filter_applies_after_metadata_enrichment(monkeypatch):
    papers = [
        {
            "title": "Snippet Paper",
            "year": None,
            "external_ids": {"CorpusId": "123"},
        },
        {"title": "Older Paper", "year": 2023},
    ]
    monkeypatch.setattr(server, "_pipeline", lambda *args, **kwargs: (papers, []))
    monkeypatch.setattr(
        server.s2_snippet_client,
        "enrich_metadata",
        lambda items: items[0].update(year=2026),
    )

    result = yaml.safe_load(server.search_papers("robot learning", year="2025-2026"))

    assert [paper["title"] for paper in result["results"]] == ["Snippet Paper"]
    assert result["results"][0]["year"] == 2026


def test_metadata_enrichment_skips_an_overloaded_s2(monkeypatch):
    papers = [{
        "title": "Snippet Paper",
        "external_ids": {"CorpusId": "123"},
    }]
    reports = [{
        "source": "semantic_scholar",
        "status": "error",
        "count": 0,
        "latency_ms": 100,
        "error": "HTTP 429",
    }]
    monkeypatch.setattr(server, "_pipeline", lambda *args, **kwargs: (papers, reports))
    monkeypatch.setattr(
        server.s2_snippet_client,
        "enrich_metadata",
        lambda items: pytest.fail("overloaded S2 should not receive a batch request"),
    )

    result = yaml.safe_load(server.search_papers("robot learning"))

    assert result["results"][0]["authors"] is None
    assert result["results"][0]["year"] is None
    assert result["results"][0]["venue"] is None


def test_similar_recommendations_fail_cleanly_during_s2_cooldown(monkeypatch):
    monkeypatch.setattr(server, "_lookup_title", lambda paper_id: "Seed Paper")
    monkeypatch.setattr(server, "_id_variants", lambda paper_id: [paper_id])
    monkeypatch.setattr(
        server.s2_client,
        "get_recommendations",
        lambda *args, **kwargs: (_ for _ in ()).throw(server.s2_client.S2CooldownError()),
    )
    result = yaml.safe_load(server.recommend_papers("W1", relation="similar"))

    assert result["temporary"] is True
    assert result["available_relations"] == ["peers", "kin"]
    assert "papers" not in result
