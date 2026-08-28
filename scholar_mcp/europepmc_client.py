"""Europe PMC API client. Covers PubMed + European repositories. Free, no key needed."""

import httpx

BASE_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"

# Europe PMC accepts pageSize up to 1000; 100 matches the pipeline's fetch
# limit and keeps response payloads reasonable.
EUROPEPMC_MAX_PAGE_SIZE = 100


def _request(query: str, limit: int) -> list[dict]:
    response = httpx.get(
        BASE_URL,
        params={
            "query": query,
            "resultType": "core",
            "pageSize": min(limit, EUROPEPMC_MAX_PAGE_SIZE),
            "format": "json",
            "sort": "CITED desc",
        },
        timeout=20,
    )
    response.raise_for_status()
    return response.json().get("resultList", {}).get("result", [])


def _pdf_urls(item: dict) -> list[str]:
    urls = []
    for entry in (item.get("fullTextUrlList") or {}).get("fullTextUrl") or []:
        if (
            str(entry.get("documentStyle") or "").lower() == "pdf"
            and str(entry.get("availabilityCode") or "").upper() == "OA"
            and entry.get("url")
        ):
            urls.append(str(entry["url"]))
    pmcid = str(item.get("pmcid") or "").strip()
    if pmcid:
        native = f"https://europepmc.org/articles/{pmcid}?pdf=render"
        if native not in urls:
            urls.append(native)
    return urls


def search_papers(query: str, limit: int = 10, **kwargs) -> list[dict]:
    """Search Europe PMC. Good for biomedical + European institutional papers."""
    papers = []
    for item in _request(query, limit):
        title = item.get("title", "")
        if not title:
            continue
        authors = []
        for a in (item.get("authorList", {}).get("author") or []):
            name = a.get("fullName", "")
            if name:
                authors.append(name)

        doi = item.get("doi", "")
        year = None
        pub_year = item.get("pubYear")
        if pub_year:
            try:
                year = int(pub_year)
            except (ValueError, TypeError):
                pass

        pmcid = str(item.get("pmcid") or "").strip()
        pdf_urls = _pdf_urls(item)
        external_ids = {"DOI": doi, "PMID": item.get("pmid", ""), "PMC": pmcid}
        papers.append({
            "paper_id": item.get("pmid") or item.get("id") or "",
            "title": title,
            "authors": authors,
            "abstract": item.get("abstractText", "") or "",
            "year": year,
            "venue": item.get("journalTitle", "") or "",
            "citation_count": item.get("citedByCount", 0) or 0,
            "_citation_count_known": item.get("citedByCount") is not None,
            "influential_citations": 0,
            "is_open_access": item.get("isOpenAccess", "N") == "Y",
            "open_access_url": pdf_urls[0] if pdf_urls else None,
            "fields_of_study": [],
            "publication_date": item.get("firstPublicationDate"),
            "tldr": None,
            "external_ids": {key: value for key, value in external_ids.items() if value},
            "url": f"https://europepmc.org/article/{item.get('source','MED')}/{item.get('id','')}",
            "source": "europepmc",
        })

    return papers[:limit]


def resolve_pdf(paper: dict) -> list[str]:
    identifiers = paper.get("external_ids") or {}
    pmcid = str(identifiers.get("PMC") or identifiers.get("PubMedCentral") or "").strip()
    if pmcid:
        return [f"https://europepmc.org/articles/{pmcid}?pdf=render"]
    doi = str(identifiers.get("DOI") or "").strip()
    title = str(paper.get("title") or "").strip()
    query = f"DOI:{doi}" if doi else f'TITLE:"{title}"' if title else ""
    if not query:
        return []
    return [url for item in _request(query, 5) for url in _pdf_urls(item)]
