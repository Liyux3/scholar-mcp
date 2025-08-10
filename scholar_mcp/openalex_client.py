"""OpenAlex API client. 250M+ papers, free API, good field taxonomy."""

import httpx
from . import config
from .cache import cached

BASE_URL = "https://api.openalex.org/works"


def _params_base() -> dict:
    """Base params shared across requests."""
    p = {}
    email = config.OPENALEX_EMAIL
    if email:
        p["mailto"] = email
    api_key = config.OPENALEX_API_KEY
    if api_key:
        p["api_key"] = api_key
    return p


def _reconstruct_abstract(inverted_index: dict) -> str:
    """OpenAlex stores abstracts as {word: [positions]}. Reconstruct to text."""
    words = []
    for word, positions in inverted_index.items():
        for pos in positions:
            words.append((pos, word))
    words.sort()
    return " ".join(w for _, w in words)


def format_paper(work: dict) -> dict | None:
    """Convert OpenAlex work record to our unified format."""
    title = work.get("title")
    if not title:
        return None

    authors_raw = work.get("authorships") or []
    authors = []
    for a in authors_raw:
        name = (a.get("author") or {}).get("display_name")
        if name:
            authors.append(name)

    year = work.get("publication_year") or 0

    abstract = None
    inv_idx = work.get("abstract_inverted_index")
    if inv_idx:
        abstract = _reconstruct_abstract(inv_idx)

    doi = work.get("doi") or ""
    if doi.startswith("https://doi.org/"):
        doi = doi[len("https://doi.org/"):]

    topics = []
    for concept in (work.get("concepts") or [])[:5]:
        name = concept.get("display_name")
        if name:
            topics.append(name)

    oa_info = work.get("open_access") or {}
    pdf_url = oa_info.get("oa_url")
    is_oa = oa_info.get("is_oa", False)

    journal = None
    loc = work.get("primary_location") or {}
    source = loc.get("source") or {}
    if source:
        journal = source.get("display_name")

    pub_date = work.get("publication_date")

    oa_id = (work.get("id") or "").split("/")[-1]
    return {
        "paper_id": doi or oa_id,
        "title": title,
        "authors": authors,
        "abstract": abstract or "",
        "year": year,
        "venue": journal or "",
        "citation_count": work.get("cited_by_count") or 0,
        "influential_citations": 0,
        "is_open_access": is_oa,
        "open_access_url": pdf_url,
        "fields_of_study": topics,
        "publication_date": pub_date[:10] if pub_date and len(pub_date) >= 10 else None,
        "tldr": None,
        "external_ids": {"DOI": doi} if doi else {},
        "url": work.get("id") or "",
        "source": "openalex",
    }


@cached(ttl=300)
def search_papers(query: str, limit: int = 10, year: str = None,
                  fields_of_study: list[str] = None) -> list[dict]:
    """Search OpenAlex works."""
    params = _params_base()
    params["search"] = query
    params["per_page"] = min(limit * 2, 100)

    filters = []
    if year:
        if "-" in year:
            start, end = year.split("-", 1)
            filters.append(f"publication_year:{start}-{end}")
        else:
            filters.append(f"publication_year:{year}")

    if fields_of_study:
        fos_filter = "|".join(fields_of_study)
        filters.append(f"topics.display_name.search:{fos_filter}")

    if filters:
        params["filter"] = ",".join(filters)

    r = httpx.get(BASE_URL, params=params, timeout=30)
    r.raise_for_status()
    data = r.json()

    results = []
    for work in data.get("results") or []:
        paper = format_paper(work)
        if paper:
            results.append(paper)

    return results[:limit]


def _resolve_oa_id(paper_id: str) -> str:
    """Convert various ID formats to OpenAlex API URL."""
    if paper_id.startswith("https://openalex.org/"):
        return f"https://api.openalex.org/works/{paper_id.split('/')[-1]}"
    if paper_id.startswith("10."):
        return f"https://api.openalex.org/works/doi:{paper_id}"
    if paper_id.startswith("W"):
        return f"https://api.openalex.org/works/{paper_id}"
    return f"https://api.openalex.org/works/{paper_id}"


def _extract_oa_short_id(full_id: str) -> str:
    """Extract W-id from full OpenAlex URL."""
    if full_id.startswith("https://openalex.org/"):
        return full_id.split("/")[-1]
    return full_id


@cached(ttl=300)
def get_paper_by_id(paper_id: str) -> dict | None:
    """Get a single paper by OpenAlex ID (W...), DOI, or full URL."""
    url = _resolve_oa_id(paper_id)
    params = _params_base()
    r = httpx.get(url, params=params, timeout=30)
    if r.status_code != 200:
        return None
    return format_paper(r.json())


@cached(ttl=300)
def get_citations(paper_id: str, limit: int = 20) -> list[dict]:
    """Get papers that cite the given paper. Uses OpenAlex cites filter."""
    oa_id = _extract_oa_short_id(paper_id)
    params = _params_base()
    params["filter"] = f"cites:{oa_id}"
    params["sort"] = "cited_by_count:desc"
    params["per_page"] = min(limit, 100)
    r = httpx.get(BASE_URL, params=params, timeout=30)
    if r.status_code != 200:
        return []
    results = []
    for work in r.json().get("results") or []:
        paper = format_paper(work)
        if paper:
            results.append(paper)
    return results[:limit]


@cached(ttl=300)
def get_references(paper_id: str, limit: int = 20) -> list[dict]:
    """Get papers referenced by the given paper."""
    url = _resolve_oa_id(paper_id)
    params = _params_base()
    r = httpx.get(url, params=params, timeout=30)
    if r.status_code != 200:
        return []
    data = r.json()
    ref_urls = data.get("referenced_works") or []
    if not ref_urls:
        return []
    ref_ids = [_extract_oa_short_id(u) for u in ref_urls[:limit]]
    id_filter = "|".join(ref_ids)
    params2 = _params_base()
    params2["filter"] = f"openalex:{id_filter}"
    params2["per_page"] = min(limit, 100)
    r2 = httpx.get(BASE_URL, params=params2, timeout=30)
    if r2.status_code != 200:
        return []
    results = []
    for work in r2.json().get("results") or []:
        paper = format_paper(work)
        if paper:
            results.append(paper)
    return results[:limit]
