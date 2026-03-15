"""PubMed E-utilities client for biomedical paper search."""

import httpx

BASE_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"


def search_papers(query: str, max_results: int = 10) -> list[dict]:
    """Search PubMed. Returns results in the same dict format as s2_client."""
    # Step 1: search for PMIDs
    search_params = {
        "db": "pubmed",
        "term": query,
        "retmax": max_results,
        "retmode": "json",
        "sort": "relevance",
    }
    r = httpx.get(f"{BASE_URL}/esearch.fcgi", params=search_params, timeout=30)
    r.raise_for_status()
    pmids = r.json().get("esearchresult", {}).get("idlist", [])
    if not pmids:
        return []

    # Step 2: fetch summaries for those PMIDs
    summary_params = {
        "db": "pubmed",
        "id": ",".join(pmids),
        "retmode": "json",
    }
    r = httpx.get(f"{BASE_URL}/esummary.fcgi", params=summary_params, timeout=30)
    r.raise_for_status()
    results = r.json().get("result", {})

    papers = []
    for pmid in pmids:
        data = results.get(pmid)
        if not data or not isinstance(data, dict):
            continue
        papers.append(format_paper(data, pmid))
    return papers


def format_paper(data: dict, pmid: str) -> dict:
    """Convert PubMed esummary record to unified format."""
    authors = [a.get("name", "") for a in data.get("authors", []) if isinstance(a, dict)]

    year = None
    pub_date = data.get("pubdate", "")
    if pub_date and len(pub_date) >= 4:
        try:
            year = int(pub_date[:4])
        except (ValueError, TypeError):
            pass

    doi = ""
    for eid in data.get("articleids", []):
        if isinstance(eid, dict) and eid.get("idtype") == "doi":
            doi = eid.get("value", "")
            break

    return {
        "paper_id": f"pmid_{pmid}",
        "title": data.get("title", "").rstrip("."),
        "authors": authors,
        "abstract": "",  # esummary doesn't include abstracts
        "year": year,
        "venue": data.get("fulljournalname", "") or data.get("source", ""),
        "citation_count": 0,
        "influential_citations": 0,
        "is_open_access": False,
        "open_access_url": None,
        "fields_of_study": [],
        "publication_date": pub_date[:10] if len(pub_date) >= 10 else None,
        "tldr": None,
        "external_ids": {"DOI": doi, "PMID": pmid} if doi else {"PMID": pmid},
        "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
        "source": "pubmed",
    }
