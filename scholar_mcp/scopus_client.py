"""Scopus search client. Requires SCOPUS_API_KEY (Elsevier developer key)."""

import httpx
from . import config

SCOPUS_SEARCH_URL = "https://api.elsevier.com/content/search/scopus"


def search_papers(query: str, limit: int = 100, **kwargs) -> list[dict]:
    api_key = config.SCOPUS_API_KEY
    if not api_key:
        return []

    scopus_query = f"TITLE-ABS-KEY({query})"
    papers = []
    per_page = min(limit, 200)

    try:
        resp = httpx.get(
            SCOPUS_SEARCH_URL,
            params={"query": scopus_query, "count": per_page, "start": 0, "sort": "citedby-count"},
            headers={"Accept": "application/json", "X-ELS-APIKey": api_key},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        entries = data.get("search-results", {}).get("entry", [])
    except Exception:
        return []

    for e in entries:
        if e.get("error"):
            continue
        doi = e.get("prism:doi")
        eid = e.get("eid", "")
        title = e.get("dc:title", "")
        if not title:
            continue

        try:
            year = int(e.get("prism:coverDate", "")[:4])
        except (ValueError, TypeError):
            year = None

        papers.append({
            "paper_id": doi or eid,
            "title": title,
            "authors": [e["dc:creator"]] if e.get("dc:creator") else [],
            "abstract": "",
            "year": year,
            "venue": e.get("prism:publicationName", ""),
            "citation_count": int(e.get("citedby-count", 0) or 0),
            "influential_citations": 0,
            "is_open_access": e.get("openaccess") == "1",
            "open_access_url": None,
            "fields_of_study": [],
            "publication_date": e.get("prism:coverDate"),
            "tldr": None,
            "external_ids": {"DOI": doi, "Scopus": eid} if doi else {"Scopus": eid},
            "url": f"https://www.scopus.com/record/display.uri?eid={eid}" if eid else "",
            "source": "scopus",
        })

    return papers[:limit]
