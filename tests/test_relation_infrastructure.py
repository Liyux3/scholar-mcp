"""Cross-source relation, hydration and uncapped ranking contracts."""
from copy import deepcopy

import httpx
import pytest

from scholar_mcp import crossref_client as cr, europepmc_client as ep, metadata, relevance, sources, expansion
from scholar_mcp import inspirehep_client as inspire


def test_doi_and_pmid_hydration_overlap(monkeypatch):
    from threading import Event
    doi_started, pmid_started = Event(), Event()
    def dois(ids):
        doi_started.set()
        assert pmid_started.wait(3)
        return {ids[0]: {"title": "DOI paper"}}
    def pmids(ids):
        pmid_started.set()
        assert doi_started.wait(3)
        return {ids[0]: {"title": "PMID paper"}}
    monkeypatch.setattr(metadata, "_batch_dois", dois)
    monkeypatch.setattr(metadata, "_batch_pmids", pmids)
    papers = [{"external_ids": {"DOI": "10.1000/example"}}, {"external_ids": {"PubMed": "1234"}}]
    assert [p["title"] for p in metadata.hydrate(papers)] == ["DOI paper", "PMID paper"]


def test_successfully_checked_missing_field_is_not_fetched_in_every_pipeline_stage(monkeypatch):
    calls = []
    def batch(ids):
        calls.append(ids)
        return {"10.1000/example": {"title": "Paper with unavailable type"}}
    monkeypatch.setattr(metadata, "_batch_dois", batch)
    paper = {"title": "Paper", "external_ids": {"DOI": "10.1000/example"}}
    metadata.hydrate([paper], fields={"publication_types"})
    merged = relevance.deduplicate([paper, {"title": "Paper", "external_ids": paper["external_ids"]}])
    metadata.hydrate(merged, fields={"publication_types"})
    assert len(calls) == 1 and not merged[0].get("publication_types")


def test_arxiv_doi_hydrates_natively_instead_of_repeating_catalog_misses(monkeypatch):
    from scholar_mcp import arxiv_client
    seen = []
    monkeypatch.setattr(metadata, "_batch_dois", lambda ids: seen.extend(ids) or {})
    monkeypatch.setattr(metadata, "_doi", lambda _: pytest.fail("arXiv DOI is not a Crossref deposit"))
    monkeypatch.setattr(arxiv_client, "get_paper", lambda pid: {"title": "Native arXiv paper", "is_open_access": True})
    paper = {"external_ids": {"DOI": "10.48550/arXiv.1706.03762"}}
    metadata.hydrate([paper])
    assert paper["title"] == "Native arXiv paper" and seen == []


def test_requested_metadata_fields_are_completed_without_overwriting_known_values(monkeypatch):
    monkeypatch.setattr(metadata, "_batch_dois", lambda ids: {"10.1000/example": {
        "title": "Native title", "year": 2025, "publication_types": ["Review"], "is_open_access": True}})
    paper = {"title": "Original title", "year": 2024, "external_ids": {"DOI": "10.1000/example"}}
    metadata.hydrate([paper], fields={"publication_types", "is_open_access"})
    assert paper["title"] == "Original title" and paper["year"] == 2024
    assert paper["publication_types"] == ["Review"] and paper["is_open_access"]


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
    assert {s.name for s in sources.citation_sources()} >= {"semantic_scholar", "openalex", "europepmc", "inspirehep"}
    assert {s.name for s in sources.reference_sources()} >= {"semantic_scholar", "openalex", "europepmc", "crossref", "inspirehep"}


