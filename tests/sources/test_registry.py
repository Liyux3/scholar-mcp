"""Tests for the source registry and per-source query routing.

Query routing is the highest-leverage decision in the pipeline: sending a
keyword API a 25-word sentence, or a semantic API a 6-word keyword string,
costs more recall than any ranking change measured so far. It had no tests.
"""

import pytest
import httpx
from urllib.parse import unquote

from scholar_mcp import sources


@pytest.mark.parametrize("name", ["doaj", "arxivgg_semantic"])
def test_adapter_transport_failure_is_not_a_successful_empty_search(monkeypatch, name):
    def failed(url, **kwargs):
        return httpx.Response(503, request=httpx.Request("GET", url))
    monkeypatch.setattr(httpx, "get", failed)
    source = sources.get(name)
    result = sources._timed_call(name, source.search, "query", 3)
    assert result.status == "error" and result.results == []


def test_doaj_internal_query_preserves_reserved_url_characters(monkeypatch):
    from scholar_mcp.sources import doaj
    query = 'doi:"10.1234/a?b#c"'
    def get(url, **kwargs):
        parsed = httpx.URL(url)
        assert not parsed.query and not parsed.fragment
        assert unquote(str(parsed).split("/articles/", 1)[1]) == query
        return httpx.Response(200, json={"results": []}, request=httpx.Request("GET", url))
    monkeypatch.setattr(httpx, "get", get)
    assert doaj._search(query, 10) == []


