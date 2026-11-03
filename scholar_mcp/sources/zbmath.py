"""zbMATH Open: the reference index for mathematics, with MSC classes and links to
DOIs and arXiv. Free, no key needed, and part of every search.
"""

import re

from .. import access
from . import pacing

BASE_URL = "https://api.zbmath.org/v1/document/_search"


def _abstract(item: dict) -> str:
    """The author's summary, else the expert review. Some texts are withheld for licensing."""
    texts = {e.get("contribution_type"): (e.get("text") or "").strip()
             for e in item.get("editorial_contributions") or []}
    for kind in ("summary", "review"):
        text = texts.get(kind, "")
        if text and "contents unavailable" not in text:
            return re.sub(r"\s+", " ", re.sub(r"^Summary:\s*", "", text))[:2000]
    return ""


def format_document(item: dict) -> dict | None:
    title = ((item.get("title") or {}).get("title") or "").strip()
    if not title:
        return None
    links = {(link.get("type") or "").lower(): link.get("identifier") for link in item.get("links") or []}
    ids = {"zbMATH": str(item.get("id") or "")}
    if links.get("doi"):
        ids["DOI"] = links["doi"]
    if links.get("arxiv"):
        ids["ArXiv"] = links["arxiv"]
    sources = item.get("source") or []
    source = (sources[0] if isinstance(sources, list) and sources else sources) or {}
    text = str(source.get("source") or "")
    venue = "arXiv" if "arxiv:" in text.lower() else re.split(r"\s+\d", text, maxsplit=1)[0].strip(" ,.")
    year = str(item.get("year") or "")
    return {"paper_id": ids.get("DOI") or f"zbMATH:{ids['zbMATH']}", "title": title,
            "authors": [a.get("name", "") for a in (item.get("contributors") or {}).get("authors") or []],
            "year": int(year) if year.isdigit() else None, "venue": venue, "abstract": _abstract(item),
            "external_ids": {k: v for k, v in ids.items() if v},
            "fields_of_study": ["Mathematics"],
            "url": item.get("zbmath_url") or "", "source": "zbmath"}


def search_papers(query: str, limit: int = 20, **kwargs) -> list[dict]:
    if limit <= 0 or not query.strip():
        return []
    pacing.wait("zbmath")
    response = access.get(BASE_URL, params={"search_string": query, "page": 0, "results_per_page": min(limit, 100)},
                         timeout=20, follow_redirects=True)
    if response.status_code == 404:  # zbMATH answers 404 when nothing matches
        return []
    response.raise_for_status()
    return [paper for paper in map(format_document, response.json().get("result") or []) if paper][:limit]
