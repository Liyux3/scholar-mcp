"""Tests for new source clients: Europe PMC, DBLP, INSPIRE-HEP."""

import pytest


@pytest.mark.integration
class TestEuropePMC:
    def test_search_returns_results(self):
        from scholar_mcp import europepmc_client
        results = europepmc_client.search_papers("CRISPR", limit=2)
        assert len(results) > 0
        p = results[0]
        assert "title" in p and p["title"]
        assert "source" in p and p["source"] == "europepmc"

    def test_result_has_citation_count(self):
        from scholar_mcp import europepmc_client
        results = europepmc_client.search_papers("deep learning", limit=1)
        if results:
            assert "citation_count" in results[0]

    def test_result_format(self):
        from scholar_mcp import europepmc_client
        results = europepmc_client.search_papers("transformer", limit=1)
        if results:
            p = results[0]
            assert isinstance(p.get("authors"), list)
            assert isinstance(p.get("year"), (int, type(None)))


@pytest.mark.integration
class TestINSPIRE:
    def test_search_returns_results(self):
        from scholar_mcp import inspirehep_client
        results = inspirehep_client.search_papers("dark matter", limit=2)
        assert len(results) > 0
        p = results[0]
        assert "title" in p and p["title"]
        assert p["source"] == "inspirehep"

    def test_physics_domain(self):
        from scholar_mcp import inspirehep_client
        results = inspirehep_client.search_papers("higgs boson", limit=1)
        if results:
            assert "Physics" in results[0].get("fields_of_study", [])


class TestDBLP:
    @pytest.mark.integration
    def test_returns_list_for_query_with_no_matches(self):
        """A query with genuinely no CS matches returns an empty list. HTTP
        failures are a separate case and must raise, see test_propagates_errors.
        """
        from scholar_mcp import dblp_client
        results = dblp_client.search_papers("nonexistent_query_xyz_12345", limit=1)
        assert isinstance(results, list)

    def test_propagates_errors(self, monkeypatch):
        """DBLP throttles with 429/503 rather than slowing down. Returning []
        would make throttling look like sparse CS coverage.
        """
        import httpx
        from scholar_mcp import dblp_client

        class FakeResponse:
            status_code = 503

            def raise_for_status(self):
                raise httpx.HTTPStatusError("503", request=None, response=None)

        monkeypatch.setattr(dblp_client.httpx, "get", lambda *a, **kw: FakeResponse())
        with pytest.raises(httpx.HTTPStatusError):
            dblp_client.search_papers("anything", limit=10)


class TestGoogleScholarBlocking:
    def test_redirect_to_sorry_page_raises(self, monkeypatch):
        """Google answers scraped requests with a 302 to /sorry/ rather than an
        error status, so a bare status check reads it as an ordinary empty
        page and the source silently reports 'no results' while blocked.
        """
        from scholar_mcp import scholar_client

        class FakeResponse:
            status_code = 302
            url = "https://www.google.com/sorry/index?continue=..."
            text = ""
            request = None

        monkeypatch.setattr(scholar_client.httpx, "get", lambda *a, **kw: FakeResponse())
        monkeypatch.setattr(scholar_client.time, "sleep", lambda *_: None)

        with pytest.raises(scholar_client.BlockedError):
            scholar_client.search_papers("anything", max_results=5)


class TestScopusPaging:
    """Free Elsevier keys cap `count` at 25 and reject larger values with
    HTTP 400 instead of clamping. Requesting limit=100 in one shot therefore
    returned zero results for every query; these tests pin the paging fix.
    """

    def _fake_entry(self, i):
        return {
            "dc:title": f"Paper {i}",
            "prism:doi": f"10.1000/{i}",
            "eid": f"2-s2.0-{i}",
            "citedby-count": str(100 - i),
            "prism:coverDate": "2023-01-01",
            "prism:publicationName": "Journal of Testing",
        }

    def test_pages_until_limit(self, monkeypatch):
        from scholar_mcp import config, scopus_client
        monkeypatch.setattr(config, "SCOPUS_API_KEY", "test-key")

        calls = []

        def fake_fetch(query, key, start, count):
            calls.append({"start": start, "count": count})
            return [self._fake_entry(start + i) for i in range(count)]

        monkeypatch.setattr(scopus_client, "_fetch_page", fake_fetch)
        papers = scopus_client.search_papers("knowledge distillation", limit=60)

        assert len(papers) == 60
        assert all(c["count"] <= scopus_client.SCOPUS_MAX_COUNT for c in calls), \
            f"a page exceeded the service cap: {calls}"
        # Pages are fetched concurrently, so call order is not deterministic;
        # what matters is that the offsets tile the range exactly once.
        assert sorted(c["start"] for c in calls) == [0, 25, 50]
        by_start = {c["start"]: c["count"] for c in calls}
        assert by_start[50] == 10, "final page should request only the remainder"

    def test_preserves_api_sort_order(self, monkeypatch):
        """Scopus sorts by citation count server-side. Concurrent pages must be
        reassembled by offset, otherwise whichever page returns first wins.
        """
        from scholar_mcp import config, scopus_client
        monkeypatch.setattr(config, "SCOPUS_API_KEY", "test-key")

        def fake_fetch(query, key, start, count):
            return [self._fake_entry(start + i) for i in range(count)]

        monkeypatch.setattr(scopus_client, "_fetch_page", fake_fetch)
        papers = scopus_client.search_papers("knowledge distillation", limit=75)

        titles = [p["title"] for p in papers]
        assert titles == [f"Paper {i}" for i in range(75)]

    def test_stops_on_empty_page(self, monkeypatch):
        """Exhausted result sets must terminate the loop, not spin forever."""
        from scholar_mcp import config, scopus_client
        monkeypatch.setattr(config, "SCOPUS_API_KEY", "test-key")

        def fake_fetch(query, key, start, count):
            return [self._fake_entry(i) for i in range(5)] if start == 0 else []

        monkeypatch.setattr(scopus_client, "_fetch_page", fake_fetch)
        assert len(scopus_client.search_papers("rare topic", limit=100)) == 5

    def test_propagates_http_errors(self, monkeypatch):
        """Silent [] on failure is what hid this bug for two months. The
        registry layer records exceptions, so clients must let them through.
        """
        import httpx
        from scholar_mcp import config, scopus_client
        monkeypatch.setattr(config, "SCOPUS_API_KEY", "test-key")

        def fake_fetch(query, key, start, count):
            raise httpx.HTTPStatusError("400", request=None, response=None)

        monkeypatch.setattr(scopus_client, "_fetch_page", fake_fetch)
        with pytest.raises(httpx.HTTPStatusError):
            scopus_client.search_papers("anything", limit=10)

    def test_no_key_returns_empty(self, monkeypatch):
        from scholar_mcp import config, scopus_client
        monkeypatch.setattr(config, "SCOPUS_API_KEY", "")
        assert scopus_client.search_papers("anything", limit=10) == []
