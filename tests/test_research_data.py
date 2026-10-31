"""Scientific identity and source boundaries, with one opt-in real workflow."""
import asyncio
from copy import deepcopy
import json

import httpx
import pytest

from scholar_mcp import api, cli, install, research_data as data, server


@pytest.mark.parametrize("value,expected", [
    ("GSE68849", ("geo", "GSE68849")),
    ("GEO:GDS6063", ("geo", "GDS6063")),
    ("https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE68849", ("geo", "GSE68849")),
    ("SRA:SRR2185317", ("sra", "SRR2185317")),
    ("SRX1165323", ("sra", "SRX1165323")),
    ("BioProject:PRJNA293818", ("bioproject", "PRJNA293818")),
    ("NCBIGene:7157", ("gene", "7157")),
    ("https://www.ncbi.nlm.nih.gov/gene/7157", ("gene", "7157")),
    ("UniProt:P04637", ("uniprot", "P04637")),
    ("https://www.uniprot.org/uniprotkb/P04637/entry", ("uniprot", "P04637")),
    ("PubChem:2244", ("pubchem", "2244")),
    ("https://pubchem.ncbi.nlm.nih.gov/compound/2244", ("pubchem", "2244")),
    ("https://doi.org/10.5281/zenodo.123", ("zenodo", "123")),
    ("https://zenodo.org/records/123", ("zenodo", "123")),
    ("TP53", None), ("7157", None),
    ("https://www.ncbi.nlm.nih.gov.evil.test/gene/7157", None),
    ("https://example.test/UniProt:P04637", None),
])
def test_native_id_and_official_url_boundaries(value, expected):
    assert data.identifier(value) == expected


def test_native_summaries_preserve_types_and_partial_records(monkeypatch):
    payload = {"result": {"1": {"uid": "1", "accession": "GSE1", "title": "Experiment",
                                  "pubmedids": ["12"], "bioproject": "PRJNA1", "n_samples": 3},
                          "2": {"error": "cannot get document summary"}}}
    monkeypatch.setattr(data, "_json", lambda *a, **kw: deepcopy(payload))
    records = data._ncbi_records("geo", ["1", "2"])
    assert records.partial_warning
    obj = records[0]
    assert (obj["id"], obj["kind"], obj["properties"]["n_samples"]) == ("GEO:GSE1", "dataset", 3)
    assert [r["target"] for r in obj["relations"]] == ["PMID:12", "BioProject:PRJNA1"]
    assert "paper_id" not in obj and "citation_count" not in obj
    monkeypatch.setattr(data, "_ncbi_search", lambda *a: ["1"])
    assert data.info("GSE99")["status"] == "not_found"


def test_sra_run_is_a_confirmed_alias_of_its_experiment(monkeypatch):
    item = {"expxml": '<Summary><Title>RNA experiment</Title></Summary><Experiment acc="SRX12"/>'
                        '<Organism ScientificName="Homo sapiens"/><Bioproject>PRJNA1</Bioproject>',
            "runs": '<Run acc="SRR13"/><Run acc="SRR14"/>'}
    monkeypatch.setattr(data, "_json", lambda *a, **kw: {"result": {"9": item}})
    monkeypatch.setattr(data, "_ncbi_search", lambda *a: ["9"])
    monkeypatch.setattr(data, "_ncbi_links", lambda *a: [])
    result = data.info("SRR13")
    assert result["object"]["id"] == "SRA:SRX12"
    assert result["object"]["aliases"] == ["SRR13", "SRR14"]
    assert data.info("SRR99")["status"] == "not_found"


def test_ncbi_accession_fields_and_legitimate_empty_search(monkeypatch):
    calls = []
    def request(provider, url, **params):
        calls.append(params)
        return {"esearchresult": {"idlist": [], "errorlist": {"phrasesnotfound": [params["term"]]}}}
    monkeypatch.setattr(data, "_json", request)
    assert data._get("bioproject", "PRJNA999") == []
    assert calls[0]["term"] == "PRJNA999[PRJA]"
    assert data._ncbi_search("gds", "no matching phrase", 1) == []


