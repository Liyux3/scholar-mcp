"""Google Scholar search fallback. Adapted from paper-search-mcp."""

import time
import random
import hashlib
from datetime import datetime
from typing import Optional

import httpx
from bs4 import BeautifulSoup

SCHOLAR_URL = "https://scholar.google.com/scholar"
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
]


def _extract_year(text: str) -> Optional[int]:
    for word in text.split():
        if word.isdigit() and 1900 <= int(word) <= datetime.now().year:
            return int(word)
    return None


def _stable_id(url: str) -> str:
    """Deterministic ID from URL using md5."""
    return "gs_" + hashlib.md5(url.encode()).hexdigest()[:12]


def _parse_paper(item) -> Optional[dict]:
    try:
        title_elem = item.find("h3", class_="gs_rt")
        info_elem = item.find("div", class_="gs_a")
        abstract_elem = item.find("div", class_="gs_rs")

        if not title_elem or not info_elem:
            return None

        title = title_elem.get_text(strip=True)
        for tag in ["[PDF]", "[HTML]", "[BOOK]"]:
            title = title.replace(tag, "").strip()

        link = title_elem.find("a", href=True)
        url = link["href"] if link else ""

        info_text = info_elem.get_text()
        parts = info_text.split(" - ")
        authors = [a.strip() for a in parts[0].split(",")] if parts else []
        year = _extract_year(info_text)

        return {
            "paper_id": _stable_id(url) if url else _stable_id(title),
            "title": title,
            "authors": authors,
            "abstract": abstract_elem.get_text() if abstract_elem else "",
            "year": year,
            "venue": parts[1].strip() if len(parts) > 1 else "",
            "citation_count": 0,
            "influential_citations": 0,
            "is_open_access": False,
            "open_access_url": None,
            "fields_of_study": [],
            "publication_date": f"{year}-01-01" if year else None,
            "tldr": None,
            "external_ids": {},
            "url": url,
            "source": "google_scholar",
        }
    except Exception:
        return None


def search_papers(query: str, max_results: int = 10) -> list[dict]:
    """Search Google Scholar via HTML scraping. Use as last-resort fallback."""
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9",
    }

    papers = []
    start = 0

    while len(papers) < max_results:
        time.sleep(random.uniform(1.5, 3.0))

        params = {"q": query, "start": start, "hl": "en", "as_sdt": "0,5"}
        try:
            response = httpx.get(SCHOLAR_URL, params=params, headers=headers, timeout=15)
            if response.status_code != 200:
                break
        except httpx.HTTPError:
            break

        soup = BeautifulSoup(response.text, "html.parser")
        results = soup.find_all("div", class_="gs_ri")
        if not results:
            break

        for item in results:
            if len(papers) >= max_results:
                break
            paper = _parse_paper(item)
            if paper:
                papers.append(paper)

        start += 10

    return papers[:max_results]