@pytest.fixture
def isolated_registry(monkeypatch):
    """Swap in an empty registry so tests do not depend on, or disturb, the
    real sources registered at import time.
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


def test_recovery_reuses_finished_sources_and_retries_failed_ones(isolated_registry):
    from scholar_mcp import cache
    calls = {'healthy': 0, 'retry': 0}
    def healthy(*args, **kwargs):
        calls['healthy'] += 1
        return [{'title': 'Completed work'}]
    def retry(*args, **kwargs):
        calls['retry'] += 1
        if calls['retry'] == 1:
            raise TimeoutError('interrupted source')
        return [{'title': 'Recovered work'}]
    for name, fn in [('healthy', healthy), ('retry', retry)]:
        sources.register(sources.Source(name=name, search=fn))
    first = sources.parallel_search('same request')
    assert {r.source: r.status for r in first} == {'healthy': 'ok', 'retry': 'timeout'}
    cache._cache.clear()
    recovered = sources.parallel_search('same request')
    assert all(r.status == 'ok' for r in recovered)
    assert calls == {'healthy': 1, 'retry': 2}


def test_web_lane_runs_during_seed_ranking_and_still_joins(isolated_registry):
    import threading
    release = threading.Event()
    sources.register(sources.Source(name='api', search=lambda *a, **kw: [{'title': 'API seed'}]))
    def web(*args, **kwargs):
        assert release.wait(5)
        return [{'title': 'Web result'}]
    sources.register(sources.Source(name='web', group='web', search=web))
    flow = sources.search_flow([('q', '', '')], budget_s=5)
    try:
        first = flow.take(include_deferred=False)
        assert any(r.results and r.results[0]['title'] == 'API seed' for r in first)
        release.set()
        assert any(r.results and r.results[0]['title'] == 'Web result' for r in flow.take())
    finally:
        release.set()
        flow.close()


def test_progress_is_immutable_and_survives_a_later_source_failure():
    import threading
    published, release = threading.Event(), threading.Event()
    def fetch():
        sources.publish([{'title': 'Completed page'}])
        published.set()
        assert release.wait(3)
        raise TimeoutError('later page failed')
    flow = sources.SourceFlow({'paged': [fetch]}, budget_s=1)
    try:
        assert published.wait(2)
        first = flow.take(wait_for_lanes=False)
        assert first[0].status == 'partial' and first[0].results[0]['title'] == 'Completed page'
        first[0].results[0]['title'] = 'caller mutation'
        release.set()
        last = flow.take()
        assert last[0].status == 'partial' and last[0].results[0]['title'] == 'Completed page'
        assert 'TimeoutError' in last[0].error
    finally:
        release.set()
        flow.close()


def test_completed_first_query_does_not_hide_an_unfinished_later_query():
    from threading import Event
    release = Event()
    def later():
        release.wait(3)
        return []
    flow = sources.SourceFlow({'one-provider': [lambda: [{'title': 'Completed first query'}], later]},
                              budget_s=0.05, hydrate=False)
    try:
        result = flow.collect()
        assert result[0].results[0]['title'] == 'Completed first query'
        assert result[0].status == 'partial'
        assert 'queries' in result[0].error
    finally:
        release.set()


def test_ready_records_keep_native_rank_while_other_metadata_runs(monkeypatch):
    import threading
    from scholar_mcp import metadata, server
    release = threading.Event()
    def hydrate(rows, fields):
        assert release.wait(3)
        rows[0]['title'] = 'Resolved first record'
        rows[0].pop('_needs_metadata')
    monkeypatch.setattr(metadata, 'hydrate', hydrate)
    flow = sources.SourceFlow({'mixed': [lambda: [
        {'title': 'Pending', '_needs_metadata': True}, {'title': 'Ready'}]]}, budget_s=1)
    try:
        first = flow.take(wait_for_metadata=False)
        papers, reports = [], []
        server._fold_results(first, papers, reports)
        assert papers[0]['title'] == 'Ready' and papers[0]['_source_ranks']['mixed'] == 1
        assert reports[0]['status'] == 'partial'
        release.set()
        server._fold_results(flow.take(), papers, reports)
        assert len(reports) == 1 and reports[0]['count'] == 2 and reports[0]['status'] == 'ok'
    finally:
        release.set()
        flow.close()


def test_nested_flow_publishes_before_its_slowest_lane_finishes():
    import threading
    snapshots, published, release = [], threading.Event(), threading.Event()
    def receive(rows):
        snapshots.append(rows)
        if rows:
            published.set()
        return True
    token = sources._progress.set(receive)
    try:
        flow = sources.SourceFlow({'quick': [lambda: [{'title': 'Fast answer'}]],
            'slow': [lambda: release.wait(3) and [{'title': 'Late answer'}]]}, budget_s=1)
    finally:
        sources._progress.reset(token)
    try:
        assert published.wait(2)
        assert snapshots[0][0]['title'] == 'Fast answer'
        release.set()
        assert sum(len(r.results) for r in flow.take()) == 2
    finally:
        release.set()
        flow.close()


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

    def test_keyword_budget_is_source_specific(self, isolated_registry):
        src, seen = _recording_source("wide_keyword", keyword_words=4)
        sources.register(src)

        sources.parallel_search(
            "shared compressed query",
            raw_query="alpha beta gamma delta epsilon zeta",
        )

        assert seen == ["alpha beta gamma delta"]


class TestSourceAvailability:
    def test_keyless_source_is_available(self, isolated_registry):
        assert sources.Source(name="free", search=lambda q, limit: []).available()

    def test_keyed_source_hidden_without_key(self, isolated_registry):
        src = sources.Source(name="paid", search=lambda q, limit: [],
                             requires_key=True, key_available=lambda: False)
        sources.register(src)
        assert not src.available()
        assert sources.search_sources() == []

    def test_keyed_source_visible_with_key(self, isolated_registry):
        src = sources.Source(name="paid", search=lambda q, limit: [],
                             requires_key=True, key_available=lambda: True)
        sources.register(src)
        assert [s.name for s in sources.search_sources()] == ["paid"]


class TestParallelSearch:
    @pytest.mark.parametrize("kind", ["search", "references", "citations"])
    def test_metadata_starts_before_the_slow_source_finishes(self, isolated_registry, monkeypatch, kind):
        from threading import Event
        from scholar_mcp import metadata
        hydration_started = Event()
        def slow(*args, **kwargs):
            assert hydration_started.wait(3), "metadata waited for the slow source"
            return [{"title": "Slow source paper"}]
        def hydrate(papers, fields=None):
            hydration_started.set()
            papers[0]["title"] = "Resolved early reference"
            return papers
        monkeypatch.setattr(metadata, "hydrate", hydrate)
        method = {"search": "search", "references": "get_references", "citations": "get_citations"}[kind]
        sources.register(sources.Source(name="fast", **{method: lambda *a, **kw: [{"_needs_metadata": True}]}))
        sources.register(sources.Source(name="slow", **{method: slow}))
        call = getattr(sources, "parallel_" + kind)
        result = call("query-or-id")
        assert {p["title"] for sr in result for p in sr.results} == {"Resolved early reference", "Slow source paper"}
        assert all(sr.status == "ok" for sr in result)

    def test_reports_status_per_source(self, isolated_registry):
        sources.register(sources.Source(name="ok", search=lambda q, limit, **kw: [{"title": "x"}]))
        sources.register(sources.Source(name="empty", search=lambda q, limit, **kw: []))

        by_name = {r.source: r for r in sources.parallel_search("q")}
        assert by_name["ok"].status == "ok"
        assert by_name["empty"].status == "empty"

    def test_failing_source_does_not_fail_the_search(self, isolated_registry):
        """One broken API must degrade recall, not break the request."""
        def boom(q, limit, **kw):
            raise RuntimeError("upstream is down")

        sources.register(sources.Source(name="broken", search=boom))
        sources.register(sources.Source(name="healthy", search=lambda q, limit, **kw: [{"title": "x"}]))

        by_name = {r.source: r for r in sources.parallel_search("q")}
        assert by_name["broken"].status == "error"
        assert "upstream is down" in by_name["broken"].error
        assert by_name["healthy"].status == "ok"

    def test_records_latency(self, isolated_registry):
        sources.register(sources.Source(name="ok", search=lambda q, limit, **kw: [{"title": "x"}]))
        assert sources.parallel_search("q")[0].latency_ms >= 0

    def test_empty_registry_returns_empty(self, isolated_registry):
        assert sources.parallel_search("q") == []


class TestPdfResolution:
    def test_preserves_registry_priority_and_deduplicates_urls(self, isolated_registry):
        sources.register(sources.Source(
            name="secondary",
            priority=10,
            resolve_pdf=lambda paper: ["https://example.test/shared.pdf"],
        ))
        sources.register(sources.Source(
            name="primary",
            priority=90,
            resolve_pdf=lambda paper: [
                "https://example.test/primary.pdf",
                "https://example.test/shared.pdf",
            ],
        ))

        assert sources.resolve_pdf_candidates({"title": "paper"}) == [
            ("primary", "https://example.test/primary.pdf", "unknown"),
            ("primary", "https://example.test/shared.pdf", "unknown"),
        ]


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
                     "dblp", "doaj", "inspirehep", "openaire", "hal"):
            src = sources.get(name)
            assert src is not None, f"{name} is no longer registered"
            assert src.query_style == sources.QUERY_COMPRESSED

    def test_measured_keyword_budgets_are_registered(self):
        assert sources.get("arxiv").keyword_words == 10
        assert sources.get("crossref").keyword_words == 12
        assert sources.get("openaire").keyword_words == 10
        assert sources.get("hal").keyword_words == 10
        assert sources.get("zenodo").applies("dataset", "q") and not sources.get("zenodo").applies("", "q")
        assert sources.get("zenodo").resolve_pdf is not None

    def test_semantic_property_tracks_query_style(self):
        assert sources.get("openalex_semantic").semantic is True
        assert sources.get("openalex").semantic is False

    def test_capability_matrix_is_derived_from_registry(self):
        matrix = {row["name"]: row for row in sources.capabilities()}
        assert matrix["openaire"]["search"] is True
        assert matrix["openaire"]["pdf"] is True
        assert matrix["zenodo"]["search"] is True
        assert matrix["zenodo"]["pdf"] is True


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
        sources.register(sources.Source(name="fast", search=lambda q, limit, **kw: [{"title": "quick"}]))

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

        sources.register(sources.Source(name="a", search=lambda q, limit, **kw: [{"title": "x"}]))
        sources.register(sources.Source(name="b", search=lambda q, limit, **kw: (time.sleep(5), [])[1]))

        reported = {r.source for r in sources.parallel_search("q", budget_s=0.5)}
        assert reported == {"a", "b"}

    def test_budget_defaults_from_config(self, isolated_registry, monkeypatch):
        from scholar_mcp import config
        monkeypatch.setattr(config, "SOURCE_BUDGET_S", 0.3)

        import time
        sources.register(sources.Source(name="slow", search=lambda q, limit, **kw: (time.sleep(5), [])[1]))

        t0 = time.monotonic()
        results = sources.parallel_search("q")
        assert time.monotonic() - t0 < 2.0
        assert results[0].status == "timeout"


class TestSearchFlow:
    """Several queries share one flow: one lane per source, lanes side by side."""

    def test_each_source_answers_queries_in_order_one_at_a_time(self, isolated_registry):
        import threading
        log, lock = [], threading.Lock()
        both_started, fast_done = threading.Barrier(2), threading.Event()
        def lane(name):
            def search(q, limit, **kwargs):
                with lock:
                    assert not any(n == name and done is None for n, _, done in log)
                    entry = [name, q, None]
                    log.append(entry)
                if q == 'a':
                    both_started.wait(timeout=10)
                    if name == 'slow':
                        assert fast_done.wait(10), 'fast lane waited for the slow round'
                entry[2] = True
                if name == 'fast' and q == 'c':
                    fast_done.set()
                return [{'title': f'{name} {q}'}]
            return sources.Source(name=name, search=search)
        sources.register(lane('fast'))
        sources.register(lane('slow'))
        flow = sources.search_flow([("a", "", ""), ("b", "", ""), ("c", "", "")])
        results = flow.collect()

        for name in ("fast", "slow"):
            assert [q for n, q, _ in log if n == name] == ["a", "b", "c"]
        assert all(result.status == 'ok' for result in results)
        assert sorted((sr.source, sr.query_index) for sr in results) == [
            ("fast", 0), ("fast", 1), ("fast", 2), ("slow", 0), ("slow", 1), ("slow", 2)]

    def test_a_late_answer_is_collected_by_the_next_take(self, isolated_registry):
        import threading
        release = threading.Event()
        sources.register(sources.Source(name="fast", search=lambda q, limit, **kw: [{"title": "quick"}]))
        sources.register(sources.Source(name="late", search=lambda q, limit, **kw: release.wait(5) and [{"title": "late"}]))

        flow = sources.search_flow([("q", "", "")], budget_s=0.2)
        first = {sr.source: sr.status for sr in flow.take()}
        assert first == {"fast": "ok", "late": "timeout"}

        release.set()
        flow.deadline += 5  # the merge point arrives later in the pipeline
        second = flow.take()
        assert [(sr.source, sr.status) for sr in second] == [("late", "ok")]
        flow.close()

    def test_close_starts_nothing_new(self, isolated_registry):
        import threading
        started, release = [], threading.Event()

        def search(q, limit, **kw):
            started.append(q)
            release.wait(5)
            return []
        sources.register(sources.Source(name="one", search=search))
        flow = sources.search_flow([("a", "", ""), ("b", "", "")], budget_s=0.1)
        flow.take()
        flow.close()
        release.set()
        import time
        time.sleep(0.2)
        assert started == ["a"]

    def test_a_slow_metadata_lookup_cannot_hold_the_search_past_its_deadline(self, isolated_registry, monkeypatch):
        import threading
        import time
        from scholar_mcp import metadata
        release = threading.Event()
        monkeypatch.setattr(metadata, "needs_metadata", lambda paper, fields=None: paper["title"] == "bare")
        monkeypatch.setattr(metadata, "hydrate", lambda papers, fields=None: release.wait(5) and papers)
        sources.register(sources.Source(name="plain", search=lambda q, limit, **kw: [{"title": "full"}]))
        sources.register(sources.Source(name="sparse", search=lambda q, limit, **kw: [{"title": "bare"}]))

        flow = sources.search_flow([("q", "", "")], budget_s=0.3)
        t0 = time.monotonic()
        first = {sr.source: sr.status for sr in flow.take()}
        assert time.monotonic() - t0 < 1.0
        assert first == {"plain": "ok", "sparse": "timeout"}

        release.set()
        time.sleep(0.1)
        assert [(sr.source, sr.status) for sr in flow.take(wait_for_lanes=False)] == [("sparse", "ok")]
        flow.close()

    def test_close_between_calls_sends_nothing_more(self, isolated_registry):
        import threading
        import time
        started, gate = [], threading.Event()

        def search(q, limit, **kw):
            started.append(q)
            gate.wait(1)
            return []
        sources.register(sources.Source(name="one", search=search))
        flow = sources.search_flow([("a", "", ""), ("b", "", "")], budget_s=0.1)
        flow.take()  # "a" is still out
        flow.close()
        gate.set()
        time.sleep(0.2)
        assert started == ["a"]

    def test_opt_in_and_routed_sources_leave_page_sizes_alone(self, isolated_registry):
        limits = {}

        def source(name, **options):
            return sources.Source(name=name, search=lambda q, limit, **kw: limits.setdefault(name, limit) and [],
                                  **options)
        sources.register(source("base"))
        sources.register(source("keyed", opt_in=True))
        sources.register(source("routed", applies=lambda intent, text: True))
        sources.parallel_search("q", limit=10)
        assert limits["base"] == limits["keyed"] == sources._scale_limit(10, 1)

    def test_every_operation_shares_one_flow_and_reports_timeouts(self, isolated_registry):
        """Citation lookups and PDF resolution run in the same lanes as searches,
        so a hanging provider costs them the budget, not the whole call."""
        import threading
        import time
        hang = threading.Event()
        sources.register(sources.Source(name="quick", get_citations=lambda pid, limit, **kw: [{"title": "C"}],
                                        resolve_pdf=lambda paper: ["https://quick.example/a.pdf"]))
        sources.register(sources.Source(name="stuck", get_citations=lambda pid, limit, **kw: hang.wait(5) and [],
                                        resolve_pdf=lambda paper: hang.wait(5) and []))
        from scholar_mcp import config
        config_budget = config.SOURCE_BUDGET_S
        try:
            config.SOURCE_BUDGET_S = 0.3
            t0 = time.monotonic()
            cites = {r.source: r.status for r in sources.parallel_citations("W1")}
            pdfs = sources.resolve_pdf_candidates({"title": "T"})
            assert time.monotonic() - t0 < 1.5
        finally:
            config.SOURCE_BUDGET_S = config_budget
            hang.set()
        assert cites == {"quick": "ok", "stuck": "timeout"}
        assert pdfs == [("quick", "https://quick.example/a.pdf", "unknown")]

    def test_every_client_request_is_paced_by_a_published_limit(self):
        """Every provider a client names has a row, and every row has a client."""
        import pathlib
        import re
        from scholar_mcp.sources import pacing
        code = "".join(p.read_text() for p in pathlib.Path(pacing.__file__).parents[1].rglob("*.py"))
        named = set(re.findall(r'''pacing\.wait\(["']([a-z-]+)["']''', code)) | {"crossref", "crossref-record", "semantic-scholar"}
        assert named == set(pacing.LIMITS)
        assert pacing.LIMITS["arxiv"][0] == ((1, 3.0),) and pacing.LIMITS["openaire"][0] == ((60, 3600),)


class TestCallersRouteQueries:
    """parallel_search accepts raw_query and short_query as keyword arguments,
    so a caller that omits them silently sends one string to every source.
    Earlier field mapping and title expansion callers did exactly that for
    months. Nothing fails at runtime, every source still returns results, just
    worse ones, which is why this is checked structurally.
    """

    def _unrouted_call_sites(self):
        import ast
        from pathlib import Path

        pkg = Path(sources.__file__).parent.parent
        offenders = []
        for path in sorted(pkg.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                fn = node.func
                name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
                if name != "parallel_search":
                    continue
                kwargs = {k.arg for k in node.keywords}
                if "raw_query" not in kwargs:
                    offenders.append((path.relative_to(pkg).as_posix(), node.lineno))
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
            path = Path(sources.__file__).parent.parent / filename
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
    keyless user drops from the full fleet to about 4 and from ~590 candidates to
    ~240. The sources that remain are not themselves constrained, so they are
    asked to carry more of the pool.
    """

    def test_full_fleet_is_unchanged(self):
        assert sources._scale_limit(100, sources.FULL_FLEET) == 100

    def test_full_fleet_matches_registry(self):
        # Sources routed to some searches only (datasets) are not part of every fan-out.
        assert sources.FULL_FLEET == len(
            [source for source in sources.all_sources() if source.search and source.applies is None and not source.opt_in]
        )

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


