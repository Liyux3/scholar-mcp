import os
import re
import hashlib
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import httpx
from pypdf import PdfReader
from . import config
from . import sources

DOWNLOAD_TIMEOUT = 60
DOWNLOAD_CHUNK_SIZE = 256 * 1024
PDF_HEADER_SCAN_BYTES = 1024
PDF_PROBE_TIMEOUT = 12
PDF_PROBE_BUDGET = 15
PDF_PROBE_WORKERS = 4
DEFAULT_READ_PAGE_COUNT = 10
DEFAULT_READ_PAGES = f"1-{DEFAULT_READ_PAGE_COUNT}"
MAX_READ_PAGES = 20
USER_AGENT = "scholar-mcp/0.1.0 (academic research tool)"
_pdf_probe_pool = ThreadPoolExecutor(max_workers=PDF_PROBE_WORKERS)

# Institutional proxy. Off unless a session cookie is configured.
DEFAULT_PROXY_BASE = "https://eproxy.lib.hku.hk"
# Short on purpose: an expired session or an uncovered paper is the common
# case, and it must not slow the rest of the download chain.
PROXY_TIMEOUT = 12
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
    if not identifier:
        digest = hashlib.sha256(title.encode("utf-8")).hexdigest()[:12]
        safe = f"{safe or 'paper'}-{digest}"
    return f"{safe or 'paper'}.pdf"


def _cached_pdf(save_path: str, filename: str) -> str | None:
    path = Path(save_path).expanduser() / filename
    try:
        if path.is_file():
            with path.open("rb") as stream:
                if stream.read(5) == b"%PDF-":
                    return str(path)
    except OSError:
        pass
    return None


def _atomic_pdf_bytes(content: bytes, save_path: str, filename: str) -> str | None:
    """Atomically persist an already-buffered PDF payload."""
    if b"%PDF-" not in content[:PDF_HEADER_SCAN_BYTES]:
        return None

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
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        staging.replace(destination)
        return str(destination)
    except OSError:
        return None
    finally:
        staging.unlink(missing_ok=True)


