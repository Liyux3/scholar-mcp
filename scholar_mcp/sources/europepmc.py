"""Europe PMC API client. Covers PubMed + European repositories. Free, no key needed."""

import hashlib
from io import BytesIO
from pathlib import PurePosixPath
import re
from urllib.parse import parse_qs, urlsplit
import xml.etree.ElementTree as ET

import httpx

from .. import access
from ..cache import cached
from ..records import external_ids, matches_paper, pmc_id
from . import pacing
from .crossref import doi_id

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
            'full_text_locations': [{'url': url, 'version': _copy_version(item), 'source': 'europepmc'} for url in urls],
            "url": f"https://europepmc.org/article/{source}/{pid}"}


@cached(ttl=3600)
def _identity(paper_id: str) -> dict:
    doi = doi_id(paper_id)
    if doi:
        query = f'DOI:"{doi}"'
    elif re.fullmatch(r"PMID:\d+", paper_id, re.I):
        query = f"EXT_ID:{paper_id.split(':')[1]} AND SRC:MED"
    elif paper_id.upper().startswith('PMC') and pmc_id(paper_id):
        query = f"PMCID:{pmc_id(paper_id)}"
    else:
        return {}
    records = _request(query, 1)
    return records[0] if records else {}


def get_paper(paper_id: str) -> dict | None:
    item = _identity(paper_id)
    return format_paper(item) if item else None


def companion_files(paper_id: str) -> dict | None:
    """Resolve deposited companion archives from exact DOI and native JATS references."""
    item = _identity(paper_id)
    if not item.get('pmcid') or item.get('isOpenAccess') != 'Y':
        return None
    doi = doi_id(paper_id).casefold()
    if not doi or doi_id(item.get('doi', '')).casefold() != doi:
        return None
    xml_url = f"{BASE_URL.removesuffix('/search')}/{item['pmcid']}/fullTextXML"
    pacing.wait('europepmc')
    response = access.get(xml_url, kind='xml', timeout=pacing.remaining(25))
    response.raise_for_status()
    root = ET.fromstring(response.content)
    ids = {node.get('pub-id-type'): (node.text or '').strip() for node in root.findall('./front/article-meta/article-id')}
    if doi_id(ids.get('doi', '')).casefold() != doi:
        return None
    files = {'supplement': [], 'peer_review': [], 'source_data': [], 'figures': []}
    href = '{http://www.w3.org/1999/xlink}href'
    for figure in root.findall('.//fig'):
        for image in figure.findall('.//graphic'):
            if image.get(href):
                files['figures'].append(image.get(href))
    for supplement in root.findall('.//supplementary-material'):
        label = ' '.join(supplement.itertext()).casefold()
        kind = ('peer_review' if 'peer review' in label else 'source_data' if 'source data' in label else 'supplement')
        for media in supplement.findall('.//media'):
            if media.get(href):
                files[kind].append(media.get(href))
    archive = f"{BASE_URL.removesuffix('/search')}/{item['pmcid']}/supplementaryFiles"
    return {'status': 'discovered', 'landing_page': xml_url, 'source': 'europepmc',
            '_document': {'url': xml_url, 'media_type': 'application/xml', 'content': response.text},
            'assets': [{'kind': kind, 'url': archive, 'label': 'Europe PMC deposited companion archive',
                        'discovered_on': xml_url, 'version': _copy_version(item), 'container': True,
                        'expected_members': names, 'evidence': 'exact DOI in native JATS article metadata'}
                       for kind, names in files.items() if names],
            'kinds': {kind: 'discovered' if names else 'not_found' for kind, names in files.items()}}


def _relations(paper_id: str, relation: str, limit: int) -> list[dict]:
    item = _identity(paper_id)
    if not item or limit <= 0:
        return []
    base = BASE_URL.removesuffix("/search")
    papers = []
    with httpx.Client(timeout=20) as client:
        for page in range(1, (limit + 99) // 100 + 1):
            pacing.wait("europepmc")
            response = access.get(f"{base}/{item['source']}/{item['id']}/{relation}",
                                  client=client, params={"format": "json", "page": page, "pageSize": min(limit, 100)})
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
    pacing.wait("europepmc")
    response = access.get(f"{BASE_URL.removesuffix('/search')}/{pmcid}/fullTextXML", kind="xml", timeout=30)
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
                       })
    return papers


