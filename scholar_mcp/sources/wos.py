"""Web of Science Starter API (Clarivate). Requires WOS_API_KEY. Records carry no abstracts."""

import re

from .. import access

from .. import config
from . import pacing

BASE_URL = "https://api.clarivate.com/apis/wos-starter/v1/documents"


def search_papers(query: str, limit: int = 20, **kwargs) -> list[dict]:
    terms = " ".join(re.findall(r"[\w-]+", query))
    if not config.WOS_API_KEY or limit <= 0 or not terms:
        return []
    pacing.wait("wos", key=config.WOS_API_KEY)
    response = access.get(BASE_URL, params={"q": f"TS=({terms})", "db": "WOS", "limit": min(limit, 50),
                                           "page": 1, "sortField": "RS+D"},
                         headers={"X-ApiKey": config.WOS_API_KEY}, timeout=20)
    response.raise_for_status()
    papers = []
    for hit in response.json().get("hits") or []:
        title = hit.get("title") or ""
        if not title:
            continue
        source = hit.get("source") or {}
        identifiers = hit.get("identifiers") or {}
        doi = identifiers.get("doi") or ""
        counts = [c.get("count") for c in hit.get("citations") or [] if isinstance(c.get("count"), int)]
        ids = {"DOI": doi, "WoS": hit.get("uid", ""), "PMID": identifiers.get("pmid", "")}
        papers.append({"paper_id": doi or hit.get("uid", ""), "title": title,
                       "authors": [a.get("displayName", "") for a in (hit.get("names") or {}).get("authors") or []],
                       "year": source.get("publishYear"), "venue": source.get("sourceTitle") or "", "abstract": "",
                       "external_ids": {k: v for k, v in ids.items() if v},
                       "citation_count": max(counts) if counts else 0, "_citation_count_known": bool(counts),
                       "url": f"https://doi.org/{doi}" if doi else "", "source": "wos"})
    return papers[:limit]
