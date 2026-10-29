"""Springer Nature Meta API v2: journals and books from Springer Nature. Requires SPRINGER_API_KEY."""

from .. import access, config
from . import pacing

BASE_URL = "https://api.springernature.com/meta/v2/json"


def _text(value) -> str:
    if isinstance(value, dict):
        return " ".join(_text(v) for v in value.values())
    if isinstance(value, list):
        return " ".join(_text(v) for v in value)
    return str(value or "").strip()


def search_papers(query: str, limit: int = 20, **kwargs) -> list[dict]:
    if not config.SPRINGER_API_KEY or limit <= 0:
        return []
    pacing.wait("springer", key=config.SPRINGER_API_KEY)
    response = access.get(BASE_URL, params={"q": query, "p": min(limit, 50), "api_key": config.SPRINGER_API_KEY},
                         timeout=20)
    response.raise_for_status()
    papers = []
    for record in response.json().get("records") or []:
        title = _text(record.get("title"))
        if not title:
            continue
        doi = record.get("doi") or ""
        date = str(record.get("publicationDate") or "")
        pdf = next((u.get("value") for u in record.get("url") or [] if u.get("format") == "pdf"), None)
        open_access = str(record.get("openaccess", "")).lower() == "true"
        papers.append({"paper_id": doi, "title": title,
                       "authors": [c.get("creator", "") for c in record.get("creators") or [] if c.get("creator")],
                       "year": int(date[:4]) if date[:4].isdigit() else None, "publication_date": date or None,
                       "venue": record.get("publicationName") or "", "abstract": _text(record.get("abstract")),
                       "external_ids": {"DOI": doi} if doi else {},
                        "is_open_access": open_access,
                       "open_access_url": pdf if open_access else None,
                       "url": f"https://doi.org/{doi}" if doi else "", "source": "springer"})
    return papers[:limit]
