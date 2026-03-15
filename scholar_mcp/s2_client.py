"""Semantic Scholar API client using direct httpx calls."""

import time
import httpx
from . import config

BASE_URL = "https://api.semanticscholar.org/graph/v1"
REC_URL = "https://api.semanticscholar.org/recommendations/v1"

SEARCH_FIELDS = ",".join([
    "paperId", "corpusId", "title", "abstract", "year", "venue",
    "citationCount", "influentialCitationCount", "isOpenAccess",
    "openAccessPdf", "authors", "externalIds", "fieldsOfStudy",
    "publicationDate", "tldr",
])

DETAIL_FIELDS = SEARCH_FIELDS + "," + ",".join([
    "referenceCount", "publicationVenue", "publicationTypes",
    "journal", "citationStyles",
])

CITATION_FIELDS = ",".join([
    "paperId", "title", "year", "venue", "citationCount",
    "authors", "externalIds", "isOpenAccess", "openAccessPdf",
])

AUTHOR_FIELDS = ",".join([
    "authorId", "name", "affiliations", "paperCount",
    "citationCount", "hIndex",
])


def _headers() -> dict:
    h = {}
    if config.S2_API_KEY:
        h["x-api-key"] = config.S2_API_KEY
    return h


def _get(url: str, params: dict = None, retries: int = 4) -> dict:
    for attempt in range(retries):
        r = httpx.get(url, params=params, headers=_headers(), timeout=config.S2_TIMEOUT)
        if r.status_code == 429 and attempt < retries - 1:
            wait = min(2 ** (attempt + 1), 30)
            time.sleep(wait)
            continue
        r.raise_for_status()
        return r.json()
    r.raise_for_status()
    return {}


def _post(url: str, json_data: dict = None, params: dict = None) -> dict:
    r = httpx.post(url, json=json_data, params=params, headers=_headers(), timeout=config.S2_TIMEOUT)
    r.raise_for_status()
    return r.json()


def format_paper(data: dict) -> dict:
    """Convert raw S2 API paper dict into our clean format."""
    oa_pdf = data.get("openAccessPdf") or {}
    oa_url = oa_pdf.get("url") if oa_pdf else None
    if oa_url == "":
        oa_url = None

    tldr_obj = data.get("tldr") or {}
    tldr_text = tldr_obj.get("text") if isinstance(tldr_obj, dict) else None

    authors_raw = data.get("authors") or []
    authors = [a.get("name", "") for a in authors_raw if isinstance(a, dict)]

    return {
        "paper_id": data.get("paperId", ""),
        "title": data.get("title", ""),
        "authors": authors,
        "abstract": data.get("abstract") or "",
        "year": data.get("year"),
        "venue": data.get("venue") or "",
        "citation_count": data.get("citationCount") or 0,
        "influential_citations": data.get("influentialCitationCount") or 0,
        "is_open_access": data.get("isOpenAccess") or False,
        "open_access_url": oa_url,
        "fields_of_study": data.get("fieldsOfStudy") or [],
        "publication_date": data.get("publicationDate"),
        "tldr": tldr_text,
        "external_ids": data.get("externalIds") or {},
        "url": f"https://www.semanticscholar.org/paper/{data.get('paperId', '')}",
        "source": "semantic_scholar",
    }


def format_paper_detail(data: dict) -> dict:
    """Extended format with venue details and citation styles."""
    result = format_paper(data)

    pub_venue = data.get("publicationVenue") or {}
    if isinstance(pub_venue, dict):
        result["venue_type"] = pub_venue.get("type", "")
        result["venue_url"] = pub_venue.get("url", "")

    result["publication_types"] = data.get("publicationTypes") or []
    result["reference_count"] = data.get("referenceCount") or 0

    styles = data.get("citationStyles") or {}
    if isinstance(styles, dict):
        result["bibtex"] = styles.get("bibtex", "")

    return result


def search_papers(query, limit=10, year=None, venue=None,
                  fields_of_study=None, min_citations=0,
                  open_access_only=False):
    params = {
        "query": query,
        "limit": min(limit, 100),
        "fields": SEARCH_FIELDS,
    }
    if year:
        params["year"] = year
    if venue:
        params["venue"] = ",".join(venue) if isinstance(venue, list) else venue
    if fields_of_study:
        fos = fields_of_study if isinstance(fields_of_study, list) else [fields_of_study]
        params["fieldsOfStudy"] = ",".join(fos)
    if min_citations > 0:
        params["minCitationCount"] = min_citations
    if open_access_only:
        params["openAccessPdf"] = ""

    data = _get(f"{BASE_URL}/paper/search", params=params)
    return [format_paper(p) for p in data.get("data", [])]


def get_paper(paper_id: str) -> dict:
    data = _get(f"{BASE_URL}/paper/{paper_id}", params={"fields": DETAIL_FIELDS})
    return format_paper_detail(data)


def get_citations(paper_id: str, limit: int = 20):
    params = {"fields": CITATION_FIELDS, "limit": min(limit, 1000)}
    data = _get(f"{BASE_URL}/paper/{paper_id}/citations", params=params)
    results = []
    for item in data.get("data", []):
        citing = item.get("citingPaper", {})
        if citing and citing.get("paperId"):
            results.append(format_paper(citing))
    return results


def get_references(paper_id: str, limit: int = 20):
    params = {"fields": CITATION_FIELDS, "limit": min(limit, 1000)}
    data = _get(f"{BASE_URL}/paper/{paper_id}/references", params=params)
    results = []
    for item in data.get("data", []):
        cited = item.get("citedPaper", {})
        if cited and cited.get("paperId"):
            results.append(format_paper(cited))
    return results


def get_recommendations(paper_id: str, limit: int = 10):
    data = _get(
        f"{REC_URL}/papers/forpaper/{paper_id}",
        params={"fields": SEARCH_FIELDS, "limit": min(limit, 500)},
    )
    return [format_paper(p) for p in data.get("recommendedPapers", [])]


def search_authors(query: str, limit: int = 5):
    params = {"query": query, "limit": min(limit, 100), "fields": AUTHOR_FIELDS}
    data = _get(f"{BASE_URL}/author/search", params=params)
    return [
        {
            "author_id": a.get("authorId", ""),
            "name": a.get("name", ""),
            "affiliations": a.get("affiliations") or [],
            "paper_count": a.get("paperCount") or 0,
            "citation_count": a.get("citationCount") or 0,
            "h_index": a.get("hIndex") or 0,
            "url": f"https://www.semanticscholar.org/author/{a.get('authorId', '')}",
        }
        for a in data.get("data", [])
    ]