class TestIntentRouting:
    def test_routed_sources_join_only_their_searches(self, isolated_registry):
        general, general_seen = _recording_source("general")
        datasets, dataset_seen = _recording_source("datasets", applies=lambda intent, text: intent == "dataset")
        sources.register(general)
        sources.register(datasets)
        sources.parallel_search("q")
        assert dataset_seen == [] and general_seen == ["q"]
        sources.parallel_search("q", intent="dataset")
        assert dataset_seen == ["q"]

    def test_zenodo_serves_dataset_searches_only(self):
        zenodo = sources.get("zenodo")
        assert zenodo.applies("dataset", "q") and not zenodo.applies("", "q") and not zenodo.applies("survey", "q")


def test_zenodo_dataset_search_keeps_datasets_only(monkeypatch):
    import httpx
    from scholar_mcp.sources import zenodo
    hits = [{"id": 1, "metadata": {"title": "Memory benchmark logs", "resource_type": {"type": "dataset"},
                                   "doi": "10.5281/zenodo.1", "publication_date": "2025-03-01",
                                   "creators": [{"name": "Doe, Jane"}], "access_right": "open"}},
            {"id": 2, "metadata": {"title": "A talk", "resource_type": {"type": "presentation"}}}]
    seen = {}
    def get(url, params=None, timeout=None):
        seen.update(params)
        return httpx.Response(200, json={"hits": {"hits": hits}}, request=httpx.Request("GET", url))
    monkeypatch.setattr(httpx, "get", get)
    papers = zenodo.search_datasets("agent memory", limit=5)
    assert seen["type"] == "dataset" and seen["sort"] == "bestmatch"
    assert [p["title"] for p in papers] == ["Memory benchmark logs"]
    assert papers[0]["publication_types"] == ["Dataset"] and papers[0]["year"] == 2025


