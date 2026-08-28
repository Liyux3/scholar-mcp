"""Semantic Scholar API client using direct httpx calls."""

import re
import threading
import time
import httpx
from . import config
from .cache import cached

BASE_URL = "https://api.semanticscholar.org/graph/v1"
REC_URL = "https://api.semanticscholar.org/recommendations/v1"

SEARCH_FIELDS = ",".join([
    "paperId", "corpusId", "title", "abstract", "year", "venue",
    "citationCount", "influentialCitationCount", "isOpenAccess",
    "openAccessPdf", "authors", "externalIds", "fieldsOfStudy",
    "publicationDate", "tldr",
])

DETAIL_FIELDS = SEARCH_FIELDS + "," + ",".join([
    "referenceCount", "publicationVenue", "publicationTypes",
    "journal", "citationStyles",
])

CITATION_FIELDS = ",".join([
    "paperId", "title", "year", "venue", "citationCount",
    "authors", "externalIds", "isOpenAccess", "openAccessPdf",
])

AUTHOR_FIELDS = ",".join([
    "authorId", "name", "affiliations", "paperCount",
    "citationCount", "hIndex",
])


def _headers() -> dict:
    h = {}
    key = config.get_s2_api_key()
    if key:
        h["x-api-key"] = key
    return h


# S2 allows about one request per second per key. The pipeline fans out from
# several threads at once, so without a shared gate they queue on the server
# and each gets a 429, then each backs off independently and retries into the
# same contention. Serialising at the client is both faster and politer.
_s2_gate = threading.Lock()
_s2_last_request = 0.0
S2_MIN_INTERVAL = 1.05

# How long a caller will wait for its turn before giving up. Expansion runs
# several S2 calls concurrently and none of them is worth stalling the whole
# search for.
S2_GATE_TIMEOUT = 6.0

# A confirmed provider overload should not make every concurrent graph hop
# wait for the same failure. Retry once with a bounded pause, then use the
# provider's Retry-After or a short default before one core recovery probe.
# Metadata enrichment never becomes that probe.
S2_DEFAULT_COOLDOWN_SECONDS = 5.0
S2_MAX_COOLDOWN_SECONDS = 30.0
S2_MAX_RETRY_WAIT_SECONDS = 2.0
S2_METADATA_GATE_TIMEOUT = 0.05
_s2_health_lock = threading.Lock()
_s2_cooldown_until = 0.0
_s2_probe_in_flight = False


class S2CooldownError(RuntimeError):
    """Semantic Scholar is temporarily bypassed after a confirmed failure."""


def _begin_request(*, allow_probe: bool = True) -> bool:
    """Reserve one request, returning whether it is the recovery probe."""
    global _s2_probe_in_flight
    now = time.monotonic()
    with _s2_health_lock:
        if _s2_cooldown_until == 0:
            return False
        if now < _s2_cooldown_until:
            raise S2CooldownError("S2 is in a short overload cooldown")
        if not allow_probe or _s2_probe_in_flight:
            raise S2CooldownError("S2 is waiting for one recovery probe")
        _s2_probe_in_flight = True
        return True


def _retry_after_seconds(response: httpx.Response | None) -> float | None:
    """Read a bounded numeric Retry-After value when S2 provides one."""
    if response is None:
        return None
    try:
        value = float(response.headers.get("retry-after", ""))
    except (TypeError, ValueError):
        return None
    return min(max(value, 0.0), S2_MAX_COOLDOWN_SECONDS)


def _finish_request(
    *,
    probe: bool,
    healthy: bool,
    overloaded: bool = False,
    cooldown_seconds: float | None = None,
) -> None:
    """Update shared S2 health after one request finishes."""
    global _s2_cooldown_until, _s2_probe_in_flight
    with _s2_health_lock:
        if healthy:
            _s2_cooldown_until = 0.0
        elif overloaded:
            pause = (
                S2_DEFAULT_COOLDOWN_SECONDS
                if cooldown_seconds is None
                else cooldown_seconds
            )
            _s2_cooldown_until = time.monotonic() + pause
        if probe:
            _s2_probe_in_flight = False


