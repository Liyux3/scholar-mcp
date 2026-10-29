"""Lens.org scholarly search, including patent citations. Requires LENS_API_TOKEN."""

import httpx
from . import pacing

from .. import config

BASE_URL = "https://api.lens.org/scholarly/search"
ID_TYPES = {"doi": "DOI", "pmid": "PMID", "pmcid": "PMC", "magid": "MAG", "openalex": "OpenAlex"}


def search_papers(query: str, limit: int = 20, **kwargs) -> list[dict]:
    if not config.LENS_API_TOKEN or limit <= 0:
        return []
    pacing.wait("lens", key=config.LENS_API_TOKEN)
    response = httpx.post(BASE_URL, json={"query": query, "sort": [{"relevance": "desc"}], "size": min(limit, 100)},
                          headers={"Authorization": f"Bearer {config.LENS_API_TOKEN}"}, timeout=20)
    response.raise_for_status()
    papers = []
    for record in response.json().get("data") or []:
        title = record.get("title") or ""
        if not title:
            continue
        ids = {ID_TYPES[i["type"]]: i.get("value", "") for i in record.get("external_ids") or []
               if i.get("type") in ID_TYPES}
        ids["Lens"] = record.get("lens_id", "")
        open_access = record.get("open_access") or {}
        pdf = next((url for location in open_access.get("locations") or [] for url in location.get("pdf_urls") or []), None)
        papers.append({"paper_id": ids.get("DOI") or record.get("lens_id", ""), "title": title,
                       "authors": [" ".join(filter(None, (a.get("first_name"), a.get("last_name"))))
                                   for a in record.get("authors") or []],
                       "year": record.get("year_published"), "venue": (record.get("source") or {}).get("title") or "",
                       "abstract": record.get("abstract") or "", "external_ids": {k: v for k, v in ids.items() if v},
                       "citation_count": record.get("scholarly_citations_count") or 0,
                       "_citation_count_known": record.get("scholarly_citations_count") is not None,
                       "is_open_access": bool(open_access), "open_access_url": pdf,
                       "url": f"https://www.lens.org/lens/scholar/article/{record.get('lens_id', '')}", "source": "lens"})
    return papers[:limit]
