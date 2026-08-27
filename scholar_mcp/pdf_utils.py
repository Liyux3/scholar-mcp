import os
import re
import hashlib
import io
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import httpx
import pypdfium2 as pdfium
from PIL import Image, ImageChops
from pdftext.extraction import dictionary_output
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
PDF_RENDER_SCALE = 2.5
USER_AGENT = "scholar-mcp/0.1.0 (academic research tool)"
_pdf_probe_pool = ThreadPoolExecutor(max_workers=PDF_PROBE_WORKERS)
_pdfium_lock = threading.Lock()

_CAPTION_RE = re.compile(
    r"^(?P<kind>Figure|Fig\.?|Table)\s*"
    r"(?P<label>(?:[A-Za-z]\.?)?\d+(?:\.\d+)*(?:[A-Za-z])?)\s*"
    r"[:.\-]\s*(?P<caption>.+)$",
    re.IGNORECASE | re.DOTALL,
)
_NUMBERED_HEADING_RE = re.compile(
    r"^(?P<number>(?:[A-Z]\.)?\d+(?:\.\d+)*)\s+(?P<title>\S.+)$"
)
_NAMED_HEADINGS = {
    "abstract", "acknowledgment", "acknowledgments", "acknowledgement",
    "acknowledgements", "appendix", "conclusion", "conclusions",
    "limitations", "references", "bibliography",
}

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


def _parse_page_range(pages: str, total_pages: int) -> tuple[int, int]:
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

    return start, min(requested_end, total_pages)


def _lines_text(lines_data: list[dict]) -> str:
    lines = []
    for line in lines_data:
        text = "".join(span.get("text", "") for span in line.get("spans", []))
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            continue
        if lines and lines[-1].endswith("-") and text[:1].islower():
            lines[-1] = lines[-1][:-1] + text
        else:
            lines.append(text)
    return " ".join(lines)


def _block_text(block: dict) -> str:
    return _lines_text(block.get("lines", []))


def _weighted_font_size(block: dict) -> float:
    sizes = []
    for line in block.get("lines", []):
        for span in line.get("spans", []):
            text = span.get("text", "").strip()
            size = float((span.get("font") or {}).get("size") or 0)
            if text and size:
                sizes.append((size, len(text)))
    if not sizes:
        return 0.0
    sizes.sort()
    midpoint = sum(weight for _, weight in sizes) / 2
    cumulative = 0
    for size, weight in sizes:
        cumulative += weight
        if cumulative >= midpoint:
            return size
    return sizes[-1][0]


def _body_font_size(layout_pages: list[dict]) -> float:
    weighted = []
    for page in layout_pages:
        for block in page.get("blocks", []):
            size = _weighted_font_size(block)
            text = _block_text(block)
            if size and len(text) >= 40:
                weighted.append((size, min(len(text), 500)))
    if not weighted:
        return 10.0
    weighted.sort()
    midpoint = sum(weight for _, weight in weighted) / 2
    cumulative = 0
    for size, weight in weighted:
        cumulative += weight
        if cumulative >= midpoint:
            return size
    return weighted[-1][0]


