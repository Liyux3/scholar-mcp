"""HAL open-archive search and PDF resolution."""

from __future__ import annotations

import re

import httpx


BASE_URL = "https://api.archives-ouvertes.fr/search/"
FIELDS = ",".join(
    (
        "halId_s",
        "title_s",
        "authFullName_s",
        "abstract_s",
        "producedDateY_i",
        "docType_s",
        "uri_s",
        "fileMain_s",
        "doiId_s",
    )
)


def _first(value, default=""):
    if isinstance(value, list):
        return value[0] if value else default
    return value if value not in (None, "") else default


def _request(query: str, limit: int) -> list[dict]:
    response = httpx.get(
        BASE_URL,
        params={"q": query, "rows": min(limit, 100), "fl": FIELDS, "wt": "json"},
        timeout=20,
    )
    response.raise_for_status()
    return response.json().get("response", {}).get("docs", [])


def _normalize(item: dict) -> dict | None:
    title = str(_first(item.get("title_s"))).strip()
    if not title:
        return None
    hal_id = str(_first(item.get("halId_s"))).strip()
    doi = str(_first(item.get("doiId_s"))).strip()
    pdf_url = str(_first(item.get("fileMain_s"))).strip()
    year = item.get("producedDateY_i")
    try:
        year = int(year) if year else None
    except (TypeError, ValueError):
        year = None
    authors = item.get("authFullName_s") or []
    if isinstance(authors, str):
        authors = [authors]
    return {
        "paper_id": hal_id or doi,
        "title": title,
        "authors": authors,
        "abstract": str(_first(item.get("abstract_s"))).strip(),
        "year": year,
        "venue": "HAL",
            "citation_count": 0,
            "_citation_count_known": False,
        "influential_citations": 0,
        "is_open_access": bool(pdf_url),
        "open_access_url": pdf_url or None,
        "fields_of_study": [],
        "publication_date": str(year) if year else None,
        "tldr": None,
        "external_ids": {key: value for key, value in {"DOI": doi, "HAL": hal_id}.items() if value},
        "url": str(_first(item.get("uri_s"))) or (f"https://hal.science/{hal_id}" if hal_id else ""),
        "source": "hal",
    }


def search_papers(query: str, limit: int = 100, **kwargs) -> list[dict]:
    return [paper for item in _request(query, limit) if (paper := _normalize(item))][:limit]


def resolve_pdf(paper: dict) -> list[str]:
    identifiers = paper.get("external_ids") or {}
    doi = str(identifiers.get("DOI") or "").strip()
    hal_id = str(identifiers.get("HAL") or "").strip()
    title = str(paper.get("title") or "").strip()
    if hal_id:
        query = f'halId_s:"{hal_id}"'
    elif doi:
        query = f'doiId_s:"{doi}"'
    elif title:
        escaped = re.sub(r'(["\\])', r"\\\1", title)
        query = f'title_t:"{escaped}"'
    else:
        return []
    return [
        url
        for item in _request(query, 5)
        if (url := str(_first(item.get("fileMain_s"))).strip())
    ]
