"""INSPIRE-HEP API client. High-energy physics literature. Free, no key needed."""

import httpx

BASE_URL = "https://inspirehep.net/api/literature"


def search_papers(query: str, limit: int = 10) -> list[dict]:
    """Search INSPIRE-HEP for high-energy physics papers."""
    params = {
        "q": query,
        "size": min(limit, 25),
        "sort": "mostcited",
        "fields": "titles,authors,abstracts,dois,arxiv_eprints,publication_info,citation_count",
    }
    r = httpx.get(BASE_URL, params=params, timeout=15)
    r.raise_for_status()
    data = r.json()

    papers = []
    for hit in data.get("hits", {}).get("hits", []):
        meta = hit.get("metadata", {})
        titles = meta.get("titles", [])
        title = titles[0].get("title", "") if titles else ""
        if not title:
            continue

        authors = []
        for a in (meta.get("authors") or [])[:10]:
            name = a.get("full_name", "")
            if name:
                authors.append(name)

        abstracts = meta.get("abstracts", [])
        abstract = abstracts[0].get("value", "") if abstracts else ""

        year = None
        pub_info = meta.get("publication_info", [])
        if pub_info:
            try:
                year = int(pub_info[0].get("year", 0))
            except (ValueError, TypeError):
                pass

        dois = meta.get("dois", [])
        doi = dois[0].get("value", "") if dois else ""
        arxiv = meta.get("arxiv_eprints", [])
        arxiv_id = arxiv[0].get("value", "") if arxiv else ""

        venue = ""
        if pub_info:
            venue = pub_info[0].get("journal_title", "") or ""

        papers.append({
            "paper_id": str(hit.get("id", "")),
            "title": title,
            "authors": authors,
            "abstract": abstract[:500],
            "year": year,
            "venue": venue,
            "citation_count": meta.get("citation_count", 0) or 0,
            "influential_citations": 0,
            "is_open_access": bool(arxiv_id),
            "open_access_url": f"https://arxiv.org/pdf/{arxiv_id}" if arxiv_id else None,
            "fields_of_study": ["Physics"],
            "publication_date": None,
            "tldr": None,
            "external_ids": {"DOI": doi, "ArXiv": arxiv_id} if doi or arxiv_id else {},
            "url": f"https://inspirehep.net/literature/{hit.get('id', '')}",
            "source": "inspirehep",
        })

    return papers[:limit]
