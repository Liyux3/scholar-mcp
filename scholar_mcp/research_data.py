"""Typed scientific records over Scholar's existing source transport and flow.

Native accessions, database links and declared files stay distinct from papers.
No library migration, sequence download or scientific inference happens here.
"""
from __future__ import annotations

from functools import partial
import re
from urllib.parse import parse_qs, quote, urlsplit
import xml.etree.ElementTree as ET

import httpx

from . import access, config, records, sources
from .cache import PartialRecords, cached
from .sources import europepmc, pacing, pubmed, zenodo

# One NCBI adapter serves four native databases; names are part of the public ID.
DATABASES = {"geo": ("gds", "dataset", "GEO"),
             "sra": ("sra", "sequence", "SRA"),
             "bioproject": ("bioproject", "project", "BioProject"),
             "gene": ("gene", "gene", "NCBIGene")}
KINDS = {"dataset": ("geo", "zenodo"), "sequence": ("sra",),
         "project": ("bioproject",), "gene": ("gene",),
         "protein": ("uniprot",), "compound": ("pubchem",)}
UNIPROT = "https://rest.uniprot.org/uniprotkb"
PUBCHEM = "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound"


def identifier(value: str) -> tuple[str, str] | None:
    """Recognize only native namespaces and official record URLs, never titles."""
    value = value.strip()
    if value.lower().startswith(("http://", "https://")):
        try:
            parsed = urlsplit(value)
            host, path = (parsed.hostname or "").casefold(), parsed.path.rstrip("/")
        except ValueError:
            return None
        if host in {"www.ncbi.nlm.nih.gov", "ncbi.nlm.nih.gov"}:
            value = ("NCBIGene:" + path.split("/")[-1] if path.startswith("/gene/") else
                     parse_qs(parsed.query).get("acc", [""])[0] if path == "/geo/query/acc.cgi" else
                     "BioProject:" + path.split("/")[-1] if path.startswith("/bioproject/") else
                     path.split("/")[-1] if path.startswith("/sra/") else "")
        elif host in {"www.uniprot.org", "rest.uniprot.org"} and path.startswith("/uniprotkb/"):
            value = "UniProt:" + path.split("/")[2].removesuffix(".json")
        elif host == "pubchem.ncbi.nlm.nih.gov" and path.startswith("/compound/"):
            value = "PubChem:" + path.split("/")[-1]
        elif host in {"zenodo.org", "www.zenodo.org"} and re.fullmatch(r"/records?/\d+", path):
            value = "Zenodo:" + path.split("/")[-1]
        elif host in {"doi.org", "dx.doi.org"}:
            value = path.lstrip("/")
        else:
            return None
    patterns = (("geo", r"(?:GEO:)?(GSE\d+|GDS\d+)"),
                ("sra", r"(?:SRA:)?([SED]R[XR]\d+)"),
                ("bioproject", r"(?:BioProject:)?(PRJ[DEN][AB]\d+)"),
                ("gene", r"(?:NCBIGene|Gene):([1-9]\d*)"),
                ("uniprot", r"(?:UniProt:)?([OPQ][0-9][A-Z0-9]{3}[0-9]|[A-NR-Z][0-9](?:[A-Z][A-Z0-9]{2}[0-9]){1,2})"),
                ("pubchem", r"(?:PubChem|CID):([1-9]\d*)"),
                ("zenodo", r"(?:Zenodo:|10\.5281/zenodo\.)([1-9]\d*)"))
    for source, pattern in patterns:
        if match := re.fullmatch(pattern, value, re.I):
            return source, match[1].upper()
    return None


def _object(source, native, kind, title, description, url, **fields):
    prefix = DATABASES[source][2] if source in DATABASES else {"uniprot": "UniProt", "pubchem": "PubChem", "zenodo": "Zenodo"}[source]
    return {"id": f"{prefix}:{native}", "kind": kind, "source": source,
            "native_id": str(native), "title": title, "description": description or "",
            "url": url, "properties": {}, "files": [], "relations": [], **fields}


def _relation(target: str, url: str, evidence_url: str, relation="associated_with"):
    return {"target": target, "url": url, "relation": relation,
            "evidence": {"type": "database_link", "url": evidence_url}}


