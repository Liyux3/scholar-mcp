"""Crossref metadata and deposited references, respecting the provider's request limits."""

import re
import threading
import time
from html import unescape
from urllib.parse import quote

import httpx

from .. import access, config
from ..cache import cached
from . import pacing

BASE_URL = "https://api.crossref.org/works"

# Crossref serves up to 1000 rows per request. Fetch a margin above the caller's
# limit because format_paper drops entries with no title (datasets, errata).
CROSSREF_MAX_ROWS = 1000
OVERFETCH_FACTOR = 2
_gate = threading.Semaphore(3)
_public_gate = threading.Semaphore(1)
_retry_at = 0.0


def _headers() -> dict:
    contact = f"; mailto:{config.OPENALEX_EMAIL}" if config.OPENALEX_EMAIL else ""
    return {"User-Agent": f"scholar-mcp (https://github.com/Liyux3/scholar-mcp{contact})"}


def doi_id(value: str) -> str:
    value = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:|cr_)", "", str(value).strip(), flags=re.I)
    return value if re.fullmatch(r"10\.\d{4,9}/\S+", value, flags=re.I) else ""


@cached(ttl=3600)
def _get_work(doi: str) -> dict:
    return _get_json(f"{BASE_URL}/{quote(doi, safe='/')}").get("message") or {}


def _get_json(url: str, params: dict | None = None) -> dict:
    global _retry_at
    gate = _gate if config.OPENALEX_EMAIL else _public_gate
    with gate:
        if time.monotonic() < _retry_at:
            raise RuntimeError("Crossref is temporarily unavailable")
        for attempt in range(2):
            # A query or filter is a list request, limited harder than one record.
            pacing.wait("crossref" if params else "crossref-record", key=config.OPENALEX_EMAIL or None)
            try:
                response = access.get(url, params=params, headers=_headers(), timeout=pacing.remaining(25))
            except httpx.TransportError:
                _retry_at = time.monotonic() + 30
                raise
            if response.status_code == 404:
                return {}
            if response.status_code in {429, 502, 503, 504} and attempt == 0:
                try:
                    pause = float(response.headers.get("retry-after", "2"))
                except ValueError:
                    pause = 2
                if 0 <= pause <= 5:
                    time.sleep(pause)
                    continue
            if response.status_code in {429, 502, 503, 504}:
                _retry_at = time.monotonic() + 30
            response.raise_for_status()
            return response.json()
    return {}


def get_paper(paper_id: str) -> dict | None:
    doi = doi_id(paper_id)
    return format_paper(_get_work(doi)) if doi else None


def get_references(paper_id: str, limit: int = 20, **kwargs) -> list[dict]:
    doi = doi_id(paper_id)
    if not doi or limit <= 0:
        return []
    return [reference_paper(ref) for ref in _get_work(doi).get("reference", [])[:limit]]


def reference_paper(ref: dict) -> dict:
    doi = doi_id(ref.get("DOI", ""))
    text = ref.get("unstructured") or ""
    arxiv = re.search(r"arxiv(?:\.org/abs/|\s*:\s*)(\d{4}\.\d{4,5}(?:v\d+)?)", text, re.I)
    ids = {"DOI": doi} if doi else {}
    if arxiv:
        ids["ArXiv"] = arxiv[1]
    year = str(ref.get("year") or "")
    return {"paper_id": doi, "title": ref.get("article-title") or ref.get("series-title") or "",
            "year": int(year) if year.isdigit() else None,
            "authors": [ref["author"]] if ref.get("author") else [],
            "external_ids": ids, "source": "crossref", "_needs_metadata": True,
            "_reference": ref, }


@cached(ttl=3600)
def reference_candidates(text: str) -> list[dict]:
    if not text:
        return []
    return _get_json(BASE_URL, {"query.bibliographic": text[:1000], "rows": 3}).get("message", {}).get("items", [])


@cached(ttl=3600)
def match_reference(text: str, title: str = "", author: str = "", year: str = "", page: str = "") -> dict | None:
    """Resolve an exact title or a consistent author/year/first-page citation."""
    from ..records import normalize_title as _normalize_title
    if not text:
        return None
    candidates = reference_candidates(text)
    matches = []
    for candidate in candidates:
        paper = format_paper(candidate)
        if not paper:
            continue
        exact_title = (bool(title) and _normalize_title(title) == _normalize_title(paper["title"])
                       and (not year or str(paper.get("year")) == str(year)))
        surname = author.split()[-1].casefold().strip(",.") if author else ""
        coordinates = (surname and year and page and str(paper.get("year")) == str(year)
                       and str(candidate.get("page", "")).split("-")[0] == str(page)
                       and any(surname == str(a.get("family", "")).casefold() for a in candidate.get("author", [])))
        if exact_title or coordinates:
            matches.append(paper)
    return matches[0] if len(matches) == 1 else None


