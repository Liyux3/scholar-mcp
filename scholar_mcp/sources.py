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


def parallel_search(query: str, limit: int = 100, **kwargs) -> list[SourceResult]:
    sources = search_sources()
    if not sources:
        return []
    results = []
    with ThreadPoolExecutor(max_workers=min(len(sources), 12)) as pool:
        futures = {
            pool.submit(_timed_call, s.name, s.search, query, limit, **kwargs): s.name
            for s in sources
        }
        for future in as_completed(futures):
            results.append(future.result())
    return results


def parallel_citations(paper_id: str, limit: int = 20) -> list[SourceResult]:
    sources = citation_sources()
    if not sources:
        return []
    results = []
    with ThreadPoolExecutor(max_workers=min(len(sources), 6)) as pool:
        futures = {
            pool.submit(_timed_call, s.name, s.get_citations, paper_id, limit=limit): s.name
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


_register_defaults()