def is_healthy() -> bool:
    """Whether optional traffic may use S2 without becoming a probe."""
    with _s2_health_lock:
        return _s2_cooldown_until == 0.0


def _wait_for_turn(timeout: float = S2_GATE_TIMEOUT) -> bool:
    """Space out requests across threads. False if the wait would be too long."""
    global _s2_last_request
    deadline = time.monotonic() + timeout
    if not _s2_gate.acquire(timeout=timeout):
        return False
    try:
        delay = S2_MIN_INTERVAL - (time.monotonic() - _s2_last_request)
        if delay > 0:
            if time.monotonic() + delay > deadline:
                return False
            time.sleep(delay)
        _s2_last_request = time.monotonic()
        return True
    finally:
        _s2_gate.release()


def _request_json(
    method: str,
    url: str,
    *,
    params: dict | None = None,
    json_data: dict | None = None,
    retries: int = 2,
    gate_timeout: float = S2_GATE_TIMEOUT,
    allow_probe: bool = True,
) -> dict | list:
    """Run one S2 request through the shared gate and health circuit."""
    probe = _begin_request(allow_probe=allow_probe)
    response = None
    try:
        for attempt in range(max(retries, 1)):
            response = None
            if not _wait_for_turn(timeout=gate_timeout):
                raise httpx.HTTPStatusError(
                    "S2 rate-limit gate timed out", request=None, response=None
                )
            if not probe and not is_healthy():
                raise S2CooldownError("S2 entered cooldown while this request waited")
            try:
                request = httpx.get if method == "GET" else httpx.post
                kwargs = {
                    "params": params,
                    "headers": _headers(),
                    "timeout": config.S2_TIMEOUT,
                }
                if method != "GET":
                    kwargs["json"] = json_data
                response = request(url, **kwargs)
            except (httpx.TimeoutException, httpx.TransportError):
                if attempt < retries - 1:
                    continue
                _finish_request(probe=probe, healthy=False, overloaded=True)
                raise
            if response.status_code == 429 and attempt < retries - 1:
                retry_after = _retry_after_seconds(response)
                time.sleep(min(
                    S2_MAX_RETRY_WAIT_SECONDS,
                    S2_MAX_RETRY_WAIT_SECONDS if retry_after is None else retry_after,
                ))
                continue
            if response.status_code == 429 or response.status_code in {502, 503, 504}:
                _finish_request(
                    probe=probe,
                    healthy=False,
                    overloaded=True,
                    cooldown_seconds=_retry_after_seconds(response),
                )
            else:
                # A non-overload 4xx still proves that the provider is alive.
                _finish_request(probe=probe, healthy=True)
            response.raise_for_status()
            return response.json()
    except Exception:
        # Gate contention and invalid requests should not open a provider-wide
        # cooldown, but a failed recovery probe must be released.
        if response is None:
            _finish_request(probe=probe, healthy=False)
        raise
    return {}


def _get(
    url: str,
    params: dict = None,
    retries: int = 2,
    *,
    gate_timeout: float = S2_GATE_TIMEOUT,
    allow_probe: bool = True,
) -> dict:
    return _request_json(
        "GET",
        url,
        params=params,
        retries=retries,
        gate_timeout=gate_timeout,
        allow_probe=allow_probe,
    )


def _normalize_s2_id(paper_id: str) -> str:
    """Ensure paper_id has the correct prefix for S2 API.
    S2 requires DOI:xxx, ArXiv:xxx, CorpusID:xxx, or a 40-char SHA hash.
    """
    pid = paper_id.strip()
    if pid.lower().startswith("corpusid:"):
        return f"CorpusId:{pid.split(':', 1)[1]}"
    if pid.startswith(("DOI:", "ArXiv:", "CorpusID:", "PMID:", "MAG:", "ACL:")):
        return pid
    if len(pid) == 40 and all(c in "0123456789abcdef" for c in pid):
        return pid
    if pid.startswith("10."):
        return f"DOI:{pid}"
    if re.match(r"^\d{4}\.\d{4,5}(v\d+)?$", pid):
        return f"ArXiv:{pid}"
    return pid


