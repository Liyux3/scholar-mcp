"""Source registry for academic search APIs.

Each source registers its capabilities (search, citations, references, paper lookup).
The registry dispatches queries to all capable sources in parallel and returns
structured results. Adding a new source = register() call, no other files change.
"""

import re
import threading
import time as _time
from concurrent.futures import Future, ThreadPoolExecutor, wait
from contextvars import ContextVar, copy_context
from copy import deepcopy
from dataclasses import dataclass, field, replace
from functools import partial
from typing import Callable

from .. import access, config, relevance, usage_log
from .. import records as record_ops
from ..cache import cached
from . import pacing


@dataclass
class SourceResult:
    source: str
    status: str
    results: list[dict]
    latency_ms: int
    error: str | None = None
    query_index: int = 0  # which query of a multi-query flow this answers
    # Share of results that came with an abstract, before any hydration. A
    # provider that renames a field keeps answering, only this number drops.
    abstracts: float | None = None
    positions: list[int] | None = None


_progress: ContextVar[Callable | None] = ContextVar('scholar_source_progress', default=None)


def publish(results: list[dict]) -> bool:
    """Publish validated cumulative results; False asks a paged client to stop."""
    callback = _progress.get()
    return callback(results) if callback is not None else True


class PartialResults(list):
    def __init__(self, rows, warning: str):
        super().__init__(rows)
        self.partial_warning = warning


@dataclass
class _Arrival:
    result: SourceResult
    ready: list[tuple[int, object]]
    metadata: Future | None
    final: bool
    revision: int
    delivered: tuple[int, bool] | None = None


# How a source wants its query phrased. Keyword APIs match terms and lose
# recall as the query grows; semantic APIs embed the whole sentence and lose
# meaning when it is stripped to keywords.
QUERY_RAW = "raw"          # full natural-language query
QUERY_COMPRESSED = "compressed"  # ~6 keywords, the shared default
QUERY_SHORT = "short"      # ~8 keywords, for APIs with hard length limits


# What kind of source this is. The groups describe coverage, not priority:
#   index       general bibliographic indexes (OpenAlex, Semantic Scholar, Crossref, Scopus)
#   semantic    engines that embed the whole question
#   fulltext    search inside paper text
#   preprint    preprint servers and open review
#   discipline  indexes for one field (PubMed, DBLP, INSPIRE-HEP)
#   repository  open-access aggregators and repositories
#   regional    national or language indexes, routed by the query's script
#   dataset     data repositories, routed by intent="dataset"
#   web         web search engines
SOURCE_GROUPS = ("index", "semantic", "fulltext", "preprint", "discipline", "repository", "regional", "dataset", "web")


@dataclass
class Source:
    name: str
    search: Callable | None = None
    get_citations: Callable | None = None
    get_references: Callable | None = None
    get_paper: Callable | None = None
    resolve_pdf: Callable | None = None
    priority: int = 50
    domains: list[str] = field(default_factory=lambda: ["all"])
    requires_key: bool = False
    key_available: Callable | None = None
    query_style: str = QUERY_COMPRESSED
    group: str = "index"
    # Sources a user turns on with their own key. They do not count toward
    # FULL_FLEET, so installs without those keys keep their usual page sizes.
    opt_in: bool = False
    # Billed per request. Health never probes it; real searches vouch for it.
    paid: bool = False
    # Optional probe for sources whose semantic mode can degrade at runtime.
    # Returning False makes the registry route compressed queries instead.
    semantic_available: Callable | None = None
    # Some keyword endpoints have a measured source-specific optimum. When
    # set, derive that many terms from the original question instead of using
    # the shared six-word compromise.
    keyword_words: int | None = None
    # Sources that serve only some searches say which: called with the search
    # intent and the query text, False keeps the source out at no cost.
    applies: Callable[[str, str], bool] | None = None
    # Native namespaces have one authoritative getter and their own health fixture.
    id_pattern: str = ''
    probe_query: str = ''
    probe_id: str = ''
    primary_types: tuple[str, ...] = ()

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

