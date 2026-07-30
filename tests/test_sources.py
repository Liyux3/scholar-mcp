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


class TestFanOutBudget:
    """One slow source used to set the latency for all of them: a source
    taking 12s while the rest finished in 3s made every search take 12s.
    """

    def test_slow_source_does_not_block_fast_ones(self, isolated_registry):
        import time

        def slow(q, limit, **kw):
            time.sleep(5)
            return [{"title": "late"}]

        sources.register(sources.Source(name="slow", search=slow))
        sources.register(sources.Source(name="fast", search=lambda q, l, **kw: [{"title": "quick"}]))

        t0 = time.monotonic()
        results = sources.parallel_search("q", budget_s=0.5)
        elapsed = time.monotonic() - t0

        assert elapsed < 2.0, f"fan-out waited {elapsed:.1f}s for the slow source"
        by_name = {r.source: r for r in results}
        assert by_name["fast"].status == "ok"
        assert by_name["slow"].status == "timeout"

    def test_every_source_is_reported_even_when_timed_out(self, isolated_registry):
        """Callers reconcile per-source reports against the registry, so a
        dropped source must appear as timed out rather than vanish.
        """
        import time

        sources.register(sources.Source(name="a", search=lambda q, l, **kw: [{"title": "x"}]))
        sources.register(sources.Source(name="b", search=lambda q, l, **kw: (time.sleep(5), [])[1]))

        reported = {r.source for r in sources.parallel_search("q", budget_s=0.5)}
        assert reported == {"a", "b"}

    def test_budget_defaults_from_config(self, isolated_registry, monkeypatch):
        from scholar_mcp import config
        monkeypatch.setattr(config, "SOURCE_BUDGET_S", 0.3)

        import time
        sources.register(sources.Source(name="slow", search=lambda q, l, **kw: (time.sleep(5), [])[1]))

        t0 = time.monotonic()
        results = sources.parallel_search("q")
        assert time.monotonic() - t0 < 2.0
        assert results[0].status == "timeout"


class TestCallersRouteQueries:
    """parallel_search accepts raw_query and short_query as keyword arguments,
    so a caller that omits them silently sends one string to all 13 sources.
    Two callers did exactly that for months: discover_field, which then missed
    the defining paper of any field it was asked about, and the title-based
    expansion channel. Nothing fails at runtime, every source still returns
    results, just worse ones, which is why this is checked structurally.
    """

    def _unrouted_call_sites(self):
        import ast
        from pathlib import Path

        pkg = Path(sources.__file__).parent
        offenders = []
        for path in sorted(pkg.glob("*.py")):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                fn = node.func
                name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
                if name != "parallel_search":
                    continue
                kwargs = {k.arg for k in node.keywords}
                if "raw_query" not in kwargs:
                    offenders.append((path.name, node.lineno))
        return offenders

    # Callers that intentionally send one string to every source, with the
    # reason recorded at the call site.
    EXEMPT = {
        # A bag of frequent terms has no natural-language form, so there is no
        # raw variant to give a semantic source.
        ("expansion.py", "frequent_terms"),
    }

    def test_every_caller_routes_queries(self):
        offenders = self._unrouted_call_sites()
        # Resolve each offender to its enclosing function so exemptions can be
        # named rather than pinned to a line number that drifts.
        import ast
        from pathlib import Path

        named = []
        for filename, lineno in offenders:
            path = Path(sources.__file__).parent / filename
            tree = ast.parse(path.read_text())
            enclosing = "<module>"
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if node.lineno <= lineno <= (node.end_lineno or node.lineno):
                        enclosing = node.name
            named.append((filename, enclosing))

        unexpected = [n for n in named if n not in self.EXEMPT]
        assert not unexpected, (
            f"parallel_search called without raw_query at {unexpected}. Pass "
            "raw_query and short_query, or add the caller to EXEMPT with the "
            "reason it cannot be routed."
        )


class TestDegradedSemanticRouting:
    """arxiv.gg answers HTTP 206 with fallback.used when its embedding index
    is down, silently serving keyword results for a semantic request. Since
    the registry routes semantic sources the raw natural-language query, a
    degraded source receives a full sentence at a keyword matcher, which is
    the worst of both worlds and produces no error.
    """

    def test_degraded_source_gets_compressed_query(self, isolated_registry):
        src, seen = _recording_source("sem", query_style=sources.QUERY_RAW)
        src.semantic_available = lambda: False
        sources.register(src)

        sources.parallel_search("compressed kw", raw_query="the full question",
                                short_query="eight words")
        assert seen == ["compressed kw"]

    def test_healthy_source_still_gets_raw(self, isolated_registry):
        src, seen = _recording_source("sem", query_style=sources.QUERY_RAW)
        src.semantic_available = lambda: True
        sources.register(src)

        sources.parallel_search("compressed kw", raw_query="the full question")
        assert seen == ["the full question"]

    def test_sources_without_a_probe_are_unaffected(self, isolated_registry):
        """Only sources that can degrade need to declare a probe."""
        src, seen = _recording_source("sem", query_style=sources.QUERY_RAW)
        sources.register(src)

        sources.parallel_search("compressed kw", raw_query="the full question")
        assert seen == ["the full question"]


class TestKeylessFleetScaling:
    """An install without API keys loses most of the fleet. OpenAlex now bills
    per request and 429s unauthenticated, and S2 snippet needs a key, so a
    keyless user drops from 13 sources to about 4 and from ~590 candidates to
    ~240. The sources that remain are not themselves constrained, so they are
    asked to carry more of the pool.
    """

    def test_full_fleet_is_unchanged(self):
        assert sources._scale_limit(100, sources.FULL_FLEET) == 100

    def test_scales_up_as_sources_disappear(self):
        assert sources._scale_limit(100, 4) > sources._scale_limit(100, 8) > 100

    def test_respects_the_per_source_ceiling(self):
        """One source must not be asked for an unbounded page just because it
        is the only one left.
        """
        assert sources._scale_limit(100, 1) == sources.MAX_SCALED_LIMIT

    def test_handles_an_empty_fleet(self):
        assert sources._scale_limit(100, 0) == 100

    def test_applies_to_the_actual_fan_out(self, isolated_registry):
        seen = []

        def search(q, limit, **kw):
            seen.append(limit)
            return [{"title": "x"}]

        sources.register(sources.Source(name="lonely", search=search))
        sources.parallel_search("q", limit=100)
        assert seen == [sources.MAX_SCALED_LIMIT]