def test_bibliographic_hint_requires_exact_title_and_correct_year(monkeypatch):
    from scholar_mcp import dblp_client, s2_client
    monkeypatch.setattr(cr, "match_reference", lambda *a: None)
    monkeypatch.setattr(cr, "reference_candidates", lambda text: [{"title": ["A distinctive scientific paper title"]}])
    monkeypatch.setattr(dblp_client, "search_papers", lambda *a, **kw: [])
    monkeypatch.setattr(s2_client, "is_healthy", lambda: True)
    monkeypatch.setattr(s2_client, "search_match", lambda title: {"title": title, "year": 2017})
    text = "A Author. A distinctive scientific paper title. (2012)."
    assert metadata._bibliographic(text, "", "A Author", "2012", "") is None
    monkeypatch.setattr(s2_client, "search_match", lambda title: {"title": title, "year": 2012})
    assert metadata._bibliographic(text, "", "A Author", "2012", "")["year"] == 2012


def test_bibliographic_fallback_does_not_probe_overloaded_s2(monkeypatch):
    from scholar_mcp import dblp_client, s2_client
    monkeypatch.setattr(cr, "match_reference", lambda *a: None)
    monkeypatch.setattr(dblp_client, "search_papers", lambda *a, **kw: [])
    monkeypatch.setattr(s2_client, "is_healthy", lambda: False)
    monkeypatch.setattr(s2_client, "search_match", lambda *a: pytest.fail("must not consume S2 quota"))
    assert metadata._bibliographic("A sufficiently specific paper title", "A sufficiently specific paper title", "", "2020", "") is None


def test_inspire_native_references_are_resolved_in_batches(monkeypatch):
    monkeypatch.setattr(inspire, "_record", lambda _: {"metadata": {"references": [
        {"record": {"$ref": "https://inspirehep.net/api/literature/123"}},
        {"record": {"$ref": "https://inspirehep.net/api/literature/456"}},
    ]}})
    seen = []
    monkeypatch.setattr(inspire, "search_papers", lambda q, limit: seen.append(q) or [{"title": "Native record"}])
    assert inspire.get_references("INSPIRE:1")[0]["title"] == "Native record"
    assert seen == ["recid:123 OR recid:456"]


def test_inspire_identifiers_cannot_be_confused_with_bare_pmids():
    assert inspire._record("12345678") == {}
    p = inspire.format_paper({"id": "123", "metadata": {"titles": [{"title": "Paper"}]}})
    assert p["paper_id"] == "INSPIRE:123"


def test_inspire_resolves_arxiv_doi_as_native_arxiv_identifier(monkeypatch):
    queries = []
    def get(url, params, **kwargs):
        queries.append(params["q"])
        return httpx.Response(200, json={"hits": {"hits": [{"id": "123"}]}}, request=httpx.Request("GET", url))
    monkeypatch.setattr(httpx, "get", get)
    assert inspire._record("10.48550/arXiv.1706.03762")["id"] == "123"
    assert queries == ["arxiv:1706.03762"]


def test_redeposit_does_not_duplicate_original_work_or_replace_its_year():
    records = [
        {"title": "Attention Is All You Need", "year": 2025, "authors": ["Ashish Vaswani", "Noam Shazeer"],
         "external_ids": {"DOI": "10.1000/redeposit"}},
        {"title": "Attention is all you need", "year": 2017, "authors": ["A Vaswani", "N Shazeer"],
         "external_ids": {"DOI": "10.1000/original"}},
    ]
    result = relevance.deduplicate(records)
    assert len(result) == 1 and result[0]["year"] == 2017
    assert result[0]["external_ids"]["DOI"] == "10.1000/original"


def test_generic_annual_reviews_remain_distinct():
    records = [{"title": "Review of Particle Physics", "year": year,
                "authors": ["A Author", "B Author"], "external_ids": {"DOI": f"10.1000/{year}"}}
               for year in (2018, 2022)]
    assert len(relevance.deduplicate(records)) == 2


def test_later_same_title_does_not_hide_an_earlier_compatible_group():
    records = [{"title": "A sufficiently specific repeated scientific title", "year": year,
                "authors": authors} for year, authors in [(2017, ["A Smith", "B Jones"]),
                                                          (2020, ["C Other", "D Person"]),
                                                          (2017, ["Alice Smith", "Bob Jones"])]]
    assert len(relevance.deduplicate(records)) == 2


