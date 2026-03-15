"""CORE API v3 client for open access paper search and PDF discovery."""

import time
import httpx
from . import config

BASE_URL = "https://api.core.ac.uk/v3"


def _headers() -> dict:
    h = {}
    if config.CORE_API_KEY:
        h["Authorization"] = f"Bearer {config.CORE_API_KEY}"
    return h


def _get(url: str, params: dict = None, retries: int = 4) -> dict:
    for attempt in range(retries):
        r = httpx.get(url, params=params, headers=_headers(), timeout=30)
        if r.status_code == 429 and attempt < retries - 1:
            wait = min(2 ** (attempt + 1), 30)
            time.sleep(wait)
            continue
        r.raise_for_status()
        return r.json()
    r.raise_for_status()
    return {}


def format_paper(data: dict) -> dict:
    """Convert CORE API work record to our unified format."""
    authors_raw = data.get("authors") or []
    authors = [a.get("name", "") for a in authors_raw if isinstance(a, dict)]
    if not authors:
        authors = [str(a) for a in authors_raw if a]

    doi = data.get("doi") or ""
    year = None
    pub_date = data.get("publishedDate") or ""
    if pub_date and len(pub_date) >= 4:
        try:
            year = int(pub_date[:4])
        except (ValueError, TypeError):
            pass

    download_url = data.get("downloadUrl") or None
    if not download_url:
        source_urls = data.get("sourceFulltextUrls") or []
        download_url = source_urls[0] if source_urls else None

    return {
        "paper_id": f"core_{data.get('id', '')}",
        "title": data.get("title") or "",
        "authors": authors,
        "abstract": data.get("abstract") or "",
        "year": year,
        "venue": (data.get("journals") or [{}])[0].get("title", "") if data.get("journals") else "",
        "citation_count": data.get("citationCount") or 0,
        "influential_citations": 0,
        "is_open_access": download_url is not None,
        "open_access_url": download_url,
        "fields_of_study": data.get("fieldOfStudy") or [],
        "publication_date": pub_date[:10] if len(pub_date) >= 10 else None,
        "tldr": None,
        "external_ids": {"DOI": doi} if doi else {},
        "url": f"https://core.ac.uk/works/{data.get('id', '')}",
        "source": "core",
    }


def search_papers(query: str, limit: int = 10) -> list[dict]:
    """Search CORE works. Returns unified format, quality-sorted."""
    params = {"q": query, "limit": min(limit * 2, 100), "sort": "relevance"}
    data = _get(f"{BASE_URL}/search/works/", params=params)

    results = []
    for item in data.get("results") or []:
        paper = format_paper(item)
        results.append(paper)

    # Quality sort: research with PDFs and citations first
    def _quality(p):
        has_pdf = 1 if p["open_access_url"] else 0
        cites = p["citation_count"] or 0
        return (has_pdf, cites)

    results.sort(key=_quality, reverse=True)
    return results[:limit]


def get_pdf_url(doi: str = None, title: str = None) -> str | None:
    """Find a PDF URL via CORE. Tries DOI (exact) first, then title (fuzzy)."""
    queries = []
    if doi:
        queries.append(f'doi:"{doi}"')
    if title:
        clean = title.replace('"', '\\"')
        queries.append(f'title:("{clean}")')

    for q in queries:
        try:
            params = {"q": q, "limit": 5}
            data = _get(f"{BASE_URL}/search/works/", params=params)
            for item in data.get("results") or []:
                url = item.get("downloadUrl")
                if url:
                    return url
                source_urls = item.get("sourceFulltextUrls") or []
                if source_urls:
                    return source_urls[0]
        except (httpx.HTTPError, KeyError):
            continue

    return None
