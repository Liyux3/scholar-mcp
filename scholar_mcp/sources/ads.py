"""NASA ADS: astronomy, astrophysics and physics. Requires ADS_API_TOKEN (free ADS account)."""

from .. import access
from . import pacing

from .. import config

BASE_URL = "https://api.adsabs.harvard.edu/v1/search/query"
FIELDS = "title,author,year,pub,abstract,doi,bibcode,identifier,citation_count"


def search_papers(query: str, limit: int = 20, **kwargs) -> list[dict]:
    if not config.ADS_API_TOKEN or limit <= 0:
        return []
    pacing.wait("ads", key=config.ADS_API_TOKEN)
    response = access.get(BASE_URL, params={"q": query, "fl": FIELDS, "rows": min(limit, 200), "sort": "score desc"},
                         headers={"Authorization": f"Bearer {config.ADS_API_TOKEN}"}, timeout=20)
    response.raise_for_status()
    papers = []
    for doc in response.json().get("response", {}).get("docs", []):
        title = (doc.get("title") or [""])[0]
        if not title:
            continue
        ids = {"ADS": doc.get("bibcode", "")}
        if doc.get("doi"):
            ids["DOI"] = doc["doi"][0]
        arxiv = next((i.split(":", 1)[1] for i in doc.get("identifier") or [] if i.lower().startswith("arxiv:")), "")
        if arxiv:
            ids["ArXiv"] = arxiv
        year = str(doc.get("year") or "")
        papers.append({"paper_id": ids.get("DOI") or doc.get("bibcode", ""), "title": title,
                       "authors": doc.get("author") or [], "year": int(year) if year.isdigit() else None,
                       "venue": doc.get("pub") or "", "abstract": doc.get("abstract") or "",
                       "external_ids": {k: v for k, v in ids.items() if v},
                       "citation_count": doc.get("citation_count") or 0,
                       "_citation_count_known": doc.get("citation_count") is not None,
                       "url": f"https://ui.adsabs.harvard.edu/abs/{doc.get('bibcode', '')}", "source": "ads"})
    return papers[:limit]
