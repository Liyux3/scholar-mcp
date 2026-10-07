import os
import re
import hashlib
import tempfile
import time
from html import unescape
from http.cookiejar import Cookie
from itertools import chain
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FutureTimeoutError
from pathlib import Path
from urllib.parse import quote, urljoin, urlparse, urlunparse

import httpx
from bs4 import BeautifulSoup

from . import __version__, config, sources

DOWNLOAD_TIMEOUT = 60
DOWNLOAD_CHUNK_SIZE = 256 * 1024
PDF_HEADER_SCAN_BYTES = 1024
PDF_PROBE_TIMEOUT = 12
PDF_PROBE_BUDGET = 15
PDF_PROBE_WORKERS = 4
USER_AGENT = f"scholar-mcp/{__version__} (academic research tool)"
_pdf_probe_pool = ThreadPoolExecutor(max_workers=PDF_PROBE_WORKERS)

# Institutional proxy. Off unless its login prefix and a session are configured.
# Short on purpose: an expired session or an uncovered paper is the common
# case, and it must not slow the rest of the download chain.
PROXY_TIMEOUT = 12
PROXY_BUDGET = 45
PROXY_MAX_REQUESTS = 4
# Proxies and publishers routinely reject non-browser agents outright.
BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")


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


def _atomic_pdf_bytes(content: bytes, save_path: str, filename: str) -> str | None:
    """Atomically persist an already-buffered PDF payload."""
    return _atomic_pdf_chunks((content,), save_path, filename)


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
        with httpx.Client(timeout=DOWNLOAD_TIMEOUT, follow_redirects=True) as client:
            with client.stream("GET", url, headers=headers) as response:
                response.raise_for_status()
                return _atomic_pdf_chunks(response.iter_bytes(chunk_size=DOWNLOAD_CHUNK_SIZE),
                                          save_path, filename)
    except (httpx.HTTPError, OSError):
        return None


def _probe_pdf(url: str) -> bool:
    """Read only the PDF prefix so dead candidates do not serialize latency."""
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/pdf,*/*;q=0.8",
        "Range": f"bytes=0-{PDF_HEADER_SCAN_BYTES - 1}",
    }
    try:
        with httpx.Client(timeout=PDF_PROBE_TIMEOUT, follow_redirects=True) as client:
            with client.stream("GET", url, headers=headers) as response:
                response.raise_for_status()
                prefix = bytearray()
                for chunk in response.iter_bytes(chunk_size=PDF_HEADER_SCAN_BYTES):
                    prefix.extend(chunk[: PDF_HEADER_SCAN_BYTES - len(prefix)])
                    if len(prefix) >= PDF_HEADER_SCAN_BYTES:
                        break
        return b"%PDF-" in prefix
    except (httpx.HTTPError, OSError):
        return False


def _prioritize_pdf_candidates(
    candidates: list[tuple[str, str]],
    budget_s: float = PDF_PROBE_BUDGET,
) -> list[tuple[str, str]]:
    """Probe candidates concurrently, retaining source-priority ordering.

    Confirmed PDFs move to the front. Candidates that reject range requests or
    time out remain as ordered fallbacks, so probing improves latency without
    reducing the original resolution coverage.
    """
    if len(candidates) < 2:
        return candidates
    futures = {_pdf_probe_pool.submit(_probe_pdf, url): url for _, url in candidates}
    confirmed = set()
    try:
        for future in as_completed(futures, timeout=budget_s):
            if future.result():
                confirmed.add(futures[future])
    except FutureTimeoutError:
        pass
    finally:
        for future in futures:
            if not future.done():
                future.cancel()
    return (
        [candidate for candidate in candidates if candidate[1] in confirmed]
        + [candidate for candidate in candidates if candidate[1] not in confirmed]
    )


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
    jar = httpx.Cookies()
    expired = 0
    now = time.time()
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip("\r\n")
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
            comment_url=None, rest={}, rfc2109=False,
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
_LOGIN_PATH = re.compile(r"/(?:login|signin|saml\d*|shibboleth|idp)(?:[/.?]|$)", re.I)


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
    if "sciencedirect" in host:
        match = re.match(r"/science/article/(?:abs/)?pii/([A-Za-z0-9]+)", parts.path)
        if match:
            return urlunparse(parts._replace(path=f"/science/article/pii/{match.group(1)}/pdfft",
                                             params="", query="isDTMRedir=true&download=true",
                                             fragment=""))
    return None


def _is_login_page(url: str, html: str, proxy_host: str) -> bool:
    """An expired proxy session ends on the library or identity-provider login."""
    parts = urlparse(url)
    if _LOGIN_PATH.search(parts.path or ""):
        return True
    return ((parts.hostname or "").lower() == proxy_host
            and re.search(r"""type\s*=\s*["']?password""", html, re.I) is not None)


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
    deadline = time.monotonic() + PROXY_BUDGET
    headers = {"User-Agent": BROWSER_UA, "Accept": "application/pdf,text/html;q=0.9,*/*;q=0.8"}

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
                    f"page). Log in at {proxy_host} in your browser, export cookies.txt "
                    "again, and retry.")
                return None
            if response.status_code >= 400:
                reasons.append(f"HTTP {response.status_code} from "
                               f"{urlparse(final_url).hostname}")
                continue
            pointer = _landing_pdf_url(final_url, html)
            if pointer and pointer not in seen:
                queue.insert(0, pointer)
            else:
                reasons.append(f"no PDF at {urlparse(final_url).hostname}")

    notes.append("Library proxy did not return a PDF ("
                 + ("; ".join(dict.fromkeys(reasons)) or "time budget used up")
                 + "); the library may not subscribe to this paper.")
    return None