def _post(
    url: str,
    json_data: dict = None,
    params: dict = None,
    retries: int = 2,
    *,
    gate_timeout: float = S2_GATE_TIMEOUT,
    allow_probe: bool = True,
) -> dict | list:
    return _request_json(
        "POST",
        url,
        params=params,
        json_data=json_data,
        retries=retries,
        gate_timeout=gate_timeout,
        allow_probe=allow_probe,
    )


def format_paper(data: dict) -> dict:
    """Convert raw S2 API paper dict into our clean format."""
    oa_pdf = data.get("openAccessPdf") or {}
    oa_url = oa_pdf.get("url") if oa_pdf else None
    if oa_url == "":
        oa_url = None

    tldr_obj = data.get("tldr") or {}
    tldr_text = tldr_obj.get("text") if isinstance(tldr_obj, dict) else None

    authors_raw = data.get("authors") or []
    authors = [a.get("name", "") for a in authors_raw if isinstance(a, dict)]

    return {
        "paper_id": data.get("paperId", ""),
        "title": data.get("title", ""),
        "authors": authors,
        "abstract": data.get("abstract") or "",
        "year": data.get("year"),
        "venue": data.get("venue") or "",
        "citation_count": data.get("citationCount") or 0,
        "_citation_count_known": data.get("citationCount") is not None,
        "influential_citations": data.get("influentialCitationCount") or 0,
        "is_open_access": data.get("isOpenAccess") or False,
        "open_access_url": oa_url,
        "fields_of_study": data.get("fieldsOfStudy") or [],
        "publication_date": data.get("publicationDate"),
        "tldr": tldr_text,
        "external_ids": data.get("externalIds") or {},
        "url": f"https://www.semanticscholar.org/paper/{data.get('paperId', '')}",
        "source": "semantic_scholar",
    }


def get_papers_batch(paper_ids: list[str]) -> list[dict | None]:
    """Resolve paper metadata in one rate-limited S2 request."""
    if not paper_ids:
        return []
    ids = [_normalize_s2_id(paper_id) for paper_id in paper_ids[:500]]
    data = _post(
        f"{BASE_URL}/paper/batch",
        json_data={"ids": ids},
        params={"fields": SEARCH_FIELDS},
        retries=1,
        gate_timeout=S2_METADATA_GATE_TIMEOUT,
        allow_probe=False,
    )
    return data if isinstance(data, list) else []


def format_paper_detail(data: dict) -> dict:
    """Extended format with venue details and citation styles."""
    result = format_paper(data)

    pub_venue = data.get("publicationVenue") or {}
    if isinstance(pub_venue, dict):
        result["venue_type"] = pub_venue.get("type", "")
        result["venue_url"] = pub_venue.get("url", "")

    result["publication_types"] = data.get("publicationTypes") or []
    result["reference_count"] = data.get("referenceCount") or 0

    styles = data.get("citationStyles") or {}
    if isinstance(styles, dict):
        result["bibtex"] = styles.get("bibtex", "")

    return result


@cached(ttl=300)
def search_papers(query, limit=10, year=None, venue=None,
                  fields_of_study=None, publication_types=None,
                  min_citations=0, open_access_only=False, sort="", **kwargs):
    params = {
        "query": query,
        "limit": min(limit, 100),
        "fields": SEARCH_FIELDS,
    }
    if year:
        params["year"] = year
    if venue:
        params["venue"] = ",".join(venue) if isinstance(venue, list) else venue
    if fields_of_study:
        fos = fields_of_study if isinstance(fields_of_study, list) else [fields_of_study]
        params["fieldsOfStudy"] = ",".join(fos)
    if min_citations > 0:
        params["minCitationCount"] = min_citations
    if publication_types:
        pts = publication_types if isinstance(publication_types, list) else [publication_types]
        params["publicationTypes"] = ",".join(pts)
    if open_access_only:
        params["openAccessPdf"] = ""

    if sort:
        if sort == "citations":
            params["sort"] = "citationCount:desc"
        elif sort == "date":
            params["sort"] = "publicationDate:desc"
        params.pop("limit", None)
        data = _get(f"{BASE_URL}/paper/search/bulk", params=params)
    else:
        data = _get(f"{BASE_URL}/paper/search", params=params)
    return [format_paper(p) for p in data.get("data", [])][:limit]


