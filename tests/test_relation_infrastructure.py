"""Cross-source relation, hydration and uncapped ranking contracts."""
from copy import deepcopy

import httpx
import pytest

from scholar_mcp import crossref_client as cr, europepmc_client as ep, metadata, relevance, sources, expansion


def test_crossref_outage_does_not_repeat_for_every_missing_doi(monkeypatch):
    calls = []
    monkeypatch.setattr(cr, "_retry_at", 0)
    monkeypatch.setattr(cr.time, "sleep", lambda _: None)
    def overloaded(url, **kwargs):
        calls.append(url)
        return httpx.Response(503, request=httpx.Request("GET", url))
    monkeypatch.setattr(httpx, "get", overloaded)
    with pytest.raises(httpx.HTTPStatusError):
        cr.get_paper("10.1000/first")
    with pytest.raises(RuntimeError, match="temporarily"):
        cr.get_paper("10.1000/second")
    assert len(calls) == 2


def test_reference_records_keep_machine_identifiers_and_original_citation():
    ref = {"DOI": "10.1000/example", "unstructured": "A citation", "year": "2020"}
    paper = cr.reference_paper(ref)
    assert paper["external_ids"]["DOI"] == "10.1000/example"
    assert paper["title"] == "" and paper["_reference"] == ref
    assert cr.reference_paper({"unstructured": "Preprint at http://arxiv.org/abs/1409.4842"})["external_ids"]["ArXiv"] == "1409.4842"


def test_crossref_relations_do_not_require_s2(monkeypatch):
    monkeypatch.setattr(cr, "_get_work", lambda doi: {"reference": [{"DOI": "10.1000/child"}]})
    assert cr.get_references("10.1000/seed", 20)[0]["external_ids"]["DOI"] == "10.1000/child"
    assert cr.get_references("W123", 20) == []


def test_hydration_deduplicates_dois_and_does_not_invent_source_agreement(monkeypatch):
    calls = []
    monkeypatch.setattr(metadata, "_batch_dois", lambda dois: {})
    def lookup(doi):
        calls.append(doi)
        return {"title": "Resolved title", "authors": ["Author"], "year": 2020, "venue": "Journal",
                "abstract": "Full abstract", "source": "crossref", "external_ids": {"DOI": doi}}
    monkeypatch.setattr(metadata, "_doi", lookup)
    papers = [cr.reference_paper({"DOI": "10.1000/x"}) for _ in range(2)]
    papers[1]["source"] = "europepmc"
    metadata.hydrate(papers)
    assert calls == ["10.1000/x"]
    assert all(p["title"] == "Resolved title" for p in papers)
    assert [p["source"] for p in papers] == ["crossref", "europepmc"]
    assert all("_source_count" not in p for p in papers)


def test_batch_doi_matching_uses_identity_not_result_position(monkeypatch):
    monkeypatch.setattr(metadata.oa, "_params_base", lambda: {})
    monkeypatch.setattr(metadata.oa, "_request", lambda *a, **k: httpx.Response(200, json={"results": [
        {"doi": "https://doi.org/10.1000/b", "title": "B"},
        {"doi": "https://doi.org/10.1000/a", "title": "A"},
    ]}, request=httpx.Request("GET", "https://api.openalex.org/works")))
    monkeypatch.setattr(metadata.oa, "format_paper", lambda w: {"title": w["title"]})
    assert metadata._batch_dois(["10.1000/a", "10.1000/b"])["10.1000/a"]["title"] == "A"


def test_europepmc_native_xml_recovers_references_during_endpoint_maintenance(monkeypatch):
    monkeypatch.setattr(ep, "_identity", lambda _: {"id": "123", "source": "MED", "pmcid": "PMC456"})
    monkeypatch.setattr(httpx.Client, "get", lambda *a, **kw: httpx.Response(503))
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: httpx.Response(200, content=b'''<article><back><ref-list>
      <ref><element-citation><article-title>Child</article-title><source>Journal</source><year>2020</year>
      <pub-id pub-id-type="doi">10.1000/child</pub-id></element-citation></ref>
      </ref-list></back></article>''', request=httpx.Request("GET", "https://example.test")))
    result = ep.get_references("10.1000/parent")
    assert result[0]["title"] == "Child" and result[0]["external_ids"]["DOI"] == "10.1000/child"


def test_core_pmc_metadata_retains_journal_and_author_fields():
    paper = ep.format_paper({"id": "123", "source": "MED", "title": "Paper", "authorString": "A Author, B Author.",
                             "journalInfo": {"journal": {"title": "Journal"}}, "pubYear": "2024"})
    assert paper["authors"] == ["A Author", "B Author"]
    assert paper["venue"] == "Journal"
    assert paper["external_ids"]["PMID"] == "123"


def test_registry_additions_reach_graph_and_expansion():
    assert {s.name for s in sources.citation_sources()} >= {"semantic_scholar", "openalex", "europepmc"}
    assert {s.name for s in sources.reference_sources()} >= {"semantic_scholar", "openalex", "europepmc", "crossref"}


def test_missing_citation_metric_does_not_remove_verified_new_source_edges(monkeypatch):
    paper = {"title": "New paper", "citation_count": 0, "_citation_count_known": False}
    monkeypatch.setattr(sources, "parallel_citations", lambda *a, **kw: [sources.SourceResult("europepmc", "ok", [paper], 1)])
    assert expansion.citations({"paper_id": "10.1000/seed"}, expansion.ExpansionContext(intent="method")) == [paper]


def test_every_candidate_reaches_remote_reranker_including_last_batch(monkeypatch):
    seen = []
    def remote(query, batch, top_n, intent):
        seen.extend(p["title"] for p in batch)
        for p in batch:
            p.update(_rerank_score=1.0 if p["title"] == "target" else 0.1, _reranker_provider="test")
        return sorted(batch, key=lambda p: -p["_rerank_score"])[:top_n]
    monkeypatch.setattr(relevance, "_remote_batch", remote)
    papers = [{"title": str(i)} for i in range(1100)] + [{"title": "target"}]
    assert relevance.rerank("q", papers, 5)[0]["title"] == "target"
    assert len(seen) == 1101 and len(set(seen)) == 1101


def test_batch_failure_uses_one_consistent_provider_for_the_whole_pool(monkeypatch):
    calls = []
    def remote(query, batch, top_n, intent):
        calls.append(1)
        if len(calls) == 2:
            return None
        for p in batch:
            p.update(_rerank_score=1.0, _reranker_provider="cloud")
        return batch
    local = []
    def fallback(query, papers, top_n):
        local.extend(papers)
        for p in papers:
            p.update(_rerank_score=0.2, _reranker_provider="local")
        return papers[:top_n]
    monkeypatch.setattr(relevance, "_remote_batch", remote)
    monkeypatch.setattr(relevance, "_rerank_flashrank", fallback)
    result = relevance.rerank("q", deepcopy([{"title": str(i)} for i in range(600)]), 5)
    assert len(local) == 600
    assert all(p["_reranker_provider"] == "local" for p in result)
