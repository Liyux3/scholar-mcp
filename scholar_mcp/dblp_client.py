"""DBLP API client. CS bibliography standard. Free, no key needed."""

import httpx

BASE_URL = "https://dblp.org/search/publ/api"

# DBLP silently truncates to 100 hits regardless of a larger `h`.
DBLP_MAX_HITS = 100


def search_papers(query: str, limit: int = 10, **kwargs) -> list[dict]:
    """Search DBLP for computer science papers.

    DBLP rate-limits aggressively, returning 429 or 503 rather than throttling.
    Those are raised so sources._timed_call records them: an empty return would
    be indistinguishable from a query with no CS matches, which is exactly how
    a throttled source looks like a sparse one.
    """
    params = {
        "q": query,
        "h": min(limit, DBLP_MAX_HITS),
        "format": "json",
    }
    r = httpx.get(BASE_URL, params=params, timeout=10)
    r.raise_for_status()
    data = r.json()

    papers = []
    hits = data.get("result", {}).get("hits", {}).get("hit", [])
    for item in hits:
        info = item.get("info", {})
        title = info.get("title", "")
        if not title:
            continue
        if title.endswith("."):
            title = title[:-1]

        authors_raw = info.get("authors", {}).get("author", [])
        if isinstance(authors_raw, dict):
            authors_raw = [authors_raw]
        authors = [a.get("text", "") if isinstance(a, dict) else str(a) for a in authors_raw]

        year = None
        if info.get("year"):
            try:
                year = int(info["year"])
            except (ValueError, TypeError):
                pass

        doi = ""
        if info.get("doi"):
            doi = info["doi"]

        venue = info.get("venue", "")
        if isinstance(venue, list):
            venue = venue[0] if venue else ""

        papers.append({
            "paper_id": info.get("key", ""),
            "title": title,
            "authors": authors,
            "abstract": "",
            "year": year,
            "venue": venue,
            "citation_count": 0,
            "influential_citations": 0,
            "is_open_access": False,
            "open_access_url": info.get("ee", ""),
            "fields_of_study": ["Computer Science"],
            "publication_date": None,
            "tldr": None,
            "external_ids": {"DOI": doi} if doi else {},
            "url": info.get("url", ""),
            "source": "dblp",
        })

    return papers[:limit]
