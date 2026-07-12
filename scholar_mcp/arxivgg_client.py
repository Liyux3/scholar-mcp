"""arxiv.gg semantic search client. 644K arXiv papers with embeddings, no auth."""

import sys

import httpx

ARXIVGG_SEMANTIC_URL = "https://arxiv.gg/api/v1/search/semantic"

_degraded_warning_shown = False


def semantic_available() -> bool:
    """Whether the last call actually ran semantic search.

    arxiv.gg answers with HTTP 206 and `fallback.used: true` when its
    embedding index is unavailable, silently serving keyword results for a
    request that asked for semantic. The registry routes this source the raw
    natural-language query precisely because it is supposed to be semantic, so
    during a fallback that raw sentence goes to a keyword matcher, which is
    the worst pairing available.
    """
    return not _degraded_warning_shown


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

    _check_degraded(data.get("data") or {})

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


def _check_degraded(data: dict) -> None:
    """Warn once if arxiv.gg served keyword results for a semantic request."""
    global _degraded_warning_shown
    fallback = data.get("fallback") or {}
    if not fallback.get("used"):
        return
    if not _degraded_warning_shown:
        _degraded_warning_shown = True
        reason = fallback.get("reasonCode") or "unknown"
        print(f"scholar-mcp: arxiv.gg semantic search unavailable ({reason}), "
              f"serving keyword results; this source is routed raw queries and "
              f"will underperform until it recovers", file=sys.stderr)