# Every source call runs in one bounded process-wide pool. A fresh executor
# per call let timed-out workers outlive their request; over a long benchmark,
# those abandoned pools accumulated until every source timed out together.
# Calls wait on the network, so the bound is generous: expansion runs its
# citation, reference and title lanes for three seeds at once.
SOURCE_WORKERS = 64
_source_pool = ThreadPoolExecutor(max_workers=SOURCE_WORKERS, thread_name_prefix="scholar-source")
# Hydration orchestrators only wait on metadata lookups, whose own pool and
# provider gates (Crossref's semaphore, OpenAlex batches) set the real load.
# Two of them made expansion's citation and reference results queue: one
# 17 s lookup held the next relation's hydration back by as much.
_metadata_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="scholar-hydration")


def register(source: Source):
    # Save completed source work independently. Repeating a search after a
    # process interruption reuses the sources that finished, and retries the rest.
    for operation in ("search", "get_citations", "get_references", "get_paper", "resolve_pdf"):
        fn = getattr(source, operation)
        if fn is not None:
            version = (3 if operation == 'resolve_pdf' else
                       2 if operation == 'search' and source.name in {'openalex', 'openalex_semantic'} else 1)
            setattr(source, operation, cached(ttl=300,
                persistent=True, namespace=f"sources.v{version}.{source.name}.{operation}")(fn))
    _registry[source.name] = source


def get(name: str) -> Source | None:
    return _registry.get(name)


def native_source(identifier: str) -> Source | None:
    return next((source for source in _registry.values() if source.id_pattern and source.available()
                 and re.fullmatch(source.id_pattern, identifier)), None)


class SourceLookupError(RuntimeError):
    def __init__(self, source: str, cause: Exception):
        self.source = source
        super().__init__(f'{source} lookup failed ({type(cause).__name__})')


def all_sources() -> list[Source]:
    return sorted(_registry.values(), key=lambda s: -s.priority)


def search_sources() -> list[Source]:
    return [s for s in all_sources() if s.search and s.available()]


def citation_sources() -> list[Source]:
    return [s for s in all_sources() if s.get_citations and s.available()]


def reference_sources() -> list[Source]:
    return [s for s in all_sources() if s.get_references and s.available()]


def pdf_sources() -> list[Source]:
    return [s for s in all_sources() if s.resolve_pdf and s.available()]


def capabilities() -> list[dict]:
    """Return the registry contract without exposing credentials."""
    return [
        {
            "name": source.name,
            "available": source.available(),
            "priority": source.priority,
            "group": source.group,
            "opt_in": source.opt_in,
            "query_style": source.query_style if source.search else None,
            "search": source.search is not None,
            "paper_lookup": source.get_paper is not None,
            "citations": source.get_citations is not None,
            "references": source.get_references is not None,
            "pdf": source.resolve_pdf is not None,
            "domains": source.domains,
        }
        for source in all_sources()
    ]


# Source count a full, keyed install fans out to. Fewer than this means keys
# are missing and the remaining sources should carry more of the pool.
FULL_FLEET = 20
MAX_SCALED_LIMIT = 300
PDF_CANDIDATES_PER_SOURCE = 2
MAX_PDF_CANDIDATES = 8


def _baseline(registered: list[Source]) -> int:
    """Sources that stand in for the full fleet. Opt-in and routed sources add
    to a search, so they leave the other sources' page sizes alone."""
    return sum(1 for s in registered if not s.opt_in and s.applies is None)


def _scale_limit(limit: int, available: int) -> int:
    """Ask surviving sources for more when much of the fleet is unavailable.

    Scales in proportion to what is missing, capped so a single source is
    never asked for an unreasonable page. Measured on a keyless install:
    arXiv returns 300 in 3.5s against 100 in 2.2s, Crossref 300 in 2.3s
    against 100 in 1.8s, so the extra breadth is nearly free.
    """
    if available >= FULL_FLEET or available <= 0:
        return limit
    scaled = int(limit * FULL_FLEET / available)
    return min(scaled, MAX_SCALED_LIMIT)