@cached(ttl=3600, persistent=True, namespace="science.v2")
def _json(provider: str, url: str, **params) -> dict:
    """Reuse public transport, shared quota gates and completed-response caching."""
    key = config.NCBI_API_KEY if provider == "ncbi" else None
    pacing.wait(provider, key=key)
    if key:
        params = {**params, "api_key": key}
    response = access.get(url, params=params, timeout=pacing.remaining(20), follow_redirects=True)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or any(k in payload for k in ("error", "ERROR", "Fault")):
        raise ValueError(f"{provider} returned an invalid record")
    if provider == "ncbi":
        field = {"esearch.fcgi": "esearchresult.idlist", "esummary.fcgi": "result.uids",
                 "elink.fcgi": "linksets"}[url.rsplit("/", 1)[-1]]
    elif provider == "uniprot":
        field = "results" if url.endswith("/search") else "primaryAccession"
    elif provider == "pubchem":
        field = "PropertyTable.Properties"
    else:
        field = "hits.hits" if url == zenodo.BASE_URL else "metadata"
    node = payload
    for key in field.split("."):
        node = node.get(key) if isinstance(node, dict) else None
    expected = str if field == "primaryAccession" else dict if field == "metadata" else list
    if not isinstance(node, expected):
        raise ValueError(f"{provider} returned an invalid {field} response")
    result = payload.get("result") or {}
    search = payload.get("esearchresult") or {}
    if search.get("ERROR") or search.get("errorlist", {}).get("fieldsnotfound"):
        raise ValueError("NCBI rejected the search expression")
    if isinstance(result, dict) and any(isinstance(v, dict) and v.get("error") for v in result.values()):
        return PartialRecords(payload)
    return payload


def _ncbi_search(db: str, query: str, limit: int) -> list[str]:
    result = _json("ncbi", f"{pubmed.BASE_URL}/esearch.fcgi", db=db, term=query,
                   retmax=limit, retmode="json").get("esearchresult", {})
    return result.get("idlist", [])


def _ncbi_records(source: str, uids: list[str]) -> list[dict]:
    if not uids:
        return []
    db, kind, _ = DATABASES[source]
    url = f"{pubmed.BASE_URL}/esummary.fcgi"
    result = _json("ncbi", url, db=db, id=",".join(uids), retmode="json").get("result", {})
    output = []
    for uid in uids:
        item = result.get(uid)
        if not item or item.get("error"):
            continue
        evidence_url = f"{url}?db={db}&id={uid}&retmode=json"
        relations = []
        if source == "gene":
            native = str(item.get("uid") or uid)
            title = item.get("name") or native
            description = item.get("summary") or item.get("description")
            record_url = f"https://www.ncbi.nlm.nih.gov/gene/{native}"
            properties = {k: item[k] for k in ("description", "chromosome", "maplocation", "otheraliases", "genomicinfo", "status", "currentid") if k in item}
            organism = item.get("organism", {}).get("scientificname", "")
            aliases = []
        elif source == "bioproject":
            native = item.get("project_acc") or ""
            title = item.get("project_title") or item.get("project_name") or native
            description = item.get("project_description")
            record_url = f"https://www.ncbi.nlm.nih.gov/bioproject/{native}"
            properties = {k: item[k] for k in ("project_data_type", "project_target_material", "project_methodtype", "registration_date", "submitter_organization") if k in item}
            organism = item.get("organism_name", "")
            aliases = []
        elif source == "geo":
            native = item.get("accession") or ""
            title, description = item.get("title") or native, item.get("summary")
            record_url = f"https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc={native}"
            organism = item.get("taxon", "")
            properties = {k: item[k] for k in ("gdstype", "n_samples", "pdat", "samples", "gpl") if k in item}
            aliases = []
            relations = [_relation(f"PMID:{pid}", f"https://pubmed.ncbi.nlm.nih.gov/{pid}/", evidence_url, "cited_by") for pid in item.get("pubmedids", [])]
            if project := item.get("bioproject"):
                relations.append(_relation(f"BioProject:{project}", f"https://www.ncbi.nlm.nih.gov/bioproject/{project}", evidence_url))
        else:
            xml = ET.fromstring("<record>" + item.get("expxml", "") + item.get("runs", "") + "</record>")
            experiment = xml.find("Experiment")
            native = experiment.get("acc", "") if experiment is not None else ""
            title = xml.findtext("Summary/Title") or native
            study = xml.find("Study")
            description = study.get("name", "") if study is not None else ""
            organism_node = xml.find("Organism")
            organism = organism_node.get("ScientificName", "") if organism_node is not None else ""
            record_url = f"https://www.ncbi.nlm.nih.gov/sra/{native}"
            aliases = [node.get("acc") for node in xml.findall("Run") if node.get("acc")]
            properties = {"runs": aliases, "strategy": xml.findtext("Library_descriptor/LIBRARY_STRATEGY"),
                          "platform": xml.findtext("Summary/Platform"), "study": study.get("acc") if study is not None else None}
            if project := xml.findtext("Bioproject"):
                relations.append(_relation(f"BioProject:{project}", f"https://www.ncbi.nlm.nih.gov/bioproject/{project}", evidence_url))
        if native:
            output.append(_object(source, native, kind, title, description, record_url,
                                  source_record_id=uid, organism=organism, aliases=aliases,
                                  properties=properties, relations=relations, provenance={"url": evidence_url}))
            if source == "geo" and item.get("ftplink"):
                output[-1]["files"] = [{"url": item["ftplink"], "role": "data_directory", "format": "directory"}]
    if len(output) < len(uids):
        return sources.PartialResults(output, "Some native summaries are unavailable; completed records retained.")
    return output


