"""Source registry for academic search APIs.

Each source registers its capabilities (search, citations, references, paper lookup).
The registry dispatches queries to all capable sources in parallel and returns
structured results. Adding a new source = register() call, no other files change.
"""

import time as _time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Callable

from . import config


@dataclass
class SourceResult:
    source: str
    status: str
    results: list[dict]
    latency_ms: int
    error: str | None = None


# How a source wants its query phrased. Keyword APIs match terms and lose
# recall as the query grows; semantic APIs embed the whole sentence and lose
# meaning when it is stripped to keywords. See docs/QUERY_COMPRESSION.md.
QUERY_RAW = "raw"          # full natural-language query
QUERY_COMPRESSED = "compressed"  # ~6 keywords, the shared default
QUERY_SHORT = "short"      # ~8 keywords, for APIs with hard length limits


@dataclass
class Source:
    name: str
    search: Callable | None = None
    get_citations: Callable | None = None
    get_references: Callable | None = None
    get_paper: Callable | None = None
    priority: int = 50
    domains: list[str] = field(default_factory=lambda: ["all"])
    requires_key: bool = False
    key_available: Callable | None = None
    query_style: str = QUERY_COMPRESSED

    @property
    def semantic(self) -> bool:
        return self.query_style == QUERY_RAW

    def available(self) -> bool:
        if not self.requires_key:
            return True
        if self.key_available:
            return self.key_available()
        return True


_registry: dict[str, Source] = {}


def register(source: Source):
    _registry[source.name] = source


def get(name: str) -> Source | None:
    return _registry.get(name)


def all_sources() -> list[Source]:
    return sorted(_registry.values(), key=lambda s: -s.priority)


def search_sources() -> list[Source]:
    return [s for s in all_sources() if s.search and s.available()]


def citation_sources() -> list[Source]:
    return [s for s in all_sources() if s.get_citations and s.available()]


def reference_sources() -> list[Source]:
    return [s for s in all_sources() if s.get_references and s.available()]


def _timed_call(source_name: str, fn: Callable, *args, **kwargs) -> SourceResult:
    t0 = _time.monotonic()
    try:
        results = fn(*args, **kwargs)
        ms = int((_time.monotonic() - t0) * 1000)
        if results:
            return SourceResult(source_name, "ok", results, ms)
        return SourceResult(source_name, "empty", [], ms)
    except Exception as e:
        ms = int((_time.monotonic() - t0) * 1000)
        status = "timeout" if "timeout" in type(e).__name__.lower() else "error"
        return SourceResult(source_name, status, [], ms, f"{type(e).__name__}: {e}")


def parallel_search(query: str, limit: int = 100, raw_query: str = "", short_query: str = "",
                    budget_s: float | None = None, **kwargs) -> list[SourceResult]:
    """Query every available source concurrently and collect their results.

    budget_s caps how long the whole fan-out may take. Sources that have not
    answered by then are reported as timed out and their results discarded.
    Without a budget the slowest source sets the latency for all of them: one
    source taking 12s while the rest finish in 3s makes every search 12s.
    """
    sources = search_sources()
    if not sources:
        return []

    if budget_s is None:
        budget_s = config.SOURCE_BUDGET_S

    def _pick_query(s):
        if s.query_style == QUERY_RAW:
            return raw_query or query
        if s.query_style == QUERY_SHORT and short_query:
            return short_query
        return query

    results = []
    # Not a context manager: on timeout we must return without waiting for
    # stragglers, and ThreadPoolExecutor.__exit__ joins every worker. The
    # abandoned threads finish into nothing and are reclaimed normally.
    pool = ThreadPoolExecutor(max_workers=min(len(sources), 12))
    try:
        futures = {
            pool.submit(_timed_call, s.name, s.search, _pick_query(s), limit, **kwargs): s.name
            for s in sources
        }
        for future in as_completed(futures, timeout=budget_s):
            results.append(future.result())
    except TimeoutError:
        elapsed_ms = int(budget_s * 1000)
        answered = {r.source for r in results}
        for name in futures.values():
            if name not in answered:
                results.append(SourceResult(
                    name, "timeout", [], elapsed_ms,
                    f"exceeded {budget_s:.0f}s fan-out budget",
                ))
    finally:
        pool.shutdown(wait=False)
    return results


def parallel_citations(paper_id: str, limit: int = 20, title: str = "") -> list[SourceResult]:
    """Fetch citing papers from every capable source.

    title is a fallback identifier. OpenAlex cannot resolve arXiv identifiers
    (it does not index 10.48550 DOIs and has no arXiv-id filter), so without a
    title it silently returns nothing for arXiv papers, leaving S2 as the only
    citation source. S2 orders citations by recency, which is why the graph
    for a 2017 landmark came back full of 2026 papers with one citation each.
    """
    sources = citation_sources()
    if not sources:
        return []
    results = []
    with ThreadPoolExecutor(max_workers=min(len(sources), 6)) as pool:
        futures = {
            pool.submit(_timed_call, s.name, s.get_citations, paper_id,
                        limit=limit, title=title): s.name
            for s in sources
        }
        for future in as_completed(futures):
            results.append(future.result())
    return results


