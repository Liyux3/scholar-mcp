"""Tests for citation-graph expansion.

These channels were closures inside _pipeline and could not be tested at all,
which is how an evaluation harness that reconstructed the pipeline incorrectly
came to report that four of them contributed nothing.
"""

import pytest

from scholar_mcp import expansion


def _paper(title="A Paper", doi="10.1/x", cites=0, abstract=""):
    return {"title": title, "abstract": abstract, "citation_count": cites,
            "external_ids": {"DOI": doi} if doi else {}, "paper_id": doi or ""}


class _SourceResult:
    def __init__(self, results):
        self.results = results


class TestSeedId:
    def test_prefers_doi(self):
        paper = {"external_ids": {"DOI": "10.1/x", "OpenAlex": "W1"}}
        assert expansion._seed_id(paper) == "10.1/x"

    def test_falls_back_through_the_id_list(self):
        assert expansion._seed_id({"external_ids": {"ArXiv": "1706.03762"}}) == "1706.03762"

    def test_rejects_a_bare_openalex_paper_id(self):
        """A W-id in paper_id is not usable by the sources that take an id,
        and OpenAlex itself is reached through external_ids.
        """
        assert expansion._seed_id({"paper_id": "W123", "external_ids": {}}) == ""

    def test_returns_empty_when_nothing_identifies_the_paper(self):
        assert expansion._seed_id({"title": "No ids"}) == ""


class TestCitationsChannel:
    def test_filters_by_intent_citation_floor(self, monkeypatch):
        """A search for foundational work has no use for uncited preprints."""
        returned = [_paper("Established", cites=50), _paper("Brand New", cites=0)]
        monkeypatch.setattr(expansion.sources, "parallel_citations",
                            lambda *a, **kw: [_SourceResult(returned)])

        ctx = expansion.ExpansionContext(intent="foundational")
        titles = [p["title"] for p in expansion.citations(_paper(), ctx)]
        assert titles == ["Established"]

    def test_default_intent_keeps_anything_cited_once(self, monkeypatch):
        returned = [_paper("Cited Once", cites=1), _paper("Uncited", cites=0)]
        monkeypatch.setattr(expansion.sources, "parallel_citations",
                            lambda *a, **kw: [_SourceResult(returned)])

        titles = [p["title"] for p in
                  expansion.citations(_paper(), expansion.ExpansionContext())]
        assert titles == ["Cited Once"]

    def test_passes_the_title_through(self, monkeypatch):
        """OpenAlex cannot resolve an arXiv id without it, and losing OpenAlex
        means citations come back recency-ordered instead of impact-ordered.
        """
        seen = {}

        def fake(pid, limit=20, title=""):
            seen["title"] = title
            return []

        monkeypatch.setattr(expansion.sources, "parallel_citations", fake)
        expansion.citations(_paper(title="Attention Is All You Need"),
                            expansion.ExpansionContext())
        assert seen["title"] == "Attention Is All You Need"

    def test_unidentifiable_seed_costs_no_request(self, monkeypatch):
        monkeypatch.setattr(expansion.sources, "parallel_citations",
                            lambda *a, **kw: pytest.fail("should not be called"))
        assert expansion.citations({"title": "No ids"}, expansion.ExpansionContext()) == []


class TestTitleSearchChannel:
    def test_routes_the_title_per_source(self, monkeypatch):
        """A title is natural language: semantic sources want it verbatim,
        keyword sources want it compressed. Handing one string to all of them
        gives most of them the wrong form.
        """
        captured = {}

        def fake(query, limit=20, raw_query="", short_query="", **kw):
            captured.update(query=query, raw_query=raw_query, short_query=short_query)
            return []

        monkeypatch.setattr(expansion.sources, "parallel_search", fake)
        long_title = ("Denoising Diffusion Probabilistic Models for High Resolution "
                      "Image Synthesis with Classifier Free Guidance")
        expansion.title_search(_paper(title=long_title), expansion.ExpansionContext())

        # Semantic sources get the title untouched.
        assert captured["raw_query"] == long_title
        # Keyword sources get something shorter than the sentence.
        assert 0 < len(captured["query"].split()) < len(long_title.split())
        assert captured["short_query"]

    def test_short_title_needs_no_compression(self, monkeypatch):
        """Compression only applies above a length threshold, so a short title
        reaches keyword sources as it stands.
        """
        captured = {}

        def fake(query, limit=20, raw_query="", short_query="", **kw):
            captured.update(query=query, raw_query=raw_query)
            return []

        monkeypatch.setattr(expansion.sources, "parallel_search", fake)
        expansion.title_search(_paper(title="Attention Is All You Need"),
                               expansion.ExpansionContext())
        assert captured["query"] == captured["raw_query"] == "Attention Is All You Need"

    def test_titleless_seed_costs_no_request(self, monkeypatch):
        monkeypatch.setattr(expansion.sources, "parallel_search",
                            lambda *a, **kw: pytest.fail("should not be called"))
        assert expansion.title_search({"title": ""}, expansion.ExpansionContext()) == []