def _try_unpaywall(doi: str) -> str | None:
    """Query Unpaywall API for legal open access PDF URL. Requires OPENALEX_EMAIL."""
    email = config.OPENALEX_EMAIL
    if not email:
        return None
    try:
        r = httpx.get(f"https://api.unpaywall.org/v2/{doi}",
                      params={"email": email}, timeout=10)
        if r.status_code == 200:
            data = r.json()
            best = data.get("best_oa_location") or {}
            pdf_url = best.get("url_for_pdf")
            if pdf_url:
                return pdf_url
            landing = best.get("url_for_landing_page")
            if landing:
                return landing
    except Exception:
        pass
    return None


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


def download_paper(paper_info: dict, save_path: str) -> dict:
    """Smart download chain:
    direct record URL -> canonical archive -> registered OA repositories ->
    Unpaywall -> configured library proxy -> optional Sci-Hub.
    """
    save_path = str(Path(save_path).expanduser().resolve())
    filename = _pdf_filename(paper_info)
    cached = _cached_pdf(save_path, filename)
    if cached:
        return {"success": True, "file_path": cached, "source": "cache",
                "message": "Using the existing local PDF."}

    # 1. S2 open access
    oa_url = paper_info.get("open_access_url")
    if oa_url:
        result = _try_download(oa_url, save_path, filename)
        if result:
            return {"success": True, "file_path": result, "source": "open_access",
                    "message": "Downloaded via open access URL."}

    ext_ids = paper_info.get("external_ids", {})
    doi = ext_ids.get("DOI", "")

    # 2. arXiv
    arxiv_id = ext_ids.get("ArXiv") or ext_ids.get("ArXivId")
    if arxiv_id:
        url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"
        result = _try_download(url, save_path, filename)
        if result:
            return {"success": True, "file_path": result, "source": "arxiv",
                    "message": f"Downloaded from arXiv ({arxiv_id})."}

    # 3. Canonical preprint servers (bioRxiv, medRxiv, SSRN, OSF, ChemRxiv, etc.)
    preprint_url = _resolve_preprint_pdf(doi, oa_url)
    if preprint_url:
        result = _try_download(preprint_url, save_path, filename)
        if result:
            return {"success": True, "file_path": result, "source": "preprint",
                    "message": "Downloaded from preprint server."}

    # 4. PubMed Central, when identity resolution already supplied the PMCID.
    pmcid = ext_ids.get("PubMedCentral") or ext_ids.get("PMC")
    if pmcid:
        pmc_url = f"https://europepmc.org/articles/{pmcid}?pdf=render"
        result = _try_download(pmc_url, save_path, filename)
        if result:
            return {"success": True, "file_path": result, "source": "europepmc",
                    "message": f"Downloaded from Europe PMC ({pmcid})."}

    # 5. Registered OA repositories. Resolution happens in parallel; every
    # candidate is streamed through the same PDF validation and atomic write.
    repository_candidates = sources.resolve_pdf_candidates(paper_info)
    for source_name, candidate_url in _prioritize_pdf_candidates(repository_candidates):
        result = _try_download(candidate_url, save_path, filename)
        if result:
            return {"success": True, "file_path": result, "source": source_name,
                    "message": f"Downloaded via {source_name}."}

    # 6. Unpaywall (DOI-level OA discovery)
    if doi:
        unpaywall_url = _try_unpaywall(doi)
        if unpaywall_url:
            result = _try_download(unpaywall_url, save_path, filename)
            if result:
                return {"success": True, "file_path": result, "source": "unpaywall",
                        "message": "Downloaded via Unpaywall (legal open access)."}

    # 7. Institutional proxy, if a login prefix and session cookies are
    # configured. Tried before Sci-Hub because it is the licensed route to
    # the same paper. Why it failed is kept for the final message.
    proxy_notes: list[str] = []
    if doi:
        result = _try_ezproxy(doi, save_path, filename, proxy_notes)
        if result:
            return {"success": True, "file_path": result, "source": "library_proxy",
                    "message": f"Downloaded via institutional proxy (DOI: {doi})."}

    # 8. Sci-Hub (opt-in only)
    if config.SCIHUB_ENABLED and doi:
        result = _try_scihub(doi, save_path, filename)
        if result:
            return {"success": True, "file_path": result, "source": "scihub",
                    "message": " ".join(
                        [f"Downloaded via Sci-Hub (DOI: {doi})."] + proxy_notes)}

    # 9. Return useful identities and landing pages when no PDF was resolved.
    s2_url = paper_info.get("url", "")
    doi_link = f" or via DOI: https://doi.org/{doi}" if doi else ""
    message = (f"Could not download PDF (may not be open access). "
               f"Try: {s2_url}{doi_link}")
    prefix, _ = _library_proxy()
    if prefix and doi:
        # Opens the paper through the library login when clicked in a browser.
        message += "".join(f" {note}" for note in proxy_notes)
        message += f" Library proxy link: {_proxied(prefix, f'https://doi.org/{doi}')}"
    return {
        "success": False, "file_path": None, "source": "none",
        "message": message,
    }
