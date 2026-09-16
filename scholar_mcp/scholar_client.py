"""Google Scholar search fallback. Adapted from paper-search-mcp."""

import time
import random
import hashlib
import re
from datetime import datetime
from typing import Optional

import httpx
from bs4 import BeautifulSoup
from . import scholar_session

SCHOLAR_URL = "https://scholar.google.com/scholar"
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
]


class BlockedError(PermissionError):
    """Google served its anti-scraping interstitial instead of results."""


def _extract_year(text: str) -> Optional[int]:
    for word in re.findall(r"\b(?:19|20)\d{2}\b", text):
        if int(word) <= datetime.now().year:
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

        title = title_elem.get_text(" ", strip=True)
        for tag in ["[PDF]", "[HTML]", "[BOOK]", "[CITATION]"]:
            title = title.replace(tag, "").strip()

        link = title_elem.find("a", href=True)
        url = link["href"] if link else ""

        info_text = re.sub(r"\s+", " ", info_elem.get_text(" ", strip=True))
        parts = info_text.split(" - ")
        authors = [a.strip() for a in parts[0].split(",")] if parts else []
        year = _extract_year(info_text)
        venue = parts[1].strip() if len(parts) > 1 else ""
        if year:
            venue = re.sub(rf",?\s*\b{year}\b\s*$", "", venue).strip()
        cited = item.select_one("a[href*='cites=']")
        count = re.search(r"\d[\d,]*", cited.get_text()) if cited else None
        # The PDF link is a sibling of gs_ri inside the result card.
        card = item.find_parent("div", class_="gs_r")
        attachment = card.select_one(".gs_or_ggsm a[href]") if card else None
        pdf_url = attachment.get("href") if attachment else None

        return {
            "paper_id": _stable_id(url) if url else _stable_id(title),
            "title": title,
            "authors": authors,
            "abstract": abstract_elem.get_text() if abstract_elem else "",
            "year": year,
            "venue": venue,
            "citation_count": int(count[0].replace(",", "")) if count else 0,
            "_citation_count_known": count is not None,
            "influential_citations": 0,
            "is_open_access": bool(pdf_url),
            "open_access_url": pdf_url,
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
    """Search Scholar using one HTTP session for the complete result set."""
    if max_results <= 0:
        return []
    session = scholar_session.current()
    headers = {
        "User-Agent": session.get("user_agent") or random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9",
    }
    papers = []
    start = 0
    recovered = False
    endpoint = f'https://{session["host"]}/scholar' if session else SCHOLAR_URL
    # Preserve cookies and the selected route across pagination and redirects.
    # A fresh client for every page discards the session established by page one.
    with httpx.Client(headers=headers, timeout=15, follow_redirects=True, trust_env=False,
                      cookies=scholar_session.cookie_jar(session), proxy=scholar_session.proxy()) as client:
        while len(papers) < max_results:
            time.sleep(random.uniform(1.5, 3.0))
            params = {"q": query, "start": start, "hl": "en", "as_sdt": "0,5"}
            response = client.get(endpoint, params=params)
            soup = BeautifulSoup(response.text, "html.parser")
            if response.status_code in (403, 429) or "/sorry/" in str(response.url) or soup.select_one("#gs_captcha_ccl, #gs_captcha_f, form[action*='/sorry/']") or any(
                marker in response.text.lower()
                for marker in ("unusual traffic from your computer network", "g-recaptcha")
            ):
                if recovered:
                    raise BlockedError("Google Scholar rejected the recovered session")
                try:
                    session = scholar_session.recover(query, session)
                except PermissionError as error:
                    raise BlockedError(str(error)) from None
                client.headers["User-Agent"] = session["user_agent"]
                client.cookies = scholar_session.cookie_jar(session)
                endpoint = f'https://{session["host"]}/scholar'
                recovered = True
                continue
            response.raise_for_status()
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