def _caption(block: dict, page_number: int) -> dict | None:
    lines = block.get("lines", [])
    for index, line in enumerate(lines):
        text = _lines_text([line])
        match = _CAPTION_RE.match(text)
        if not match:
            continue
        kind = "figure" if match.group("kind").lower().startswith("fig") else "table"
        prefix = "Figure" if kind == "figure" else "Table"
        label = match.group("label")
        consumed = [line]
        bbox = [float(value) for value in line.get("bbox", [0, 0, 0, 0])]
        font_size = _weighted_font_size({"lines": [line]})
        previous_bbox = bbox
        next_index = index + 1
        while next_index < len(lines):
            next_line = lines[next_index]
            next_text = _lines_text([next_line])
            next_bbox = [
                float(value) for value in next_line.get("bbox", [0, 0, 0, 0])
            ]
            gap = next_bbox[1] - previous_bbox[3]
            same_column = _horizontal_overlap(previous_bbox, tuple(next_bbox)) >= 0.7
            next_size = _weighted_font_size({"lines": [next_line]})
            same_font = font_size and abs(next_size - font_size) <= max(0.8, font_size * 0.08)
            if not (
                next_text
                and -2 <= gap <= max(4, font_size * 0.55)
                and same_column
                and same_font
            ):
                break
            consumed.append(next_line)
            bbox = [
                min(bbox[0], next_bbox[0]), min(bbox[1], next_bbox[1]),
                max(bbox[2], next_bbox[2]), max(bbox[3], next_bbox[3]),
            ]
            previous_bbox = next_bbox
            next_index += 1
        caption_text = _lines_text(consumed)
        caption_match = _CAPTION_RE.match(caption_text)
        remainder = lines[:index] + lines[next_index:]
        return {
            "selector": f"{prefix} {label}",
            "page": page_number,
            "kind": kind,
            "caption": caption_match.group("caption").strip(),
            "_bbox": bbox,
            "_remainder_text": _lines_text(remainder),
        }
    return None


def _find_captions(layout_pages: list[dict]) -> list[dict]:
    found = []
    for page in layout_pages:
        page_number = int(page.get("page", 0)) + 1
        blocks = page.get("blocks", [])
        index = 0
        while index < len(blocks):
            block = blocks[index]
            item = _caption(block, page_number)
            if item:
                item["_anchor_bbox"] = list(item["_bbox"])
                item["_continuation_bboxes"] = []
                font_size = _weighted_font_size(block)
                previous_bbox = item["_bbox"]
                next_index = index + 1
                while next_index < len(blocks):
                    next_block = blocks[next_index]
                    next_text = _block_text(next_block)
                    next_bbox = [
                        float(value) for value in next_block.get("bbox", [0, 0, 0, 0])
                    ]
                    gap = next_bbox[1] - previous_bbox[3]
                    same_column = _horizontal_overlap(previous_bbox, tuple(next_bbox)) >= 0.7
                    next_size = _weighted_font_size(next_block)
                    same_font = font_size and abs(next_size - font_size) <= max(0.8, font_size * 0.08)
                    if not (
                        next_text
                        and -1 <= gap <= max(4, font_size * 0.55)
                        and same_column
                        and same_font
                        and len(item["caption"]) + len(next_text) <= 1200
                    ):
                        break
                    item["caption"] += " " + next_text
                    item["_continuation_bboxes"].append(next_bbox)
                    item["_bbox"] = [
                        min(item["_bbox"][0], next_bbox[0]),
                        min(item["_bbox"][1], next_bbox[1]),
                        max(item["_bbox"][2], next_bbox[2]),
                        max(item["_bbox"][3], next_bbox[3]),
                    ]
                    previous_bbox = next_bbox
                    next_index += 1
                found.append(item)
                index = next_index
                continue
            index += 1
    return found


def _normalize_selector(value: str) -> str:
    value = re.sub(r"^fig\.?(?=\s*\w)", "figure", value.strip(), flags=re.I)
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _layout_pages(file_path: str, page_numbers: list[int]) -> list[dict]:
    with _pdfium_lock:
        return dictionary_output(
            file_path,
            sort=False,
            page_range=[number - 1 for number in page_numbers],
            keep_chars=False,
            workers=1,
        )


