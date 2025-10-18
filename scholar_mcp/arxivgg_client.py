"""arxiv.gg semantic search client. 644K arXiv papers with embeddings, no auth."""

import httpx

ARXIVGG_SEMANTIC_URL = "https://arxiv.gg/api/v1/search/semantic"


def search_papers(query: str, limit: int = 20, **kwargs) -> list[dict]:
    try:
        resp = httpx.get(
            ARXIVGG_SEMANTIC_URL,
            params={"q": query, "limit": min(limit, 100)},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return []

    if not data.get("success"):
        return []

    papers = []
    for item in data.get("data", {}).get("papers", []) if "papers" in data.get("data", {}) else data.get("data", {}).get("results", []):
        paper = item.get("paper", item)
        arxiv_id = paper.get("ID", "")
        title = paper.get("Title", "")
        if not title:
            continue

        authors_str = paper.get("Authors", "")
        authors = [a.strip() for a in authors_str.split(",") if a.strip()] if authors_str else []

        try:
            year = int(paper.get("Created", "")[:4])
        except (ValueError, TypeError):
            year = None

        papers.append({
            "paper_id": arxiv_id,
            "title": title.replace("\n", " ").strip(),
            "authors": authors,
            "abstract": (paper.get("Abstract") or "").replace("\n", " ").strip()[:500],
            "year": year,
            "venue": "arXiv",
            "citation_count": 0,
            "influential_citations": 0,
            "is_open_access": True,
            "open_access_url": f"https://arxiv.org/pdf/{arxiv_id}.pdf" if arxiv_id else None,
            "fields_of_study": [c.strip() for c in (paper.get("Categories") or "").split() if c.strip()],
            "publication_date": paper.get("Created", "")[:10] if paper.get("Created") else None,
            "tldr": None,
            "external_ids": {"ArXiv": arxiv_id} if arxiv_id else {},
            "url": f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else "",
            "source": "arxivgg_semantic",
        })

    return papers[:limit]