def _ncbi_links(db: str, uid: str, target_db: str, limit: int) -> list[str]:
    payload = _json("ncbi", f"{pubmed.BASE_URL}/elink.fcgi", dbfrom=db, db=target_db,
                    id=uid, retmode="json")
    return list(dict.fromkeys(str(link) for group in payload.get("linksets", [])
                             for block in group.get("linksetdbs", []) if block.get("dbto") == target_db
                             for link in block.get("links", [])))[:limit]


def _uniprot(item: dict) -> dict:
    native = item["primaryAccession"]
    url = f"https://www.uniprot.org/uniprotkb/{native}/entry"
    evidence_url = f"{UNIPROT}/{native}.json"
    description = item.get("proteinDescription", {})
    name = description.get("recommendedName", {}).get("fullName", {}).get("value")
    names = description.get("submissionNames") or []
    name = name or (names[0].get("fullName", {}).get("value") if names else None) or item.get("uniProtkbId") or native
    functions = [text.get("value", "") for comment in item.get("comments", []) if comment.get("commentType") == "FUNCTION" for text in comment.get("texts", [])]
    relations = []
    for reference in item.get("references", []):
        for ref in reference.get("citation", {}).get("citationCrossReferences", []):
            if ref.get("database") == "PubMed":
                pid = ref["id"]
                relations.append(_relation(f"PMID:{pid}", f"https://pubmed.ncbi.nlm.nih.gov/{pid}/", evidence_url, "cited_by"))
    for ref in item.get("uniProtKBCrossReferences", []):
        if ref.get("database") == "GeneID":
            relations.append(_relation(f"NCBIGene:{ref['id']}", f"https://www.ncbi.nlm.nih.gov/gene/{ref['id']}", evidence_url))
    sequence = item.get("sequence", {})
    return _object("uniprot", native, "protein", name, "\n".join(functions), url,
                   aliases=item.get("secondaryAccessions", []), organism=item.get("organism", {}).get("scientificName", ""),
                   properties={"entry_type": item.get("entryType"), "genes": item.get("genes", []),
                               "length": sequence.get("length"), "mass": sequence.get("molWeight"),
                               "version": item.get("entryAudit", {})}, relations=relations,
                   files=[{"url": f"{UNIPROT}/{native}.fasta", "format": "fasta", "role": "sequence"}],
                   provenance={"url": evidence_url})


def _compounds(cids: list[str]) -> list[dict]:
    if not cids:
        return []
    url = f"{PUBCHEM}/cid/{','.join(cids)}/property/Title,MolecularFormula,MolecularWeight,IUPACName,InChIKey/JSON"
    payload = _json("pubchem", url)
    return [_object("pubchem", str(row["CID"]), "compound", row.get("Title") or row.get("IUPACName") or str(row["CID"]),
                    "", f"https://pubchem.ncbi.nlm.nih.gov/compound/{row['CID']}",
                    properties={k: v for k, v in row.items() if k != "CID"}, provenance={"url": url})
            for row in payload.get("PropertyTable", {}).get("Properties", [])]


