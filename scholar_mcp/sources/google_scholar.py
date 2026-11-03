"""Google Scholar search fallback. Adapted from paper-search-mcp."""

import atexit
import hashlib
import re
import threading
from datetime import datetime
from typing import Optional
from urllib.parse import unquote, urlsplit

import httpx
from bs4 import BeautifulSoup

from .. import access, scholar_session
from ..cache import DEFAULT_TTL, _disk, cached
from . import PartialResults, pacing, publish

SCHOLAR_URL = "https://scholar.google.com/scholar"
PAGE_SIZE = 10
_client: httpx.Client | None = None
_client_lock = threading.Lock()


def _session(headers: dict) -> httpx.Client:
    """Keep connection and cookie continuity across the server's queries."""
    global _client
    with _client_lock:
        if _client is None:
            _client = scholar_session.http_client(headers)
            atexit.register(_client.close)
        else:
            _client.headers.update(headers)
        return _client


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

        if not title_elem:
            return None

        title = title_elem.get_text(" ", strip=True)
        is_book = bool(re.match(r"^\[(?:BOOK|B)\]", title))
        title = re.sub(r"^(?:\[(?:PDF|HTML|BOOK|CITATION|B)\]\s*)+", "", title).strip()
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
            # Publisher PDF paths can append /full or .pdf to a DOI-looking
            # string. Only explicit resolver URLs establish an exact DOI here.
            if parsed.hostname in {"doi.org", "dx.doi.org"}:
                doi = re.fullmatch(r"/(10\.\d{4,9}/.+)", path, re.I)
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
            "is_open_access": bool(pdf_url),
            "open_access_url": pdf_url,
            "publication_types": ["Book"] if is_book else [],
            "publication_date": f"{year}-01-01" if year else None,
            "external_ids": identifiers,
            "url": url,
            "source": "google_scholar",
        }
    except Exception:
        return None


def _blocked(response, soup, results) -> bool:
    return response.status_code in (403, 429) or (not results and (
        "/sorry/" in str(response.url)
        or soup.select_one("#gs_captcha_ccl, #gs_captcha_f, form[action*='/sorry/']")
        or any(marker in response.text.lower() for marker in (
            "unusual traffic from your computer network", "g-recaptcha"))))


def _page(query: str, start: int, state: dict) -> tuple[list[dict], int]:
    """Read one page using the transport that actually obtained it."""
    params = {'q': query, 'hl': 'en'}
    if start:
        params['start'] = start
    url = str(httpx.URL(state['endpoint'], params=params))
    pacing.wait('google-scholar')

    def browser():
        try:
            session = scholar_session.recover(query, state['session'], target=url)
        except (PermissionError, TimeoutError) as error:
            raise BlockedError(str(error)) from None
        html = session.pop('_html')
        state.update(session=session, browser=True)
        return httpx.Response(200, text=html, request=httpx.Request('GET', session.pop('_final_url', url)))

    if state.get('browser'):
        response = browser()
    elif state['host_access']:
        response = access.fetch(url, kind='html', timeout=pacing.remaining(45), anonymous=False)
    else:
        try:
            response = state['client'].get(state['endpoint'], params=params)
        except httpx.HTTPError:
            if access.available():
                response = access.fetch(url, kind='html', timeout=pacing.remaining(45), anonymous=False)
                state['host_access'] = True
            else:
                response = browser()
    soup = BeautifulSoup(response.text, 'html.parser')
    results = soup.select('div.gs_ri')
    empty = 'did not match any articles' in soup.get_text(' ', strip=True).lower()
    recoverable_page = (_blocked(response, soup, results) or
                        not results and not empty and (access.available() or soup.find('script') is not None))
    if recoverable_page and not state.get('browser'):
        if state['host_access']:
            raise BlockedError('The host access session did not return Scholar results')
        pacing.wait('google-scholar')
        if access.available():
            response = access.fetch(url, kind='html', timeout=pacing.remaining(45), anonymous=False)
            state['host_access'] = True
        else:
            response = browser()
        soup = BeautifulSoup(response.text, 'html.parser')
        results = soup.select('div.gs_ri')
    if _blocked(response, soup, results):
        raise BlockedError('Google rejected the page in its recovery transport')
    response.raise_for_status()
    if not results:
        if 'did not match any articles' in soup.get_text(' ', strip=True).lower():
            return [], 0
        raise ValueError('Google Scholar returned an unrecognized results page')
    papers = [paper for item in results if (paper := _parse_paper(item))]
    if not papers:
        raise ValueError('Google result cards could not be parsed')
    return papers, len(results)


@cached(ttl=300)
def search_papers(query: str, max_results: int = 10) -> list[dict]:
    """Read and publish cumulative pages, replaying completed pages after an interruption."""
    if max_results <= 0:
        return []
    session = scholar_session.current()
    headers = {"User-Agent": session.get('user_agent') or scholar_session.browser_user_agent(),
               "Accept": "text/html,application/xhtml+xml", "Accept-Language": "en-US,en;q=0.9"}
    endpoint = SCHOLAR_URL
    state = {'session': session, 'client': _session(headers), 'endpoint': endpoint,
             'host_access': access.preferred(endpoint), 'browser': False}
    papers, seen, start = [], set(), 0
    while len(papers) < max_results:
        key = f'google-page.v1:{scholar_session._route()}:{state["endpoint"]}:{query}:{start}'
        saved = _disk(key)
        try:
            if saved is not None:
                page, cards = saved[1]['papers'], saved[1]['cards']
            else:
                page, cards = _page(query, start, state)
        except (httpx.HTTPError, access.AccessError, pacing.Paced, BlockedError, ValueError) as error:
            if papers:
                return PartialResults(papers[:max_results], f'Pagination interrupted ({type(error).__name__})')
            raise
        if not cards:
            break
        before = len(papers)
        for paper in page:
            if paper['paper_id'] not in seen:
                seen.add(paper['paper_id'])
                papers.append(paper)
        if len(papers) == before:
            return PartialResults(papers[:max_results], 'Google returned a repeated or unreadable results page')
        if saved is None:
            # Store all parsed cards, not just the caller's output slice. A
            # later larger request must not skip the remainder of this page.
            import time
            key = f'google-page.v1:{scholar_session._route()}:{state["endpoint"]}:{query}:{start}'
            _disk(key, (time.time() + DEFAULT_TTL, {'papers': page, 'cards': cards}))
        if not publish(papers[:max_results]):
            return PartialResults(papers[:max_results], 'Search round closed after completed pages')
        start += cards
    return papers[:max_results]
