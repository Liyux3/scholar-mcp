"""Tests for the multi-source merge search_papers in server.py."""

import pytest
import yaml

from scholar_mcp import server


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
    monkeypatch.setattr(
        server.s2_client,
        "get_recommendations",
        lambda *args, **kwargs: (_ for _ in ()).throw(server.s2_client.S2CooldownError()),
    )
    result = yaml.safe_load(server.recommend_papers("W1", relation="similar"))

    assert result["temporary"] is True
    assert result["available_relations"] == ["peers", "kin"]
    assert "papers" not in result
