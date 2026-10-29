"""Resolve sparse relation records before ranking, without depending on S2 quota."""

import re
from concurrent.futures import Future, ThreadPoolExecutor, wait
from contextvars import copy_context
from copy import deepcopy

from . import records as record_ops
from .cache import PartialRecords, cached
from .sources import crossref, openaire, pacing
from .sources import openalex as oa

# Shared leaf-I/O workers. Orchestration never runs in this pool, avoiding
# recursive fan-out deadlocks and one new executor per graph neighborhood.
_lookup_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="scholar-metadata")
# Raw citation matching can wait on a slow catalog or verification gate.
# It keeps its existing capacity without blocking exact ids or the shortlist.
_reference_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix='scholar-reference')


def _reference_args(paper):
    ref = paper.get('_reference') or {}
    ref = ref if isinstance(ref, dict) else {}
    text = ref.get('unstructured') or ' '.join(str(ref.get(k) or '') for k in (
        'article-title', 'author', 'year', 'journal-title', 'first-page'))
    return text, ref.get('article-title', ''), ref.get('author', ''), str(ref.get('year', '')), str(ref.get('first-page', ''))


@cached(ttl=3600, miss_ttl=30)
def _bibliographic(text: str, title: str, author: str, year: str, page: str) -> dict | None:
    if not text.strip():
        return None
    try:
        found = crossref.match_reference(text, title, author, year, page)
        if found:
            return found
    except Exception:
        pass
    # Titles supplied by another index are hints only. Require literal title
    # evidence in the original citation and validate the final record's year.
    normalized = record_ops.normalize_title(text)
    titles = [title] if title else []
    if not titles:
        try:
            for record in crossref.reference_candidates(text):
                candidate = (record.get("title") or [""])[0]
                nt = record_ops.normalize_title(candidate)
                if len(nt) >= 20 and nt in normalized:
                    titles.append(candidate)
        except Exception:
            pass
        # Citation prose can provide candidate title sentences even when the
        # catalog has no DOI deposit. These are lookup hints, never accepted
        # metadata until an external record matches the title and year.
        for sentence in re.split(r"(?<=[.!?])\s+(?=[A-Z])", text):
            sentence = sentence.strip().rstrip(".")
            words = re.findall(r"[A-Za-z]{2,}", sentence)
            if (5 <= len(words) and len(sentence) <= 240
                    and not sentence.startswith(("In ", "This ", "See "))):
                titles.append(sentence)
            if len(titles) >= 3:
                break
    words = [w for w in re.findall(r"[^\W\d_]+", author.split(",")[0]) if len(w) > 1]
    surname = words[-1].casefold() if words else ""
    from .sources import dblp, s2
    query = title or (f"{surname} {year}" if surname and year else "")
    if query:
        try:
            matches = [p for p in dblp.search_papers(query, limit=20)
                       if len(record_ops.normalize_title(p.get("title", ""))) >= 20
                       and record_ops.normalize_title(p["title"]) in normalized
                       and (not year or str(p.get("year")) == year)]
            unique = record_ops.deduplicate(matches)
            if len(unique) == 1:
                return unique[0]
        except Exception:
            pass
    if s2.is_healthy():
        for candidate in dict.fromkeys(titles):
            try:
                paper = s2.search_match(candidate)
                if (paper and record_ops.normalize_title(paper.get("title", "")) == record_ops.normalize_title(candidate)
                        and (not year or str(paper.get("year")) == year)):
                    return paper
            except Exception:
                break
    return None


@cached(ttl=3600, miss_ttl=30)
def _doi(doi: str) -> dict | None:
    try:
        return crossref.get_paper(doi)
    except Exception:
        return None


@cached(ttl=3600, miss_ttl=30)
def _arxiv_page(identity: str) -> dict | None:
    from .sources import arxiv
    try:
        return arxiv.get_paper(identity)
    except Exception:
        return None


def published_record(paper: dict) -> dict:
    """Follow source-declared publication links, retaining each manuscript identity."""
    if paper.get('_requested_arxiv'):
        return paper
    ids = record_ops.external_ids(paper)
    if ids.get('ArXiv') and record_ops.publication_version(paper) != 'published':
        if native := _arxiv_page(ids['ArXiv']):
            paper = record_ops.merge_records(paper, native)
    targets = [r['id'] for r in paper.get('relations') or []
               if re.sub(r'[^a-z]', '', str(r.get('type', '')).casefold()) in {'ispreprintof', 'ismanuscriptof'}
               and str(r.get('id_type', '')).casefold() == 'doi']
    if record_ops.publication_version(paper) != 'published':
        for doi in dict.fromkeys(targets):
            if published := _doi(doi):
                paper = record_ops.merge_records(paper, published)
                break
    return paper


