"""Exa neural search client. Requires EXA_API_KEY."""

import re
import httpx
from . import config

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
    s2_key = config.get_s2_api_key()
    headers = {"x-api-key": s2_key} if s2_key else {}
    mapping = {}
    for i in range(0, len(need), 50):
        batch = need[i:i+50]
        try:
            resp = httpx.post("https://api.semanticscholar.org/graph/v1/paper/batch",
                              json={"ids": [f"ArXiv:{aid}" for aid in batch]},
                              params={"fields": "title"}, headers=headers, timeout=10)
            if resp.status_code == 200:
                for aid, paper in zip(batch, resp.json()):
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
    try:
        results = exa.search(query, num_results=limit, type="auto",
                             category="research paper", contents=False)
    except Exception:
        return []

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
            "citation_count": 0,
            "_citation_count_known": False,
            "influential_citations": 0,
            "is_open_access": True,
            "open_access_url": r.url or "",
            "fields_of_study": [],
            "publication_date": r.published_date[:10] if getattr(r, "published_date", None) and r.published_date else None,
            "tldr": None,
            "external_ids": {"ArXiv": arxiv_id} if arxiv_id else {},
            "url": r.url or "",
            "source": "exa",
        })

    _resolve_missing_titles(papers)
    return papers
