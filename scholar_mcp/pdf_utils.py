import os
import httpx
from pypdf import PdfReader
from . import core_client

DOWNLOAD_TIMEOUT = 60
USER_AGENT = "scholar-mcp/0.1.0 (academic research tool)"


def _try_download(url: str, save_path: str, filename: str) -> str | None:
    """Attempt to download a PDF from url. Returns file path on success, None on failure."""
    try:
        headers = {"User-Agent": USER_AGENT}
        with httpx.Client(timeout=DOWNLOAD_TIMEOUT, follow_redirects=True) as client:
            response = client.get(url, headers=headers)
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            is_pdf = "pdf" in content_type or response.content[:5] == b"%PDF-"
            if not is_pdf:
                return None
            os.makedirs(save_path, exist_ok=True)
            file_path = os.path.join(save_path, filename)
            with open(file_path, "wb") as f:
                f.write(response.content)
            return file_path
    except (httpx.HTTPError, OSError):
        return None


def download_paper(paper_info: dict, save_path: str) -> dict:
    """Smart download chain:
    1. S2 open access URL
    2. arXiv direct (if ArXiv ID in external_ids)
    3. CORE (search by DOI or title for institutional PDFs)
    4. bioRxiv/medRxiv (if DOI starts with 10.1101)
    5. Fail gracefully with URLs
    """
    safe_id = str(paper_info.get("paper_id", "unknown")).replace("/", "_").replace(":", "_")
    filename = f"{safe_id}.pdf"

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
        arxiv_filename = f"{arxiv_id.replace('/', '_')}.pdf"
        url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"
        result = _try_download(url, save_path, arxiv_filename)
        if result:
            return {"success": True, "file_path": result, "source": "arxiv",
                    "message": f"Downloaded from arXiv ({arxiv_id})."}

    # 3. CORE (search by DOI or title for institutional PDFs)
    try:
        title = paper_info.get("title", "")
        core_url = core_client.get_pdf_url(doi=doi or None, title=title or None)
        if core_url:
            result = _try_download(core_url, save_path, filename)
            if result:
                return {"success": True, "file_path": result, "source": "core",
                        "message": "Downloaded via CORE (institutional repository)."}
    except Exception:
        pass

    # 4. bioRxiv / medRxiv
    if doi.startswith("10.1101/"):
        for name, base in [("bioRxiv", "biorxiv"), ("medRxiv", "medrxiv")]:
            url = f"https://www.{base}.org/content/{doi}v1.full.pdf"
            result = _try_download(url, save_path, filename)
            if result:
                return {"success": True, "file_path": result, "source": base,
                        "message": f"Downloaded from {name}."}

    # 5. Fail gracefully
    s2_url = paper_info.get("url", "")
    doi_link = f" or via DOI: https://doi.org/{doi}" if doi else ""
    return {
        "success": False, "file_path": None, "source": "none",
        "message": f"Could not download PDF (may not be open access). "
                   f"Try: {s2_url}{doi_link}",
    }


def extract_text(file_path: str, max_pages: int = 0) -> str:
    """Extract text from a PDF file."""
    reader = PdfReader(file_path)
    pages = reader.pages
    if max_pages > 0:
        pages = pages[:max_pages]

    parts = []
    for i, page in enumerate(pages):
        text = page.extract_text()
        if text:
            parts.append(f"--- Page {i + 1} ---\n{text}")

    return "\n\n".join(parts) if parts else "(No text could be extracted from this PDF.)"
