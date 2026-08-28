"""DOAJ (Directory of Open Access Journals) search client. No auth, 2 req/s."""

import httpx

DOAJ_SEARCH_URL = "https://doaj.org/api/search/articles"


def search_papers(query: str, limit: int = 100, **kwargs) -> list[dict]:
    try:
        resp = httpx.get(
            f"{DOAJ_SEARCH_URL}/{query}",
            params={"pageSize": min(limit, 100)},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return []

    papers = []
    for entry in data.get("results", []):
        bib = entry.get("bibjson", {})
        title = bib.get("title", "")
        if not title:
            continue

        doi = None
        for ident in bib.get("identifier", []):
            if ident.get("type") == "doi":
                doi = ident.get("id")
                break

        authors = [a.get("name", "") for a in bib.get("author", []) if a.get("name")]

        try:
            year = int(bib.get("year", 0))
        except (ValueError, TypeError):
            year = None

        journal_title = bib.get("journal", {}).get("title", "")

        full_text_url = None
        for link in bib.get("link", []):
            if link.get("type") == "fulltext":
                full_text_url = link.get("url")
                break

        papers.append({
            "paper_id": doi or f"doaj:{entry.get('id', '')}",
            "title": title,
            "authors": authors,
            "abstract": bib.get("abstract", ""),
            "year": year,
            "venue": journal_title,
            "citation_count": 0,
            "_citation_count_known": False,
            "influential_citations": 0,
            "is_open_access": True,
            "open_access_url": full_text_url,
            "fields_of_study": [s.get("term", "") for s in bib.get("subject", []) if s.get("term")],
            "publication_date": f"{bib.get('year', '')}-{bib.get('month', '01').zfill(2)}-01" if bib.get("year") else None,
            "tldr": None,
            "external_ids": {"DOI": doi} if doi else {},
            "url": full_text_url or f"https://doaj.org/article/{entry.get('id', '')}",
            "source": "doaj",
        })

    return papers[:limit]


def resolve_pdf(paper: dict) -> list[str]:
    identifiers = paper.get("external_ids") or {}
    doi = str(identifiers.get("DOI") or "").strip()
    title = str(paper.get("title") or "").strip()
    query = f'doi:"{doi}"' if doi else f'title:"{title}"' if title else ""
    if not query:
        return []
    return [
        result["open_access_url"]
        for result in search_papers(query, limit=5)
        if result.get("open_access_url")
    ]
