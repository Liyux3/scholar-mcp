"""Tests for citation-graph traversal primitives.

These relations are the part of the system that can cross field boundaries,
so the logic that decides what counts as a link is worth pinning precisely.
"""

import pytest

from scholar_mcp import traversal


class _Response:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


def _work(wid, title, cites=0):
    return {"id": f"https://openalex.org/{wid}", "title": title,
            "cited_by_count": cites, "publication_year": 2020, "doi": None}


class TestCoCitation:
    def test_counts_shared_references(self, monkeypatch):
        """Papers appearing in many citing papers' reference lists rank first."""
        monkeypatch.setattr(traversal, "_wid", lambda *a, **kw: "WSEED")

        citing = {"results": [
            {"id": "https://openalex.org/WA", "referenced_works": [
                "https://openalex.org/WSEED", "https://openalex.org/WX", "https://openalex.org/WY"]},
            {"id": "https://openalex.org/WB", "referenced_works": [
                "https://openalex.org/WSEED", "https://openalex.org/WX"]},
            {"id": "https://openalex.org/WC", "referenced_works": [
                "https://openalex.org/WX", "https://openalex.org/WY"]},
        ]}
        monkeypatch.setattr(traversal.oa, "_request", lambda *a, **kw: _Response(citing))
        monkeypatch.setattr(traversal, "_fetch_works", lambda wids, **kw: {
            "https://openalex.org/WX": _work("WX", "Cited Three Times"),
            "https://openalex.org/WY": _work("WY", "Cited Twice"),
        })

        results = traversal.co_citation("seed", limit=10)
        assert [p["title"] for p in results] == ["Cited Three Times", "Cited Twice"]
        assert results[0]["_relation_strength"] == 3
        assert results[0]["_relation"] == "co_citation"

    def test_excludes_the_seed_itself(self, monkeypatch):
        monkeypatch.setattr(traversal, "_wid", lambda *a, **kw: "WSEED")
        citing = {"results": [
            {"id": "https://openalex.org/WA",
             "referenced_works": ["https://openalex.org/WSEED"] * 5},
        ]}
        monkeypatch.setattr(traversal.oa, "_request", lambda *a, **kw: _Response(citing))

        requested = []

        def fake_fetch(wids, **kw):
            requested.append(list(wids))
            return {}

        monkeypatch.setattr(traversal, "_fetch_works", fake_fetch)

        assert traversal.co_citation("seed") == []
        assert all("WSEED" not in ids for ids in requested), \
            f"the seed was looked up as its own co-citation: {requested}"

    def test_requires_minimum_co_occurrence(self, monkeypatch):
        """A single shared reference is coincidence, not a relation."""
        monkeypatch.setattr(traversal, "_wid", lambda *a, **kw: "WSEED")
        citing = {"results": [
            {"id": "https://openalex.org/WA", "referenced_works": ["https://openalex.org/WONCE"]},
        ]}
        monkeypatch.setattr(traversal.oa, "_request", lambda *a, **kw: _Response(citing))
        monkeypatch.setattr(traversal, "_fetch_works", lambda wids, **kw: {})
        assert traversal.co_citation("seed") == []

    def test_unresolvable_id_returns_empty(self, monkeypatch):
        monkeypatch.setattr(traversal, "_wid", lambda *a, **kw: None)
        assert traversal.co_citation("nonsense") == []


class TestBibliographicCoupling:
    def test_ranks_by_shared_reference_count(self, monkeypatch):
        monkeypatch.setattr(traversal, "_wid", lambda *a, **kw: "WSEED")

        def fake_request(url, params, *a, **kw):
            if url.endswith("/WSEED"):
                return _Response({"referenced_works": [
                    "https://openalex.org/WR1", "https://openalex.org/WR2"]})
            # Both references are cited by WP; only one is cited by WQ.
            if "WR1" in params.get("filter", ""):
                return _Response({"results": [{"id": "https://openalex.org/WP"},
                                              {"id": "https://openalex.org/WQ"}]})
            return _Response({"results": [{"id": "https://openalex.org/WP"}]})

        monkeypatch.setattr(traversal.oa, "_request", fake_request)
        monkeypatch.setattr(traversal, "_fetch_works", lambda wids, **kw: {
            "https://openalex.org/WP": _work("WP", "Strongly Coupled"),
        })

        results = traversal.bibliographic_coupling("seed", limit=5)
        assert results[0]["title"] == "Strongly Coupled"
        assert results[0]["_relation_strength"] == 2
        assert results[0]["_relation"] == "bibliographic_coupling"

    def test_paper_with_no_references_returns_empty(self, monkeypatch):
        monkeypatch.setattr(traversal, "_wid", lambda *a, **kw: "WSEED")
        monkeypatch.setattr(traversal.oa, "_request",
                            lambda *a, **kw: _Response({"referenced_works": []}))
        assert traversal.bibliographic_coupling("seed") == []

    def test_excludes_the_seed_itself(self, monkeypatch):
        monkeypatch.setattr(traversal, "_wid", lambda *a, **kw: "WSEED")

        def fake_request(url, params, *a, **kw):
            if url.endswith("/WSEED"):
                return _Response({"referenced_works": ["https://openalex.org/WR1"]})
            return _Response({"results": [{"id": "https://openalex.org/WSEED"}] * 5})

        monkeypatch.setattr(traversal.oa, "_request", fake_request)
        monkeypatch.setattr(traversal, "_fetch_works", lambda wids, **kw: {})
        assert traversal.bibliographic_coupling("seed") == []