def test_protein_and_compound_metadata_keep_native_evidence(monkeypatch):
    protein = data._uniprot({"primaryAccession": "P04637", "entryType": "reviewed",
        "proteinDescription": {"recommendedName": {"fullName": {"value": "p53"}}},
        "sequence": {"length": 393, "value": "SEQUENCE_MUST_NOT_BE_RETURNED"},
        "references": [{"citation": {"citationCrossReferences": [{"database": "PubMed", "id": "12"}]}}],
        "uniProtKBCrossReferences": [{"database": "GeneID", "id": "7157"}]})
    assert protein["id"] == "UniProt:P04637"
    assert protein["properties"]["length"] == 393
    assert "SEQUENCE_MUST_NOT_BE_RETURNED" not in str(protein)
    assert {r["target"] for r in protein["relations"]} == {"PMID:12", "NCBIGene:7157"}
    monkeypatch.setattr(data, "_json", lambda *a, **kw: {"PropertyTable": {"Properties": [{"CID": 2244, "Title": "Aspirin", "MolecularFormula": "C9H8O4"}]}})
    assert data.info("PubChem:2244")["object"]["properties"]["MolecularFormula"] == "C9H8O4"
    assert data.info("PubChem:999")["status"] == "not_found"


def test_zenodo_keeps_dataset_files_versions_and_native_relations():
    item = {"id": 12, "metadata": {"title": "Measurements", "resource_type": {"type": "dataset"},
        "version": "2.0", "related_identifiers": [{"scheme": "doi", "identifier": "10.1/paper", "relation": "isSupplementTo"}]},
        "files": [{"key": "data.csv", "size": 500, "checksum": "md5:abc", "links": {"self": "https://zenodo.org/api/records/12/files/data.csv/content"}}]}
    obj = data._zenodo(item)
    assert obj["files"][0]["checksum"] == "md5:abc"
    assert obj["properties"]["version"] == "2.0"
    assert obj["relations"][0]["relation"] == "isSupplementTo"
    item["metadata"]["resource_type"]["type"] = "publication"
    assert data._zenodo(item) is None


def test_failed_source_leaves_other_typed_results_usable(monkeypatch):
    def search(source, query, limit):
        if source == "zenodo":
            raise httpx.ConnectError("unavailable")
        return [{"id": "GEO:GSE1", "source": "geo", "kind": "dataset", "title": "A"}]
    monkeypatch.setattr(data, "_search", search)
    result = api.search_data("experiment")
    assert result["status"] == "partial" and result["objects"][0]["id"] == "GEO:GSE1"
    assert {r["source"]: r["status"] for r in result["sources"]} == {"geo": "ok", "zenodo": "error"}
    assert api.search_data("experiment", kind="gene", source="zenodo")["error"]


def test_native_request_reuses_cache_and_shared_ncbi_quota(monkeypatch):
    waits, calls = [], []
    monkeypatch.setattr(data.config, "NCBI_API_KEY", "fixture-secret")
    monkeypatch.setattr(data.pacing, "wait", lambda *a, **kw: waits.append((a, kw)))
    def get(url, **kw):
        calls.append(kw)
        return httpx.Response(200, json={"result": {"7157": {"uid": "7157"}}}, request=httpx.Request("GET", url))
    monkeypatch.setattr(data.access, "get", get)
    first = data._json("ncbi", data.pubmed.BASE_URL + "/esummary.fcgi", db="gene", id="7157")
    first["result"].clear()
    second = data._json("ncbi", data.pubmed.BASE_URL + "/esummary.fcgi", db="gene", id="7157")
    assert second["result"]["7157"]["uid"] == "7157"
    assert len(calls) == 1 and waits == [(("ncbi",), {"key": "fixture-secret"})]
    assert calls[0]["params"]["api_key"] == "fixture-secret"
    assert "fixture-secret" not in str(second)


