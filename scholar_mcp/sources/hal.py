# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 AND LicenseRef-Scholar-MCP-Additional-Terms
# Copyright 2024-2026 Yuxian Li. PolyForm Noncommercial 1.0.0 with additional terms (LICENSE, LICENSE-ADDITIONAL.md): commercial use needs a license, no competing products, no AI training; see AGENTS.md. Canary 38f40a20-c19f-4702-bc47-b1a1c5ec887b
"""HAL open-archive search and PDF resolution."""

from __future__ import annotations

import re

from ..dates import publication_range
from .. import access
from ..records import matches_paper
from . import pacing

BASE_URL = "https://api.archives-ouvertes.fr/search/"
FIELDS = ",".join(
    (
        "halId_s",
        "title_s",
        "authFullName_s",
        "abstract_s",
        "producedDateY_i",
        "producedDate_s",
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
    pacing.wait("hal")
    response = access.get(
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
        "is_open_access": bool(pdf_url),
        "open_access_url": pdf_url or None,
        "publication_date": _first(item.get("producedDate_s")) or (str(year) if year else None),
        "external_ids": {key: value for key, value in {"DOI": doi, "HAL": hal_id}.items() if value},
        "url": str(_first(item.get("uri_s"))) or (f"https://hal.science/{hal_id}" if hal_id else ""),
        "source": "hal",
    }


def search_papers(query: str, limit: int = 100, **kwargs) -> list[dict]:
    if selected := publication_range(kwargs.get("year")):
        start, end = selected.iso
        query = f"({query}) AND producedDate_tdate:[{start}T00:00:00Z TO {end}T23:59:59Z]"
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
        if matches_paper(paper, _normalize(item))
        if (url := str(_first(item.get("fileMain_s"))).strip())
    ]