@cached(ttl=300)
def _request(query: str, limit: int) -> list[dict]:
    pacing.wait("europepmc")
    response = access.get(
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


PMC_OA_BUCKET = "https://pmc-oa-opendata.s3.amazonaws.com"


@cached(ttl=3600)
def _pmc_keys(pmcid: str) -> list[str]:
    response = access.get(PMC_OA_BUCKET, kind='xml', params={'list-type': '2', 'prefix': f'{pmcid}.'}, timeout=15)
    response.raise_for_status()
    return re.findall(r'<Key>([^<]+)</Key>', response.text)


@cached(ttl=3600)
def _pmc_version_record(pmcid: str, version: int) -> dict:
    document_id = f'{pmcid}.{version}'
    response = access.get(f'{PMC_OA_BUCKET}/{document_id}/{document_id}.json', timeout=15)
    response.raise_for_status()
    record = response.json()
    if pmc_id(record.get('pmcid')) != pmcid or record.get('version') != version:
        raise ValueError('PMC dataset metadata identity mismatch')
    return {**record, 'document_id': document_id}


def _dataset_version(record: dict) -> str:
    return {True: 'accepted', False: 'published'}.get(record.get('is_manuscript'), 'unknown')


@cached(ttl=3600)
def _pmc_record(pmcid: str) -> dict:
    # Dataset versions are ingestion order. A higher number can be a manuscript
    # deposited after the version of record, so inspect the native copy metadata.
    versions = sorted((int(m[1]) for key in _pmc_keys(pmcid)
                       if (m := re.fullmatch(rf'{pmcid}\.(\d+)/{pmcid}\.\1\.json', key))), reverse=True)
    newest = {}
    for version in versions:
        record = _pmc_version_record(pmcid, version)
        newest = newest or record
        if _dataset_version(record) == 'published':
            return record
    return newest


def _dataset_url(value: str, document_id: str) -> str:
    """Accept only declared assets inside the exact first-party article version."""
    parsed = urlsplit(value)
    if parsed.scheme != 's3' or parsed.netloc != 'pmc-oa-opendata':
        return ''
    path = PurePosixPath(parsed.path)
    if path.parent != PurePosixPath('/') / document_id or path.name in {'', '.', '..'}:
        return ''
    return f'{PMC_OA_BUCKET}{parsed.path}' + (f'?{parsed.query}' if parsed.query else '')


def _dataset_bytes(url: str, *, kind: str) -> bytes:
    response = access.get(url, kind=kind, timeout=25)
    response.raise_for_status()
    content = response.content
    expected = parse_qs(urlsplit(url).query).get('md5', [''])[0]
    if expected and hashlib.md5(content).hexdigest() != expected:  # provider integrity, not authentication
        raise ValueError('PMC asset checksum mismatch')
    return content


@cached(ttl=300)
def structured_text(paper: dict) -> dict | None:
    """Acquire identity-bound native JATS when the requested copy has no usable PDF."""
    identifiers = external_ids(paper)
    pmcid = pmc_id(identifiers.get('PubMedCentral'))
    doi = doi_id(identifiers.get('DOI', ''))
    if not pmcid and doi:
        pmcid = pmc_id(_identity(doi).get('pmcid'))
    if not pmcid:
        return None
    record = _pmc_record(pmcid)
    if not record:
        return None
    if doi and doi_id(record.get('doi', '')).casefold() != doi.casefold():
        raise ValueError('PMC dataset DOI differs from the requested paper')
    url = _dataset_url(record.get('xml_url') or '', record['document_id'])
    if not url:
        return None
    content = _dataset_bytes(url, kind='xml')
    root = ET.fromstring(content)
    ids = {node.get('pub-id-type'): ''.join(node.itertext()).strip()
           for node in root.findall('./front/article-meta/article-id')}
    if (pmc_id(ids.get('pmc') or ids.get('pmcid')) != pmcid
            or ids.get('pmcid-ver', record['document_id']) != record['document_id']
            or doi and doi_id(ids.get('doi', '')).casefold() != doi.casefold()):
        raise ValueError('JATS identity differs from the requested paper')
    return {'content': content, 'record': record, 'source': 'europepmc',
            'version': 'preprint' if root.get('article-type') == 'preprint' else _dataset_version(record),
            'document': {'id': record['document_id'], 'url': url,
                         'sha256': hashlib.sha256(content).hexdigest(),
                         'license': record.get('license_code'), 'pmcid': pmcid, 'doi': record.get('doi')}}


def structured_visual(document: dict, target: dict) -> dict:
    """Fetch only the original asset declared for the selected native figure."""
    from PIL import Image
    from bs4 import BeautifulSoup

    record = document['record']
    filename = PurePosixPath(target.get('asset') or '').name
    urls = [_dataset_url(value, record['document_id']) for value in record.get('media_urls') or []
            if PurePosixPath(urlsplit(value).path).name == filename]
    url = next((value for value in urls if value), '')
    if not url and target.get('xml_id') and filename:
        landing = f"https://pmc.ncbi.nlm.nih.gov/articles/{record['pmcid']}/"
        response = access.get(landing, kind='html', timeout=25)
        response.raise_for_status()
        figure = BeautifulSoup(response.text, 'html.parser').find(id=target['xml_id'])
        for image in figure.find_all('img') if figure else []:
            candidate = image.get('src', '')
            parsed = urlsplit(candidate)
            if (parsed.scheme == 'https' and parsed.netloc == 'cdn.ncbi.nlm.nih.gov'
                    and parsed.path.startswith('/pmc/') and PurePosixPath(parsed.path).name == filename):
                url = candidate
                break
    if not url:
        return {'image_status': 'unavailable', 'image_note': 'The source did not declare an accessible original image for this selection.'}
    content = _dataset_bytes(url, kind='image')
    with Image.open(BytesIO(content)) as original:
        original.load()
        output = BytesIO()
        original.convert('RGB').save(output, format='PNG')
        return {'_image_bytes': output.getvalue(), 'image_source': {
            'url': url, 'sha256': hashlib.sha256(content).hexdigest(),
            'width': original.width, 'height': original.height, 'format': original.format}}


def pmc_pdf_urls(pmcid: str) -> list[str]:
    """Open-access PDFs from NCBI's PMC article dataset on AWS.

    Europe PMC and NCBI web PDF links now answer automated clients with browser
    challenges; the official dataset serves the same files directly.
    """
    pmcid = pmc_id(pmcid)
    if not pmcid:
        return []
    try:
        keys = _pmc_keys(pmcid)
    except httpx.HTTPError:
        keys = []
    # The article itself is <PMCID>.<version>/<PMCID>.<version>.pdf; other PDFs are supplements.
    versions = sorted(int(m[1]) for key in keys if (m := re.fullmatch(rf'{pmcid}\.(\d+)/{pmcid}\.\1\.pdf', key)))
    if len(versions) > 1:
        try:
            preferred = _pmc_record(pmcid).get('version')
            if preferred in versions:
                versions = [preferred]
        except (httpx.HTTPError, ValueError):
            pass
    keys = [f"{pmcid}.{v}/{pmcid}.{v}.pdf" for v in versions]
    return [f"{PMC_OA_BUCKET}/{key}" for key in keys[-1:]] + [f"https://europepmc.org/articles/{pmcid}?pdf=render"]


def _pdf_urls(item: dict) -> list[str]:
    """PDF links the record itself lists. Formatting a search page must stay
    offline: looking up the PMC dataset here cost one request per result and
    held every search for up to two minutes, so only resolve_pdf does that."""
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
        urls.append(f"https://europepmc.org/articles/{pmcid}?pdf=render")
    return list(dict.fromkeys(urls))


def search_papers(query: str, limit: int = 10, **kwargs) -> list[dict]:
    """Search Europe PMC with the same normalized fields used for relation records."""
    return [format_paper(item) for item in _request(query, limit) if item.get("title")][:limit]


def _copy_version(item: dict) -> str:
    if item.get('source') == 'PPR' or 'Preprint' in (item.get('pubTypeList') or {}).get('pubType', []):
        return 'preprint'
    return {'Y': 'accepted', 'N': 'published'}.get(str(item.get('authMan') or '').upper(), 'unknown')


def resolve_pdf(paper: dict) -> list[dict]:
    identifiers = external_ids(paper)
    pmcid = pmc_id(identifiers.get('PubMedCentral'))
    if pmcid:
        try:
            item = _identity(pmcid) or {}
        except (httpx.HTTPError, access.AccessError, pacing.Paced):
            item = {}
        return _pmc_locations(pmcid, item)
    doi = str(identifiers.get("DOI") or "").strip()
    title = str(paper.get("title") or "").strip()
    query = f"DOI:{doi}" if doi else f'TITLE:"{title}"' if title else ""
    if not query:
        return []
    locations = {}
    for item in _request(query, 5):
        if matches_paper(paper, format_paper(item)):
            for location in [*_pmc_locations(item.get('pmcid') or '', item),
                             *({'url': url, 'version': _copy_version(item)} for url in _pdf_urls(item))]:
                locations.setdefault(location['url'], location)
    return list(locations.values())


def _pmc_locations(pmcid: str, item: dict) -> list[dict]:
    locations = []
    for url in pmc_pdf_urls(pmcid):
        version = _copy_version(item)
        match = re.fullmatch(r'/PMC\d+\.(\d+)/PMC\d+\.\1\.pdf', urlsplit(url).path)
        if match:
            try:
                version = _dataset_version(_pmc_version_record(pmc_id(pmcid), int(match[1])))
            except (httpx.HTTPError, ValueError):
                version = 'unknown'
        locations.append({'url': url, 'version': version})
    return locations