def parallel_references(paper_id: str, limit: int = 20) -> list[SourceResult]:
    sources = reference_sources()
    if not sources:
        return []
    results = []
    with ThreadPoolExecutor(max_workers=min(len(sources), 6)) as pool:
        futures = {
            pool.submit(_timed_call, s.name, s.get_references, paper_id, limit=limit): s.name
            for s in sources
        }
        for future in as_completed(futures):
            results.append(future.result())
    return results


def _register_defaults():
    from . import s2_client, arxiv_client, openalex_client, crossref_client
    from . import core_client, pubmed_client
    from . import europepmc_client, dblp_client, inspirehep_client
    from . import scholar_client
    from . import exa_client, scopus_client, arxivgg_client, doaj_client

    register(Source(
        name="semantic_scholar",
        search=lambda q, limit, **kw: s2_client.search_papers(q, limit=limit, **kw),
        get_citations=s2_client.get_citations,
        get_references=s2_client.get_references,
        get_paper=s2_client.get_paper,
        priority=90,
        domains=["all"],
        requires_key=False,
        key_available=lambda: bool(config.get_s2_api_key()),
        query_style=QUERY_SHORT,
    ))

    register(Source(
        name="openalex",
        search=lambda q, limit, **kw: openalex_client.search_papers(q, limit=limit, **kw),
        get_citations=openalex_client.get_citations,
        get_references=openalex_client.get_references,
        get_paper=openalex_client.get_paper_by_id,
        priority=80,
        domains=["all"],
    ))

    register(Source(
        name="openalex_semantic",
        search=lambda q, limit, **kw: openalex_client.search_papers_semantic(q, limit=min(limit, 50)),
        priority=75,
        domains=["all"],
        query_style=QUERY_RAW,
    ))

    register(Source(
        name="arxiv",
        search=lambda q, limit, **kw: arxiv_client.search_papers(q, max_results=limit),
        priority=70,
        domains=["computer science", "physics", "mathematics", "statistics"],
    ))

    register(Source(
        name="pubmed",
        search=lambda q, limit, **kw: pubmed_client.search_papers(q, max_results=limit),
        priority=40,
        domains=["medicine", "biology", "healthcare"],
    ))

    register(Source(
        name="europepmc",
        search=lambda q, limit, **kw: europepmc_client.search_papers(q, limit=limit),
        priority=35,
        domains=["medicine", "biology", "healthcare", "biochemistry"],
    ))

    register(Source(
        name="crossref",
        search=lambda q, limit, **kw: crossref_client.search_papers(q, limit=limit),
        priority=30,
        domains=["all"],
    ))

    register(Source(
        name="dblp",
        search=lambda q, limit, **kw: dblp_client.search_papers(q, limit=limit),
        priority=25,
        domains=["computer science"],
    ))

    register(Source(
        name="inspirehep",
        search=lambda q, limit, **kw: inspirehep_client.search_papers(q, limit=limit),
        priority=15,
        domains=["physics", "astronomy", "high-energy physics"],
    ))

    register(Source(
        name="core",
        search=lambda q, limit, **kw: core_client.search_papers(q, limit=limit),
        priority=10,
        domains=["all"],
        requires_key=True,
        key_available=lambda: bool(config.CORE_API_KEY),
    ))

    register(Source(
        name="google_scholar",
        search=lambda q, limit, **kw: scholar_client.search_papers(q, max_results=limit),
        priority=5,
        domains=["all"],
    ))

    register(Source(
        name="exa",
        search=lambda q, limit, **kw: exa_client.search_papers(q, limit=limit),
        priority=85,
        domains=["all"],
        query_style=QUERY_RAW,
        requires_key=True,
        key_available=lambda: bool(config.EXA_API_KEY),
    ))

    register(Source(
        name="scopus",
        search=lambda q, limit, **kw: scopus_client.search_papers(q, limit=limit),
        priority=75,
        domains=["all"],
        requires_key=True,
        key_available=lambda: bool(config.SCOPUS_API_KEY),
    ))

    register(Source(
        name="arxivgg_semantic",
        search=lambda q, limit, **kw: arxivgg_client.search_papers(q, limit=limit),
        priority=65,
        domains=["computer science", "physics", "mathematics", "statistics"],
        query_style=QUERY_RAW,
    ))

    register(Source(
        name="doaj",
        search=lambda q, limit, **kw: doaj_client.search_papers(q, limit=limit),
        priority=20,
        domains=["all"],
    ))


_register_defaults()