def format_paper(item: dict) -> dict | None:
    """Convert Crossref work to unified format."""
    title_list = item.get("title") or []
    if not title_list:
        return None
    title = unescape(title_list[0])

    authors = []
    for a in item.get("author") or []:
        given = a.get("given", "")
        family = a.get("family", "")
        name = f"{given} {family}".strip()
        if name:
            authors.append(name)

    year = None
    date_roles = ('published-print', 'published-online', 'published', 'posted', 'issued')
    # Deposits can contain [[None]], including a print placeholder before a
    # valid online date. Keep the work and prefer the first usable year.
    date_parts = next((parts for role in date_roles
                       if (parts := (item.get(role) or {}).get('date-parts'))
                       and parts[0] and isinstance(parts[0][0], int)), None)
    if date_parts and date_parts[0]:
        year = date_parts[0][0]

    doi = item.get("DOI") or ""
    abstract = item.get("abstract") or ""
    if abstract.startswith("<jats:"):
        import re
        abstract = re.sub(r"<[^>]+>", "", abstract).strip()

    venue = ""
    container = item.get("container-title")
    if container:
        venue = container[0] if isinstance(container, list) else container

    url = item.get("URL") or ""
    if doi and not url:
        url = f"https://doi.org/{doi}"

    pub_date = None
    if date_parts and date_parts[0]:
        parts = date_parts[0]
        if len(parts) >= 3 and all(isinstance(part, int) for part in parts[:3]):
            pub_date = f"{parts[0]:04d}-{parts[1]:02d}-{parts[2]:02d}"
        elif len(parts) < 3:
            pub_date = f"{parts[0]:04d}-01-01"

    paper = {
        "paper_id": f"cr_{doi}" if doi else f"cr_{title[:30]}",
        "title": title,
        "authors": authors,
        "abstract": abstract,
        "year": year,
        "venue": venue,
        "citation_count": item.get("is-referenced-by-count") or 0,
        "_citation_count_known": item.get("is-referenced-by-count") is not None,
        "is_open_access": False,
        "publication_types": [item["type"]] if item.get("type") else [],
        "publication_date": pub_date,
        "external_ids": {"DOI": doi} if doi else {},
        "url": url,
        "source": "crossref",
    }
    # Keep source-declared relationships and date roles; do not turn a chapter,
    # review or later online publication date into a different work's identity.
    paper["publication_dates"] = [
        {"type": kind, "date_parts": item[kind]["date-parts"]}
        for kind in date_roles
        if (item.get(kind) or {}).get("date-parts")
    ]
    paper["relations"] = [
        {"type": kind, "id": rel["id"], "id_type": rel.get("id-type"), "source": "crossref"}
        for kind, entries in (item.get("relation") or {}).items()
        for rel in entries if isinstance(rel, dict) and rel.get("id")
    ]
    from ..records import normalize_version, publication_version
    role = publication_version(paper)
    paper['full_text_locations'] = [
        {'url': link['URL'], 'version': 'preprint' if role == 'preprint' else normalize_version(link.get('content-version')),
         'source': 'crossref'}
        for link in item.get('link') or [] if link.get('URL') and link.get('content-type') in
        (None, '', 'unspecified', 'application/pdf', 'application/octet-stream')
    ]
    updates = item.get("update-to") or []
    if updates:
        paper["updates"] = [
            {
                key: update[key]
                for key in ("DOI", "type", "label", "source", "updated")
                if update.get(key) not in (None, "")
            }
            for update in updates
        ]
    return paper


def search_papers(query: str, limit: int = 10, **kwargs) -> list[dict]:
    """Search Crossref works."""
    params = {
        "query": query,
        "rows": min(limit * OVERFETCH_FACTOR, CROSSREF_MAX_ROWS),
        "sort": "relevance",
        "order": "desc",
    }
    data = _get_json(BASE_URL, params)

    results = []
    for item in (data.get("message") or {}).get("items") or []:
        paper = format_paper(item)
        if paper:
            results.append(paper)

    return results[:limit]


