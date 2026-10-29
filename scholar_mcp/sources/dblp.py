"""DBLP API client. CS bibliography standard. Free, no key needed."""

import atexit
from contextlib import contextmanager
from http.cookiejar import LWPCookieJar, LoadError
import os
from pathlib import Path
import re
import tempfile
import threading
import time
from html import unescape

import httpx
from bs4 import BeautifulSoup
from filelock import FileLock, Timeout

from .. import access, config
from . import pacing

BASE_URL = "https://dblp.org/search/publ/api"

# Keep the existing 100-hit window for interactive source fan-out.
DBLP_MAX_HITS = 100
_client: httpx.Client | None = None
_client_lock = threading.Lock()


def _session() -> httpx.Client:
    """Reuse the native API's verified cookies and connection pool within the server."""
    global _client
    with _client_lock:
        if _client is None:
            _client = httpx.Client(timeout=15, follow_redirects=True, headers={"Accept": "application/json"})
            atexit.register(_client.close)
        return _client


@contextmanager
def _verified_session():
    """Share verification across CLI/MCP processes; only one may renew it."""
    path = Path(config.DATA_DIR) / "sessions" / "dblp.cookies"
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        with FileLock(str(path) + ".lock", timeout=pacing.remaining(15)):
            client = _session()
            jar = LWPCookieJar(str(path))
            try:
                jar.load(ignore_discard=True)
            except (OSError, LoadError):
                pass
            client.cookies = jar
            try:
                yield client
            finally:
                # Keep the verified cookie even if the following API call failed.
                fd, temporary = tempfile.mkstemp(prefix=".dblp-", dir=path.parent)
                os.close(fd)
                try:
                    jar.save(temporary, ignore_discard=True)
                    os.replace(temporary, path)
                finally:
                    Path(temporary).unlink(missing_ok=True)
    except Timeout:
        raise pacing.Paced("DBLP verification is being renewed by another request") from None


def _get(client: httpx.Client, url, **kwargs) -> httpx.Response:
    """Redirects are real requests and must use the same provider pacing."""
    for _ in range(5):
        pacing.wait("dblp")
        response = client.get(url, follow_redirects=False, timeout=pacing.remaining(15), **kwargs)
        retry = response.headers.get("retry-after", "")
        if response.status_code == 429 and retry.isdigit():
            pacing.defer("dblp", float(retry))
        if not response.is_redirect or "location" not in response.headers:
            response.raise_for_status()
            return response
        target = response.url.join(response.headers["location"])
        if (target.scheme, target.host, target.port) != ("https", response.url.host, response.url.port):
            raise PermissionError("DBLP redirected verification outside its origin")
        url, kwargs = target, {}
    raise ValueError("DBLP exceeded its redirect limit")


def search_papers(query: str, limit: int = 10, **kwargs) -> list[dict]:
    """Search the native API, with optional host recovery after a transport failure."""
    if limit <= 0 or not query.strip():
        return []
    try:
        return _api_search(query, limit)
    except (httpx.HTTPError, PermissionError, ValueError, pacing.Paced) as error:
        failure = error
    limited = isinstance(failure, pacing.Paced) or getattr(getattr(failure, "response", None), "status_code", None) == 429
    if access.available() and not limited:
        pacing.wait("dblp")
        url = str(httpx.URL(BASE_URL, params={"q": query, "h": min(limit, DBLP_MAX_HITS),
                                             "format": "json", "c": 0}))
        try:
            return _papers(access.fetch(url, anonymous=False).json(), limit)
        except (access.AccessError, httpx.HTTPError, ValueError) as error:
            failure = error
    raise failure


def _api_search(query: str, limit: int) -> list[dict]:
    """Search DBLP for computer science papers.

    DBLP rate-limits aggressively, returning 429 or 503 rather than throttling.
    Those are raised so sources._timed_call records them: an empty return would
    be indistinguishable from a query with no CS matches, which is exactly how
    a throttled source looks like a sparse one.
    """
    params = {
        "q": query,
        "h": min(limit, DBLP_MAX_HITS),
        "format": "json",
        "c": 0,
    }
    with _verified_session() as client:
        return _search(client, params, limit)


def _search(client: httpx.Client, params: dict, limit: int) -> list[dict]:
    # This client owns Anubis's metarefresh exchange. Generic host recovery
    # must run only after that native exchange has actually failed.
    r = _get(client, BASE_URL, params=params)
    for _ in range(2):
        if "text/html" not in r.headers.get("content-type", "").lower():
            break
        # Anubis's documented metarefresh mode asks clients to keep the
        # verification cookie and revisit a same-origin URL after a delay.
        # A renewed challenge can arrive after the first redirect. Bound
        # the exchange and keep both verification cookies in this client.
        soup = BeautifulSoup(r.text, "html.parser")
        refresh = soup.find("meta", attrs={"http-equiv": re.compile("^refresh$", re.I)})
        instruction = r.headers.get("refresh") or (refresh.get("content", "") if refresh else "")
        match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*;\s*url=(.+)", str(instruction), re.I)
        if match and soup.find("script", id="anubis_challenge"):
            delay = float(match[1])
            target = r.url.join(match[2].strip("\"'"))
            if (0 <= delay <= 5 and target.scheme == "https"
                    and target.host == r.url.host and target.port == r.url.port
                    and target.path == "/.within.website/x/cmd/anubis/api/pass-challenge"):
                if pacing.remaining(delay + 1) < delay + 1:
                    raise pacing.Paced("DBLP verification exceeds the remaining acquisition budget")
                time.sleep(delay + 1)
                r = _get(client, target)
                continue
        break
    if "text/html" in r.headers.get("content-type", "").lower():
        page = r.text.lower()
        if any(marker in page for marker in (
            "anubis", "making sure", "captcha", "verify you are human",
        )):
            raise PermissionError("DBLP requires browser verification on this connection")
        raise ValueError("DBLP returned an HTML page instead of API data")
    return _papers(r.json(), limit)


def _papers(data: dict, limit: int) -> list[dict]:
    if (not isinstance(data, dict) or not isinstance(data.get("result"), dict)
            or not isinstance(data["result"].get("hits"), dict)):
        raise ValueError("DBLP returned an invalid search response")
    papers = []
    hits = data["result"]["hits"].get("hit", [])
    for item in hits:
        info = item.get("info", {})
        title = unescape(info.get("title", ""))
        if not title:
            continue
        if title.endswith("."):
            title = title[:-1]

        authors_raw = info.get("authors", {}).get("author", [])
        if isinstance(authors_raw, dict):
            authors_raw = [authors_raw]
        authors = [a.get("text", "") if isinstance(a, dict) else str(a) for a in authors_raw]

        year = None
        if info.get("year"):
            try:
                year = int(info["year"])
            except (ValueError, TypeError):
                pass

        doi = ""
        if info.get("doi"):
            doi = info["doi"]

        venue = info.get("venue", "")
        if isinstance(venue, list):
            venue = venue[0] if venue else ""

        papers.append({
            "paper_id": info.get("key", ""),
            "title": title,
            "authors": authors,
            "abstract": "",
            "year": year,
            "venue": venue,
            "is_open_access": False,
            "open_access_url": info.get("ee", ""),
            "fields_of_study": ["Computer Science"],
            "publication_date": None,
            "external_ids": {"DOI": doi} if doi else {},
            "url": info.get("url", ""),
            "source": "dblp",
        })

    return papers[:limit]
