"""Europe PMC API client. Covers PubMed + European repositories. Free, no key needed."""

import httpx
import re
import xml.etree.ElementTree as ET

from .cache import cached
from .crossref_client import doi_id

BASE_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"

# Europe PMC accepts pageSize up to 1000; 100 matches the pipeline's fetch
# limit and keeps response payloads reasonable.
EUROPEPMC_MAX_PAGE_SIZE = 100


def format_paper(item: dict) -> dict:
    authors = [a.get("fullName", "") for a in (item.get("authorList") or {}).get("author", []) if a.get("fullName")]
    if not authors:
        authors = [a.strip() for a in (item.get("authorString") or "").rstrip(".").split(",") if a.strip()]
    year = str(item.get("pubYear") or "")
    source = item.get("source") or "MED"
    pid = str(item.get("id") or item.get("pmid") or "")
    ids = {k: v for k, v in {"DOI": item.get("doi"), "PMID": item.get("pmid") or (pid if source == "MED" else None), "PMC": item.get("pmcid")}.items() if v}
    urls = _pdf_urls(item)
    return {"paper_id": "PMID:" + pid if source == "MED" and pid else pid,
            "title": item.get("title") or "", "authors": authors,
            "year": int(year) if year.isdigit() else None,
            "venue": item.get("journalTitle") or (item.get("journalInfo") or {}).get("journal", {}).get("title") or "",
            "abstract": item.get("abstractText") or "", "external_ids": ids,
            "citation_count": item.get("citedByCount") or 0,
            "_citation_count_known": item.get("citedByCount") is not None,
            "source": "europepmc", "is_open_access": item.get("isOpenAccess") == "Y",
            "publication_types": (item.get("pubTypeList") or {}).get("pubType") or [],
            "open_access_url": urls[0] if urls else None,
            "publication_date": item.get("firstPublicationDate"),
            "url": f"https://europepmc.org/article/{source}/{pid}"}


@cached(ttl=3600)
def _identity(paper_id: str) -> dict:
    doi = doi_id(paper_id)
    if doi:
        query = f'DOI:"{doi}"'
    elif re.fullmatch(r"PMID:\d+", paper_id, re.I):
        query = f"EXT_ID:{paper_id.split(':')[1]} AND SRC:MED"
    elif re.fullmatch(r"PMC\d+", paper_id, re.I):
        query = f"EXT_ID:{paper_id.upper()}"
    else:
        return {}
    records = _request(query, 1)
    return records[0] if records else {}


def get_paper(paper_id: str) -> dict | None:
    item = _identity(paper_id)
    return format_paper(item) if item else None


def _relations(paper_id: str, relation: str, limit: int) -> list[dict]:
    item = _identity(paper_id)
    if not item or limit <= 0:
        return []
    base = BASE_URL.removesuffix("/search")
    papers = []
    with httpx.Client(timeout=20) as client:
        for page in range(1, (limit + 99) // 100 + 1):
            response = client.get(f"{base}/{item['source']}/{item['id']}/{relation}",
                                  params={"format": "json", "page": page, "pageSize": min(limit, 100)})
            if relation == "references" and response.status_code in {404, 500, 502, 503, 504} and item.get("pmcid"):
                return _xml_references(item["pmcid"], limit)
            response.raise_for_status()
            key = "citation" if relation == "citations" else "reference"
            rows = response.json().get(f"{key}List", {}).get(key, [])
            for row in rows:
                paper = format_paper(row)
                paper["_needs_metadata"] = True
                paper["_reference"] = {"article-title": row.get("title", ""), "author": row.get("authorString", ""), "year": row.get("pubYear", "")}
                papers.append(paper)
            if len(rows) < min(limit, 100):
                break
    return papers[:limit]


def get_citations(paper_id: str, limit: int = 20, **kwargs) -> list[dict]:
    return _relations(paper_id, "citations", limit)


def get_references(paper_id: str, limit: int = 20, **kwargs) -> list[dict]:
    return _relations(paper_id, "references", limit)


@cached(ttl=3600)
def _xml_references(pmcid: str, limit: int) -> list[dict]:
    response = httpx.get(f"{BASE_URL.removesuffix('/search')}/{pmcid}/fullTextXML", timeout=30)
    response.raise_for_status()
    root = ET.fromstring(response.content)
    papers = []
    for ref in root.findall(".//ref-list/ref")[:limit]:
        def text(tag):
            node = ref.find(f".//{tag}")
            return " ".join("".join(node.itertext()).split()).strip('“”"') if node is not None else ""
        ids = {p.get("pub-id-type"): (p.text or "").strip() for p in ref.findall(".//pub-id")}
        raw = {"article-title": text("article-title"), "author": text("surname"),
               "year": text("year"), "first-page": text("fpage"),
               "unstructured": " ".join(ref.itertext())}
        papers.append({"title": raw["article-title"], "authors": [], "venue": text("source"),
                       "year": int(raw["year"]) if raw["year"].isdigit() else None,
                       "external_ids": {k: v for k, v in {"DOI": ids.get("doi"), "PMID": ids.get("pmid")}.items() if v},
                       "source": "europepmc", "_needs_metadata": True, "_reference": raw,
                       "citation_count": 0, "_citation_count_known": False})
    return papers


@cached(ttl=300)
def _request(query: str, limit: int) -> list[dict]:
    response = httpx.get(
        BASE_URL,
        params={
            "query": query,
            "resultType": "core",
            "pageSize": min(limit, EUROPEPMC_MAX_PAGE_SIZE),
            "format": "json",
            "sort": "CITED desc",
        },
        timeout=20,
    )
    response.raise_for_status()
    return response.json().get("resultList", {}).get("result", [])


def _pdf_urls(item: dict) -> list[str]:
    urls = []
    for entry in (item.get("fullTextUrlList") or {}).get("fullTextUrl") or []:
        if (
            str(entry.get("documentStyle") or "").lower() == "pdf"
            and str(entry.get("availabilityCode") or "").upper() == "OA"
            and entry.get("url")
        ):
            urls.append(str(entry["url"]))
    pmcid = str(item.get("pmcid") or "").strip()
    if pmcid:
        native = f"https://europepmc.org/articles/{pmcid}?pdf=render"
        if native not in urls:
            urls.append(native)
    return urls


def search_papers(query: str, limit: int = 10, **kwargs) -> list[dict]:
    """Search Europe PMC with the same normalized fields used for relation records."""
    return [format_paper(item) for item in _request(query, limit) if item.get("title")][:limit]


def resolve_pdf(paper: dict) -> list[str]:
    identifiers = paper.get("external_ids") or {}
    pmcid = str(identifiers.get("PMC") or identifiers.get("PubMedCentral") or "").strip()
    if pmcid:
        return [f"https://europepmc.org/articles/{pmcid}?pdf=render"]
    doi = str(identifiers.get("DOI") or "").strip()
    title = str(paper.get("title") or "").strip()
    query = f"DOI:{doi}" if doi else f'TITLE:"{title}"' if title else ""
    if not query:
        return []
    return [url for item in _request(query, 5) for url in _pdf_urls(item)]
