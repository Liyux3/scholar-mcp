"""OpenAlex API client. 250M+ papers, free API, good field taxonomy."""

import re

import httpx
from . import config
from .cache import cached
from .relevance import _normalize_title

BASE_URL = "https://api.openalex.org/works"

OA_SELECT_FIELDS = ",".join([
    "id", "doi", "title", "authorships", "publication_year",
    "cited_by_count", "abstract_inverted_index", "open_access",
    "primary_location", "publication_date", "concepts",
])


def _params_base() -> dict:
    """Base params shared across requests."""
    p = {}
    email = config.get_openalex_email()
    if email:
        p["mailto"] = email
    api_key = config.get_openalex_api_key()
    if api_key:
        p["api_key"] = api_key
    return p


def _request(url: str, params: dict, timeout: int = 30) -> httpx.Response:
    """GET with key rotation on 429, without raising.

    OpenAlex keys carry a small daily budget and are exhausted independently.
    get_openalex_api_key picks one at random, so a depleted key fails roughly
    half the requests even when a healthy key is configured. On 429, retry
    with each of the other keys before giving up.
    """
    response = httpx.get(url, params=params, timeout=timeout)
    if response.status_code != 429 or not config.OPENALEX_API_KEYS:
        return response

    _note_exhausted(params.get("api_key"), response)

    tried = {params.get("api_key")}
    for key in config.OPENALEX_API_KEYS:
        if key in tried:
            continue
        tried.add(key)
        response = httpx.get(url, params={**params, "api_key": key}, timeout=timeout)
        if response.status_code != 429:
            break
        _note_exhausted(key, response)
    return response


def _note_exhausted(key: str | None, response: httpx.Response) -> None:
    """Record a spent key so rotation stops picking it until it refills.

    OpenAlex reports the seconds remaining until the allowance resets, so the
    key can come back on its own rather than being dropped for good.
    """
    if not key:
        return
    try:
        headers = getattr(response, "headers", {}) or {}
        reset_after = float(headers.get("x-ratelimit-reset", 0))
    except (TypeError, ValueError):
        reset_after = 0
    config.mark_openalex_exhausted(key, reset_after)


def _get(url: str, params: dict, timeout: int = 30) -> httpx.Response:
    """_request, raising on error. Used by the search paths, where a failure
    must reach sources._timed_call rather than look like an empty result.
    """
    response = _request(url, params, timeout)
    response.raise_for_status()
    return response


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
        "external_ids": {**({"DOI": doi} if doi else {}), "OpenAlex": oa_id},
        "url": work.get("id") or "",
        "source": "openalex",
    }


# OpenAlex reads ? and * in `search` as wildcard operators and rejects the
# request with HTTP 400 rather than treating them as literals. A trailing
# question mark is the normal shape of a natural-language query, so any short
# question routed to the keyword endpoint failed outright.
_OA_SEARCH_OPERATORS = str.maketrans({"?": " ", "*": " "})


def _strip_search_operators(query: str) -> str:
    return " ".join(query.translate(_OA_SEARCH_OPERATORS).split())


@cached(ttl=300)
def search_papers(query: str, limit: int = 10, year: str = None,
                  fields_of_study: list[str] = None, publication_types: list[str] = None,
                  min_citations: int = 0, open_access_only: bool = False, **kwargs) -> list[dict]:
    """Search OpenAlex works."""
    params = _params_base()
    params["search"] = _strip_search_operators(query)
    params["per_page"] = min(limit, 100)
    params["select"] = OA_SELECT_FIELDS

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

    if publication_types:
        oa_type_map = {"JournalArticle": "article", "Conference": "article", "Review": "review", "Book": "book", "Dataset": "dataset"}
        oa_types = [oa_type_map.get(t, t.lower()) for t in publication_types]
        filters.append(f"type:{'|'.join(oa_types)}")

    if min_citations > 0:
        filters.append(f"cited_by_count:>{min_citations}")

    if open_access_only:
        filters.append("open_access.is_oa:true")

    if filters:
        params["filter"] = ",".join(filters)

    r = _get(BASE_URL, params)
    data = r.json()

    results = []
    for work in data.get("results") or []:
        paper = format_paper(work)
        if paper:
            results.append(paper)

    return results[:limit]


@cached(ttl=300)
def search_papers_semantic(query: str, limit: int = 50) -> list[dict]:
    """Semantic search via OA embeddings. Matches by concept, not keywords."""
    params = _params_base()
    params["search.semantic"] = query
    params["per_page"] = min(limit, 50)
    params["select"] = OA_SELECT_FIELDS

    r = _get(BASE_URL, params)

    results = []
    for work in r.json().get("results") or []:
        paper = format_paper(work)
        if paper:
            paper["source"] = "openalex_semantic"
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


