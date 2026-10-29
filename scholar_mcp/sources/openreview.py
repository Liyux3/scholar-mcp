"""Search public conference submissions through OpenReview API v2."""

from .. import access
from . import pacing

BASE_URL = "https://api2.openreview.net"

def _get(url: str, params: dict = None) -> dict:
    pacing.wait("openreview")
    r = access.get(url, params=params, timeout=30)
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
        "is_open_access": True,
        "open_access_url": pdf_url,
        "publication_date": None,
        "external_ids": {"OpenReview": note_id},
        "url": f"https://openreview.net/forum?id={forum}",
        "source": "openreview",
    }


def get_paper(paper_id: str) -> dict | None:
    """Resolve an OpenReview note id, as returned in search results."""
    value = str(paper_id).strip()
    if not value.casefold().startswith(("openreview:", "openreview_")):
        return None
    notes = _get(f"{BASE_URL}/notes", {"id": value[len("openreview:"):]}).get("notes") or []
    return format_paper(notes[0]) if notes else None


def search_papers(query: str, max_results: int = 10, venue: str = None) -> list[dict]:
    """Search OpenReview for papers. Optionally filter by venue ID.

    venue examples: 'ICLR.cc/2026/Conference', 'NeurIPS.cc/2025/Conference'
    """
    params = {
        "term": query,
        "source": "forum",
        "limit": min(max_results, 1000),
    }
    if venue:
        params["group"] = venue

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
