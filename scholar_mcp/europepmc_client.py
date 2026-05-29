"""Europe PMC API client. Covers PubMed + European repositories. Free, no key needed."""

import httpx

BASE_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"

# Europe PMC accepts pageSize up to 1000; 100 matches the pipeline's fetch
# limit and keeps response payloads reasonable.
EUROPEPMC_MAX_PAGE_SIZE = 100


def search_papers(query: str, limit: int = 10, **kwargs) -> list[dict]:
    """Search Europe PMC. Good for biomedical + European institutional papers."""
    params = {
        "query": query,
        "resultType": "core",
        "pageSize": min(limit, EUROPEPMC_MAX_PAGE_SIZE),
        "format": "json",
        "sort": "CITED desc",
    }
    r = httpx.get(BASE_URL, params=params, timeout=20)
    r.raise_for_status()
    data = r.json()

    papers = []
    for item in data.get("resultList", {}).get("result", []):
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

        papers.append({
            "paper_id": item.get("pmid") or item.get("id") or "",
            "title": title,
            "authors": authors,
            "abstract": item.get("abstractText", "") or "",
            "year": year,
            "venue": item.get("journalTitle", "") or "",
            "citation_count": item.get("citedByCount", 0) or 0,
            "influential_citations": 0,
            "is_open_access": item.get("isOpenAccess", "N") == "Y",
            "open_access_url": None,
            "fields_of_study": [],
            "publication_date": item.get("firstPublicationDate"),
            "tldr": None,
            "external_ids": {"DOI": doi, "PMID": item.get("pmid", "")},
            "url": f"https://europepmc.org/article/{item.get('source','MED')}/{item.get('id','')}",
            "source": "europepmc",
        })

    return papers[:limit]