def _locate_visual(file_path: str, selector: str, total_pages: int) -> dict:
    page_match = re.fullmatch(r"\s*page\s+(\d+)\s*", selector, re.I)
    if page_match:
        page_number = int(page_match.group(1))
        if not 1 <= page_number <= total_pages:
            raise ValueError(f"paper has {total_pages} pages; requested page {page_number}")
        return {
            "selector": f"page {page_number}", "page": page_number,
            "kind": "page", "caption": "", "_bbox": None,
        }

    normalized = _normalize_selector(selector)
    for chunk_start in range(1, total_pages + 1, MAX_READ_PAGES):
        chunk_end = min(chunk_start + MAX_READ_PAGES - 1, total_pages)
        layout = _layout_pages(file_path, list(range(chunk_start, chunk_end + 1)))
        for caption in _find_captions(layout):
            if _normalize_selector(caption["selector"]) == normalized:
                return caption
    raise ValueError(
        f"visual '{selector}' was not found; use a selector returned by read_paper"
    )


def _horizontal_overlap(first: list[float], second: tuple[float, ...]) -> float:
    overlap = max(0.0, min(first[2], second[2]) - max(first[0], second[0]))
    width = max(1.0, min(first[2] - first[0], second[2] - second[0]))
    return overlap / width


def _clean_table_rows(rows: list[list[str | None]]) -> list[list[str]]:
    cleaned = []
    for row in rows:
        values = [re.sub(r"\s+", " ", str(cell or "")).strip() for cell in row]
        if any(values):
            cleaned.append(values)
    if not cleaned:
        return []
    width = max(len(row) for row in cleaned)
    cleaned = [row + [""] * (width - len(row)) for row in cleaned]
    keep = [index for index in range(width) if any(row[index] for row in cleaned)]
    return [[row[index] for index in keep] for row in cleaned]


def _markdown_table(rows: list[list[str]]) -> str:
    escaped = [
        [cell.replace("|", "\\|").replace("\n", "<br>") for cell in row]
        for row in rows
    ]
    header = escaped[0]
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join("---" for _ in header) + " |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in escaped[1:])
    return "\n".join(lines)


def _horizontal_table_region(page, caption_bbox: list[float]) -> tuple[list[float], str] | None:
    lines = []
    for item in [*page.lines, *page.rects]:
        x0 = float(item.get("x0", 0))
        x1 = float(item.get("x1", 0))
        top = float(item.get("top", 0))
        bottom = float(item.get("bottom", top))
        if x1 - x0 >= page.width * 0.25 and bottom - top <= 3:
            if abs(top - caption_bbox[3]) <= page.height * 0.7:
                lines.append((top, x0, x1))
    if len(lines) < 2:
        return None
    lines.sort()
    clusters = []
    current = [lines[0]]
    for line in lines[1:]:
        if line[0] - current[-1][0] <= page.height * 0.14:
            current.append(line)
        else:
            if len(current) >= 2:
                clusters.append(current)
            current = [line]
    if len(current) >= 2:
        clusters.append(current)
    if not clusters:
        return None

    def distance(cluster):
        top = cluster[0][0]
        bottom = cluster[-1][0]
        return min(abs(top - caption_bbox[3]), abs(caption_bbox[1] - bottom))

    cluster = min(clusters, key=distance)
    bbox = [
        min(line[1] for line in cluster), cluster[0][0],
        max(line[2] for line in cluster), cluster[-1][0],
    ]
    text = page.crop(tuple(bbox)).extract_text(
        layout=True,
        x_tolerance=1,
        x_density=7.25,
        y_density=13,
    ) or ""
    text = "\n".join(line.rstrip() for line in text.splitlines()).strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    if len(text.splitlines()) < 2 or not re.search(r"\S {2,}\S", text):
        return None
    return bbox, f"```text\n{text}\n```"