EXPLICIT_ARXIV_VERSION = re.compile(r'(?:arxiv:|10\.48550/arxiv\.)?(?:\d{4}\.\d{4,5}|[a-z-]+/\d{7})v\d+', re.I)


def lookup(paper_id: str) -> dict | None:
    """Resolve one native identifier through shared sources, without MCP state."""
    from . import sources
    if EXPLICIT_ARXIV_VERSION.fullmatch(paper_id):
        from .sources import arxiv
        try:
            paper = arxiv.get_paper(paper_id)
        except Exception as error:
            raise sources.SourceLookupError('arxiv', error) from error
        return {**paper, '_requested_arxiv': paper['external_ids']['ArXiv']} if paper else None
    if source := sources.native_source(paper_id):
        try:
            return source.get_paper(paper_id)
        except Exception as error:
            raise sources.SourceLookupError(source.name, error) from error
    for source in sources.all_sources():
        if source.get_paper is None or not source.available():
            continue
        try:
            paper = source.get_paper(paper_id)
        except Exception:
            continue
        if paper:
            from .sources.crossref import doi_id
            requested, returned = doi_id(paper_id), doi_id((paper.get('external_ids') or {}).get('DOI', ''))
            if requested and returned and requested.casefold() != returned.casefold():
                # Some indexes resolve a preprint DOI straight to the formal
                # work. Retain its deposited version link instead of losing
                # the requested manifestation at this resolution boundary.
                if exact := _doi(requested):
                    exact = published_record(exact)
                    paper = record_ops.merge_records(exact, paper) if record_ops.matches_paper(exact, paper) else exact
            return published_record(paper)
    return None


@cached(ttl=3600, miss_ttl=30)
def _batch_works(identifiers: tuple[str, ...], kind: str) -> dict[str, dict]:
    result = {}
    pending = []
    for identifier in identifiers:
        hit, records = _batch_works.peek((identifier,), kind)
        if hit:
            result.update(records)
        else:
            pending.append(identifier)
    for offset in range(0, len(pending), 40):
        batch = pending[offset:offset + 40]
        params = oa._params_base()
        params.update(filter=kind + ":" + "|".join(batch), per_page=len(batch), select=oa.OA_SELECT_FIELDS)
        try:
            response = oa._request("https://api.openalex.org/works", params, timeout=20)
            response.raise_for_status()
            raw = response.json()
            entries = raw.get('results')
            if not isinstance(entries, list):
                raise ValueError('OpenAlex returned an invalid metadata batch')
            listed = set()
            for work in entries:
                key = (crossref.doi_id(work.get("doi") or "").casefold() if kind == 'doi' else
                       record_ops.normalize_id('OpenAlex', work.get('id', '')).upper())
                if key in batch:
                    listed.add(key)
                    result[key] = oa.format_paper(work)
            total = (raw.get('meta') or {}).get('count')
            complete = total <= len(entries) if isinstance(total, int) else len(entries) < len(batch)
            for identifier in batch:
                if result.get(identifier):
                    _batch_works.store({identifier: result[identifier]}, (identifier,), kind)
                elif complete and identifier not in listed:
                    _batch_works.store({}, (identifier,), kind)
            if not complete and any(identifier not in listed for identifier in batch):
                return PartialRecords(result)
        except Exception:
            # Stop batch traffic during overload; exact Crossref lookup remains available.
            return PartialRecords(result)
    return result


def _batch_dois(dois: list[str]) -> dict[str, dict]:
    return _batch_works(tuple(sorted(set(dois))), 'doi')


def _batch_openalex(ids: list[str]) -> dict[str, dict]:
    return _batch_works(tuple(sorted(set(ids))), 'openalex')


def _batch_pmids(pmids: list[str]) -> dict[str, dict]:
    return _pmid_records(tuple(sorted(set(pmids))))


@cached(ttl=3600, miss_ttl=30)
def _pmid_records(pmids: tuple[str, ...]) -> dict[str, dict]:
    from .sources import europepmc as epmc
    med = {}
    for offset in range(0, len(pmids), 40):
        query = "(" + " OR ".join(f"EXT_ID:{pid}" for pid in pmids[offset:offset + 40]) + ") AND SRC:MED"
        try:
            med.update({str(item.get("id")): epmc.format_paper(item) for item in epmc._request(query, 100)})
        except Exception:
            return PartialRecords(med)
    return med


