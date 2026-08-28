"""Crossref API client. 150M+ works, no API key, no rate limit with polite pool."""

import httpx

from . import config

BASE_URL = "https://api.crossref.org/works"

# Crossref serves up to 1000 rows per request. Fetch a margin above the caller's
# limit because format_paper drops entries with no title (datasets, errata).
CROSSREF_MAX_ROWS = 1000
OVERFETCH_FACTOR = 2


def _headers() -> dict:
    email = config.OPENALEX_EMAIL or "scholar-mcp@example.com"
    return {"User-Agent": f"scholar-mcp/0.3 (mailto:{email})"}


def format_paper(item: dict) -> dict | None:
    """Convert Crossref work to unified format."""
    title_list = item.get("title") or []
    if not title_list:
        return None
    title = title_list[0]

    authors = []
    for a in item.get("author") or []:
        given = a.get("given", "")
        family = a.get("family", "")
        name = f"{given} {family}".strip()
        if name:
            authors.append(name)

    year = None
    date_parts = (item.get("published-print") or item.get("published-online") or {}).get("date-parts")
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
        if len(parts) >= 3:
            pub_date = f"{parts[0]:04d}-{parts[1]:02d}-{parts[2]:02d}"
        elif len(parts) >= 1:
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
        "influential_citations": 0,
        "is_open_access": False,
        "open_access_url": None,
        "fields_of_study": [],
        "publication_date": pub_date,
        "tldr": None,
        "external_ids": {"DOI": doi} if doi else {},
        "url": url,
        "source": "crossref",
    }
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
    r = httpx.get(BASE_URL, params=params, headers=_headers(), timeout=30)
    r.raise_for_status()
    data = r.json()

    results = []
    for item in (data.get("message") or {}).get("items") or []:
        paper = format_paper(item)
        if paper:
            results.append(paper)

    return results[:limit]