def _structured_tables(file_path: str, captions: list[dict]) -> dict[str, dict]:
    table_captions = [item for item in captions if item["kind"] == "table"]
    if not table_captions:
        return {}
    import pdfplumber

    matched = {}
    with pdfplumber.open(file_path) as document:
        by_page = {}
        for caption in table_captions:
            by_page.setdefault(caption["page"], []).append(caption)
        for page_number, page_captions in by_page.items():
            page = document.pages[page_number - 1]
            candidates = page.find_tables()
            used = set()
            for caption in page_captions:
                bbox = caption["_bbox"]
                ranked = []
                for index, table in enumerate(candidates):
                    if index in used:
                        continue
                    overlap = _horizontal_overlap(bbox, table.bbox)
                    gap = min(
                        abs(table.bbox[1] - bbox[3]),
                        abs(bbox[1] - table.bbox[3]),
                    )
                    direction_penalty = page.height * 0.05 if table.bbox[1] < bbox[1] else 0
                    score = gap + (1 - overlap) * page.height * 0.25 + direction_penalty
                    ranked.append((score, index, table))
                if ranked:
                    score, index, table = min(ranked, key=lambda item: item[0])
                    rows = _clean_table_rows(table.extract())
                    if (
                        score <= page.height * 0.4
                        and len(rows) >= 2
                        and len(rows[0]) >= 2
                    ):
                        used.add(index)
                        matched[caption["selector"]] = {
                            "page": page_number,
                            "bbox": [float(value) for value in table.bbox],
                            "format": "markdown",
                            "markdown": _markdown_table(rows),
                        }
                        continue
                spatial = _horizontal_table_region(page, bbox)
                if spatial:
                    table_bbox, table_text = spatial
                    matched[caption["selector"]] = {
                        "page": page_number,
                        "bbox": table_bbox,
                        "format": "spatial_text",
                        "markdown": table_text,
                    }
    return matched


def _intersects(first: list[float], second: list[float]) -> bool:
    return not (
        first[2] <= second[0] or second[2] <= first[0]
        or first[3] <= second[1] or second[3] <= first[1]
    )


def _expanded_intersects(first: list[float], second: list[float], pad: float = 14) -> bool:
    expanded = [first[0] - pad, first[1] - pad, first[2] + pad, first[3] + pad]
    return _intersects(expanded, second)


def _union_bbox(boxes: list[list[float]]) -> list[float]:
    return [
        min(box[0] for box in boxes), min(box[1] for box in boxes),
        max(box[2] for box in boxes), max(box[3] for box in boxes),
    ]


def _figure_region(file_path: str, target: dict) -> list[float] | None:
    import pdfplumber

    with pdfplumber.open(file_path) as document:
        page = document.pages[target["page"] - 1]
        caption = target["_bbox"]
        center = (caption[0] + caption[2]) / 2
        if center < page.width * 0.42:
            left, right = page.width * 0.02, page.width * 0.54
        elif center > page.width * 0.58:
            left, right = page.width * 0.46, page.width * 0.98
        else:
            left, right = page.width * 0.02, page.width * 0.98
        above = caption[1] >= page.height * 0.2
        search = [
            left,
            max(0, caption[1] - page.height * 0.65) if above else caption[3],
            right,
            caption[1] if above else min(page.height, caption[3] + page.height * 0.65),
        ]
        objects = []
        for item in [*page.images, *page.rects, *page.curves, *page.lines]:
            bbox = [
                float(item.get("x0", 0)),
                float(item.get("top", 0)),
                float(item.get("x1", 0)),
                float(item.get("bottom", item.get("top", 0))),
            ]
            if bbox[2] <= bbox[0]:
                bbox[2] = bbox[0] + 0.5
            if bbox[3] <= bbox[1]:
                bbox[3] = bbox[1] + 0.5
            if _intersects(bbox, search):
                objects.append([
                    max(bbox[0], search[0]), max(bbox[1], search[1]),
                    min(bbox[2], search[2]), min(bbox[3], search[3]),
                ])
        if not objects:
            return None

        components = []
        for bbox in objects:
            overlapping = [
                index for index, component in enumerate(components)
                if _expanded_intersects(component, bbox)
            ]
            if not overlapping:
                components.append(bbox)
                continue
            merged = _union_bbox([bbox, *(components[index] for index in overlapping)])
            components = [
                component for index, component in enumerate(components)
                if index not in overlapping
            ]
            components.append(merged)

        def distance(component: list[float]) -> float:
            vertical = (
                abs(caption[1] - component[3])
                if above else abs(component[1] - caption[3])
            )
            horizontal = max(
                0,
                abs((component[0] + component[2]) / 2 - center)
                - (caption[2] - caption[0]) / 2,
            )
            return vertical + horizontal * 0.25

        viable = [
            component for component in components
            if (component[2] - component[0]) * (component[3] - component[1])
            >= page.width * page.height * 0.002
        ]
        if not viable:
            return None
        region = min(viable, key=distance)
        if distance(region) > page.height * 0.25:
            return None
        return _union_bbox([region, caption])