def _batch_arxiv(ids: list[str]) -> dict[str, dict]:
    return _arxiv_records(tuple(sorted(set(ids))))


@cached(ttl=3600, miss_ttl=30)
def _arxiv_records(ids: tuple[str, ...]) -> dict[str, dict]:
    """arXiv records in batched API requests; an unreachable API leaves them for other resolvers."""
    from .sources import arxiv
    try:
        return arxiv.get_papers(list(ids)) if ids else {}
    except Exception:
        return {}


def filter_fields(filters: dict) -> set[str]:
    """Fields required to apply explicit constraints across every source."""
    return {field for option, field in (
        ("year", "year"), ("venue", "venue"), ("fields_of_study", "fields_of_study"),
        ("publication_types", "publication_types"), ("open_access_only", "is_open_access"),
        ("min_citations", "citation_count"),
    ) if filters.get(option)}


def needs_metadata(paper: dict, fields: set[str] | None = None) -> bool:
    if paper.get('record_type') not in (None, 'paper'):
        return False  # A trial registration or court opinion is not a sparse article.
    if paper.get("_needs_metadata") or not paper.get("title"):
        return True
    for field in set(fields or ()) - set(paper.get("_metadata_checked_fields") or ()):
        if field == "is_open_access":
            if not (paper.get("is_open_access") or paper.get("open_access_url")):
                return True
        elif field == "citation_count" and paper.get("_citation_count_known"):
            continue
        elif not paper.get(field):
            return True
    return False