def _zenodo(item: dict) -> dict | None:
    metadata = item.get("metadata") or {}
    if metadata.get("resource_type", {}).get("type") != "dataset":
        return None
    native = str(item["id"])
    url = f"https://zenodo.org/records/{native}"
    evidence_url = f"{zenodo.BASE_URL}/{native}"
    relations = []
    for ref in metadata.get("related_identifiers", []):
        if ref.get("scheme") == "doi":
            target = ref.get("identifier", "")
            relations.append(_relation(f"DOI:{target}", f"https://doi.org/{quote(target, safe='/')}", evidence_url, ref.get("relation") or "associated_with"))
    return _object("zenodo", native, "dataset", metadata.get("title") or native,
                   zenodo._plain_text(metadata.get("description", "")), url,
                   properties={k: metadata[k] for k in ("doi", "version", "license", "creators", "publication_date", "access_right", "keywords") if k in metadata},
                   files=[{"name": f.get("key"), "size": f.get("size"), "checksum": f.get("checksum"),
                           "url": f.get("links", {}).get("self")} for f in item.get("files", [])],
                   relations=relations, provenance={"url": evidence_url})


def _search(source: str, query: str, limit: int) -> list[dict]:
    if source in DATABASES:
        return _ncbi_records(source, _ncbi_search(DATABASES[source][0], query, limit))
    if source == "uniprot":
        result = _json("uniprot", f"{UNIPROT}/search", query=query, format="json", size=limit)
        return [_uniprot(item) for item in result.get("results", [])]
    if source == "pubchem":
        return _compounds(_ncbi_search("pccompound", query, limit))
    result = _json("zenodo", zenodo.BASE_URL, q=query, type="dataset", size=limit, sort="bestmatch")
    return [obj for item in result.get("hits", {}).get("hits", []) if (obj := _zenodo(item))]


def _get_native(source: str, native: str) -> list[dict]:
    if source in DATABASES:
        field = "PRJA" if source == "bioproject" else "ACCN"
        uids = [native] if source == "gene" else _ncbi_search(DATABASES[source][0], f"{native}[{field}]", 20)
        found = _ncbi_records(source, uids)
        return [r for r in found if native in [r["native_id"], *r.get("aliases", [])]]
    if source == "uniprot":
        obj = _uniprot(_json("uniprot", f"{UNIPROT}/{native}.json"))
        return [obj] if native in [obj["native_id"], *obj["aliases"]] else []
    if source == "pubchem":
        return [obj for obj in _compounds([native]) if obj["native_id"] == native]
    obj = _zenodo(_json("zenodo", f"{zenodo.BASE_URL}/{native}"))
    return [obj] if obj and obj["native_id"] == native else []


def _get(source: str, native: str) -> list[dict]:
    try:
        return _get_native(source, native)
    except httpx.HTTPStatusError as error:
        if error.response.status_code == 404:
            return []
        raise


def _collect(calls: dict) -> tuple[list[dict], list[dict]]:
    packets = sources.SourceFlow({name: [call] for name, call in calls.items()},
                                  budget_s=30, hydrate=False, op="data").collect()
    reports = [{"source": p.source, "status": p.status, "count": len(p.results),
                "latency_ms": p.latency_ms, **({"error": p.error} if p.error else {})} for p in packets]
    return [{k: v for k, v in row.items() if not k.startswith("_")}
            for p in packets for row in p.results], reports


def _status(reports: list[dict]) -> str:
    if all(r["status"] in {"ok", "empty"} for r in reports):
        return "ok"
    return "partial" if any(r["count"] or r["status"] in {"ok", "empty"} for r in reports) else "unavailable"


