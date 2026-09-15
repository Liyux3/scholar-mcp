"""INSPIRE-HEP search, native citation/reference traversal and identifier lookup."""
import re
import httpx
from .cache import cached
from .crossref_client import doi_id

BASE_URL = "https://inspirehep.net/api/literature"
INSPIRE_MAX_SIZE = 100
_FIELDS = "titles,authors,abstracts,dois,arxiv_eprints,publication_info,citation_count"


def format_paper(hit: dict) -> dict | None:
    meta = hit.get("metadata") or {}
    titles = meta.get("titles") or []
    if not titles or not titles[0].get("title"):
        return None
    publication = (meta.get("publication_info") or [{}])[0]
    try:
        year = int(publication.get("year") or 0) or None
    except (ValueError, TypeError):
        year = None
    doi = (meta.get("dois") or [{}])[0].get("value") or ""
    arxiv = (meta.get("arxiv_eprints") or [{}])[0].get("value") or ""
    rid = str(hit.get("id") or meta.get("control_number") or "")
    return {
        "paper_id": f"INSPIRE:{rid}", "title": titles[0]["title"],
        "authors": [a["full_name"] for a in meta.get("authors", []) if a.get("full_name")],
        "abstract": (meta.get("abstracts") or [{}])[0].get("value") or "",
        "year": year, "venue": publication.get("journal_title") or "",
        "citation_count": meta.get("citation_count") or 0,
        "_citation_count_known": meta.get("citation_count") is not None,
        "influential_citations": 0, "is_open_access": bool(arxiv),
        "open_access_url": f"https://arxiv.org/pdf/{arxiv}" if arxiv else None,
        "fields_of_study": ["Physics"], "publication_date": None, "tldr": None,
        "external_ids": {k: v for k, v in {"DOI": doi, "ArXiv": arxiv, "INSPIRE": rid}.items() if v},
        "url": f"https://inspirehep.net/literature/{rid}", "source": "inspirehep",
    }


@cached(ttl=300)
def _search(query: str, limit: int) -> list[dict]:
    response = httpx.get(BASE_URL, params={"q": query, "size": min(limit, INSPIRE_MAX_SIZE),
                                         "sort": "mostcited", "fields": _FIELDS}, timeout=20)
    response.raise_for_status()
    return response.json().get("hits", {}).get("hits", [])


def search_papers(query: str, limit: int = 10, **kwargs) -> list[dict]:
    return [paper for hit in _search(query, limit) if (paper := format_paper(hit))][:limit]


@cached(ttl=3600)
def _record(paper_id: str) -> dict:
    match = re.fullmatch(r"INSPIRE:(\d+)", paper_id, re.I)
    if match:
        response = httpx.get(f"{BASE_URL}/{match[1]}", timeout=20)
    else:
        doi = doi_id(paper_id)
        arxiv_value = re.sub(r"^10\.48550/arxiv\.", "", doi, flags=re.I) if doi.lower().startswith("10.48550/arxiv.") else paper_id
        arxiv = re.fullmatch(r"(?:arxiv:)?(\d{4}\.\d{4,5}(?:v\d+)?)", arxiv_value, re.I)
        query = f"arxiv:{arxiv[1]}" if arxiv else f"doi:{doi}" if doi else ""
        if not query:
            return {}
        response = httpx.get(BASE_URL, params={"q": query, "size": 1}, timeout=20)
    if response.status_code == 404:
        return {}
    response.raise_for_status()
    data = response.json()
    return data if match else next(iter(data.get("hits", {}).get("hits", [])), {})


def get_paper(paper_id: str) -> dict | None:
    return format_paper(_record(paper_id))


def get_citations(paper_id: str, limit: int = 20, **kwargs) -> list[dict]:
    record = _record(paper_id)
    rid = str(record.get("id") or "")
    return search_papers(f"refersto:recid:{rid}", limit) if rid else []


def get_references(paper_id: str, limit: int = 20, **kwargs) -> list[dict]:
    refs = _record(paper_id).get("metadata", {}).get("references", [])
    ids = []
    for ref in refs:
        url = (ref.get("record") or {}).get("$ref") or ""
        match = re.fullmatch(r"https://inspirehep.net/api/literature/(\d+)", url)
        if match and match[1] not in ids:
            ids.append(match[1])
        if len(ids) >= limit:
            break
    papers = []
    for start in range(0, len(ids), 30):
        batch = ids[start:start + 30]
        papers.extend(search_papers(" OR ".join(f"recid:{rid}" for rid in batch), len(batch)))
    return papers[:limit]