def _page_title(page: dict, page_height: float) -> tuple[str, set[tuple[float, ...]]]:
    candidates = []
    for block in page.get("blocks", []):
        for line in block.get("lines", []):
            text = _lines_text([line])
            size = _weighted_font_size({"lines": [line]})
            bbox = tuple(float(value) for value in line.get("bbox", []))
            if bbox and not 4 <= size <= 48:
                size = bbox[3] - bbox[1]
            if (
                text
                and not re.match(r"^(?:arXiv:|Published as|Proceedings of)", text, re.I)
                and 4 <= size <= 48
                and bbox
                and bbox[1] < page_height * 0.25
            ):
                candidates.append((bbox[1], bbox[0], size, bbox, text))
    if not candidates:
        return "", set()
    largest = max(item[2] for item in candidates)
    selected = sorted(
        (item for item in candidates if item[2] >= largest * 0.9 and len(item[4]) <= 240),
        key=lambda item: (item[0], item[1]),
    )
    if not selected:
        return "", set()
    title_lines = [selected[0]]
    for item in selected[1:]:
        previous = title_lines[-1]
        gap = item[0] - previous[3][3]
        if -2 <= gap <= max(10, previous[2] * 0.9):
            title_lines.append(item)
        else:
            break
    return (
        " ".join(item[4] for item in title_lines),
        {item[3] for item in title_lines},
    )


def _heading(text: str, font_size: float, body_size: float) -> str | None:
    normalized = text.strip().rstrip(":").lower()
    if normalized in _NAMED_HEADINGS:
        return f"## {text}"
    match = _NUMBERED_HEADING_RE.match(text)
    if match and len(text) <= 180:
        number = match.group("number")
        level = min(4, 2 + number.count("."))
        return f"{'#' * level} {text}"
    if len(text) <= 120 and font_size >= body_size * 1.18:
        return f"## {text}"
    return None


