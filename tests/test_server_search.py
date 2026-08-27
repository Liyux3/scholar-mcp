"""Tests for the multi-source merge search_papers in server.py."""

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