def _try_download(url: str, save_path: str, filename: str) -> str | None:
    """Stream a PDF to a staging file and atomically publish it on success."""
    destination = Path(save_path).expanduser() / filename
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, staging_name = tempfile.mkstemp(
        dir=destination.parent,
        prefix=f".{destination.name}.",
        suffix=".part",
    )
    os.close(descriptor)
    staging = Path(staging_name)
    try:
        headers = {"User-Agent": USER_AGENT}
        with httpx.Client(timeout=DOWNLOAD_TIMEOUT, follow_redirects=True) as client:
            with client.stream("GET", url, headers=headers) as response:
                response.raise_for_status()
                prefix = bytearray()
                with staging.open("wb") as output:
                    for chunk in response.iter_bytes(chunk_size=DOWNLOAD_CHUNK_SIZE):
                        if not chunk:
                            continue
                        if len(prefix) < PDF_HEADER_SCAN_BYTES:
                            remaining = PDF_HEADER_SCAN_BYTES - len(prefix)
                            prefix.extend(chunk[:remaining])
                        output.write(chunk)
                    output.flush()
                    os.fsync(output.fileno())

        if b"%PDF-" not in prefix:
            return None
        staging.replace(destination)
        return str(destination)
    except (httpx.HTTPError, OSError):
        return None
    finally:
        staging.unlink(missing_ok=True)


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
    except TimeoutError:
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
    """Session cookie for the institutional proxy, if the user set one up.

    Read from LIBRARY_PROXY_COOKIE or ~/.scholar-mcp/library_cookie.txt. Kept
    out of the repo and out of any output, since it is a live credential.
    """
    cookie = os.environ.get("LIBRARY_PROXY_COOKIE", "").strip()
    if cookie:
        return cookie
    path = Path(os.path.expanduser("~/.scholar-mcp/library_cookie.txt"))
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _try_ezproxy(doi: str, save_path: str, filename: str) -> str | None:
    """Resolve a DOI through an EZproxy session, if one is configured.

    Deliberately the least persistent step in the chain. Proxy sessions expire,
    publishers vary in whether they serve a PDF or an interstitial, and many
    subscriptions simply do not cover a given paper, so a failure here is
    ordinary and must stay quiet and fast rather than slow every download.

    Only ever used for a single paper the caller already asked for. Bulk or
    automated harvesting through a library subscription breaches the terms
    every university attaches to these licences.
    """
    cookie = _library_cookie()
    base = os.environ.get("LIBRARY_PROXY_BASE", DEFAULT_PROXY_BASE).strip()
    if not cookie or not base:
        return None

    url = f"{base.rstrip('/')}/login?url=https://doi.org/{doi}"
    try:
        response = httpx.get(
            url, headers={"Cookie": cookie, "User-Agent": BROWSER_UA},
            timeout=PROXY_TIMEOUT, follow_redirects=True)
    except Exception:
        return None

    if response.status_code != 200:
        return None
    if not response.headers.get("content-type", "").lower().startswith("application/pdf"):
        # Landing page or login form rather than the file itself. Following
        # publisher-specific paths from here is where scraping would begin.
        return None

    return _atomic_pdf_bytes(response.content, save_path, filename)


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
    save_path = os.path.expanduser(save_path)
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

    # 7. Institutional proxy, if a session cookie is configured. Tried before
    # Sci-Hub because it is the licensed route to the same paper.
    if doi:
        result = _try_ezproxy(doi, save_path, filename)
        if result:
            return {"success": True, "file_path": result, "source": "library_proxy",
                    "message": f"Downloaded via institutional proxy (DOI: {doi})."}

    # 8. Sci-Hub (opt-in only)
    if config.SCIHUB_ENABLED and doi:
        result = _try_scihub(doi, save_path, filename)
        if result:
            return {"success": True, "file_path": result, "source": "scihub",
                    "message": f"Downloaded via Sci-Hub (DOI: {doi})."}

    # 9. Return useful identities and landing pages when no PDF was resolved.
    s2_url = paper_info.get("url", "")
    doi_link = f" or via DOI: https://doi.org/{doi}" if doi else ""
    return {
        "success": False, "file_path": None, "source": "none",
        "message": f"Could not download PDF (may not be open access). "
                   f"Try: {s2_url}{doi_link}",
    }


def extract_text(file_path: str, pages: str = DEFAULT_READ_PAGES) -> dict:
    """Extract one bounded, one-indexed page range from a PDF file."""
    reader = PdfReader(file_path)
    total_pages = len(reader.pages)
    match = re.fullmatch(r"\s*(\d+)(?:\s*-\s*(\d+))?\s*", pages)
    if not match:
        raise ValueError("pages must be a page number or range such as '1-10'")

    start = int(match.group(1))
    requested_end = int(match.group(2) or start)
    if start < 1 or requested_end < start:
        raise ValueError("pages must be an ascending, one-indexed range")
    if requested_end - start + 1 > MAX_READ_PAGES:
        raise ValueError(f"read at most {MAX_READ_PAGES} pages per call")
    if start > total_pages:
        raise ValueError(f"paper has {total_pages} pages; requested page {start}")

    end = min(requested_end, total_pages)

    parts = []
    for page_number in range(start, end + 1):
        page = reader.pages[page_number - 1]
        text = page.extract_text()
        if text:
            parts.append(f"--- Page {page_number} ---\n{text}")

    result = {
        "content": "\n\n".join(parts)
        if parts else "(No text could be extracted from this PDF.)",
        "pages": f"{start}-{end}" if start != end else str(start),
        "total_pages": total_pages,
    }
    if end < total_pages:
        next_end = min(end + DEFAULT_READ_PAGE_COUNT, total_pages)
        result["next_pages"] = f"{end + 1}-{next_end}"
    return result