def _markdown_content(
    layout_pages: list[dict],
    captions: list[dict],
    tables: dict[str, dict],
) -> tuple[str, list[dict], list[dict]]:
    body_size = _body_font_size(layout_pages)
    caption_by_page_bbox = {
        (item["page"], tuple(item.get("_anchor_bbox", item["_bbox"]))): item
        for item in captions
    }
    caption_continuations = {
        (item["page"], tuple(bbox))
        for item in captions
        for bbox in item.get("_continuation_bboxes", [])
    }
    parts = []
    visuals = []
    table_index = []
    for page in layout_pages:
        page_number = int(page.get("page", 0)) + 1
        page_height = float((page.get("bbox") or [0, 0, 0, 792])[3])
        parts.append(f"--- Page {page_number} ---")
        page_table_bboxes = [
            entry["bbox"] for entry in tables.values()
            if entry["page"] == page_number
        ]
        title_text, title_lines = (
            _page_title(page, page_height) if page_number == 1 else ("", set())
        )
        title_emitted = False
        for block in page.get("blocks", []):
            text = _block_text(block)
            if not text:
                continue
            bbox = [float(value) for value in block.get("bbox", [0, 0, 0, 0])]
            if re.fullmatch(r"\d+", text) and bbox[1] > page_height * 0.9:
                continue
            bbox_key = (page_number, tuple(bbox))
            if bbox_key in caption_continuations:
                continue
            block_title_lines = [
                line for line in block.get("lines", [])
                if tuple(float(value) for value in line.get("bbox", [])) in title_lines
            ]
            if block_title_lines:
                if not title_emitted:
                    parts.append(f"# {title_text}")
                    title_emitted = True
                remainder_lines = [
                    line for line in block.get("lines", [])
                    if tuple(float(value) for value in line.get("bbox", [])) not in title_lines
                ]
                remainder_text = _lines_text(remainder_lines)
                if remainder_text:
                    parts.append(remainder_text)
                continue
            caption = caption_by_page_bbox.get(bbox_key)
            if caption:
                label = caption["selector"]
                parts.append(f"**{label}.** {caption['caption']}")
                table = tables.get(label)
                if table:
                    parts.append(table["markdown"])
                    table_index.append({
                        "selector": label,
                        "page": page_number,
                        "structured": True,
                        "format": table["format"],
                    })
                elif caption["kind"] == "table":
                    table_index.append({
                        "selector": label, "page": page_number, "structured": False,
                    })
                    visuals.append({key: caption[key] for key in (
                        "selector", "page", "kind", "caption"
                    )})
                else:
                    visuals.append({key: caption[key] for key in (
                        "selector", "page", "kind", "caption"
                    )})
                if caption.get("_remainder_text"):
                    parts.append(caption["_remainder_text"])
                continue
            if any(_intersects(bbox, table_bbox) for table_bbox in page_table_bboxes):
                continue
            heading = _heading(text, _weighted_font_size(block), body_size)
            parts.append(heading or text)
    content = "\n\n".join(parts)
    content = re.sub(r"(?<=\w)-\n\n(?=[a-z])", "", content)
    return content, visuals, table_index


def _render_visual(file_path: str, target: dict, table: dict | None = None) -> bytes:
    with _pdfium_lock:
        document = pdfium.PdfDocument(file_path)
        page = document[target["page"] - 1]
        width, height = page.get_size()
        image = page.render(scale=PDF_RENDER_SCALE).to_pil()
        page.close()
        document.close()

    crop_kind = "full-page"
    crop = None
    if table:
        caption_bbox = target.get("_bbox") or table["bbox"]
        crop = [
            min(table["bbox"][0], caption_bbox[0]),
            min(table["bbox"][1], caption_bbox[1]),
            max(table["bbox"][2], caption_bbox[2]),
            max(table["bbox"][3], caption_bbox[3]),
        ]
        crop_kind = "table-bbox"
    elif target.get("kind") == "figure" and target.get("_bbox"):
        crop = _figure_region(file_path, target)
        if crop:
            crop_kind = "figure-geometry"
    if crop is None and target.get("_bbox"):
        x0, y0, x1, y1 = target["_bbox"]
        margin = 14.0
        center = (x0 + x1) / 2
        if center < width * 0.42:
            crop_left, crop_right = width * 0.035, width * 0.52
        elif center > width * 0.58:
            crop_left, crop_right = width * 0.48, width * 0.965
        else:
            crop_left, crop_right = width * 0.035, width * 0.965
        if target["kind"] == "figure" and y0 >= height * 0.2:
            crop = [crop_left, max(0, y0 - height * 0.6), crop_right, y1 + 8]
        elif target["kind"] == "figure":
            crop = [crop_left, max(0, y0 - margin), crop_right, min(height, y1 + height * 0.6)]
        elif y1 <= height * 0.8:
            crop = [crop_left, max(0, y0 - margin), crop_right, min(height, y1 + height * 0.6)]
        else:
            crop = [crop_left, max(0, y0 - height * 0.6), crop_right, min(height, y1 + margin)]
        crop_kind = "caption-guided"
    if crop:
        margin = 4.0 if target.get("kind") == "figure" else 10.0
        left = max(0, crop[0] - margin) * PDF_RENDER_SCALE
        top = max(0, crop[1] - margin) * PDF_RENDER_SCALE
        right = min(width, crop[2] + margin) * PDF_RENDER_SCALE
        bottom = min(height, crop[3] + margin) * PDF_RENDER_SCALE
        image = image.crop((round(left), round(top), round(right), round(bottom)))
    background = Image.new(image.mode, image.size, "white")
    content_bbox = ImageChops.difference(image, background).getbbox()
    if content_bbox:
        pad = 18
        image = image.crop((
            max(0, content_bbox[0] - pad),
            max(0, content_bbox[1] - pad),
            min(image.width, content_bbox[2] + pad),
            min(image.height, content_bbox[3] + pad),
        ))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    target["crop"] = crop_kind
    target["image_size"] = list(image.size)
    return buffer.getvalue()


