"""Google Scholar search fallback. Adapted from paper-search-mcp."""

import time
import random
import hashlib
import re
from datetime import datetime
from typing import Optional
from threading import Lock
from urllib.parse import unquote, urlsplit

import httpx
from bs4 import BeautifulSoup
from . import scholar_session
from .cache import cached

SCHOLAR_URL = "https://scholar.google.com/scholar"
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
]

_request_lock = Lock()
_last_request_started = 0.0
REQUEST_INTERVAL = 2.0


def _wait_for_request() -> None:
    """Pace the shared source, counting network time toward the interval."""
    global _last_request_started
    with _request_lock:
        remaining = _last_request_started + REQUEST_INTERVAL - time.monotonic()
        if remaining > 0:
            time.sleep(remaining)
        _last_request_started = time.monotonic()


class BlockedError(PermissionError):
    """Google served its anti-scraping interstitial instead of results."""


class _PartialResults(list):
    """Keep completed pages, with a warning consumed at the source boundary."""
    def __init__(self, papers, warning):
        super().__init__(papers)
        self.partial_warning = warning


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

        if not title_elem:
            return None

        title = title_elem.get_text(" ", strip=True)
        for tag in ["[PDF]", "[HTML]", "[BOOK]", "[CITATION]"]:
            title = title.replace(tag, "").strip()
        if not title:
            return None

        link = title_elem.find("a", href=True)
        url = link["href"] if link else ""

        info_text = re.sub(r"\s+", " ", info_elem.get_text(" ", strip=True)) if info_elem else ""
        parts = info_text.split(" - ")
        authors = [a.strip() for a in parts[0].split(",") if a.strip()] if parts else []
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
        identifiers = {}
        for candidate in (url, pdf_url):
            if not candidate:
                continue
            parsed = urlsplit(candidate)
            path = unquote(parsed.path)
            if (parsed.hostname or "").removeprefix("www.") in {"arxiv.org", "export.arxiv.org"}:
                match = re.fullmatch(r"/(?:abs|pdf)/((?:\d{4}\.\d{4,5}|[a-z-]+/\d{7})(?:v\d+)?)(?:\.pdf)?/?", path)
                if match:
                    identifiers.setdefault("ArXiv", match[1])
            doi = re.search(r"(?:^|/)(10\.\d{4,9}/[^?#]+)", path, re.I)
            if doi:
                identifiers.setdefault("DOI", doi[1])

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
            "external_ids": identifiers,
            "url": url,
            "source": "google_scholar",
        }
    except Exception:
        return None


@cached(ttl=300)
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
            _wait_for_request()
            params = {"q": query, "start": start, "hl": "en", "as_sdt": "0,5"}
            try:
                response = client.get(endpoint, params=params)
            except httpx.HTTPError as error:
                if papers:
                    return _PartialResults(papers, f"Pagination interrupted ({type(error).__name__})")
                raise
            soup = BeautifulSoup(response.text, "html.parser")
            results = soup.find_all("div", class_="gs_ri")
            if response.status_code in (403, 429) or (not results and (
                "/sorry/" in str(response.url) or soup.select_one("#gs_captcha_ccl, #gs_captcha_f, form[action*='/sorry/']") or any(
                marker in response.text.lower()
                for marker in ("unusual traffic from your computer network", "g-recaptcha")
            ))):
                if recovered:
                    if papers:
                        return _PartialResults(papers, "Google rejected pagination after session recovery")
                    raise BlockedError("Google Scholar rejected the recovered session")
                try:
                    session = scholar_session.recover(query, session)
                except (PermissionError, TimeoutError) as error:
                    if papers:
                        return _PartialResults(papers, str(error))
                    raise BlockedError(str(error)) from None
                client.headers["User-Agent"] = session["user_agent"]
                client.cookies = scholar_session.cookie_jar(session)
                endpoint = f'https://{session["host"]}/scholar'
                recovered = True
                continue
            try:
                response.raise_for_status()
            except httpx.HTTPError as error:
                if papers:
                    return _PartialResults(papers, f"Pagination interrupted ({type(error).__name__})")
                raise
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