class TestFrequentTermsChannel:
    def test_builds_one_query_from_terms_shared_across_seeds(self, monkeypatch):
        captured = {}

        def fake(query, limit=50, **kw):
            captured["query"] = query
            return []

        monkeypatch.setattr(expansion.sources, "parallel_search", fake)
        seeds = [
            _paper("Knowledge distillation for language models",
                   abstract="distillation compresses transformer networks"),
            _paper("Distilling transformers efficiently",
                   abstract="knowledge distillation of neural networks"),
        ]
        expansion.frequent_terms(expansion.ExpansionContext(seeds=seeds))
        assert "distillation" in captured["query"]

    def test_no_usable_terms_means_no_request(self, monkeypatch):
        monkeypatch.setattr(expansion.sources, "parallel_search",
                            lambda *a, **kw: pytest.fail("should not be called"))
        ctx = expansion.ExpansionContext(seeds=[{"title": "", "abstract": ""}])
        assert expansion.frequent_terms(ctx) == []


class TestExpand:
    def test_runs_each_channel_for_each_seed(self, monkeypatch):
        calls = []
        monkeypatch.setattr(expansion, "GLOBAL_CHANNELS", {})
        monkeypatch.setattr(expansion, "CHANNELS", {
            "a": lambda seed, ctx: calls.append(("a", seed["title"])) or [_paper("from-a")],
            "b": lambda seed, ctx: calls.append(("b", seed["title"])) or [],
        })

        out = expansion.expand([_paper(title="s1"), _paper(title="s2")])
        assert sorted(calls) == [("a", "s1"), ("a", "s2"), ("b", "s1"), ("b", "s2")]
        assert len(out["a"]) == 2
        assert out["b"] == []

    def test_results_are_keyed_by_channel(self, monkeypatch):
        """Attribution is the point: flattening happens in the caller, so an
        evaluation harness can see which channel produced what.
        """
        monkeypatch.setattr(expansion, "GLOBAL_CHANNELS", {})
        monkeypatch.setattr(expansion, "CHANNELS", {
            "refs": lambda seed, ctx: [_paper("ref-paper")],
            "cites": lambda seed, ctx: [_paper("cite-paper")],
        })

        out = expansion.expand([_paper()])
        assert out["refs"][0]["title"] == "ref-paper"
        assert out["cites"][0]["title"] == "cite-paper"

    def test_a_failing_channel_does_not_lose_the_others(self, monkeypatch):
        def boom(seed, ctx):
            raise RuntimeError("API down")

        monkeypatch.setattr(expansion, "GLOBAL_CHANNELS", {})
        monkeypatch.setattr(expansion, "CHANNELS", {
            "broken": boom,
            "working": lambda seed, ctx: [_paper("survived")],
        })

        out = expansion.expand([_paper()])
        assert out["broken"] == []
        assert out["working"][0]["title"] == "survived"

    def test_global_channels_run_once_not_per_seed(self, monkeypatch):
        calls = []
        monkeypatch.setattr(expansion, "CHANNELS", {})
        monkeypatch.setattr(expansion, "GLOBAL_CHANNELS", {
            "title_search": lambda ctx: calls.append(len(ctx.seeds)) or [_paper("global")],
        })

        out = expansion.expand([_paper(title="s1"), _paper(title="s2")])
        assert calls == [2]
        assert len(out["title_search"]) == 1

    def test_optional_channels_are_off_by_default(self, monkeypatch):
        monkeypatch.setattr(expansion, "GLOBAL_CHANNELS", {})
        monkeypatch.setattr(expansion, "OPTIONAL_CHANNELS", {
            "peers": lambda seed, ctx: pytest.fail("should not run by default"),
        })
        monkeypatch.setattr(expansion, "CHANNELS", {"a": lambda seed, ctx: []})
        expansion.expand([_paper()])

    def test_optional_channels_run_when_named(self, monkeypatch):
        monkeypatch.setattr(expansion, "OPTIONAL_CHANNELS", {
            "peers": lambda seed, ctx: [_paper("peer")],
        })
        out = expansion.expand([_paper()], channels=["peers"])
        assert out["peers"][0]["title"] == "peer"

    def test_no_seeds_means_no_work(self, monkeypatch):
        monkeypatch.setattr(expansion, "CHANNELS", {
            "a": lambda seed, ctx: pytest.fail("should not be called"),
        })
        assert expansion.expand([]) == {}

    def test_per_seed_limit_reaches_the_channel(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(expansion, "GLOBAL_CHANNELS", {})

        def channel(seed, ctx):
            seen["limit"] = ctx.per_seed_limit
            return []

        monkeypatch.setattr(expansion, "CHANNELS", {"a": channel})
        expansion.expand([_paper()], per_seed_limit=7)
        assert seen["limit"] == 7
