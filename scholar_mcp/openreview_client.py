"""OpenReview API v2 client for conference paper search (ICLR, NeurIPS, ICML, etc.).
Requires OPENREVIEW_USERNAME and OPENREVIEW_PASSWORD env vars for API access."""

import httpx
from . import config

BASE_URL = "https://api2.openreview.net"

_token_cache: dict = {"token": None}


def _login() -> str:
    if _token_cache["token"]:
        return _token_cache["token"]
    username = config.OPENREVIEW_USERNAME
    password = config.OPENREVIEW_PASSWORD
    if not username or not password:
        raise RuntimeError(
            "OpenReview credentials not configured. "
            "Set OPENREVIEW_USERNAME and OPENREVIEW_PASSWORD env vars."
        )
    r = httpx.post(f"{BASE_URL}/login", json={"id": username, "password": password}, timeout=15)
    r.raise_for_status()
    token = r.json().get("token", "")
    if not token:
        raise RuntimeError("OpenReview login returned no token.")
    _token_cache["token"] = token
    return token


def _headers() -> dict:
    token = _login()
    return {"Authorization": f"Bearer {token}"}


def _get(url: str, params: dict = None) -> dict:
    try:
        r = httpx.get(url, params=params, headers=_headers(), timeout=30)
    except RuntimeError:
        raise
    if r.status_code == 401:
        _token_cache["token"] = None
        r = httpx.get(url, params=params, headers=_headers(), timeout=30)
    r.raise_for_status()
    return r.json()


def _extract_field(content: dict, key: str, default=""):
    val = content.get(key, default)
    if isinstance(val, dict):
        return val.get("value", default)
    return val


def format_paper(note: dict) -> dict:
    content = note.get("content", {})

    title = _extract_field(content, "title", "")
    abstract = _extract_field(content, "abstract", "")
    venue = _extract_field(content, "venue", "")
    venueid = _extract_field(content, "venueid", "")

    authors_field = content.get("authors", {})
    if isinstance(authors_field, dict):
        authors = authors_field.get("value", [])
    elif isinstance(authors_field, list):
        authors = authors_field
    else:
        authors = []

    year = None
    if venue:
        for token in venue.split():
            if token.isdigit() and len(token) == 4:
                year = int(token)
                break
    if not year:
        cdate = note.get("cdate") or note.get("tcdate")
        if cdate:
            import datetime
            try:
                year = datetime.datetime.fromtimestamp(cdate / 1000).year
            except (ValueError, TypeError, OSError):
                pass

    note_id = note.get("id", "")
    forum = note.get("forum", note_id)
    pdf_url = f"https://openreview.net/pdf?id={forum}" if forum else None

    return {
        "paper_id": f"openreview_{note_id}",
        "title": title,
        "authors": authors if isinstance(authors, list) else [str(authors)],
        "abstract": abstract,
        "year": year,
        "venue": venue or venueid,
        "citation_count": 0,
        "influential_citations": 0,
        "is_open_access": True,
        "open_access_url": pdf_url,
        "fields_of_study": [],
        "publication_date": None,
        "tldr": None,
        "external_ids": {"OpenReview": note_id},
        "url": f"https://openreview.net/forum?id={forum}",
        "source": "openreview",
    }


def is_configured() -> bool:
    return bool(config.OPENREVIEW_USERNAME and config.OPENREVIEW_PASSWORD)


def search_papers(query: str, max_results: int = 10, venue: str = None) -> list[dict]:
    """Search OpenReview for papers. Optionally filter by venue ID.

    venue examples: 'ICLR.cc/2026/Conference', 'NeurIPS.cc/2025/Conference'
    """
    if not is_configured():
        return []

    params = {
        "query": query,
        "limit": min(max_results, 50),
    }
    if venue:
        params["content.venueid"] = venue

    data = _get(f"{BASE_URL}/notes/search", params=params)

    results = []
    for note in data.get("notes", []):
        try:
            paper = format_paper(note)
            if paper["title"]:
                results.append(paper)
        except Exception:
            continue

    return results[:max_results]


def search_by_venue(venue_id: str, limit: int = 25) -> list[dict]:
    """List papers from a specific venue (e.g., accepted ICLR 2026 papers)."""
    if not is_configured():
        return []

    params = {
        "content.venueid": venue_id,
        "limit": min(limit, 100),
    }
    data = _get(f"{BASE_URL}/notes", params=params)

    results = []
    for note in data.get("notes", []):
        try:
            paper = format_paper(note)
            if paper["title"]:
                results.append(paper)
        except Exception:
            continue

    return results[:limit]
