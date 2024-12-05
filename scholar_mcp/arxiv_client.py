import feedparser
import httpx

ARXIV_API_URL = "https://export.arxiv.org/api/query"


def search_papers(query: str, max_results: int = 10) -> list[dict]:
    """Search arXiv. Returns results in the same dict format as s2_client."""
    params = {
        "search_query": f"all:{query}",
        "max_results": max_results,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }
    response = httpx.get(ARXIV_API_URL, params=params, timeout=30)
    response.raise_for_status()
    feed = feedparser.parse(response.content)

    papers = []
    for entry in feed.entries:
        arxiv_id = entry.id.split("/abs/")[-1]
        pdf_url = next(
            (link.href for link in entry.links
             if link.get("type") == "application/pdf"),
            f"https://arxiv.org/pdf/{arxiv_id}.pdf",
        )
        authors = [a.get("name", "") for a in getattr(entry, "authors", [])]
        try:
            year = int(entry.published[:4])
        except (ValueError, TypeError, AttributeError):
            year = None

        papers.append({
            "paper_id": arxiv_id,
            "title": entry.title.replace("\n", " ").strip(),
            "authors": authors,
            "abstract": getattr(entry, "summary", "").replace("\n", " ").strip(),
            "year": year,
            "venue": "arXiv",
            "citation_count": 0,
            "influential_citations": 0,
            "is_open_access": True,
            "open_access_url": pdf_url,
            "fields_of_study": [tag.term for tag in getattr(entry, "tags", [])],
            "publication_date": entry.published[:10] if getattr(entry, "published", None) else None,
            "tldr": None,
            "external_ids": {"ArXiv": arxiv_id},
            "url": entry.id,
            "source": "arxiv",
        })
    return papers


def get_pdf_url(arxiv_id: str) -> str:
    return f"https://arxiv.org/pdf/{arxiv_id}.pdf"