def _timed_call(source_name: str, fn: Callable, *args, **kwargs) -> SourceResult:
    t0 = _time.monotonic()
    try:
        results = fn(*args, **kwargs)
        ms = int((_time.monotonic() - t0) * 1000)
        warning = getattr(results, "partial_warning", None)
        if results or warning:
            papers = [r for r in results if isinstance(r, dict)]
            share = round(sum(bool(p.get("abstract")) for p in papers) / len(papers), 2) if papers else None
            return SourceResult(source_name, "partial" if warning else "ok", results, ms, warning, abstracts=share)
        return SourceResult(source_name, "empty", [], ms)
    except Exception as e:
        ms = int((_time.monotonic() - t0) * 1000)
        code = getattr(e, "code", "")
        http_status = getattr(getattr(e, "response", None), "status_code", None)
        status = ("paced" if isinstance(e, pacing.Paced) or code == "RATE_LIMITED" or http_status == 429 else
                  "blocked" if isinstance(e, PermissionError) or code in {"AUTH_REQUIRED", "CHALLENGE"} or http_status in {401, 403} else
                  "timeout" if "timeout" in type(e).__name__.lower() or code == "TIMEOUT" else "error")
        # httpx quotes the request URL, which for several sources carries the key.
        return SourceResult(source_name, status, [], ms, re.sub(r"https?://\S+", "<url>", f"{type(e).__name__}: {e}"))


def _hydration(result: SourceResult, fields: set[str] | None = None):
    """Start completing a finished source's records while other sources are still out."""
    from ..metadata import hydrate, needs_metadata
    papers = [p for p in result.results if isinstance(p, dict)]
    if any(needs_metadata(paper, fields) for paper in papers):
        return _metadata_pool.submit(copy_context().run, hydrate, papers, fields)
    return None


def _pick_query(s: Source, query: str, raw_query: str = "", short_query: str = "") -> str:
    style = s.query_style
    # A semantic source that has fallen back to keyword matching should be
    # fed a keyword query, not the raw sentence it would normally want.
    if style == QUERY_RAW and s.semantic_available and not s.semantic_available():
        style = QUERY_COMPRESSED
    if style == QUERY_RAW:
        return raw_query or query
    if s.keyword_words:
        base = raw_query or query
        return relevance.keyword_query(base, max_words=s.keyword_words) or query
    if style == QUERY_SHORT and short_query:
        return short_query
    return relevance.optimize_query(query)


def _search_budget(registered: list[Source], limit: int) -> float:
    """How long one query may hold the fan-out before its seeds are taken.

    Without a budget the slowest source sets the latency for all of them: one
    source taking 12s while the rest finish in 3s makes every search 12s.
    """
    budget_s = config.SOURCE_BUDGET_S
    if any(s.name == "google_scholar" for s in registered):
        from .. import scholar_session
        from .google_scholar import PAGE_SIZE
        # Allow the requested page count while preserving completed pages.
        pages = (_scale_limit(limit, _baseline(registered)) + PAGE_SIZE - 1) // PAGE_SIZE
        budget_s = max(budget_s, min(120, 15 + pages * 6))
        if not access.available() and scholar_session.recovery_available() and not scholar_session.current():
            # First-use verification is exceptional setup work. Let its
            # bounded worker finish before the seeds are chosen.
            budget_s = max(budget_s, scholar_session.RECOVERY_TIMEOUT + 30)
    return budget_s


