"""arxiv.gg semantic search client. 644K arXiv papers with embeddings, no auth."""

import sys

from .. import access
from . import pacing

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


class _KeywordResults(list):
    partial_warning = "arxiv.gg semantic index unavailable; returned keyword matches"


def search_papers(query: str, limit: int = 20, **kwargs) -> list[dict]:
    pacing.wait("arxivgg")
    resp = access.get(
        ARXIVGG_SEMANTIC_URL,
        params={"q": query, "limit": min(limit, 100)},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()

    if not data.get("success"):  # an answer that failed must not read as no papers
        raise RuntimeError(f"arxiv.gg answered without success: {str(data.get('error') or data.get('message') or '')[:80]}")

    degraded = _check_degraded(data.get("data") or {})

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
            "is_open_access": True,
            "open_access_url": f"https://arxiv.org/pdf/{arxiv_id}.pdf" if arxiv_id else None,
            "fields_of_study": [c.strip() for c in (paper.get("Categories") or "").split() if c.strip()],
            "publication_date": paper.get("Created", "")[:10] if paper.get("Created") else None,
            "external_ids": {"ArXiv": arxiv_id} if arxiv_id else {},
            "url": f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else "",
            "source": "arxivgg_semantic",
        })

    return _KeywordResults(papers[:limit]) if degraded else papers[:limit]


def _check_degraded(data: dict) -> bool:
    """Warn once if arxiv.gg served keyword results for a semantic request."""
    global _degraded_warning_shown
    fallback = data.get("fallback") or {}
    if not fallback.get("used"):
        _degraded_warning_shown = False
        return False
    if not _degraded_warning_shown:
        _degraded_warning_shown = True
        reason = fallback.get("reasonCode") or "unknown"
        print(f"scholar-mcp: arxiv.gg semantic search unavailable ({reason}), "
              f"serving keyword results; later requests use compressed queries", file=sys.stderr)
    return True
