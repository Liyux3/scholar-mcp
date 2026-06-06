"""Tests for the source registry and per-source query routing.

Query routing is the highest-leverage decision in the pipeline: sending a
keyword API a 25-word sentence, or a semantic API a 6-word keyword string,
costs more recall than any ranking change measured so far. It had no tests.
"""

import pytest

from scholar_mcp import sources


@pytest.fixture
def isolated_registry(monkeypatch):
    """Swap in an empty registry so tests do not depend on, or disturb, the
    13 real sources registered at import time.
    """
    monkeypatch.setattr(sources, "_registry", {})
    return sources


def _recording_source(name, query_style=sources.QUERY_COMPRESSED, **kw):
    """A source that records the query it was handed."""
    seen = []

    def search(q, limit, **kwargs):
        seen.append(q)
        return [{"title": f"{name} result"}]

    src = sources.Source(name=name, search=search, query_style=query_style, **kw)
    return src, seen


class TestQueryRouting:
    def test_raw_style_gets_unmodified_query(self, isolated_registry):
        src, seen = _recording_source("sem", query_style=sources.QUERY_RAW)
        sources.register(src)

        sources.parallel_search("compressed kw", raw_query="the full original question",
                                short_query="eight word version")
        assert seen == ["the full original question"]

    def test_short_style_gets_short_query(self, isolated_registry):
        src, seen = _recording_source("s2", query_style=sources.QUERY_SHORT)
        sources.register(src)

        sources.parallel_search("compressed kw", raw_query="the full original question",
                                short_query="eight word version")
        assert seen == ["eight word version"]

    def test_compressed_is_the_default(self, isolated_registry):
        src, seen = _recording_source("keyword_api")
        sources.register(src)

        sources.parallel_search("compressed kw", raw_query="the full original question",
                                short_query="eight word version")
        assert seen == ["compressed kw"]

    def test_raw_falls_back_when_absent(self, isolated_registry):
        """Callers that do not supply raw_query still need a working search."""
        src, seen = _recording_source("sem", query_style=sources.QUERY_RAW)
        sources.register(src)

        sources.parallel_search("compressed kw")
        assert seen == ["compressed kw"]

    def test_short_falls_back_when_absent(self, isolated_registry):
        src, seen = _recording_source("s2", query_style=sources.QUERY_SHORT)
        sources.register(src)

        sources.parallel_search("compressed kw")
        assert seen == ["compressed kw"]

    def test_each_source_gets_its_own_style(self, isolated_registry):
        """The whole point of routing: one search, three phrasings."""
        raw_src, raw_seen = _recording_source("sem", query_style=sources.QUERY_RAW)
        short_src, short_seen = _recording_source("s2", query_style=sources.QUERY_SHORT)
        kw_src, kw_seen = _recording_source("oa")
        for s in (raw_src, short_src, kw_src):
            sources.register(s)

        sources.parallel_search("kw six words here", raw_query="full sentence",
                                short_query="eight words")
        assert raw_seen == ["full sentence"]
        assert short_seen == ["eight words"]
        assert kw_seen == ["kw six words here"]


class TestSourceAvailability:
    def test_keyless_source_is_available(self, isolated_registry):
        assert sources.Source(name="free", search=lambda q, l: []).available()

    def test_keyed_source_hidden_without_key(self, isolated_registry):
        src = sources.Source(name="paid", search=lambda q, l: [],
                             requires_key=True, key_available=lambda: False)
        sources.register(src)
        assert not src.available()
        assert sources.search_sources() == []

    def test_keyed_source_visible_with_key(self, isolated_registry):
        src = sources.Source(name="paid", search=lambda q, l: [],
                             requires_key=True, key_available=lambda: True)
        sources.register(src)
        assert [s.name for s in sources.search_sources()] == ["paid"]


class TestParallelSearch:
    def test_reports_status_per_source(self, isolated_registry):
        sources.register(sources.Source(name="ok", search=lambda q, l, **kw: [{"title": "x"}]))
        sources.register(sources.Source(name="empty", search=lambda q, l, **kw: []))

        by_name = {r.source: r for r in sources.parallel_search("q")}
        assert by_name["ok"].status == "ok"
        assert by_name["empty"].status == "empty"

    def test_failing_source_does_not_fail_the_search(self, isolated_registry):
        """One broken API must degrade recall, not break the request."""
        def boom(q, limit, **kw):
            raise RuntimeError("upstream is down")

        sources.register(sources.Source(name="broken", search=boom))
        sources.register(sources.Source(name="healthy", search=lambda q, l, **kw: [{"title": "x"}]))

        by_name = {r.source: r for r in sources.parallel_search("q")}
        assert by_name["broken"].status == "error"
        assert "upstream is down" in by_name["broken"].error
        assert by_name["healthy"].status == "ok"

    def test_records_latency(self, isolated_registry):
        sources.register(sources.Source(name="ok", search=lambda q, l, **kw: [{"title": "x"}]))
        assert sources.parallel_search("q")[0].latency_ms >= 0

    def test_empty_registry_returns_empty(self, isolated_registry):
        assert sources.parallel_search("q") == []


class TestRegisteredDefaults:
    """Guards against a source silently losing its routing during a refactor.

    These assert against the real registry, so they fail if someone changes
    a registration without meaning to.
    """

    def test_semantic_sources_get_raw_queries(self):
        for name in ("openalex_semantic", "arxivgg_semantic"):
            src = sources.get(name)
            assert src is not None, f"{name} is no longer registered"
            assert src.query_style == sources.QUERY_RAW

    def test_s2_gets_short_queries(self):
        """S2's /paper/search returns nothing past roughly 10 words."""
        assert sources.get("semantic_scholar").query_style == sources.QUERY_SHORT

    def test_keyword_sources_get_compressed_queries(self):
        for name in ("openalex", "arxiv", "crossref", "pubmed", "europepmc",
                     "dblp", "doaj", "inspirehep"):
            src = sources.get(name)
            assert src is not None, f"{name} is no longer registered"
            assert src.query_style == sources.QUERY_COMPRESSED

    def test_semantic_property_tracks_query_style(self):
        assert sources.get("openalex_semantic").semantic is True
        assert sources.get("openalex").semantic is False
