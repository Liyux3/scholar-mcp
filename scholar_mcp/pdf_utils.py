
import hashlib
import os
import re
import tempfile
import time
import uuid
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextvars import copy_context
from html import unescape
from http.cookiejar import Cookie
from itertools import chain
from pathlib import Path
from typing import Callable, NamedTuple
from urllib.parse import quote, urljoin, urlparse, urlunparse

import httpx
from bs4 import BeautifulSoup

from . import __version__, access, config, scholar_session, sources
from . import records as record_ops
from .cache import cached
from .sources import pacing

DOWNLOAD_TIMEOUT = 60
DOWNLOAD_CHUNK_SIZE = 256 * 1024
PDF_HEADER_SCAN_BYTES = 1024
USER_AGENT = f"scholar-mcp/{__version__} (academic research tool)"

# Copies of one paper differ in version, best first: the publisher's version of
# record, the accepted manuscript in a repository, then a preprint.
VERSIONS = record_ops.PUBLICATION_VERSIONS
# Downloads run side by side. Once one copy arrives, better versions still on
# their way get this long before the best copy so far is kept.
UPGRADE_GRACE_S = 15
DOWNLOAD_WORKERS = 6
UNPAYWALL_COPIES = 5
_fetch_pool = ThreadPoolExecutor(max_workers=DOWNLOAD_WORKERS, thread_name_prefix="scholar-pdf")


class _Copy(NamedTuple):
    source: str
    version: str
    fetch: Callable
    url_digest: str = ''


def _copy_keys(paper: dict) -> set[str]:
    keys = record_ops.paper_identity_keys(paper, include_title=False)
    return keys or {'local:' + _pdf_filename(paper)}


def _retained_copies(paper: dict) -> list[dict]:
    from . import knowledge_base as kb
    if not (Path(kb.DEFAULT_KB_DIR) / 'library.sqlite3').is_file():
        return []
    try:
        return kb.get_store().find_pdf_copies(_copy_keys(paper))
    except Exception:
        return []  # An unavailable local index must not prevent reading the paper.

# Institutional proxy. Off unless its login prefix and a session are configured.
# Short on purpose: an expired session or an uncovered paper is the common
# case, and it must not slow the rest of the download chain.
PROXY_TIMEOUT = 12
PROXY_BUDGET = 45
PROXY_MAX_REQUESTS = 4


