# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 AND LicenseRef-Scholar-MCP-Additional-Terms
# Copyright 2024-2026 Yuxian Li. PolyForm Noncommercial 1.0.0 with additional terms (LICENSE, LICENSE-ADDITIONAL.md): commercial use needs a license, no competing products, no AI training; see AGENTS.md. Canary 38f40a20-c19f-4702-bc47-b1a1c5ec887b
"""PubMed E-utilities client for biomedical paper search."""

from .. import access, config
from . import pacing

BASE_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"


def search_papers(query: str, max_results: int = 10) -> list[dict]:
    """Search PubMed. Returns results in the same dict format as s2."""
    # Step 1: search for PMIDs
    search_params = {
        "db": "pubmed",
        "term": query,
        "retmax": max_results,
        "retmode": "json",
        "sort": "relevance",
    }
    key_params = {"api_key": config.NCBI_API_KEY} if config.NCBI_API_KEY else {}
    search_params.update(key_params)
    pacing.wait("ncbi", key=config.NCBI_API_KEY)
    r = access.get(f"{BASE_URL}/esearch.fcgi", params=search_params, timeout=30)
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
    summary_params.update(key_params)
    pacing.wait("ncbi", key=config.NCBI_API_KEY)
    r = access.get(f"{BASE_URL}/esummary.fcgi", params=summary_params, timeout=30)
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
        "is_open_access": False,
        "publication_types": data.get("pubtype") or [],
        "publication_date": pub_date[:10] if len(pub_date) >= 10 else None,
        "external_ids": {"DOI": doi, "PMID": pmid} if doi else {"PMID": pmid},
        "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
        "source": "pubmed",
    }