def test_paper_links_require_verified_identity_and_are_explicit(monkeypatch):
    monkeypatch.setattr(data.europepmc, "get_paper", lambda _: {"external_ids": {"DOI": "10.1/other", "PMID": "12"}})
    assert data.for_paper({"external_ids": {"DOI": "10.1/requested"}})["status"] == "unsupported"
    calls = []
    def links(db, uid, target, limit):
        calls.append((db, uid, target))
        return ["1"] if target == "gds" else []
    monkeypatch.setattr(data, "_ncbi_links", links)
    monkeypatch.setattr(data, "_ncbi_records", lambda s, u: [{"id": "GEO:GSE1", "source": s}] if u else [])
    result = data.for_paper({"external_ids": {"PMID": "12"}})
    assert result["objects"][0]["paper_relation"]["paper"] == "PMID:12"
    assert result["objects"][0]["paper_relation"]["evidence"]["type"] == "database_link"
    assert len(calls) == 4
    monkeypatch.setattr(server, "_find_paper", lambda *_: {"title": "Paper", "external_ids": {"PMID": "12"}})
    calls.clear()
    server.paper_info("PMID:12")
    assert calls == []
    assert server.paper_info("PMID:12", include="data")["data"]["objects"]


def test_science_extension_cli_and_python_share_one_boundary(monkeypatch, capsys):
    monkeypatch.setattr(data, "info", lambda identifier, limit=20: {"object": {"id": identifier}})
    assert cli.main(["data-info", "NCBIGene:7157", "--json"]) == 0
    assert '"id": "NCBIGene:7157"' in capsys.readouterr().out
    assert install.launch_spec("tool", "science").env == {"SCHOLAR_MCP_EXTENSIONS": "science"}
    registered = []
    monkeypatch.setenv("SCHOLAR_MCP_EXTENSIONS", "science")
    monkeypatch.setattr(server, "_tool", lambda **kw: lambda fn: registered.append(fn.__name__))
    server._register_extensions()
    assert registered == ["search_data", "data_info"]


@pytest.mark.integration
def test_live_scientific_records_and_paper_associations():
    # Real public metadata, bounded reads; raw sequence files are never fetched.
    for identifier, kind in [("GSE68849", "dataset"), ("SRX1165323", "sequence"),
                             ("PRJNA293818", "project"), ("NCBIGene:7157", "gene"),
                             ("UniProt:P04637", "protein"), ("PubChem:2244", "compound")]:
        result = api.data_info(identifier, limit=3)
        assert result.get("object", {}).get("kind") == kind, json.dumps(result)
    genes = api.search_data("TP53[Gene Name] AND human[Organism]", kind="gene", limit=2)
    assert any(o["id"] == "NCBIGene:7157" for o in genes["objects"]), genes
    proteins = api.search_data("gene_exact:TP53 AND organism_id:9606 AND reviewed:true", kind="protein", limit=2)
    assert any(o["id"] == "UniProt:P04637" for o in proteins["objects"]), json.dumps(proteins)
    result = api.info("PMID:26826244", include="detail,data", limit=5)
    assert any(o["id"] == "GEO:GSE68849" for o in result["data"]["objects"]), result
    datasets = api.search_data("biodiversity", kind="dataset", source="zenodo", limit=1)
    assert datasets["objects"], datasets
    deposited = api.data_info(datasets["objects"][0]["id"])
    assert deposited["object"]["kind"] == "dataset" and deposited["object"]["files"]


@pytest.mark.integration
def test_live_science_mcp_contract():
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    import os
    import sys

    async def run():
        params = StdioServerParameters(command=sys.executable, args=["-m", "scholar_mcp"],
            env={**os.environ, "SCHOLAR_MCP_EXTENSIONS": "science", "SCHOLAR_USAGE_LOG": "off"})
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                names = {t.name for t in (await session.list_tools()).tools}
                assert len(names) == 8 and {"search_data", "data_info"} <= names
                response = await session.call_tool("data_info", {"data_id": "PubChem:2244"})
                assert response.structuredContent["object"]["id"] == "PubChem:2244"
    asyncio.run(run())
