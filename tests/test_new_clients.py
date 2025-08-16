"""Tests for new source clients: Europe PMC, DBLP, INSPIRE-HEP."""

import pytest


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
    def test_graceful_on_error(self):
        from scholar_mcp import dblp_client
        results = dblp_client.search_papers("nonexistent_query_xyz_12345", limit=1)
        assert isinstance(results, list)