def _pdf_filename(paper_info: dict) -> str:
    """Return one stable, collision-resistant filename for a paper."""
    external = paper_info.get("external_ids") or {}
    identifier = (
        external.get("ArXiv")
        or external.get("ArXivId")
        or external.get("DOI")
        or paper_info.get("paper_id")
        or ""
    )
    title = str(paper_info.get("title") or "")
    identity = str(identifier or title or "paper")
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", identity).strip("._-")[:120]
    # Windows reserves these stems even when a filename has an extension.
    if re.match(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", safe, re.I):
        safe = "paper-" + safe
    if not identifier:
        digest = hashlib.sha256(title.encode("utf-8")).hexdigest()[:12]
        safe = f"{safe or 'paper'}-{digest}"
    return f"{safe or 'paper'}.pdf"


def _cached_pdf(save_path: str, filename: str) -> str | None:
    path = Path(save_path).expanduser() / filename
    try:
        if path.is_file():
            with path.open("rb") as stream:
                if b"%PDF-" in stream.read(PDF_HEADER_SCAN_BYTES):
                    return str(path)
    except OSError:
        pass
    return None


def _atomic_pdf_chunks(chunks, save_path: str, filename: str) -> str | None:
    """Share bounded-memory, atomic storage across direct and session downloads."""
    destination = Path(save_path).expanduser() / filename
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, staging_name = tempfile.mkstemp(
        dir=destination.parent,
        prefix=f".{destination.name}.",
        suffix=".part",
    )
    staging = Path(staging_name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            prefix = bytearray()
            for chunk in chunks:
                pacing.checkpoint(len(chunk))
                if len(prefix) < PDF_HEADER_SCAN_BYTES:
                    prefix.extend(chunk[:PDF_HEADER_SCAN_BYTES - len(prefix)])
                if len(prefix) >= PDF_HEADER_SCAN_BYTES and b"%PDF-" not in prefix:
                    return None
                output.write(chunk)
            if b"%PDF-" not in prefix:
                return None
            output.flush()
            os.fsync(output.fileno())
        staging.replace(destination)
        return str(destination)
    except (httpx.HTTPError, OSError):
        return None
    finally:
        staging.unlink(missing_ok=True)


def _try_download(url: str, save_path: str, filename: str) -> str | None:
    """Stream a PDF to a staging file and atomically publish it on success."""
    try:
        headers = {"User-Agent": USER_AGENT}
        with pacing.download_slot(url), httpx.Client(timeout=pacing.remaining(DOWNLOAD_TIMEOUT), follow_redirects=True) as client:
            with client.stream("GET", url, headers=headers) as response:
                response.raise_for_status()
                return _atomic_pdf_chunks(response.iter_bytes(chunk_size=DOWNLOAD_CHUNK_SIZE),
                                          save_path, filename)
    except (httpx.HTTPError, OSError):
        return None


def _try_elsevier_api(doi: str, save_path: str, filename: str) -> str | None:
    """Elsevier's Article API, with the Scopus key, for the PDFs the caller is entitled to.

    For papers outside the entitlement it still answers with a valid PDF of
    the first page only and says so in x-els-status, so only OK is accepted.
    """
    if not config.SCOPUS_API_KEY or not doi.lower().startswith("10.1016/"):
        return None
    headers = {"X-ELS-APIKey": config.SCOPUS_API_KEY, "Accept": "application/pdf", "User-Agent": USER_AGENT}
    try:
        with httpx.Client(timeout=DOWNLOAD_TIMEOUT, follow_redirects=True) as client:
            with client.stream("GET", f"https://api.elsevier.com/content/article/doi/{doi}", headers=headers) as response:
                if not response.is_success or response.headers.get("x-els-status", "").upper() != "OK":
                    return None
                return _atomic_pdf_chunks(response.iter_bytes(chunk_size=DOWNLOAD_CHUNK_SIZE), save_path, filename)
    except (httpx.HTTPError, OSError):
        return None


SCIHUB_MIRRORS = ["https://sci-hub.mksa.top", "https://sci-hub.se", "https://sci-hub.st"]


def _try_scihub(doi: str, save_path: str, filename: str) -> str | None:
    """Try downloading a PDF from Sci-Hub mirrors. Returns file path or None."""
    headers = {"User-Agent": "Mozilla/5.0"}
    for mirror in SCIHUB_MIRRORS:
        try:
            with httpx.Client(timeout=DOWNLOAD_TIMEOUT, follow_redirects=True) as client:
                r = client.get(f"{mirror}/{doi}", headers=headers)
                if r.status_code != 200:
                    continue
                # Skip DDoS-Guard / CAPTCHA pages
                if "ddos-guard" in r.text.lower() or len(r.text) < 500:
                    continue
                # Find PDF URL: embed/iframe src, or direct .pdf link
                match = re.search(r'<(?:embed|iframe)[^>]*src=["\']([^"\']+\.pdf[^"\']*)', r.text)
                if not match:
                    match = re.search(r'(https?://[^\s"\'<>]+\.pdf(?:\?[^\s"\'<>]*)?)', r.text)
                if not match:
                    continue
                pdf_url = match.group(1)
                if pdf_url.startswith("//"):
                    pdf_url = "https:" + pdf_url
                return _try_download(pdf_url, save_path, filename)
        except (httpx.HTTPError, OSError):
            continue
    return None


def _library_cookie() -> str:
    """Legacy raw Cookie header for the institutional proxy, if one was set up.

    Read from LIBRARY_PROXY_COOKIE or <DATA_DIR>/library_cookie.txt. Kept
    out of the repo and out of any output, since it is a live credential.
    """
    cookie = os.environ.get("LIBRARY_PROXY_COOKIE", "").strip()
    if cookie:
        return cookie
    path = Path(config.DATA_DIR) / "library_cookie.txt"
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _library_proxy() -> tuple[str, str]:
    """Login prefix and proxy host, or ("", "") when no proxy is configured.

    LIBRARY_PROXY_PREFIX is the full login prefix (".../login?url="). The
    older LIBRARY_PROXY_BASE (scheme and host only) still works. A prefix
    that is not an http(s) URL returns ("", "") with the prefix kept out of
    the result, so callers report it instead of sending requests to it.
    """
    prefix = os.environ.get("LIBRARY_PROXY_PREFIX", "").strip()
    if not prefix:
        base = os.environ.get("LIBRARY_PROXY_BASE", "").strip()
        prefix = f"{base.rstrip('/')}/login?url=" if base else ""
    parts = urlparse(prefix)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return "", ""
    return prefix, parts.hostname.lower()


def _proxied(prefix: str, target: str) -> str:
    """Wrap a target URL in the login prefix (encoded when the prefix is qurl=)."""
    return prefix + (quote(target, safe="") if prefix.endswith("qurl=") else target)


def _in_proxy_tree(url: str, proxy_host: str) -> bool:
    """True for the proxy host itself and the hostnames it rewrites publishers to."""
    host = (urlparse(url).hostname or "").lower()
    return host == proxy_host or host.endswith("." + proxy_host)


def _cookie_applies_to(domain: str, proxy_host: str) -> bool:
    """Keep only cookies a browser would send to the proxy tree.

    A cookies.txt export may hold every site the user is signed in to; the
    rest must never be attached to a request, so they are dropped on load.
    """
    d = domain.lstrip(".").lower()
    return bool(d) and (proxy_host == d or proxy_host.endswith("." + d)
                        or d.endswith("." + proxy_host))


def _load_cookie_file(path: Path, proxy_host: str) -> tuple[httpx.Cookies, int]:
    """Parse a Netscape cookies.txt into a jar scoped to the proxy tree.

    Returns the jar and how many in-scope cookies were already expired.
    Browser exports mark HttpOnly cookies with a "#HttpOnly_" prefix on the
    domain, which a plain comment filter would silently drop.
    """
    return _load_cookie_text(path.read_text(encoding="utf-8", errors="ignore"), proxy_host)


def _load_cookie_text(text: str, proxy_host: str) -> tuple[httpx.Cookies, int]:
    jar = httpx.Cookies()
    expired = 0
    now = time.time()
    for raw in text.splitlines():
        line = raw.strip("\r\n")
        http_only = line.startswith("#HttpOnly_")
        if line.startswith("#HttpOnly_"):
            line = line[len("#HttpOnly_"):]
        elif not line.strip() or line.startswith("#"):
            continue
        fields = line.split("\t", 6)
        if len(fields) < 6:
            continue
        domain, subdomains, cookie_path, secure, expires, name = fields[:6]
        value = fields[6] if len(fields) > 6 else ""
        if not name or not _cookie_applies_to(domain, proxy_host):
            continue
        try:
            lifetime = int(float(expires))
        except (ValueError, OverflowError):
            lifetime = 0
        if 0 < lifetime < now:
            expired += 1
            continue
        if subdomains.upper() == "TRUE" and not domain.startswith("."):
            domain = "." + domain
        jar.jar.set_cookie(Cookie(
            version=0, name=name, value=value, port=None, port_specified=False,
            domain=domain, domain_specified=subdomains.upper() == "TRUE",
            domain_initial_dot=domain.startswith("."), path=cookie_path or "/",
            path_specified=True, secure=secure.upper() == "TRUE",
            expires=lifetime or None, discard=lifetime == 0, comment=None,
            comment_url=None, rest={"HttpOnly": None} if http_only else {}, rfc2109=False,
        ))
    return jar, expired


def _library_cookies(proxy_host: str, notes: list[str]) -> httpx.Cookies | None:
    """Session cookies for the proxy, or None (with the reason in notes).

    LIBRARY_PROXY_COOKIES is a cookies.txt the user exported from their own
    logged-in browser. The legacy raw Cookie header is accepted too and is
    scoped to the proxy domain tree rather than sent to every redirect hop.
    Passwords are never read, and cookie values never reach notes or logs.
    """
    configured = os.environ.get("LIBRARY_PROXY_COOKIES", "").strip()
    if configured:
        path = Path(configured).expanduser()
        try:
            jar, expired = _load_cookie_file(path, proxy_host)
        except OSError:
            notes.append(f"Library proxy cookie file {path} could not be read; "
                         "check LIBRARY_PROXY_COOKIES.")
            return None
        if len(jar.jar) == 0:
            reason = "all of them expired" if expired else "none belong to the proxy"
            notes.append(f"Library proxy cookie file {path} has no usable cookies for "
                         f"{proxy_host} ({reason}). Log in to the library in your "
                         "browser and export cookies.txt again.")
            return None
        return jar

    legacy = _library_cookie()
    if legacy:
        jar = httpx.Cookies()
        for pair in legacy.split(";"):
            name, _, value = pair.strip().partition("=")
            if name:
                jar.set(name, value, domain="." + proxy_host, path="/")
        return jar

    notes.append("Library proxy has no session cookies; set LIBRARY_PROXY_COOKIES to a "
                 "cookies.txt exported from your browser after logging in to the library.")
    return None


# Publisher PDF locations that follow one pattern per DOI prefix. They are
# requested through the proxy before the DOI landing page, which costs an
# extra request and is often an interstitial. IEEE and Elsevier have no
# DOI-derived pattern, so they are resolved from their landing page below.
_PUBLISHER_PDF_PATTERNS = (
    ("10.1126/", "https://www.science.org/doi/pdf/{doi}"),                # Science / AAAS
    ("10.1038/", "https://www.nature.com/articles/{suffix}.pdf"),         # Nature
    ("10.1007/", "https://link.springer.com/content/pdf/{doi}.pdf"),      # Springer
    ("10.1145/", "https://dl.acm.org/doi/pdf/{doi}"),                     # ACM
    ("10.1002/", "https://onlinelibrary.wiley.com/doi/pdfdirect/{doi}"),  # Wiley
    ("10.1111/", "https://onlinelibrary.wiley.com/doi/pdfdirect/{doi}"),
)
# A path segment that starts a login flow: the proxy's own /login, a CAS or
# Shibboleth identity provider. Anchored to segment starts so DOIs do not match.
_LOGIN_PATH = re.compile(r"/(?:login|signin|authenticate|authentication|authorize|oauth\d*|saml\d*|shibboleth|idp)(?:[/.?]|$)", re.I)


def _publisher_pdf_urls(doi: str) -> list[str]:
    lowered = doi.lower()
    for prefix, template in _PUBLISHER_PDF_PATTERNS:
        if lowered.startswith(prefix):
            safe = "/()._-;:"
            return [template.format(doi=quote(doi, safe=safe),
                                    suffix=quote(doi[len(prefix):], safe=safe))]
    return []


def _landing_pdf_url(page_url: str, html: str) -> str | None:
    """Find the PDF a publisher landing page points to.

    Highwire citation_pdf_url meta tags come first (the indexing standard
    publishers already provide). IEEE Xplore and ScienceDirect article URLs
    carry the identifier their PDF endpoint needs, so those are rebuilt on
    the same host, which keeps a proxy-rewritten hostname intact.
    """
    for tag in re.finditer(r"<meta\b[^>]*>", html, re.I):
        text = tag.group(0)
        if re.search(r"""(?:name|property)\s*=\s*["']citation_pdf_url["']""", text, re.I):
            content = re.search(r"""content\s*=\s*(?:"([^"]*)"|'([^']*)')""", text, re.I)
            if content:
                url = unescape(content.group(1) or content.group(2) or "").strip()
                if url:
                    return urljoin(page_url, url)
    parts = urlparse(page_url)
    host = parts.hostname or ""
    if "ieeexplore" in host:
        match = re.match(r"/document/(\d+)", parts.path)
        if match:
            return urlunparse(parts._replace(path="/stampPDF/getPDF.jsp", params="",
                                             query=f"tp=&arnumber={match.group(1)}", fragment=""))
    if "linkinghub" in host:
        # Elsevier's DOI hop redirects by script, so take the article page it
        # would load, keeping a proxy-rewritten hostname in the proxy.
        match = re.match(r"/retrieve/pii/([A-Za-z0-9]+)", parts.path)
        if match:
            article = host.replace("linkinghub.elsevier.com", "www.sciencedirect.com").replace(
                "linkinghub-elsevier-com", "www-sciencedirect-com")
            return urlunparse(parts._replace(netloc=article, path=f"/science/article/pii/{match.group(1)}",
                                             params="", query="", fragment=""))
    if "sciencedirect" in host:
        match = re.match(r"/science/article/(?:abs/)?pii/([A-Za-z0-9]+)", parts.path)
        if match:
            return urlunparse(parts._replace(path=f"/science/article/pii/{match.group(1)}/pdfft",
                                             params="", query="isDTMRedir=true&download=true",
                                             fragment=""))
    return None


# Name browser checks separately from missing subscription coverage, so the
# caller can open the library link in their own browser when needed.
_WALLS = (
    ("cloudflare", re.compile(r"challenges\.cloudflare\.com|cf-chl-|<title>Just a moment", re.I)),
    ("radware", re.compile(r"perfdrive\.com|radware", re.I)),
    ("elsevier", re.compile(r"cra_js_challenge|/craft/challenge/", re.I)),
    ("captcha", re.compile(r"captcha|are you a robot|verify (?:that )?you are (?:a )?human", re.I)),
)


def _wall(url: str, html: str) -> str | None:
    """Name the browser check a page is, or None for an ordinary page."""
    text = url + "\n" + html[:50_000]
    return next((name for name, pattern in _WALLS if pattern.search(text)), None)


def _is_login_page(url: str, html: str, proxy_host: str) -> bool:
    """An expired proxy session ends on the library or identity-provider login.

    A publisher's own sign-in, reached through the proxy's rewritten hostnames,
    means the library does not cover the paper; the session is still valid.
    """
    parts = urlparse(url)
    host = (parts.hostname or "").lower()
    if _LOGIN_PATH.search(parts.path or ""):
        return host == proxy_host or not _in_proxy_tree(url, proxy_host)
    return host == proxy_host and re.search(r"""type\s*=\s*["']?password""", html, re.I) is not None


def _try_ezproxy(doi: str, save_path: str, filename: str,
                 notes: list[str] | None = None) -> str | None:
    """Fetch one requested DOI through the user's library proxy session.

    The cookie jar is the user's own export; no password is handled. Each
    candidate (a publisher PDF pattern, then the DOI landing page and the
    PDF it points to) is requested through the login prefix and accepted
    only if the bytes are a PDF. Why the route failed is appended to notes.
    """
    notes = notes if notes is not None else []
    prefix, proxy_host = _library_proxy()
    if not prefix:
        if os.environ.get("LIBRARY_PROXY_PREFIX", "").strip():
            notes.append("LIBRARY_PROXY_PREFIX is not an http(s) URL; library proxy skipped.")
        return None
    cookies = _library_cookies(proxy_host, notes)
    if cookies is None:
        return None

    queue = _publisher_pdf_urls(doi) + [f"https://doi.org/{doi}"]
    seen: set[str] = set()
    reasons: list[str] = []
    walls: list[str] = []
    deadline = time.monotonic() + PROXY_BUDGET
    # Proxies and publishers reject non-browser and outdated browser agents.
    headers = {"User-Agent": scholar_session.browser_user_agent(),
               "Accept": "application/pdf,text/html;q=0.9,*/*;q=0.8"}

    def scope_session(request):
        # Parent-domain cookies must not escape through an external redirect.
        if not _in_proxy_tree(str(request.url), proxy_host):
            request.headers.pop("cookie", None)

    with httpx.Client(headers=headers, cookies=cookies, timeout=PROXY_TIMEOUT,
                      follow_redirects=True, event_hooks={"request": [scope_session]}) as client:
        requests = 0
        while queue and requests < PROXY_MAX_REQUESTS and time.monotonic() < deadline:
            target = queue.pop(0)
            if target in seen:
                continue
            seen.add(target)
            url = target if _in_proxy_tree(target, proxy_host) else _proxied(prefix, target)
            requests += 1
            try:
                with client.stream("GET", url, timeout=min(PROXY_TIMEOUT, max(0.1, deadline - time.monotonic()))) as response:
                    chunks = response.iter_bytes(chunk_size=DOWNLOAD_CHUNK_SIZE)
                    first = next(chunks, b"")
                    if response.is_success and b"%PDF-" in first[:PDF_HEADER_SCAN_BYTES]:
                        saved = _atomic_pdf_chunks(chain((first,), chunks), save_path, filename)
                        if saved:
                            return saved
                        reasons.append("PDF could not be written")
                        continue
                    # A landing page needs only a bounded head for its PDF pointer.
                    head = bytearray(first[:200_000])
                    while len(head) < 200_000:
                        chunk = next(chunks, b"")
                        if not chunk:
                            break
                        head.extend(chunk[:200_000 - len(head)])
                    html = head.decode("utf-8", errors="ignore")
                    final_url = str(response.url)
            except httpx.HTTPError as error:
                reasons.append(f"request failed ({type(error).__name__})")
                continue

            if urlparse(final_url).hostname == proxy_host:
                # Some proxies finish login with a script and a visible continue
                # link, not an HTTP redirect. Follow only one same-proxy target.
                page = BeautifulSoup(html, "html.parser")
                handoffs = {urljoin(final_url, a["href"]) for a in page.select("a[href]")}
                handoffs = {target for target in handoffs
                            if _in_proxy_tree(target, proxy_host)
                            and urlparse(target).hostname != proxy_host
                            and urlparse(target).scheme == urlparse(prefix).scheme}
                if len(handoffs) == 1 and not page.select("input[type=password]"):
                    target = handoffs.pop()
                    if target not in seen:
                        queue.insert(0, target)
                        continue

            if _is_login_page(final_url, html, proxy_host):
                notes.append(
                    "Library proxy session looks expired (the proxy returned a login "
                    f"page). Log in at {proxy_host} in your browser and export cookies.txt again.")
                return None
            host = urlparse(final_url).hostname
            pointer = _landing_pdf_url(final_url, html) if response.status_code < 400 else None
            if pointer and pointer not in seen:
                queue.insert(0, pointer)
            elif wall := _wall(final_url, html):
                walls.append(wall)
                reasons.append(f"{wall} check at {host}")
            elif response.status_code >= 400:
                reasons.append(f"HTTP {response.status_code} from {host}")
            else:
                reasons.append(f"no PDF at {host}")

    notes.append("Library proxy did not return a PDF ("
                 + ("; ".join(dict.fromkeys(reasons)) or "time budget used up")
                 + (f"); the publisher asks for a browser check ({', '.join(dict.fromkeys(walls))}), "
                    "open the library link below in a browser." if walls
                    else "); the library may not subscribe to this paper."))
    return None


@cached(ttl=3600, miss_ttl=30)
def _unpaywall_locations(doi: str) -> list[tuple[str, str]]:
    """Every open copy Unpaywall knows, as (url, version), best location first. Needs OPENALEX_EMAIL."""
    email = config.get_openalex_email()
    if not email or not doi:
        return []
    try:
        r = httpx.get(f"https://api.unpaywall.org/v2/{doi}", params={"email": email}, timeout=10)
        data = r.json() if r.status_code == 200 else {}
    except (httpx.HTTPError, ValueError):
        return []
    best = data.get("best_oa_location") or {}
    found = []
    # Unpaywall lists locations best first and may know dozens; landing pages are
    # HTML, so only direct PDF links are worth a download.
    for location in [best, *(data.get("oa_locations") or [])]:
        url = location.get("url_for_pdf")
        if url and url not in [u for u, _ in found]:
            found.append((url, record_ops.normalize_version(location.get('version'))))
    return found[:UNPAYWALL_COPIES]


def _biorxiv_latest_version(doi: str, server: str = "biorxiv") -> int:
    """Query bioRxiv/medRxiv API for latest revision number."""
    try:
        r = httpx.get(f"https://api.biorxiv.org/details/{server}/{doi}/na/json", timeout=10)
        if r.status_code == 200:
            entries = r.json().get("collection", [])
            if entries:
                return max(int(e.get("version", 1)) for e in entries)
    except Exception:
        pass
    return 1


def _resolve_preprint_pdf(doi: str, oa_url: str | None = None) -> str | None:
    """Resolve DOI to preprint server PDF URL.
    Supports: bioRxiv, medRxiv, SSRN, PsyArXiv, engrXiv, AgriXiv, ChemRxiv.
    """
    if not doi:
        return None
    dl = doi.lower()

    if dl.startswith("10.1101/"):
        if oa_url and "medrxiv" in oa_url:
            v = _biorxiv_latest_version(doi, "medrxiv")
            return f"https://www.medrxiv.org/content/{doi}v{v}.full.pdf"
        v = _biorxiv_latest_version(doi, "biorxiv")
        return f"https://www.biorxiv.org/content/{doi}v{v}.full.pdf"

    if dl.startswith("10.2139/"):
        m = re.match(r"10\.2139/ssrn\.(\d+)", doi, re.IGNORECASE)
        if m:
            sid = m.group(1)
            return f"https://papers.ssrn.com/sol3/Delivery.cfm/SSRN_ID{sid}_code.pdf?abstractid={sid}"

    osf_prefixes = (
        "10.31234/",  # PsyArXiv
        "10.31224/",  # engrXiv
        "10.31220/",  # AgriXiv
        "10.31223/",  # EarthArXiv
        "10.31235/",  # SocArXiv
        "10.51224/",  # SportRxiv
    )
    for prefix in osf_prefixes:
        if dl.startswith(prefix):
            m = re.match(rf"{re.escape(prefix)}osf\.io/(\w+)", doi, re.IGNORECASE)
            if m:
                return f"https://osf.io/{m.group(1)}/download"

    if dl.startswith("10.26434/"):
        return f"https://chemrxiv.org/engage/api-gateway/chemrxiv/assets/orp/resource/item/{doi}/original"

    if dl.startswith("10.20944/"):
        return f"https://www.preprints.org/manuscript/{doi}/download"

    return None


_PREPRINT_HOSTS = ("arxiv.org", "biorxiv.org", "medrxiv.org", "ssrn.com", "osf.io", "chemrxiv.org",
                   "preprints.org", "researchsquare.com", "authorea.com")


def _version_of(url: str) -> str:
    """Recognize preprint hosts and retain unknown status for other copies."""
    host = (urlparse(url).hostname or "").lower()
    if any(host == h or host.endswith("." + h) for h in _PREPRINT_HOSTS):
        return "preprint"
    return 'unknown'


def _locate(paper_info: dict, doi: str) -> list[tuple[str, str, str]]:
    """Every known copy as (source, url, version), found at once and in source order."""
    if identity := paper_info.get('_requested_arxiv'):
        return [('arxiv', f'https://arxiv.org/pdf/{identity}.pdf', 'preprint')]
    if url := paper_info.get('_requested_copy_url'):
        return [(paper_info.get('source') or 'requested', url, _version_of(url))]
    if paper_info.get('record_type') not in (None, 'paper'):
        url = paper_info.get('open_access_url')
        return [(paper_info.get('source') or 'native', url, 'unknown')] if url else []
    ext_ids = paper_info.get("external_ids", {})
    oa_url = paper_info.get("open_access_url")
    arxiv_id = ext_ids.get("ArXiv") or ext_ids.get("ArXivId")
    lookups = {
        "preprint": _fetch_pool.submit(_resolve_preprint_pdf, doi, oa_url),
        "repositories": _fetch_pool.submit(sources.resolve_pdf_candidates, paper_info),
        "unpaywall": _fetch_pool.submit(_unpaywall_locations, doi) if doi else None,
    }

    def found(name):
        try:
            return lookups[name].result() if lookups[name] else None
        except Exception:
            return None

    located = [(location.get('source') or 'metadata', location['url'], record_ops.normalize_version(location.get('version')))
               for location in paper_info.get('full_text_locations') or [] if location.get('url')]
    if oa_url:
        located.append(("open_access", oa_url, _version_of(oa_url)))
    if arxiv_id:
        located.append(("arxiv", f"https://arxiv.org/pdf/{arxiv_id}.pdf", "preprint"))
    if url := found("preprint"):
        located.append(("preprint", url, "preprint"))
    for location in found('repositories') or []:
        name, url, *declared = location
        located.append((name, url, record_ops.normalize_version(declared[0]) if declared else _version_of(url)))
    located += [("unpaywall", url, version) for url, version in found("unpaywall") or []]
    seen, unique = {}, []
    for source, url, version in located:
        if _version_of(url) == 'preprint':
            version = 'preprint'
        key = url.rstrip('/').removesuffix('.pdf')
        if key in seen:
            index = seen[key]
            if VERSIONS.index(version) < VERSIONS.index(unique[index][2]):
                unique[index] = (source, url, version)
        else:
            seen[key] = len(unique)
            unique.append((source, url, version))
    return unique


def _race(jobs: list, save_path: str, filename: str) -> dict | None:
    """Run the downloads side by side and keep one copy: the best version that arrives.

    Each new copy lands under a request-owned staging name. The selected copy
    becomes an immutable artifact; late alternatives clean up only their own
    staging files. Existing local copies are never overwritten or removed.
    """
    if not jobs:
        return None
    pacing.checkpoint()
    jobs = [_Copy(*job) for job in jobs]
    rank = {job: (VERSIONS.index(job[1]), i) for i, job in enumerate(jobs)}
    ordered = sorted(jobs, key=rank.get)
    request = uuid.uuid4().hex
    names = {job: f".{filename}.{request}.{i}.candidate" for i, job in enumerate(ordered)}
    best, best_at = None, None
    for job in ordered:
        if job.source == 'cache' and (path := job.fetch(save_path, names[job])):
            if best is None or rank[job] < rank[best[0]]:
                best, best_at = (job, path), time.monotonic()
    futures = {_fetch_pool.submit(copy_context().run, job.fetch, save_path, names[job]): job
               for job in ordered if job.source != 'cache'}
    pending = set(futures)
    while pending:
        timeout = None if best is None else max(0.0, best_at + UPGRADE_GRACE_S - time.monotonic())
        done, pending = wait(pending, timeout=timeout, return_when=FIRST_COMPLETED)
        for future in done:
            try:
                path = future.result()
            except Exception:
                path = None
            job = futures[future]
            if path and (best is None or rank[job] < rank[best[0]]):
                best = (job, path)
                best_at = best_at or time.monotonic()
        if best and (not done or all(rank[futures[f]] > rank[best[0]] for f in pending)):
            break
    winner_path = best[1] if best else None

    def discard(future):
        try:
            path = future.result()
        except Exception:
            return
        owned = str(Path(save_path) / names[futures[future]])
        if path and path != winner_path and str(path) == owned:
            Path(path).unlink(missing_ok=True)

    for future in futures:
        future.cancel()
        future.add_done_callback(discard)
    if best is None:
        return None
    job, path = best
    if job.source == 'cache':
        return {'source': 'cache', 'version': job.version, 'file_path': path}
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(DOWNLOAD_CHUNK_SIZE), b''):
            digest.update(chunk)
    checksum = digest.hexdigest()
    stem = Path(filename).stem
    final = Path(save_path) / f'{stem}.{job.version}.{checksum[:12]}.{request[:8]}.pdf'
    os.replace(path, final)
    return {'source': job.source, 'version': job.version, 'file_path': str(final),
            'url_digest': job.url_digest, 'sha256': checksum}


def download_paper(paper_info: dict, save_path: str, *, temporary: bool = False, retained=()) -> dict:
    """Prefer a version of record and retain older copies as separate artifacts.

    Temporary reads can reuse an explicitly retained copy; they never retain a
    new PDF or create a library entry. Declared versions are preferred over
    guesses from a repository hostname.
    Batch owners can supply their verified retained receipts without indexing
    them in the personal library; version selection still lives here.
    """
    save_path = str(Path(save_path).expanduser().resolve())
    filename = _pdf_filename(paper_info)
    Path(save_path).mkdir(parents=True, exist_ok=True)
    expected = paper_info.get('_requested_copy_url')
    if paper_info.get('_requested_arxiv'):
        expected = f'https://arxiv.org/pdf/{paper_info["_requested_arxiv"]}.pdf'
    if not expected and paper_info.get('record_type') not in (None, 'paper'):
        expected = paper_info.get('open_access_url')
    expected_digest = hashlib.sha256(expected.encode()).hexdigest() if expected else None
    copies = [{**copy, 'version': record_ops.normalize_version(copy.get('version'))}
              for copy in [*_retained_copies(paper_info), *retained]]
    copies = [copy for copy in copies if (temporary or Path(copy['file_path']).parent == Path(save_path))
              and _cached_pdf(str(Path(copy['file_path']).parent), Path(copy['file_path']).name)
              and (not expected_digest or copy.get('url_digest') == expected_digest)]
    # Older installs have no copy receipt. Keep their file, with an unknown version.
    if not expected_digest:
        directories = list(dict.fromkeys([save_path, *([config.DOWNLOAD_DIR] if temporary else [])]))
        for directory in directories:
            if path := _cached_pdf(directory, filename):
                copies.append({'file_path': path, 'source': 'cache', 'version': 'unknown'})
    copies.sort(key=lambda copy: VERSIONS.index(record_ops.normalize_version(copy.get('version'))))
    fallback = copies[0] if copies else None
    wants_published = record_ops.publication_version(paper_info) == 'published'
    proxy_notes = []

    def remember(copy):
        if temporary or copy.get('source') == 'cache':
            return
        from . import knowledge_base as kb
        try:
            kb.get_store().remember_pdf_copy(_copy_keys(paper_info), copy)
        except Exception as error:
            proxy_notes.append(f'PDF saved; provenance index unavailable ({type(error).__name__}).')

    def finish(copy):
        version = copy.get('version', 'unknown')
        message = (f'Downloaded the {version} version via {copy["source"]}.' if version != 'unknown'
                   else f'Saved a readable copy via {copy["source"]}.')
        if copy['source'] == 'cache':
            message = 'Using the existing local PDF.'
        return {'success': True, 'file_path': copy['file_path'], 'source': copy['source'],
                'version': version, 'message': ' '.join([message, *proxy_notes])}

    if fallback and (fallback['version'] == 'published' or not wants_published or expected):
        return finish({**fallback, 'source': 'cache'})
    doi = paper_info.get('external_ids', {}).get('DOI', '')
    located = _locate(paper_info, doi)
    jobs = [_Copy(source, version, lambda folder, name, url=url: _try_download(url, folder, name),
                  hashlib.sha256(url.encode()).hexdigest())
            for source, url, version in located
            if not fallback or VERSIONS.index(version) < VERSIONS.index(fallback['version'])]
    if fallback:
        jobs.insert(0, _Copy('cache', fallback['version'], lambda folder, name: fallback['file_path']))
    won = _race(jobs, save_path, filename)
    if won:
        remember(won)
        if won['version'] == 'published' or expected:
            return finish(won)

    def institutional(upgrade):
        if not doi:
            return []
        routes = [_Copy('elsevier', 'published', lambda folder, name: _try_elsevier_api(doi, folder, name),
                        hashlib.sha256(f'https://doi.org/{doi}'.encode()).hexdigest())]
        try:
            pacing.wait("library-proxy", max_wait=0 if upgrade else 60)
            role = record_ops.publication_version(paper_info)
            routes.append(_Copy('library_proxy', role,
                lambda folder, name: _try_ezproxy(doi, folder, name, proxy_notes), routes[0].url_digest))
        except pacing.Paced as paced:
            proxy_notes.append(f'Library proxy skipped: {paced}.')
        return routes

    # A free manuscript must not prevent an authorized upgrade when a free VoR failed.
    previous = won
    upgrades = institutional(upgrade=bool(previous))
    if access.available() and (not previous or doi):
        host_locations = [(url, version) for _, url, version in located]
        host_locations += [(url, 'published' if doi and wants_published else _version_of(url))
                           for url in (f'https://doi.org/{doi}' if doi else '', paper_info.get('url', '')) if url]
        urls = {}
        for url, version in sorted(host_locations, key=lambda entry: VERSIONS.index(entry[1])):
            urls.setdefault(url, version)
        for url, role in urls.items():
            def host_copy(folder, name, url=url):
                with tempfile.TemporaryDirectory(prefix='scholar-host-') as directory:
                    payload = Path(directory) / 'paper.pdf'
                    try:
                        downloaded = access.download(url, payload)
                    except access.AccessError as error:
                        proxy_notes.append(f'Host access unavailable ({error.code}).')
                        return None
                    if not downloaded:
                        return None
                    with payload.open('rb') as stream:
                        return _atomic_pdf_chunks(iter(lambda: stream.read(DOWNLOAD_CHUNK_SIZE), b''), folder, name)
            upgrades.append(_Copy('host_access', role, host_copy, hashlib.sha256(url.encode()).hexdigest()))
    if previous:
        upgrades.insert(0, _Copy('cache', previous['version'], lambda folder, name: previous['file_path']))
    better = _race(upgrades, save_path, filename)
    if not better and config.SCIHUB_ENABLED and doi:
        better = _race([_Copy('scihub', 'published' if wants_published else 'unknown',
                              lambda folder, name: _try_scihub(doi, folder, name))], save_path, filename)
    if better:
        if previous and better['source'] == 'cache':
            better = previous
        else:
            remember(better)
        return finish(better)
    if previous:
        return finish(previous)

    # 9. Return useful identities and landing pages when no PDF was resolved.
    s2_url = paper_info.get("url", "")
    doi_link = f" or via DOI: https://doi.org/{doi}" if doi else ""
    message = (f"Could not download PDF (may not be open access). "
               f"Try: {s2_url}{doi_link}")
    message += ''.join(f' {note}' for note in proxy_notes)
    prefix, _ = _library_proxy()
    if prefix and doi:
        # Opens the paper through the library login when clicked in a browser.
        message += f" Library proxy link: {_proxied(prefix, f'https://doi.org/{doi}')}"
    result = {
        "success": False, "file_path": None, "source": "none",
        "message": message,
    }
    return result