class SourceFlow:
    """Bounded per-source lanes with progressive, immutable answer snapshots.

    Pagination and metadata can continue while callers rank ready records.
    Native positions survive partial delivery. Every completed answer replaces
    the previous snapshot for its call, so a later take can finish the merge.
    """

    def __init__(self, calls: dict[str, list[Callable[[], list]]], budget_s: float,
                 metadata_fields: set[str] | None = None, hydrate: bool = True, op: str = "search",
                 deferred_sources: set[str] | None = None, executor: ThreadPoolExecutor | None = None,
                 max_workers: int | None = None):
        self.started = _time.monotonic()
        self.deadline = self.started + pacing.remaining(budget_s * max((len(c) for c in calls.values()), default=1))
        self._fields = set(metadata_fields or ())
        self._hydrate, self._op = hydrate, op
        self._deferred = set(deferred_sources or ())
        self._lock = threading.Lock()
        self._notification_lock = threading.Lock()
        self._parent_progress = _progress.get()
        self._parent_version = None
        self._arrived: dict[tuple[str, int], _Arrival] = {}
        self._call_counts = {name: len(lane) for name, lane in calls.items()}
        self._closed = False
        self._capacity = threading.Semaphore(max_workers or max(len(calls), 1))
        pool = executor or _source_pool
        self._lanes = {}
        for name, lane in calls.items():
            future = pool.submit(copy_context().run, self._lane, name, lane)
            self._lanes[future] = name
            if self._closed:
                future.cancel()

    def _arrive(self, result: SourceResult, final: bool) -> bool:
        if self._closed:
            return False
        from ..metadata import needs_metadata
        key = result.source, result.query_index
        with self._lock:
            previous = self._arrived.get(key)
            if previous and previous.final and not final:
                return not self._closed
        if final and not result.results and previous and previous.result.results:
            result = replace(result, results=deepcopy(previous.result.results), status="partial")
        elif not final:
            result = replace(result, results=deepcopy(result.results))
        ready = [(i, paper) for i, paper in enumerate(result.results)
                 if not self._hydrate or isinstance(paper, dict) and not needs_metadata(paper, self._fields)]
        hydration = _hydration(result, self._fields) if final and self._hydrate else None
        with self._lock:
            previous = self._arrived.get(key)
            if previous and previous.final and not final:
                return not self._closed
            self._arrived[key] = _Arrival(result, ready, hydration, final,
                1 + (previous.revision if previous else 0), previous.delivered if previous else None)
        if hydration is not None:
            hydration.add_done_callback(lambda _: self._notify_parent())
        self._notify_parent()
        return not self._closed

    def _notify_parent(self) -> None:
        if self._parent_progress is None:
            return
        # Serialize cumulative snapshots, including metadata completions. A
        # slower notification must not overwrite a newer parent snapshot.
        with self._notification_lock:
            with self._lock:
                version = tuple((key, a.revision, a.metadata is None or a.metadata.done())
                                for key, a in self._arrived.items())
                if version == self._parent_version:
                    return
                self._parent_version = version
                packets = [p for a in self._arrived.values() if (p := self._packet(a, mark=False)) is not None]
            if not self._parent_progress([paper for packet in packets for paper in packet.results]):
                self.close()

    def _lane(self, name: str, calls: list[Callable[[], list]]) -> None:
        with self._capacity:
            self._run_lane(name, calls)

    def _run_lane(self, name: str, calls: list[Callable[[], list]]) -> None:
        for index, call in enumerate(calls):
            if self._closed:
                return
            started = _time.monotonic()
            def progressed(rows):
                return self._arrive(SourceResult(name, "partial", rows,
                    int((_time.monotonic() - started) * 1000), "source results still arriving", index), False)
            token = _progress.set(progressed)
            try:
                result = _timed_call(name, call)
            finally:
                _progress.reset(token)
            result.query_index = index
            self._arrive(result, True)

    def _packet(self, arrival: _Arrival, mark: bool = True) -> SourceResult | None:
        future = arrival.metadata
        done = future is None or future.done()
        failure = future is not None and done and (future.cancelled() or future.exception() is not None)
        full = arrival.final and done and not failure
        signature = arrival.revision, done
        if mark and arrival.delivered == signature:
            return None
        rows = list(enumerate(arrival.result.results)) if full else arrival.ready
        if not rows and not full and not failure:
            return None
        if mark:
            arrival.delivered = signature
        result = arrival.result
        status = result.status if full else 'partial'
        error = result.error if full or not arrival.final else (
            'source metadata recovery failed' if failure else 'some source metadata is still resolving')
        if self._hydrate and full:
            valid = [(i, paper) for i, paper in rows if isinstance(paper, dict) and paper.get('title')]
            if len(valid) != len(rows):
                status, error = 'partial', f'{len(rows) - len(valid)} source record identities remain unresolved'
                rows = valid
        papers = deepcopy([paper for _, paper in rows])
        for (position, _), paper in zip(rows, papers):
            if isinstance(paper, dict):
                paper.setdefault('_retrieval_ranks', {})[f'{self._op}/{result.source}/{result.query_index}'] = position
        return replace(result, results=papers, positions=[i for i, _ in rows], status=status, error=error)

    def take(self, wait_for_lanes: bool = True, include_deferred: bool = True,
             wait_for_metadata: bool = True) -> list[SourceResult]:
        """New snapshots since the last take, retaining already usable records.

        A seed take can leave metadata in flight. A final take waits for it up
        to the same deadline, without exposing a dict still being mutated.
        """
        deadline = self.deadline if include_deferred else min(self.deadline, self.started + config.SOURCE_BUDGET_S)
        if wait_for_lanes:
            lanes = [future for future, name in self._lanes.items()
                     if include_deferred or name not in self._deferred]
            wait(lanes, timeout=max(0.0, deadline - _time.monotonic()))
            if wait_for_metadata:
                with self._lock:
                    held = [a.metadata for (name, _), a in self._arrived.items() if a.metadata is not None
                            and (include_deferred or name not in self._deferred)]
                wait(held, timeout=max(0.0, deadline - _time.monotonic()))
        with self._lock:
            answers = [packet for a in self._arrived.values() if (packet := self._packet(a)) is not None]
            holding = {name for (name, _), a in self._arrived.items()
                       if a.metadata is not None and not a.metadata.done()}
            supplied = {name for (name, _), a in self._arrived.items() if a.delivered is not None
                        and (a.ready or a.final and a.result.results)}
            unfinished = {name for name, count in self._call_counts.items()
                          if sum(a.final for (source, _), a in self._arrived.items() if source == name) < count}
        seen = {a.source for a in answers} | supplied
        elapsed_ms = int((_time.monotonic() - self.started) * 1000)
        for answer in answers:
            if answer.source in unfinished and answer.status in {'ok', 'empty'}:
                answer.status = 'partial'
                answer.error = f'some source queries are still answering after {elapsed_ms / 1000:.0f}s'
        answers += [SourceResult(name, "timeout", [], elapsed_ms, f"still answering after {elapsed_ms / 1000:.0f}s")
                    for future, name in self._lanes.items() if name not in seen and (not future.done() or name in holding)]
        for answer in answers:
            usage_log.note_source(answer.source if self._op == "search" else f"{answer.source}/{self._op}",
                answer.status, answer.latency_ms, len(answer.results), answer.abstracts, answer.error or "")
        return answers

    def close(self) -> None:
        """Stop new lane calls; paged clients stop at their next publication."""
        self._closed = True
        for future in list(self._lanes):
            future.cancel()

    def collect(self) -> list[SourceResult]:
        try:
            return self.take()
        finally:
            self.close()


