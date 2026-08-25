"""Zenodo publication search and native file resolution."""

from __future__ import annotations

import html
import re

import httpx


BASE_URL = "https://zenodo.org/api/records"


def _plain_text(value: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", " ", value or "")).strip()


def _pdf_urls(record: dict) -> list[str]:
    urls = []
    for file_info in record.get("files") or []:
        key = str(file_info.get("key") or "").lower()
        content_type = str(file_info.get("type") or "").lower()
        url = str((file_info.get("links") or {}).get("self") or "").strip()
        if url and (key.endswith(".pdf") or "pdf" in content_type):
            urls.append(url)
    return urls


def _request(query: str, limit: int) -> list[dict]:
    response = httpx.get(
        BASE_URL,
        params={"q": query, "size": min(max(limit, 1), 100), "sort": "bestmatch"},
        timeout=20,
    )
    response.raise_for_status()
    return response.json().get("hits", {}).get("hits", [])


def _normalize(record: dict) -> dict | None:
    metadata = record.get("metadata") or {}
    resource_type = metadata.get("resource_type") or {}
    if resource_type.get("type") != "publication":
        return None
    title = str(metadata.get("title") or "").strip()
    if not title:
        return None
    urls = _pdf_urls(record)
    date = str(metadata.get("publication_date") or "")
    try:
        year = int(date[:4]) if len(date) >= 4 else None
    except ValueError:
        year = None
    doi = str(metadata.get("doi") or "").strip()
    creators = metadata.get("creators") or []
    links = record.get("links") or {}
    return {
        "paper_id": doi or f"zenodo:{record.get('id', '')}",
        "title": title,
        "authors": [str(author.get("name") or "") for author in creators if author.get("name")],
        "abstract": _plain_text(str(metadata.get("description") or "")),
        "year": year,
        "venue": str((metadata.get("journal") or {}).get("title") or "Zenodo"),
        "citation_count": 0,
        "influential_citations": 0,
        "is_open_access": metadata.get("access_right") == "open",
        "open_access_url": urls[0] if urls else None,
        "fields_of_study": [str(keyword) for keyword in metadata.get("keywords") or []],
        "publication_date": date or None,
        "tldr": None,
        "external_ids": {"DOI": doi, "Zenodo": str(record.get("id") or "")},
        "url": str(links.get("self_html") or links.get("doi") or ""),
        "source": "zenodo",
    }


def search_papers(query: str, limit: int = 100, **kwargs) -> list[dict]:
    # Zenodo also stores software, datasets, and presentations. Fetch a wider
    # candidate set, then admit publications only so those records do not add
    # noise to the academic reranker.
    records = _request(query, min(limit * 2, 100))
    return [paper for record in records if (paper := _normalize(record))][:limit]


def resolve_pdf(paper: dict) -> list[str]:
    identifiers = paper.get("external_ids") or {}
    doi = str(identifiers.get("DOI") or "").strip()
    zenodo_id = str(identifiers.get("Zenodo") or "").strip()
    title = str(paper.get("title") or "").strip()
    if zenodo_id:
        response = httpx.get(f"{BASE_URL}/{zenodo_id}", timeout=20)
        response.raise_for_status()
        return _pdf_urls(response.json())
    query = f'metadata.doi:"{doi}"' if doi else f'metadata.title:"{title}"' if title else ""
    if not query:
        return []
    return [url for record in _request(query, 5) for url in _pdf_urls(record)]