def _resolve_to_wid(paper_id: str, title: str = "") -> str | None:
    """Resolve any paper ID to an OA W-ID for use in cites/cited_by filters.

    Tries the id directly, then its DOI, then the published DOI behind an
    arXiv identity, then the title. The extra routes exist because arXiv
    identifiers have no path of their own: OpenAlex does not index arXiv DOIs
    (10.48550/*) and offers no arXiv-id filter, so without them an arXiv paper
    resolves to None and OpenAlex silently contributes no citations at all.
    That leaves S2 as the only citation source, and S2 returns citations
    recency-first, so the graph for a 2017 landmark came back consisting
    entirely of 2026 papers with one or two citations each.
    """
    if paper_id.startswith("W"):
        return paper_id
    if paper_id.startswith("https://openalex.org/"):
        wid = paper_id.split("/")[-1]
        if wid.startswith("W"):
            return wid
    if paper_id.startswith("10."):
        wid = _wid_by_doi(paper_id)
        if wid:
            return wid

    published = _published_doi(paper_id)
    if published:
        wid = _wid_by_doi(published)
        if wid:
            return wid

    if title:
        return _resolve_by_title(title)
    return None


def _wid_by_doi(doi: str) -> str | None:
    params = _params_base()
    params["select"] = "id"
    r = _request(f"https://api.openalex.org/works/doi:{doi}", params, timeout=15)
    if r.status_code != 200:
        return None
    oa_url = r.json().get("id", "")
    return oa_url.split("/")[-1] if "openalex.org/" in oa_url else None


_ARXIV_DOI = re.compile(r"^10\.48550/arxiv\.(.+)$", re.I)
_ARXIV_ID = re.compile(r"^\d{4}\.\d{4,5}(v\d+)?$")


def _published_doi(paper_id: str) -> str | None:
    """Find the journal or conference DOI for a paper given its arXiv identity.

    OpenAlex does not index arXiv DOIs, so a paper whose id is an arXiv DOI has
    no direct route in. Title search does not save it either: OpenAlex's title
    index does not surface BERT at all, returning only a Japanese paper-review
    article that quotes the title. The result was that every relation in
    recommend_papers returned nothing for conference papers addressed by arXiv
    id, which is most of them.

    Semantic Scholar knows both identities, so it can bridge the two. Note it
    404s on the arXiv DOI form and needs `ArXiv:<id>` instead, which is why the
    id is rewritten rather than passed through. Papers that never appeared
    outside arXiv have no published DOI and fall through to the title route.
    """
    match = _ARXIV_DOI.match(paper_id)
    if match:
        s2_id = f"ArXiv:{match.group(1)}"
    elif _ARXIV_ID.match(paper_id):
        s2_id = f"ArXiv:{paper_id}"
    else:
        return None

    from . import s2_client
    try:
        paper = s2_client.get_paper(s2_id)
    except Exception:
        return None

    doi = (paper or {}).get("external_ids", {}).get("DOI") or ""
    if doi and not doi.lower().startswith("10.48550"):
        return doi
    return None


def _resolve_by_title(title: str) -> str | None:
    """Look up a W-ID by title, verifying the match.

    title.search is fuzzy and will happily return a different paper: searching
    for the BERT paper returns "FAD-BERT: Improved prediction of FAD binding".
    Requiring normalised title equality keeps a near-miss from silently
    attaching another paper's citation graph.
    """
    params = _params_base()
    params["filter"] = f"title.search:{title}"
    params["per_page"] = 1
    params["select"] = "id,title"
    r = _request(BASE_URL, params, timeout=15)
    if r.status_code != 200:
        return None

    for work in r.json().get("results") or []:
        if _normalize_title(work.get("title") or "") == _normalize_title(title):
            oa_url = work.get("id") or ""
            if "openalex.org/" in oa_url:
                return oa_url.split("/")[-1]
    return None


@cached(ttl=300)
def get_paper_by_id(paper_id: str) -> dict | None:
    """Get a single paper by OpenAlex ID (W...), DOI, or full URL."""
    url = _resolve_oa_id(paper_id)
    params = _params_base()
    params["select"] = OA_SELECT_FIELDS
    r = _request(url, params, timeout=30)
    if r.status_code != 200:
        return None
    return format_paper(r.json())


@cached(ttl=300)
def get_citations(paper_id: str, limit: int = 20, title: str = "", **kwargs) -> list[dict]:
    """Get papers that cite the given paper, sorted by impact."""
    wid = _resolve_to_wid(paper_id, title=title)
    if not wid:
        return []
    params = _params_base()
    params["filter"] = f"cites:{wid}"
    params["sort"] = "cited_by_count:desc"
    params["per_page"] = min(limit, 100)
    params["select"] = OA_SELECT_FIELDS
    r = _request(BASE_URL, params, timeout=30)
    if r.status_code != 200:
        return []
    results = []
    for work in r.json().get("results") or []:
        paper = format_paper(work)
        if paper:
            results.append(paper)
    return results[:limit]


@cached(ttl=300)
def get_references(paper_id: str, limit: int = 20, **kwargs) -> list[dict]:
    """Get papers referenced by the given paper."""
    url = _resolve_oa_id(paper_id)
    params = _params_base()
    params["select"] = "id,referenced_works"
    r = _request(url, params, timeout=30)
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
    params2["select"] = OA_SELECT_FIELDS
    r2 = _request(BASE_URL, params2, timeout=30)
    if r2.status_code != 200:
        return []
    results = []
    for work in r2.json().get("results") or []:
        paper = format_paper(work)
        if paper:
            results.append(paper)
    return results[:limit]