def search_flow(queries: list[tuple[str, str, str]], limit: int = 100, budget_s: float | None = None,
                metadata_fields: set[str] | None = None, intent: str = "", operation: str = 'search', **kwargs) -> SourceFlow:
    """Send (keyword, raw, short) query triples to every available source.

    budget_s is per query; the flow's deadline is that budget times the number
    of queries, the same bound the queries had when they ran in sequence.
    intent routes sources that serve only some searches, such as datasets.
    """
    wanted = set(record_ops.normalize_publication_types(kwargs.get('publication_types')))
    text = ' '.join([*(raw or keyword for keyword, raw, _ in queries), ' '.join(sorted(wanted)),
                     str(kwargs.get('fields_of_study') or '')])
    registered = [s for s in search_sources() if s.applies is None or s.applies(intent, text)]
    primary = {kind for source in all_sources() for kind in source.primary_types}
    if wanted and wanted <= primary:
        registered = [source for source in registered if wanted.intersection(source.primary_types)]
    # Without API keys most of the fleet is unavailable: OpenAlex now bills per
    # request and returns 429 unauthenticated, and S2 snippet needs a key too.
    # A keyless install loses several high-yield channels and much of the
    # candidate pool. The survivors are not themselves limited; arXiv and Crossref both
    # serve 300 with almost no extra latency, so ask them for more rather than
    # leaving the pool thin.
    if budget_s is None:
        budget_s = _search_budget(registered, limit)
    from ..metadata import filter_fields
    size = _scale_limit(limit, _baseline(registered))

    def search(source: Source, query: tuple[str, str, str]) -> Callable[[], list]:
        # The query form is chosen when the call starts: a semantic source can degrade meanwhile.
        return lambda: source.search(_pick_query(source, *query), size, **kwargs)
    return SourceFlow({s.name: [search(s, q) for q in queries] for s in registered}, budget_s,
                      set(metadata_fields or ()) | filter_fields(kwargs),
                      deferred_sources={s.name for s in registered if s.group == "web"}, op=operation)


def parallel_search(query: str, limit: int = 100, raw_query: str = "", short_query: str = "",
                    budget_s: float | None = None, metadata_fields: set[str] | None = None,
                    intent: str = "", **kwargs) -> list[SourceResult]:
    """One query to every available source; sources still out at the budget report a timeout."""
    return search_flow([(query, raw_query, short_query)], limit, budget_s, metadata_fields, intent, **kwargs).collect()