def test_arxiv_doi_and_arxiv_id_share_identity_even_with_corrupt_provider_title(monkeypatch):
    from scholar_mcp import arxiv_client
    records = [{"title": "Actual original paper title", "external_ids": {"ArXiv": "2005.11401"}},
               {"title": "Incorrect unrelated provider title", "external_ids": {"DOI": "10.48550/arxiv.2005.11401"}}]
    result = relevance.deduplicate(records)
    assert len(result) == 1 and result[0]["_title_conflict"]
    monkeypatch.setattr(metadata, "_batch_dois", lambda _: {})
    monkeypatch.setattr(arxiv_client, "get_paper", lambda _: {"title": "Actual original paper title", "source": "arxiv"})
    metadata.hydrate(result)
    assert result[0]["title"] == "Actual original paper title"
    assert not result[0].get("_title_conflict")


def test_native_title_repair_preserves_known_citations(monkeypatch):
    from scholar_mcp import arxiv_client
    paper = {"title": "Corrupt title", "abstract": "Wrong abstract", "_title_conflict": True,
             "_needs_metadata": True, "citation_count": 1000, "_citation_count_known": True,
             "external_ids": {"ArXiv": "1706.03762"}}
    monkeypatch.setattr(metadata, "_batch_dois", lambda _: {})
    monkeypatch.setattr(arxiv_client, "get_paper", lambda _: {"title": "Correct title", "abstract": "Original abstract",
                       "citation_count": 0, "_citation_count_known": False, "source": "arxiv"})
    metadata.hydrate([paper])
    assert paper["citation_count"] == 1000 and paper["_citation_count_known"] is True
    assert paper["title"] == "Correct title" and paper["abstract"] == "Original abstract"


def test_hydration_keeps_resolved_native_paper_id(monkeypatch):
    monkeypatch.setattr(metadata, "_batch_dois", lambda _: {})
    monkeypatch.setattr(metadata, "_bibliographic", lambda *args: {"title": "Resolved paper", "paper_id": "native-id", "source": "semantic_scholar"})
    paper = {"title": "", "paper_id": "", "_reference": {"unstructured": "A reference"}, "_needs_metadata": True}
    metadata.hydrate([paper])
    assert paper["paper_id"] == "native-id"


def test_pipeline_applies_full_ranking_before_any_result_cut(monkeypatch):
    from scholar_mcp import server
    papers = [{"title": f"Filler document {i}", "citation_count": 1, "_rerank_score": 0.82} for i in range(99)]
    papers.append({"title": "Target foundation paper", "citation_count": 100000, "_rerank_score": 0.81})
    monkeypatch.setattr(sources, "parallel_search", lambda *a, **kw: [sources.SourceResult("fixture", "ok", papers, 1)])
    seen = []
    def rerank(query, rows, top_n, **kw):
        seen.append(top_n)
        return sorted(rows, key=lambda p: -p["_rerank_score"])[:top_n]
    monkeypatch.setattr(relevance, "rerank", rerank)
    monkeypatch.setattr(relevance, "_load_rank_params", lambda: {"gamma": 1, "alpha": .05, "beta": .02, "delta": .1})
    results, _ = server._pipeline("search", "query", 1, rerank_query="query", expand_citations=False)
    assert seen == [100]
    assert results[0]["title"] == "Target foundation paper"


def test_search_filters_full_pool_before_final_limit(monkeypatch):
    from scholar_mcp import server
    import yaml
    papers = [{"title": f"Earlier ranked paper {i}", "year": 2020} for i in range(10)]
    papers.append({"title": "Requested recent paper", "year": 2025})
    def pipeline(dispatch, query, limit, **kwargs):
        return (papers if kwargs.get("return_all") else papers[:limit]), []
    monkeypatch.setattr(server, "_pipeline", pipeline)
    result = yaml.safe_load(server.search_papers("research question", year="2025", limit=1))
    assert result["results"][0]["title"] == "Requested recent paper"


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
