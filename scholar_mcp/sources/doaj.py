"""DOAJ (Directory of Open Access Journals) search client. No auth, 2 req/s."""

import re
from urllib.parse import quote

from .. import access
from ..records import matches_paper
from . import pacing

DOAJ_SEARCH_URL = "https://doaj.org/api/search/articles"


def _literal(text: str) -> str:
    return re.sub(r'([+\-!(){}\[\]^"~*?:\\/]|&&|\|\|)', r'\\\1', text)


def search_papers(query: str, limit: int = 100, **kwargs) -> list[dict]:
    """Search natural-language text without treating punctuation as Lucene operators."""
    return _search(_literal(query), limit)


def _search(query: str, limit: int) -> list[dict]:
    pacing.wait("doaj")
    url = f"{DOAJ_SEARCH_URL}/{quote(query, safe='')}"
    params = {"pageSize": min(limit, 100)}
    resp = access.get(url, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()

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
            "is_open_access": True,
            "open_access_url": full_text_url,
            "fields_of_study": [s.get("term", "") for s in bib.get("subject", []) if s.get("term")],
            "publication_date": f"{bib.get('year', '')}-{bib.get('month', '01').zfill(2)}-01" if bib.get("year") else None,
            "external_ids": {"DOI": doi} if doi else {},
            "url": full_text_url or f"https://doaj.org/article/{entry.get('id', '')}",
            "source": "doaj",
        })

    return papers[:limit]


def resolve_pdf(paper: dict) -> list[dict]:
    identifiers = paper.get("external_ids") or {}
    doi = str(identifiers.get("DOI") or "").strip()
    title = str(paper.get("title") or "").strip()
    query = f'bibjson.identifier.id.exact:"{_literal(doi)}"' if doi else f'bibjson.title.exact:"{_literal(title)}"' if title else ""
    if not query:
        return []
    return [
        {'url': result['open_access_url'], 'version': 'published'}
        for result in _search(query, limit=5)
        if result.get("open_access_url") and matches_paper(paper, result)
    ]