def parallel_citations(paper_id: str, limit: int = 20, title: str = "",
                       metadata_fields: set[str] | None = None, openalex_id: str = "") -> list[SourceResult]:
    """Fetch citing papers from every capable source.

    title is a fallback identifier. OpenAlex cannot resolve arXiv identifiers
    (it does not index 10.48550 DOIs and has no arXiv-id filter), so without a
    title it silently returns nothing for arXiv papers, leaving S2 as the only
    citation source. S2 orders citations by recency, which is why the graph
    for a 2017 landmark came back full of 2026 papers with one citation each.
    """
    return _parallel_relations(citation_sources(), "get_citations", paper_id, limit, metadata_fields,
                               title=title, openalex_id=openalex_id)


def parallel_references(paper_id: str, limit: int = 20,
                        metadata_fields: set[str] | None = None) -> list[SourceResult]:
    return _parallel_relations(reference_sources(), "get_references", paper_id, limit, metadata_fields)


def _parallel_relations(registered: list[Source], method: str, paper_id: str,
                        limit: int, metadata_fields: set[str] | None = None, **kwargs) -> list[SourceResult]:
    openalex_id = kwargs.pop('openalex_id', '')
    if native := native_source(paper_id):
        registered = [native] if getattr(native, method) else []
    if not registered:
        return []
    results = SourceFlow({s.name: [partial(getattr(s, method),
                         openalex_id if s.name == 'openalex' and openalex_id else paper_id,
                         limit=limit, **kwargs)] for s in registered},
                         config.SOURCE_BUDGET_S, metadata_fields, op=method.removeprefix("get_")).collect()
    for result in results:
        unresolved = sum(not paper.get("title") for paper in result.results)
        if unresolved:
            result.error = f"{unresolved} reference identities could not be resolved"
            result.status = 'partial'
        result.results = [paper for paper in result.results if paper.get("title")]
    return results


def resolve_pdf_candidates(paper: dict, budget_s: float | None = None) -> list[tuple[str, str, str]]:
    """Resolve repository PDF candidates through registered sources in parallel.

    Resolution is separate from downloading: providers return candidate URLs,
    while ``pdf_utils`` performs the same streamed PDF validation for every
    source. Results retain source priority rather than network completion order.
    """
    registered = pdf_sources()
    if not registered:
        return []
    results = SourceFlow({s.name: [partial(s.resolve_pdf, paper)] for s in registered},
                         config.SOURCE_BUDGET_S if budget_s is None else budget_s, hydrate=False, op="pdf").collect()
    resolved = {r.source: r.results[:PDF_CANDIDATES_PER_SOURCE] for r in results}

    candidates = []
    seen = {}
    for source in registered:
        for location in resolved.get(source.name, []):
            url = str(location.get('url', '') if isinstance(location, dict) else location).strip()
            if not url:
                continue
            version = record_ops.normalize_version(location.get('version') if isinstance(location, dict) else None)
            if url in seen:
                index = seen[url]
                if record_ops.PUBLICATION_VERSIONS.index(version) < record_ops.PUBLICATION_VERSIONS.index(candidates[index][2]):
                    candidates[index] = (source.name, url, version)
                continue
            seen[url] = len(candidates)
            candidates.append((source.name, url, version))
            if len(candidates) >= MAX_PDF_CANDIDATES:
                return candidates
    return candidates