def search(query: str, kind: str = "dataset", source: str = "", limit: int = 10) -> dict:
    """Search within a scientific object type; retain the providers' own order."""
    if kind not in KINDS:
        return {"error": "Unknown data kind", "allowed": list(KINDS)}
    if not query.strip():
        return {"error": "A nonempty query is required"}
    selected = list(KINDS[kind])
    if source:
        if source not in selected:
            return {"error": "Source does not serve this kind", "allowed": selected}
        selected = [source]
    limit = min(max(limit, 1), 50)
    with pacing.budget(30):
        objects, reports = _collect({s: partial(_search, s, query.strip(), limit) for s in selected})
    # Round robin preserves each database's rank without inventing a common score.
    by_source = [[obj for obj in objects if obj["source"] == s] for s in selected]
    merged = [rows[index] for index in range(limit) for rows in by_source if len(rows) > index][:limit]
    # Discovery stays compact even for proteins with hundreds of references or
    # GEO series with many samples; the native getter retains their detail.
    merged = [{**{k: obj[k] for k in ("id", "kind", "source", "native_id", "title", "url", "provenance")},
               "description": obj.get("description", "")[:1200],
               "description_truncated": len(obj.get("description", "")) > 1200,
               "organism": obj.get("organism", ""), "files_count": len(obj.get("files", [])),
               "relations_count": len(obj.get("relations", []))} for obj in merged]
    status = _status(reports)
    return {"query": query, "kind": kind, "objects": merged, "total": len(merged),
            "sources": reports, "status": status,
            **({"error": "Scientific sources could not be read"} if status == "unavailable" else {}),
            "scope": "First bounded result page; database order within each source, no cross-type relevance score."}


def info(data_id: str, limit: int = 20) -> dict:
    parsed = identifier(data_id)
    if parsed is None:
        return {"error": "Use a GEO GSE/GDS, SRA experiment/run, BioProject, NCBIGene, UniProt, PubChem CID or Zenodo ID/record URL."}
    source, native = parsed
    with pacing.budget(30):
        objects, reports = _collect({source: partial(_get, source, native)})
        if objects and source in {"gene", "sra", "bioproject"}:
            obj = objects[0]
            db = DATABASES[source][0]
            evidence_url = f"{pubmed.BASE_URL}/elink.fcgi?dbfrom={db}&db=pubmed&id={obj['source_record_id']}&retmode=json"
            def publications():
                return [_relation(f"PMID:{pid}", f"https://pubmed.ncbi.nlm.nih.gov/{pid}/", evidence_url, "cited_by")
                        for pid in _ncbi_links(db, obj["source_record_id"], "pubmed", min(max(limit, 1), 100))]
            links, link_reports = _collect({source + "/pubmed": publications})
            obj["relations"].extend(links)
            reports.extend(link_reports)
    if not objects:
        status = reports[0]["status"]
        missing = status in {"empty", "ok"}
        return {"requested_id": data_id, "status": "not_found" if missing else "unavailable", "sources": reports,
                "error": "Scientific record not found" if missing else "Scientific source could not be read"}
    obj = objects[0]
    count = len(obj["relations"])
    obj["relations"] = obj["relations"][:min(max(limit, 1), 100)]
    return {"requested_id": data_id, "object": obj,
            "status": _status(reports),
            "relations_truncated": count > len(obj["relations"]), "sources": reports}


def for_paper(paper: dict, limit: int = 20) -> dict:
    """Explicit database associations, not a claim that an article generated data."""
    ids = records.external_ids(paper)
    pmid, doi = ids.get("PubMed", ""), ids.get("DOI", "")
    if not pmid and doi:
        try:
            linked = europepmc.get_paper(doi)
            if linked and records.normalize_id("DOI", records.external_ids(linked).get("DOI", "")) == records.normalize_id("DOI", doi):
                pmid = records.external_ids(linked).get("PubMed", "")
        except Exception as error:
            return {"status": "unavailable", "objects": [], "error": f"PubMed identity lookup failed ({type(error).__name__})"}
    if not str(pmid).isdigit():
        return {"status": "unsupported", "objects": [], "scope": "Database associations require a verified PubMed identity."}
    limit = min(max(limit, 1), 50)

    def linked(source):
        db = DATABASES[source][0]
        uids = _ncbi_links("pubmed", pmid, db, limit)
        found = _ncbi_records(source, uids)
        for obj in found:
            obj["paper_relation"] = {"paper": f"PMID:{pmid}", "relation": "associated_with",
                                     "evidence": {"type": "database_link", "url": f"{pubmed.BASE_URL}/elink.fcgi?dbfrom=pubmed&db={db}&id={pmid}&retmode=json"}}
        return found

    with pacing.budget(30):
        objects, reports = _collect({s: partial(linked, s) for s in DATABASES})
    objects = [obj for source in DATABASES for obj in objects if obj.get("source") == source]
    return {"paper": f"PMID:{pmid}", "objects": objects[:limit], "sources": reports,
            "status": _status(reports),
            "scope": "NCBI PubMed links to GEO, SRA, BioProject and Gene; associations do not establish experimental use or causation."}
