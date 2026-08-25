"""OpenAIRE Graph search and repository PDF resolution."""

from __future__ import annotations

import httpx


BASE_URL = "https://api.openaire.eu/graph/v3/research-products"
OPEN_LABELS = {"OPEN", "OPEN ACCESS"}


def _request(params: dict) -> list[dict]:
    response = httpx.get(BASE_URL, params=params, timeout=20)
    response.raise_for_status()
    return response.json().get("results") or []


def _doi(item: dict) -> str:
    for pid in item.get("pids") or []:
        if str(pid.get("scheme") or "").lower() == "doi":
            return str(pid.get("value") or "").strip()
    return ""


def _instance_urls(item: dict, *, open_only: bool = False) -> list[str]:
    urls = []
    for instance in item.get("instances") or []:
        label = str((instance.get("accessRight") or {}).get("label") or "").upper()
        if open_only and label not in OPEN_LABELS:
            continue
        for url in instance.get("urls") or []:
            url = str(url or "").strip()
            if url and url not in urls and "doi.org/" not in url:
                urls.append(url)
    return urls


def _normalize(item: dict) -> dict | None:
    title = str(item.get("mainTitle") or "").strip()
    if not title:
        return None
    doi = _doi(item)
    date = str(item.get("publicationDate") or "")
    try:
        year = int(date[:4]) if len(date) >= 4 else None
    except ValueError:
        year = None
    access = str((item.get("bestAccessRight") or {}).get("label") or "").upper()
    urls = _instance_urls(item, open_only=True)
    indicators = (item.get("indicators") or {}).get("citationImpact") or {}
    container = item.get("container") or {}
    return {
        "paper_id": doi or str(item.get("id") or ""),
        "title": title,
        "authors": [
            str(author.get("fullName") or "")
            for author in item.get("authors") or []
            if author.get("fullName")
        ],
        "abstract": "\n".join(str(value) for value in item.get("descriptions") or []),
        "year": year,
        "venue": str(container.get("name") or item.get("publisher") or ""),
        "citation_count": int(float(indicators.get("citationCount") or 0)),
        "influential_citations": 0,
        "is_open_access": access in OPEN_LABELS,
        "open_access_url": urls[0] if urls else None,
        "fields_of_study": [
            str((subject.get("subject") or {}).get("value") or "")
            for subject in item.get("subjects") or []
            if (subject.get("subject") or {}).get("value")
        ],
        "publication_date": date or None,
        "tldr": None,
        "external_ids": {"DOI": doi, "OpenAIRE": str(item.get("id") or "")},
        "url": f"https://doi.org/{doi}" if doi else (urls[0] if urls else ""),
        "source": "openaire",
    }


def search_papers(query: str, limit: int = 100, **kwargs) -> list[dict]:
    items = _request({"search": query, "type": "publication", "pageSize": min(limit, 100)})
    return [paper for item in items if (paper := _normalize(item))][:limit]


def resolve_pdf(paper: dict) -> list[str]:
    identifiers = paper.get("external_ids") or {}
    doi = str(identifiers.get("DOI") or "").strip()
    title = str(paper.get("title") or "").strip()
    params = {"type": "publication", "pageSize": 5}
    if doi:
        params["pid"] = doi
    elif title:
        params["search"] = title
    else:
        return []
    return [url for item in _request(params) for url in _instance_urls(item, open_only=True)]
