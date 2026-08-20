"""Tests for citation-graph traversal primitives.

These relations are the part of the system that can cross field boundaries,
so the logic that decides what counts as a link is worth pinning precisely.
"""

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


class TestDeadEdgeRecovery:
    """OpenAlex silently omits ids it no longer serves from batched lookups.

    These are not marginal papers. The two strongest co-citation edges for
    BERT are "Attention Is All You Need" and ELMo, and both were dead ids, so
    the relation dropped exactly what it exists to surface.
    """

    def test_recovers_mag_era_ids_through_s2(self, monkeypatch):
        monkeypatch.setattr(traversal.oa, "_request",
                            lambda *a, **kw: _Response({"results": []}))

        def fake_get_paper(pid):
            assert pid == "MAG:2963403868"
            return {"title": "Attention is All you Need", "citation_count": 186604,
                    "year": 2017, "external_ids": {"DOI": "10.5555/3295222"}}

        monkeypatch.setattr(traversal.s2_client, "get_paper", fake_get_paper)

        works = traversal._fetch_works(["W2963403868"])
        recovered = works["https://openalex.org/W2963403868"]
        assert recovered["title"] == "Attention is All you Need"
        assert recovered["cited_by_count"] == 186604
        assert recovered["doi"] == "https://doi.org/10.5555/3295222"

    def test_skips_ids_with_no_mag_counterpart(self, monkeypatch):
        """W6 ids were minted by OpenAlex itself, so no MAG id exists."""
        monkeypatch.setattr(traversal.oa, "_request",
                            lambda *a, **kw: _Response({"results": []}))

        called = []
        monkeypatch.setattr(traversal.s2_client, "get_paper",
                            lambda pid: called.append(pid))

        assert traversal._fetch_works(["W6739901393"]) == {}
        assert called == []

    def test_survives_s2_failure(self, monkeypatch):
        monkeypatch.setattr(traversal.oa, "_request",
                            lambda *a, **kw: _Response({"results": []}))

        def boom(pid):
            raise RuntimeError("S2 down")

        monkeypatch.setattr(traversal.s2_client, "get_paper", boom)
        assert traversal._fetch_works(["W2963403868"]) == {}

    def test_live_records_are_not_looked_up_again(self, monkeypatch):
        monkeypatch.setattr(traversal.oa, "_request", lambda *a, **kw: _Response(
            {"results": [_work("W2963403868", "Already Here")]}))

        called = []
        monkeypatch.setattr(traversal.s2_client, "get_paper",
                            lambda pid: called.append(pid))

        works = traversal._fetch_works(["W2963403868"])
        assert works["https://openalex.org/W2963403868"]["title"] == "Already Here"
        assert called == []


class TestDuplicateRecordMerging:
    """OpenAlex holds several work records for one paper, splitting its votes.

    VGG appeared twice in ResNet's peers, at 32 and 20 votes, because the
    preprint and the published version are separate works. Neither number is
    the real edge weight.
    """

    def test_merges_by_title_and_sums_strength(self, monkeypatch):
        monkeypatch.setattr(traversal, "_fetch_works", lambda wids, **kw: {
            "https://openalex.org/WA": _work("WA", "Very Deep Convolutional Networks", 75545),
            "https://openalex.org/WB": _work("WB", "Very deep convolutional networks.", 113102),
            "https://openalex.org/WC": _work("WC", "Something Else", 10),
        })

        results = traversal._materialise(
            [("https://openalex.org/WA", 32), ("https://openalex.org/WB", 20),
             ("https://openalex.org/WC", 25)],
            "co_citation", limit=10)

        assert len(results) == 2
        assert results[0]["_relation_strength"] == 52
        # The better-attested record wins the metadata.
        assert results[0]["citation_count"] == 113102
        assert results[1]["title"] == "Something Else"

    def test_limit_applies_after_merging(self, monkeypatch):
        """Two records merging into one must not cost a slot in the output."""
        monkeypatch.setattr(traversal, "_fetch_works", lambda wids, **kw: {
            "https://openalex.org/WA": _work("WA", "Same Paper", 9),
            "https://openalex.org/WB": _work("WB", "Same paper", 5),
            "https://openalex.org/WC": _work("WC", "Other", 1),
        })

        results = traversal._materialise(
            [("https://openalex.org/WA", 3), ("https://openalex.org/WB", 3),
             ("https://openalex.org/WC", 2)],
            "co_citation", limit=2)

        assert [p["title"] for p in results] == ["Same Paper", "Other"]
        assert results[0]["_relation_strength"] == 6

    def test_titleless_records_are_dropped(self, monkeypatch):
        monkeypatch.setattr(traversal, "_fetch_works", lambda wids, **kw: {
            "https://openalex.org/WA": _work("WA", ""),
            "https://openalex.org/WB": _work("WB", "Real Paper"),
        })

        results = traversal._materialise(
            [("https://openalex.org/WA", 9), ("https://openalex.org/WB", 2)],
            "co_citation", limit=10)
        assert [p["title"] for p in results] == ["Real Paper"]
