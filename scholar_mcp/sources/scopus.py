# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 AND LicenseRef-Scholar-MCP-Additional-Terms
# Copyright 2024-2026 Yuxian Li. PolyForm Noncommercial 1.0.0 with additional terms (LICENSE, LICENSE-ADDITIONAL.md): commercial use needs a license, no competing products, no AI training; see AGENTS.md. Canary 38f40a20-c19f-4702-bc47-b1a1c5ec887b
"""Scopus search client. Requires SCOPUS_API_KEY (Elsevier developer key)."""

import re
from concurrent.futures import ThreadPoolExecutor

from .. import access, config
from ..relevance import extract_keywords
from . import pacing

SCOPUS_SEARCH_URL = "https://api.elsevier.com/content/search/scopus"

# TITLE-ABS-KEY treats a bare term list as AND, so every added word shrinks the
# candidate set rather than refining its ranking; Scopus gets the shortest query.
SCOPUS_MAX_TERMS = 2

# Elsevier caps `count` per service level. Free developer keys allow 25; asking
# for more returns HTTP 400 INVALID_INPUT rather than clamping, so exceeding it
# fails the whole request. Larger result sets require paging via `start`.
SCOPUS_MAX_COUNT = 25

# Scopus allows 6 req/s. Pages are fetched concurrently because sequential
# paging made this the slowest source in the parallel fan-out by ~4x: four
# round trips at ~1.7s each against ~2s total for every other source.
SCOPUS_PAGE_WORKERS = 4


def _shorten_query(query: str) -> str:
    words = re.findall(r"[a-zA-Z0-9][\w\-]*", query)
    if len(words) <= SCOPUS_MAX_TERMS:
        return query
    return " ".join(extract_keywords(query, max_keywords=SCOPUS_MAX_TERMS))


def _fetch_page(scopus_query: str, api_key: str, start: int, count: int) -> list[dict]:
    pacing.wait("scopus", key=api_key)
    resp = access.get(
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

    # Page offsets are known from `limit`, so all pages can be requested at
    # once. Results are reassembled in offset order to preserve the
    # citedby-count sort the API applied.
    offsets = list(range(0, limit, SCOPUS_MAX_COUNT))
    with ThreadPoolExecutor(max_workers=SCOPUS_PAGE_WORKERS) as pool:
        pages = pool.map(
            lambda start: _fetch_page(scopus_query, api_key, start,
                                      min(limit - start, SCOPUS_MAX_COUNT)),
            offsets,
        )
        entries = [entry for page in pages for entry in page]

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
            "_citation_count_known": e.get("citedby-count") is not None,
            "is_open_access": e.get("openaccess") == "1",
            "publication_date": e.get("prism:coverDate"),
            "external_ids": {"DOI": doi, "Scopus": eid} if doi else {"Scopus": eid},
            "url": f"https://www.scopus.com/record/display.uri?eid={eid}" if eid else "",
            "source": "scopus",
        })

    return papers[:limit]