def _pypdf_fallback(reader: PdfReader, start: int, end: int) -> str:
    parts = []
    for page_number in range(start, end + 1):
        text = reader.pages[page_number - 1].extract_text()
        if text:
            parts.append(f"--- Page {page_number} ---\n{text}")
    return "\n\n".join(parts) if parts else "(No text could be extracted from this PDF.)"


def _replace_low_quality_pages(
    content: str,
    reader: PdfReader,
) -> tuple[str, list[int]]:
    sections = re.split(r"(?m)(?=^--- Page \d+ ---$)", content)
    fallback_pages = []
    repaired = []
    for section in sections:
        match = re.match(r"--- Page (\d+) ---", section)
        if not match:
            if section:
                repaired.append(section)
            continue
        page_number = int(match.group(1))
        replacements = section.count("�")
        if replacements < 3 or replacements / max(1, len(section)) <= 0.001:
            repaired.append(section)
            continue
        fallback = reader.pages[page_number - 1].extract_text() or ""
        fallback_ratio = fallback.count("�") / max(1, len(fallback))
        if (
            fallback
            and fallback_ratio < replacements / len(section)
            and len(fallback) >= len(section) * 0.5
        ):
            repaired.append(f"--- Page {page_number} ---\n\n{fallback.strip()}\n\n")
            fallback_pages.append(page_number)
        else:
            repaired.append(section)
    return "".join(repaired).strip(), fallback_pages


def extract_text(
    file_path: str,
    pages: str = DEFAULT_READ_PAGES,
    visual: str = "",
) -> dict:
    """Extract page-aware Markdown and an optional focused visual from a PDF."""
    reader = PdfReader(file_path)
    total_pages = len(reader.pages)
    target = _locate_visual(file_path, visual, total_pages) if visual else None
    if target:
        start = end = target["page"]
    else:
        start, end = _parse_page_range(pages, total_pages)

    try:
        layout = _layout_pages(file_path, list(range(start, end + 1)))
        captions = _find_captions(layout)
        tables = _structured_tables(file_path, captions)
        content, visuals, table_index = _markdown_content(layout, captions, tables)
        content, fallback_pages = _replace_low_quality_pages(content, reader)
    except Exception:
        if target and target["kind"] != "page":
            raise
        content = _pypdf_fallback(reader, start, end)
        visuals = []
        table_index = []
        tables = {}
        fallback_pages = list(range(start, end + 1))

    result = {
        "content": content,
        "pages": f"{start}-{end}" if start != end else str(start),
        "total_pages": total_pages,
    }
    if visuals:
        result["visuals"] = visuals
    if table_index:
        result["tables"] = table_index
    if fallback_pages:
        result["text_fallback_pages"] = fallback_pages
    if target:
        matching_table = tables.get(target["selector"])
        image = _render_visual(file_path, target, matching_table)
        result["visual"] = {key: value for key, value in target.items() if not key.startswith("_")}
        result["_image_bytes"] = image
    elif end < total_pages:
        next_end = min(end + DEFAULT_READ_PAGE_COUNT, total_pages)
        result["next_pages"] = f"{end + 1}-{next_end}"
    return result