@cached(ttl=300)
def search_match(query: str) -> dict | None:
    """Find the single best title match for a query."""
    try:
        data = _get(f"{BASE_URL}/paper/search/match",
                    params={"query": query, "fields": SEARCH_FIELDS})
        papers = data.get("data", [])
        return format_paper(papers[0]) if papers else None
    except Exception:
        return None


@cached(ttl=300)
def get_paper(paper_id: str) -> dict:
    paper_id = _normalize_s2_id(paper_id)
    data = _get(f"{BASE_URL}/paper/{paper_id}", params={"fields": DETAIL_FIELDS})
    return format_paper_detail(data)


@cached(ttl=300)
def get_citations(paper_id: str, limit: int = 20, **kwargs):
    paper_id = _normalize_s2_id(paper_id)
    fetch_limit = min(max(limit * 3, 100), 1000)
    params = {"fields": CITATION_FIELDS + ",isInfluential", "limit": fetch_limit}
    data = _get(f"{BASE_URL}/paper/{paper_id}/citations", params=params)
    influential = []
    others = []
    for item in data.get("data", []):
        citing = item.get("citingPaper", {})
        if citing and citing.get("paperId"):
            paper = format_paper(citing)
            if item.get("isInfluential"):
                influential.append(paper)
            else:
                others.append(paper)
    influential.sort(key=lambda p: p.get("citation_count", 0), reverse=True)
    others.sort(key=lambda p: p.get("citation_count", 0), reverse=True)
    return (influential + others)[:limit]


@cached(ttl=300)
def get_references(paper_id: str, limit: int = 20, **kwargs):
    paper_id = _normalize_s2_id(paper_id)
    params = {"fields": CITATION_FIELDS, "limit": min(limit, 1000)}
    data = _get(f"{BASE_URL}/paper/{paper_id}/references", params=params)
    results = []
    for item in data.get("data", []):
        cited = item.get("citedPaper", {})
        if cited and cited.get("paperId"):
            results.append(format_paper(cited))
    return results


REC_FIELDS = ",".join([
    "paperId", "title", "abstract", "year", "venue",
    "citationCount", "authors", "externalIds", "openAccessPdf",
])


def get_recommendations(paper_id: str, limit: int = 10, pool_from: str = ""):
    """Papers similar to the seed, via SPECTER2 embeddings.

    `from` selects the candidate pool: "all-cs" spans computer science across
    all time, "recent" only the last 60 days. Those are the only two values
    the API accepts, and "recent" returns nothing for any older seed.
    """
    pool = pool_from or config.S2_RECOMMEND_POOL
    data = _get(
        f"{REC_URL}/papers/forpaper/{_normalize_s2_id(paper_id)}",
        params={"fields": REC_FIELDS, "limit": min(limit, 500), "from": pool},
    )
    return [format_paper(p) for p in data.get("recommendedPapers", [])]


def search_authors(query: str, limit: int = 5):
    params = {"query": query, "limit": min(limit, 100), "fields": AUTHOR_FIELDS}
    data = _get(f"{BASE_URL}/author/search", params=params)
    return [
        {
            "author_id": a.get("authorId", ""),
            "name": a.get("name", ""),
            "affiliations": a.get("affiliations") or [],
            "paper_count": a.get("paperCount") or 0,
            "citation_count": a.get("citationCount") or 0,
            "_citation_count_known": a.get("citationCount") is not None,
            "h_index": a.get("hIndex") or 0,
            "url": f"https://www.semanticscholar.org/author/{a.get('authorId', '')}",
        }
        for a in data.get("data", [])
    ]