def test_cinii_joins_only_japanese_searches_with_keyword_runs(monkeypatch):
    import httpx
    from scholar_mcp.sources import cinii
    regional = sources.get("cinii")
    assert regional.applies("", "大規模言語モデルの長期記憶に関する論文")
    assert not regional.applies("", "long-term memory for LLM agents") and not regional.applies("", "深度学习记忆模型")
    assert cinii.japanese_keywords("深層学習を使った記憶モデルの研究を探しています") == "深層学習 記憶モデル"
    seen = {}
    def get(url, params=None, timeout=None):
        seen.update(params)
        item = {"title": "記憶モデルの提案", "dc:creator": ["山田 太郎"], "prism:publicationDate": "2015-05-01",
                "prism:publicationName": "人工知能学会全国大会論文集", "link": {"@id": "https://cir.nii.ac.jp/crid/1"},
                "dc:identifier": [{"@type": "cir:DOI", "@value": "10.11517/x"}]}
        return httpx.Response(200, json={"items": [item]}, request=httpx.Request("GET", url))
    monkeypatch.setattr(httpx, "get", get)
    [paper] = cinii.search_papers("深層学習を使った記憶モデルの研究", limit=5)
    assert seen["q"] == "深層学習 記憶モデル"
    assert paper["external_ids"] == {"DOI": "10.11517/x"} and paper["year"] == 2015 and paper["authors"] == ["山田 太郎"]


