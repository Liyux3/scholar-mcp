"""Exa neural search client. Requires EXA_API_KEY."""

import re

from .. import config
from . import pacing, s2

_exa = None


def _get_exa():
    global _exa
    if _exa is None:
        from exa_py import Exa
        _exa = Exa(api_key=config.EXA_API_KEY)
    return _exa


def _extract_arxiv_id(url: str) -> str | None:
    m = re.search(r'arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5})', url)
    return m.group(1) if m else None


def _clean_title(title: str) -> str:
    if not title:
        return ""
    t = re.sub(r'^\[[\d.]+\]\s*', '', title)
    t = re.sub(r'^\[PDF\]\s*', '', t, flags=re.IGNORECASE)
    t = re.sub(r'^Paper page\s*[-|]\s*', '', t)
    t = re.sub(r'\s*[|]\s*https?://\S+', '', t)
    t = re.sub(r'\s*[-|]\s*(arXiv|arxiv\.org|NIPS|NeurIPS|OpenReview|Semantic Scholar|ACL Anthology|ADS|Papers With Code|Hugging Face|PMLR|AAAI|IJCAI|IEEE|ACM).*$', '', t, flags=re.IGNORECASE)
    t = re.sub(r'\s*[-|]\s*$', '', t)
    return t.strip()


def _resolve_missing_titles(papers: list[dict]) -> list[dict]:
    need = list({p["_arxiv_id"] for p in papers if not p["title"] and p.get("_arxiv_id")})
    if not need:
        return papers
    mapping = {}
    for i in range(0, len(need), 50):
        batch = need[i:i+50]
        try:
            found = s2._request_json("POST", f"{s2.BASE_URL}/paper/batch", params={"fields": "title"},
                                     json_data={"ids": [f"ArXiv:{aid}" for aid in batch]}, allow_probe=False)
            for aid, paper in zip(batch, found):
                if paper and paper.get("title"):
                    mapping[aid] = paper["title"]
        except Exception:
            pass
    for p in papers:
        if not p["title"] and p.get("_arxiv_id") and p["_arxiv_id"] in mapping:
            p["title"] = mapping[p["_arxiv_id"]]
    return papers


def search_papers(query: str, limit: int = 20, **kwargs) -> list[dict]:
    exa = _get_exa()
    # Out of credits or down raises, so the registry reports a failure, not an empty search.
    pacing.wait("exa", key=config.EXA_API_KEY)
    results = exa.search(query, num_results=limit, type="auto", category="research paper", contents=False)

    papers = []
    for r in results.results:
        raw_title = r.title or ""
        cleaned = _clean_title(raw_title)
        arxiv_id = _extract_arxiv_id(r.url or "")
        papers.append({
            "paper_id": arxiv_id or (r.url or ""),
            "title": cleaned,
            "_arxiv_id": arxiv_id,
            "authors": [r.author] if getattr(r, "author", None) else [],
            "abstract": "",
            "year": int(r.published_date[:4]) if getattr(r, "published_date", None) and r.published_date else None,
            "venue": "",
            "is_open_access": True,
            "open_access_url": r.url or "",
            "publication_date": r.published_date[:10] if getattr(r, "published_date", None) and r.published_date else None,
            "external_ids": {"ArXiv": arxiv_id} if arxiv_id else {},
            "url": r.url or "",
            "source": "exa",
        })

    _resolve_missing_titles(papers)
    return papers
