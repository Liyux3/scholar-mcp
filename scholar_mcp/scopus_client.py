"""Scopus search client. Requires SCOPUS_API_KEY (Elsevier developer key)."""

import re
import httpx
from . import config
from .relevance import extract_keywords

SCOPUS_SEARCH_URL = "https://api.elsevier.com/content/search/scopus"
SCOPUS_MAX_TERMS = 5

# Elsevier caps `count` per service level. Free developer keys allow 25; asking
# for more returns HTTP 400 INVALID_INPUT rather than clamping, so exceeding it
# fails the whole request. Larger result sets require paging via `start`.
SCOPUS_MAX_COUNT = 25


def _shorten_query(query: str) -> str:
    words = re.findall(r"[a-zA-Z0-9][\w\-]*", query)
    if len(words) <= SCOPUS_MAX_TERMS:
        return query
    return " ".join(extract_keywords(query, max_keywords=SCOPUS_MAX_TERMS))


def _fetch_page(scopus_query: str, api_key: str, start: int, count: int) -> list[dict]:
    resp = httpx.get(
        SCOPUS_SEARCH_URL,
        params={"query": scopus_query, "count": count, "start": start, "sort": "citedby-count"},
        headers={"Accept": "application/json", "X-ELS-APIKey": api_key},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json().get("search-results", {}).get("entry", [])


def search_papers(query: str, limit: int = 100, **kwargs) -> list[dict]:
    """Search Scopus, paging until `limit` results are collected.

    Raises on HTTP errors so sources._timed_call can record the failure;
    returning [] here would make an outage indistinguishable from no matches.
    """
    api_key = config.SCOPUS_API_KEY
    if not api_key:
        return []

    scopus_query = f"TITLE-ABS-KEY({_shorten_query(query)})"

    entries = []
    while len(entries) < limit:
        page = _fetch_page(scopus_query, api_key, start=len(entries),
                           count=min(limit - len(entries), SCOPUS_MAX_COUNT))
        if not page:
            break
        entries.extend(page)

    papers = []
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