def test_every_source_has_a_known_group():
    for source in sources.all_sources():
        assert source.group in sources.SOURCE_GROUPS, source.name
    matrix = {row["name"]: row["group"] for row in sources.capabilities()}
    assert matrix["cinii"] == "regional" and matrix["zenodo"] == "dataset" and matrix["openalex"] == "index"


def test_zbmath_is_part_of_every_search_and_keeps_identifiers():
    from scholar_mcp.sources import zbmath
    assert sources.get("zbmath").applies is None
    paper = zbmath.format_document({
        "id": 2207064, "title": {"title": "Spectral techniques applied to sparse random graphs"}, "year": "2005",
        "contributors": {"authors": [{"name": "Feige, Uriel"}]},
        "source": [{"source": "Random Struct. Algorithms 27, No. 2, 251-275 (2005)."}],
        "links": [{"type": "doi", "identifier": "10.1002/rsa.20089"}, {"type": "arxiv", "identifier": "math/0001"}],
        "zbmath_url": "https://zbmath.org/?q=an:1087.05056"})
    assert paper["venue"] == "Random Struct. Algorithms" and paper["year"] == 2005
    assert paper["external_ids"] == {"zbMATH": "2207064", "DOI": "10.1002/rsa.20089", "ArXiv": "math/0001"}
    reviewed = zbmath.format_document({"title": {"title": "T"}, "editorial_contributions": [
        {"contribution_type": "summary", "text": "zbMATH Open Web Interface contents unavailable due to conflicting licenses."},
        {"contribution_type": "review", "text": "This paper treats\n the global  regularity problem."}]})
    assert reviewed["abstract"] == "This paper treats the global regularity problem."
    summary = zbmath.format_document({"title": {"title": "T"}, "editorial_contributions": [
        {"contribution_type": "summary", "text": "Summary: We prove global regularity."}]})
    assert summary["abstract"] == "We prove global regularity."


@pytest.mark.parametrize("status,expected", [(429, "paced"), (401, "blocked"), (403, "blocked"), (500, "error")])
def test_http_source_failures_keep_their_operational_meaning(status, expected):
    def failed():
        httpx.Response(status, request=httpx.Request("GET", "https://provider.test")).raise_for_status()
    assert sources._timed_call("fixture", failed).status == expected
