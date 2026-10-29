"""CiNii Research: Japan's national index of articles, conference papers, theses
and reports (NII), including J-STAGE, KAKEN and NDL records. Free, no key needed.

It joins searches written in Japanese. Its search matches every token and a
Japanese sentence has no spaces, so the question is cut into its kanji,
katakana and Latin runs at the kana particles between them.
"""

import re

from .. import access
from . import pacing

BASE_URL = "https://cir.nii.ac.jp/opensearch/articles"
KANA = re.compile(r"[぀-ヿ]")
# Words that describe the request rather than the topic.
GENERIC = {"研究", "論文", "方法", "手法", "文献", "資料"}


def is_japanese(text: str) -> bool:
    return bool(KANA.search(text or ""))


def japanese_keywords(text: str) -> str:
    tokens = re.split(r"[぀-ゟ\s、。・「」『』（）()?？!！:：]+", text)
    return " ".join(token for token in tokens if len(token) >= 2 and token not in GENERIC)


def format_item(item: dict) -> dict | None:
    title = str(item.get("title") or "").strip()
    if not title:
        return None
    identifiers = item.get("dc:identifier") or []
    doi = next((i.get("@value") for i in identifiers if isinstance(i, dict) and i.get("@type") == "cir:DOI"), "")
    date = str(item.get("prism:publicationDate") or "")
    creators = item.get("dc:creator") or []
    abstract = re.sub(r"<[^>]+>", " ", str(item.get("description") or "")).strip()
    url = (item.get("link") or {}).get("@id") or item.get("@id") or ""
    return {
        "paper_id": doi or url, "title": title,
        "authors": [creators] if isinstance(creators, str) else [str(c) for c in creators],
        "year": int(date[:4]) if date[:4].isdigit() else None, "publication_date": date or None,
        "venue": str(item.get("prism:publicationName") or ""), "abstract": abstract,
        "external_ids": {"DOI": doi} if doi else {},
        "url": url, "source": "cinii",
    }


def search_papers(query: str, limit: int = 20, **kwargs) -> list[dict]:
    keywords = japanese_keywords(query) if is_japanese(query) else query
    if limit <= 0 or not keywords:
        return []
    pacing.wait("cinii")
    response = access.get(BASE_URL, params={"q": keywords, "count": min(limit, 200), "format": "json"},
                         timeout=20)
    response.raise_for_status()
    return [paper for paper in map(format_item, response.json().get("items") or []) if paper][:limit]