def hydrate(papers: list[dict], fields: set[str] | None = None) -> list[dict]:
    if pacing.remaining(1) <= 0:
        return papers
    pending = [p for p in papers if needs_metadata(p, fields)]
    if not pending:
        return papers
    dois = list(dict.fromkeys(crossref.doi_id(record_ops.external_ids(p).get("DOI", "")).casefold()
                             for p in pending if not p.get("_title_conflict")))
    dois = [doi for doi in dois if not doi.startswith("10.48550/arxiv.")]
    pmids = list(dict.fromkeys(str((p.get("external_ids") or {}).get("PMID") or (p.get("external_ids") or {}).get("PubMed") or "") for p in pending))
    # Start independent native batches together. arXiv and bibliographic
    # lookups below can progress while either catalog is still responding.
    doi_future = _lookup_pool.submit(copy_context().run, _batch_dois, [doi for doi in dois if doi])
    pmid_future = _lookup_pool.submit(copy_context().run, _batch_pmids, [pid for pid in pmids if pid.isdigit()])
    arxiv_ids = [record_ops.normalize_id("ArXiv", record_ops.external_ids(p)["ArXiv"]) for p in pending
                 if record_ops.external_ids(p).get("ArXiv")]
    arxiv_future = _lookup_pool.submit(copy_context().run, _batch_arxiv, arxiv_ids)
    # A native OpenAlex id already establishes the lookup identity. Title or
    # bibliographic guessing would discard that identity and waste requests.
    oa_ids = [record_ops.normalize_id('OpenAlex', record_ops.external_ids(p)['OpenAlex']).upper()
              for p in pending if record_ops.external_ids(p).get('OpenAlex')
              and not any(record_ops.external_ids(p).get(k) for k in ('DOI', 'ArXiv', 'PubMed'))]
    oa_future = _lookup_pool.submit(copy_context().run, _batch_openalex, oa_ids)

    def arxiv_record(paper: dict) -> dict | None:
        ids = record_ops.external_ids(paper)
        identity = re.sub(r"v\d+$", "", record_ops.normalize_id("ArXiv", ids["ArXiv"]))
        record = arxiv_future.result().get(identity)
        native_fields = {'title', 'authors', 'abstract', 'year', 'venue', 'publication_date', 'is_open_access'}
        # Abstract pages can repair identity and summaries, but have no
        # citation counts or general-index taxonomy to fill a strict filter.
        if record or not (paper.get('_needs_metadata') or paper.get('_title_conflict') or
                          not paper.get('title') or set(fields or ()) & native_fields):
            return record
        return _arxiv_page(identity)

    def lookup(paper):
        if pacing.remaining(1) <= 0:
            return None
        ids = record_ops.external_ids(paper)
        doi = crossref.doi_id(ids.get("DOI", "")).casefold()
        try:
            if doi.startswith("10.48550/arxiv.") and ids.get("ArXiv"):
                return arxiv_record(paper)
            if paper.get("_title_conflict"):
                if ids.get("ArXiv"):
                        return arxiv_record(paper)
                if doi:
                    return _doi(doi)
            pmid = str(ids.get("PMID") or ids.get("PubMed") or "")
            if pmid and (found := pmid_future.result().get(pmid)):
                return found
            if doi:
                found = doi_future.result().get(doi)
                want_abstract = "abstract" in (fields or ())
                # A resolved identity does not mean the requested text exists.
                # Only the displayed shortlist requests abstracts; reuse exact
                # DOI lookups without spending S2 quota or downloading PDFs.
                for resolver in (_doi, openaire.get_paper):
                    if found and (not want_abstract or found.get("abstract")):
                        break
                    if resolver is openaire.get_paper and not want_abstract:
                        break
                    try:
                        extra = resolver(doi)
                    except Exception:
                        continue
                    if not found:
                        found = extra
                    elif extra and extra.get("abstract"):
                        found = {**found, "abstract": extra["abstract"],
                                 "_abstract_source": extra.get("source")}
                return found
            if ids.get("ArXiv"):
                # Native arXiv metadata resolves identifiers outside DOI catalogs.
                return arxiv_record(paper)
            if ids.get('OpenAlex'):
                return oa_future.result().get(record_ops.normalize_id('OpenAlex', ids['OpenAlex']).upper())
            return _bibliographic(*_reference_args(paper))
        except Exception:
            return None

    # Deduplicate work across relation providers before issuing exact lookups.
    unique = {}
    for paper in pending:
        ids = record_ops.external_ids(paper)
        key = next((f"{kind}:{record_ops.normalize_id(kind, ids[kind])}"
                    for kind in ("DOI", "ArXiv", "PubMed", "OpenAlex", "CorpusId") if ids.get(kind)),
                   str(paper.get("_reference") or paper.get("title")))
        unique.setdefault(key, []).append(paper)
    groups = list(unique.values())
    futures = []
    for group in groups:
        native = any(record_ops.external_ids(group[0]).get(kind) for kind in ('DOI', 'ArXiv', 'PubMed', 'OpenAlex'))
        pool = _lookup_pool if native else _reference_pool
        hit, value = False, None
        if not native:
            args = _reference_args(group[0])
            if not args[0]:
                hit = True
            elif peek := getattr(_bibliographic, 'peek', None):
                hit, value = peek(*args)
        if hit:
            # A completed match or explicit miss can finish before the slow
            # fallback queue, including after its process TTL expires.
            future = Future()
            future.set_result(value)
        else:
            future = pool.submit(copy_context().run, lookup, group[0])
        futures.append(future)
    wait(futures, timeout=pacing.remaining())
    for group, future in zip(groups, futures):
        if not future.done():
            future.cancel()
            continue
        if future.cancelled():
            continue
        found = future.result()
        if not found:
            continue
        for paper in group:
            # Metadata resolution does not create another independent search vote.
            for key in ("title", "authors", "abstract", "year", "venue", "publication_date",
                        "citation_count", "_citation_count_known", "open_access_url", "is_open_access", "url",
                        "publication_types", "fields_of_study", "_abstract_source", "relations", "publication_dates"):
                if key in {"citation_count", "_citation_count_known"}:
                    if not found.get("_citation_count_known"):
                        continue
                    if key == "citation_count" and (paper.get("citation_count") or 0) > (found.get(key) or 0):
                        continue
                if found.get(key) is not None and (not paper.get(key) or key in {"citation_count", "_citation_count_known"}
                                                   or key in {"title", "authors", "abstract", "year", "publication_date", "open_access_url", "url"}
                                                   and paper.get("_title_conflict") and found.get(key)):
                    paper[key] = deepcopy(found[key])
            paper["external_ids"] = {**found.get("external_ids", {}), **paper.get("external_ids", {})}
            if not paper.get("paper_id") and found.get("paper_id"):
                paper["paper_id"] = found["paper_id"]
            paper["paper_id"] = record_ops.best_paper_id(paper)
            paper["_metadata_source"] = found.get("source")
            if fields:
                paper["_metadata_checked_fields"] = set(paper.get("_metadata_checked_fields") or ()) | fields
            paper.pop("_needs_metadata", None)
            paper.pop("_title_conflict", None)
    return papers