def _register_defaults():
    from . import (
        ads,
        arxiv,
        arxivgg,
        cinii,
        clinicaltrials,
        core,
        courtlistener,
        crossref,
        dblp,
        doaj,
        eric,
        europepmc,
        exa,
        google_scholar,
        hal,
        inspirehep,
        lens,
        openaire,
        openalex,
        openedition,
        openreview,
        pubmed,
        s2,
        s2_snippet,
        scopus,
        springer,
        wos,
        zbmath,
        zenodo,
    )

    register(Source(name='eric', search=eric.search_papers, get_paper=eric.get_paper,
                    priority=35, group='discipline', domains=['education'], applies=eric.applies,
                    id_pattern=eric.ID_PATTERN, probe_query='literacy tutoring', probe_id='ERIC:EJ1489919'))
    register(Source(name='courtlistener', search=courtlistener.search_papers, get_paper=courtlistener.get_paper,
                    priority=35, group='discipline', domains=['US case law'], applies=courtlistener.applies,
                    query_style=QUERY_RAW,
                    primary_types=('CourtDecision',),
                    id_pattern=courtlistener.ID_PATTERN, probe_query='algorithmic discrimination', probe_id='CourtListener:5010564'))
    register(Source(name='clinicaltrials', search=clinicaltrials.search_papers, get_paper=clinicaltrials.get_paper,
                    get_references=clinicaltrials.get_references, priority=35, group='discipline',
                    domains=['clinical trial registrations'], applies=clinicaltrials.applies,
                    primary_types=('ClinicalTrialRegistration',),
                    id_pattern=clinicaltrials.ID_PATTERN, probe_query='heart failure dapagliflozin', probe_id='NCT03036124'))
    register(Source(name='openedition', search=openedition.search_papers, get_paper=openedition.get_paper,
                    resolve_pdf=openedition.resolve_pdf, priority=25, group='discipline', domains=['humanities', 'social sciences'],
                    applies=openedition.applies, id_pattern=openedition.ID_PATTERN, probe_query='causalité',
                    probe_id='OpenEdition:https://books.openedition.org/psorbonne/128981'))

    register(Source(
        name="semantic_scholar",
        search=s2.search_papers,
        get_citations=s2.get_citations,
        get_references=s2.get_references,
        get_paper=s2.get_paper,
        priority=90,
        query_style=QUERY_SHORT,
    ))

    register(Source(
        name="openalex",
        search=openalex.search_papers,
        get_citations=openalex.get_citations,
        get_references=openalex.get_references,
        get_paper=openalex.get_paper_by_id,
        priority=80,
    ))

    register(Source(
        name="s2_snippet",
        group="fulltext",
        search=s2_snippet.search_papers,
        priority=78,
        query_style=QUERY_RAW,
    ))

    register(Source(
        name="openalex_semantic",
        group="semantic",
        search=lambda q, limit, **kw: openalex.search_papers_semantic(q, limit=min(limit, 50), **kw),
        priority=75,
        query_style=QUERY_RAW,
    ))

    register(Source(
        name="arxiv",
        group="preprint",
        search=lambda q, limit, **kw: arxiv.search_papers(q, max_results=limit),
        get_paper=arxiv.get_paper,
        priority=70,
        domains=["computer science", "physics", "mathematics", "statistics"],
        keyword_words=10,
        # arXiv's API terms: no more than one request every three seconds.
    ))

    register(Source(
        name="pubmed",
        group="discipline",
        search=lambda q, limit, **kw: pubmed.search_papers(q, max_results=limit),
        priority=40,
        domains=["medicine", "biology", "healthcare"],
    ))

    register(Source(
        name="europepmc",
        group="discipline",
        search=europepmc.search_papers,
        get_paper=europepmc.get_paper,
        get_citations=europepmc.get_citations,
        get_references=europepmc.get_references,
        resolve_pdf=europepmc.resolve_pdf,
        priority=35,
        domains=["medicine", "biology", "healthcare", "biochemistry"],
    ))

    register(Source(
        name="crossref",
        search=crossref.search_papers,
        get_paper=crossref.get_paper,
        get_references=crossref.get_references,
        priority=30,
        keyword_words=12,
    ))

    register(Source(
        name="dblp",
        group="discipline",
        search=dblp.search_papers,
        priority=25,
        domains=["computer science"],
    ))

    register(Source(
        name="inspirehep",
        group="discipline",
        search=inspirehep.search_papers,
        get_paper=inspirehep.get_paper,
        get_citations=inspirehep.get_citations,
        get_references=inspirehep.get_references,
        priority=15,
        domains=["physics", "astronomy", "high-energy physics"],
    ))

    register(Source(
        name="core",
        group="repository",
        search=lambda q, limit, **kw: core.search_papers(q, limit=limit),
        resolve_pdf=core.resolve_pdf,
        priority=10,
        requires_key=True,
        key_available=lambda: bool(config.CORE_API_KEY),
    ))

    register(Source(
        name="google_scholar",
        group="web",
        search=lambda q, limit, **kw: google_scholar.search_papers(q, max_results=limit),
        priority=5,
    ))

    register(Source(
        name="exa",
        group="semantic",
        search=exa.search_papers,
        priority=85,
        query_style=QUERY_RAW,
        requires_key=True,
        key_available=lambda: bool(config.EXA_API_KEY),
        paid=True,
    ))

    register(Source(
        name="scopus",
        search=scopus.search_papers,
        priority=75,
        requires_key=True,
        key_available=lambda: bool(config.SCOPUS_API_KEY),
    ))

    register(Source(
        name="arxivgg_semantic",
        group="semantic",
        search=arxivgg.search_papers,
        priority=65,
        domains=["computer science", "physics", "mathematics", "statistics"],
        query_style=QUERY_RAW,
        semantic_available=arxivgg.semantic_available,
    ))

    register(Source(
        name="doaj",
        group="repository",
        search=doaj.search_papers,
        resolve_pdf=doaj.resolve_pdf,
        priority=20,
    ))

    register(Source(
        name="openaire",
        group="repository",
        search=openaire.search_papers,
        get_paper=openaire.get_paper,
        resolve_pdf=openaire.resolve_pdf,
        priority=18,
        keyword_words=10,
    ))

    register(Source(
        name="hal",
        group="repository",
        search=hal.search_papers,
        resolve_pdf=hal.resolve_pdf,
        priority=17,
        keyword_words=10,
    ))

    register(Source(
        name="ads",
        search=ads.search_papers,
        priority=60,
        domains=["astronomy", "astrophysics", "physics"],
        group="discipline",
        requires_key=True,
        key_available=lambda: bool(config.ADS_API_TOKEN),
        opt_in=True,
        # These APIs match every term, like Scopus, so they get a short query.
        # The length is a first guess until a key allows a sweep.
        keyword_words=4,
    ))

    register(Source(
        name="springer",
        search=springer.search_papers,
        priority=60,
        group="index",
        requires_key=True,
        key_available=lambda: bool(config.SPRINGER_API_KEY),
        opt_in=True,
        # These APIs match every term, like Scopus, so they get a short query.
        # The length is a first guess until a key allows a sweep.
        keyword_words=4,
    ))

    register(Source(
        name="wos",
        search=wos.search_papers,
        priority=60,
        group="index",
        requires_key=True,
        key_available=lambda: bool(config.WOS_API_KEY),
        opt_in=True,
        # These APIs match every term, like Scopus, so they get a short query.
        # The length is a first guess until a key allows a sweep.
        keyword_words=3,
    ))

    register(Source(
        name="lens",
        search=lens.search_papers,
        priority=60,
        group="index",
        requires_key=True,
        key_available=lambda: bool(config.LENS_API_TOKEN),
        opt_in=True,
        # These APIs match every term, like Scopus, so they get a short query.
        # The length is a first guess until a key allows a sweep.
        keyword_words=6,
    ))

    register(Source(
        name="zbmath",
        search=zbmath.search_papers,
        priority=22,
        group="discipline",
        domains=["mathematics"],
        # Always on: mathematical results underlie work in many fields, and a
        # question about something else simply matches nothing here.
        # Matches every term; five keywords still find on-topic papers.
        keyword_words=5,
    ))

    register(Source(
        name="cinii",
        group="regional",
        search=cinii.search_papers,
        priority=14,
        domains=["japan"],
        # The raw question; the client cuts Japanese into keyword runs itself.
        query_style=QUERY_RAW,
        applies=lambda intent, text: cinii.is_japanese(text),
    ))

    register(Source(
        name="zenodo",
        group="dataset",
        # The largest general data repository, ranked by best match. Its
        # publications are too noisy for paper search, so it only joins
        # searches for datasets, where it is the primary registry.
        search=zenodo.search_datasets,
        applies=lambda intent, text: intent == "dataset",
        resolve_pdf=zenodo.resolve_pdf,
        priority=12,
        domains=["datasets"],
    ))

    register(Source(
        name="openreview",
        group="preprint",
        search=lambda q, limit, **kw: openreview.search_papers(
            q,
            max_results=limit,
            venue=kw.get("venue") or None,
        ),
        get_paper=openreview.get_paper,
        priority=68,
        domains=["computer science"],
        query_style=QUERY_RAW,
    ))


_register_defaults()
