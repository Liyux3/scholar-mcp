"""Cross-source relation, hydration and uncapped ranking contracts."""

from copy import deepcopy

import httpx
import pytest

from scholar_mcp import expansion, metadata, relevance, sources
from scholar_mcp import records as record_ops
from scholar_mcp.sources import crossref as cr
from scholar_mcp.sources import europepmc as ep
from scholar_mcp.sources import inspirehep as inspire


def test_distinct_dois_do_not_merge_on_a_generic_title_in_adjacent_years():
    papers = [
        {"title": "Deep learning", "year": 2015, "external_ids": {"DOI": "10.1000/article"}},
        {"title": "Deep Learning", "year": 2016, "external_ids": {"DOI": "10.1000/book"}},
    ]
    assert len(record_ops.deduplicate(papers)) == 2


def test_book_review_and_chapter_keep_distinct_identities_with_same_title():
    title = "Heat islands understanding and mitigating heat in urban areas"
    records = [{"title": title, "year": 2008, "publication_types": [kind],
                "external_ids": {"DOI": f"10.1000/{kind}"}}
               for kind in ("book", "journal-article", "book-chapter")]
    assert len(record_ops.deduplicate(records)) == 3


@pytest.mark.parametrize('authors', [(['Alice Brown'], ['Robert Green']), (['Alice Smith'], ['Bob Smith']),
                                     (['Alice Brown'], ['Arthur Brown'])])
def test_same_title_and_year_do_not_join_different_known_authors(authors):
    records = [{'title': 'Artificial Intelligence and the Rule of Law', 'year': 2025,
                'authors': names, 'external_ids': {'DOI': f'10.1000/{i}'}} for i, names in enumerate(authors)]
    assert len(record_ops.deduplicate(records)) == 2


def test_weak_title_hit_cannot_bridge_independent_works_in_any_arrival_order():
    from itertools import permutations
    title = 'Artificial Intelligence and the Rule of Law'
    records = [{'title': title, 'year': 2025, 'authors': [name], 'external_ids': {'DOI': f'10.1000/{i}'}}
               for i, name in enumerate(['Alice Brown', 'Robert Green'])]
    records.append({'title': title, 'year': 2025, 'authors': [], 'paper_id': 'gs_unknown'})
    for order in permutations(records):
        merged = record_ops.deduplicate(list(order))
        known = [p for p in merged if p.get('external_ids', {}).get('DOI')]
        assert len(known) == 2
        assert {tuple(p['authors']) for p in known} == {('Alice Brown',), ('Robert Green',)}


def test_single_author_preprint_and_publication_accept_initials_and_name_order():
    records = [{'title': 'A precise theory of algorithmic accountability', 'year': 2025, 'authors': [author],
                'external_ids': {'DOI': doi}} for author, doi in [
                    ('Brown, Alice', '10.48550/arxiv.2501.12345'), ('A. Brown', '10.1000/published')]]
    assert len(record_ops.deduplicate(records)) == 1


def test_diacritic_spelling_is_not_a_conflicting_author():
    records = [{'title': 'A precise theory of ideology and discourse', 'year': 2025, 'authors': [name]}
               for name in ['Slavoj Žižek', 'Zizek, Slavoj']]
    merged = record_ops.deduplicate(records)
    assert len(merged) == 1 and merged[0]['authors'][0] in records[0]['authors'] + records[1]['authors']


def test_different_dois_with_missing_authors_remain_ambiguous():
    records = [{'title': 'A precise theory of algorithmic accountability', 'year': 2025,
                'external_ids': {'DOI': f'10.1000/{i}'}} for i in range(2)]
    assert len(record_ops.deduplicate(records)) == 2


def test_native_batch_misses_are_reused_after_pool_order_changes(monkeypatch):
    from scholar_mcp import cache
    from scholar_mcp.sources import arxiv, europepmc
    calls = {'arxiv': 0, 'pubmed': 0}
    def arxiv_miss(ids):
        calls['arxiv'] += 1
        return {}
    def pmid_miss(*args):
        calls['pubmed'] += 1
        return []
    monkeypatch.setattr(arxiv, 'get_papers', arxiv_miss)
    monkeypatch.setattr(europepmc, '_request', pmid_miss)
    with cache.request_scope():
        assert metadata._batch_arxiv(['1706.03762', '2005.11401']) == {}
        assert metadata._batch_arxiv(['2005.11401', '1706.03762', '1706.03762']) == {}
        assert metadata._batch_pmids(['11', '22']) == {}
        assert metadata._batch_pmids(['22', '11', '22']) == {}
    assert calls == {'arxiv': 1, 'pubmed': 1}


@pytest.mark.parametrize('field', ['citation_count', 'fields_of_study', 'publication_types'])
def test_arxiv_filter_does_not_fetch_pages_without_the_required_metadata(monkeypatch, field):
    from scholar_mcp.sources import arxiv
    monkeypatch.setattr(arxiv, 'get_papers', lambda ids: {})
    monkeypatch.setattr(arxiv, 'get_paper', lambda *a: pytest.fail('abstract page cannot fill this metadata field'))
    row = {'title': 'Known identity', 'external_ids': {'ArXiv': '2012.06010'},
           'citation_count': 0, '_citation_count_known': False}
    metadata.hydrate([row], fields={field})
    assert row['citation_count'] == 0 and not row['_citation_count_known']


def test_expiring_acquisition_keeps_completed_metadata_and_freezes_the_result(monkeypatch):
    from threading import Event

    from scholar_mcp.sources import pacing
    entered, release = Event(), Event()
    clock = [1000]
    monkeypatch.setattr(pacing.time, 'monotonic', lambda: clock[0])
    def slow(*args):
        entered.set()
        assert release.wait(5)
        return {'title': 'Late citation'}
    monkeypatch.setattr(metadata, '_bibliographic', slow)
    monkeypatch.setattr(metadata, '_batch_dois', lambda ids: {identity: {'title': 'Native identity'} for identity in ids})
    def finish_native_then_expire(futures, timeout):
        assert timeout == 10 and entered.wait(5)
        futures[0].result(timeout=5)
        clock[0] += 11
    monkeypatch.setattr(metadata, 'wait', finish_native_then_expire)
    native = {'_needs_metadata': True, 'external_ids': {'DOI': '10.1000/native'}}
    pending = {'_needs_metadata': True, '_reference': {'unstructured': 'Original citation'}}
    try:
        with pacing.budget(10):
            metadata.hydrate([native, pending])
        assert entered.is_set() and not release.is_set()
        assert native['title'] == 'Native identity' and 'title' not in pending
    finally:
        release.set()


def test_slow_bibliographic_work_does_not_block_native_shortlist_metadata(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    entered, release = Event(), Event()
    def bibliographic(*args):
        entered.set()
        assert release.wait(5)
        return {'title': 'Resolved citation'}
    monkeypatch.setattr(metadata, '_bibliographic', bibliographic)
    monkeypatch.setattr(metadata, '_batch_dois', lambda ids: {
        identity: {'title': 'Resolved native work', 'abstract': 'Complete summary'} for identity in ids})
    with ThreadPoolExecutor(max_workers=1) as references, ThreadPoolExecutor(max_workers=2) as callers:
        monkeypatch.setattr(metadata, '_reference_pool', references)
        slow = callers.submit(metadata.hydrate, [{'_needs_metadata': True, '_reference': {'unstructured': 'Raw citation'}}])
        try:
            assert entered.wait(3)
            paper = {'_needs_metadata': True, 'external_ids': {'DOI': '10.1000/native'}}
            done = callers.submit(metadata.hydrate, [paper], {'abstract'})
            assert done.result(timeout=3)[0]['abstract'] == 'Complete summary'
        finally:
            release.set()
        assert slow.result()[0]['title'] == 'Resolved citation'


def test_known_bibliographic_miss_bypasses_a_busy_fallback_queue(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from scholar_mcp import cache
    release = Event()
    resolver = cache.cached(ttl=3600, miss_ttl=30)(lambda *args: None)
    monkeypatch.setattr(metadata, '_bibliographic', resolver)
    paper = {'title': 'Known citation title', '_needs_metadata': True,
             '_reference': {'unstructured': 'A literal original citation'}}
    with ThreadPoolExecutor(max_workers=1) as references, ThreadPoolExecutor(max_workers=1) as caller:
        monkeypatch.setattr(metadata, '_reference_pool', references)
        occupied = references.submit(release.wait, 5)
        try:
            with cache.request_scope():
                assert resolver(*metadata._reference_args(paper)) is None
                from contextvars import copy_context
                done = caller.submit(copy_context().run, metadata.hydrate, [paper])
                assert done.result(timeout=3)[0]['title'] == 'Known citation title'
        finally:
            release.set()
        occupied.result()


def test_failed_native_recovery_is_shared_across_long_metadata_stages(monkeypatch):
    from scholar_mcp import cache
    from scholar_mcp.sources import arxiv
    monkeypatch.setattr(arxiv, 'get_papers', lambda ids: {})
    clock, calls = [1000], []
    monkeypatch.setattr(cache.time, 'time', lambda: clock[0])
    def failed(identity):
        calls.append(identity)
        raise TimeoutError('temporary native page outage')
    monkeypatch.setattr(arxiv, 'get_paper', failed)
    def pending():
        return {'title': 'Conflicting title', '_needs_metadata': True,
                'external_ids': {'ArXiv': '2012.06010'}}
    with cache.request_scope():
        metadata.hydrate([pending()])
        clock[0] += 60
        metadata.hydrate([pending()], fields={'abstract'})
    assert calls == ['2012.06010']
    metadata.hydrate([pending()])
    assert calls == ['2012.06010', '2012.06010']


def test_arxiv_metadata_uses_native_abstract_page_when_atom_is_unavailable(monkeypatch):
    from scholar_mcp.sources import arxiv
    monkeypatch.setattr(arxiv, 'get_papers', lambda ids: {})
    calls = []
    def native(identity):
        calls.append(identity)
        return {'title': 'Verified native title', 'abstract': 'Full native summary',
                'authors': ['Alice Brown'], 'source': 'arxiv'}
    monkeypatch.setattr(arxiv, 'get_paper', native)
    row = {'title': 'Corrupted title', '_needs_metadata': True, '_title_conflict': True,
           'external_ids': {'ArXiv': '2012.06010v2'}}
    metadata.hydrate([row])
    assert calls == ['2012.06010'] and row['title'] == 'Verified native title'
    assert row['authors'] == ['Alice Brown'] and not row.get('_title_conflict')


def test_native_openalex_metadata_does_not_conflate_same_title_works(monkeypatch):
    monkeypatch.setattr(metadata, '_batch_openalex', lambda ids: {key: {
        'title': 'Shared title', 'authors': [name], 'abstract': name + ' abstract'}
        for key, name in [('W1', 'Alice Brown'), ('W2', 'Robert Green')]})
    monkeypatch.setattr(metadata, '_bibliographic', lambda *a: pytest.fail('native ids must not use title guessing'))
    rows = [{'title': 'Shared title', 'external_ids': {'OpenAlex': key}} for key in ['W1', 'W2']]
    metadata.hydrate(rows, fields={'abstract'})
    assert [p['authors'] for p in rows] == [['Alice Brown'], ['Robert Green']]


def test_native_work_batches_reuse_permuted_identifier_sets(monkeypatch):
    calls = []
    def request(url, params, **kw):
        calls.append(params['filter'])
        return httpx.Response(200, json={'results': [
            {'id': 'https://openalex.org/W2', 'title': 'Two'}, {'id': 'https://openalex.org/W1', 'title': 'One'}]},
            request=httpx.Request('GET', url))
    monkeypatch.setattr(metadata.oa, '_request', request)
    assert set(metadata._batch_openalex(['W2', 'W1', 'W2'])) == {'W1', 'W2'}
    assert set(metadata._batch_openalex(['W1', 'W2'])) == {'W1', 'W2'}
    assert calls == ['openalex:W1|W2']


def test_source_relationships_and_publication_date_roles_survive_merging():
    work = {"title": ["A book chapter"], "DOI": "10.1000/chapter", "type": "book-chapter",
            "published-print": {"date-parts": [[2001]]},
            "published-online": {"date-parts": [[2013, 6, 17]]},
            "relation": {"is-part-of": [{"id-type": "doi", "id": "10.1000/book"}]}}
    paper = cr.format_paper(work)
    merged = record_ops.deduplicate([{"title": "A book chapter", "external_ids": {"DOI": "10.1000/chapter"}}, paper])[0]
    assert merged["publication_dates"] == [{"type": "published-print", "date_parts": [[2001]]}, {"type": "published-online", "date_parts": [[2013, 6, 17]]}]
    assert merged["relations"] == [{"type": "is-part-of", "id": "10.1000/book", "id_type": "doi", "source": "crossref"}]
    assert merged["external_ids"]["DOI"] == "10.1000/chapter"
    from scholar_mcp import server
    detail = server._format_paper(merged, detailed=True)
    assert detail["relations"] == merged["relations"]
    assert detail["publication_dates"] == merged["publication_dates"]
    assert "relations" not in server._format_paper(merged)


def test_book_reference_preserves_supplied_context_without_guessing_a_doi():
    ref = {"series-title": "Heat islands: Understanding and mitigating heat in urban areas",
           "author": "Gartland", "year": "2008"}
    paper = cr.reference_paper(ref)
    assert paper["title"] == ref["series-title"]
    assert paper["year"] == 2008 and paper["authors"] == ["Gartland"]
    assert paper["_reference"] == ref and paper["external_ids"] == {}


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
    merged = record_ops.deduplicate([paper, {"title": "Paper", "external_ids": paper["external_ids"]}])
    metadata.hydrate(merged, fields={"publication_types"})
    assert len(calls) == 1 and not merged[0].get("publication_types")


def test_arxiv_doi_hydrates_natively_instead_of_repeating_catalog_misses(monkeypatch):
    from scholar_mcp.sources import arxiv
    seen = []
    monkeypatch.setattr(metadata, "_batch_dois", lambda ids: seen.extend(ids) or {})
    monkeypatch.setattr(metadata, "_doi", lambda _: pytest.fail("arXiv DOI is not a Crossref deposit"))
    monkeypatch.setattr(arxiv, "get_papers", lambda ids: {i: {"title": "Native arXiv paper", "is_open_access": True} for i in ids})
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


def test_abstract_completion_continues_past_a_sparse_catalog_record(monkeypatch):
    doi = "10.1000/abstract"
    monkeypatch.setattr(metadata, "_batch_dois", lambda _: {doi: {"title": "Catalog title", "source": "openalex"}})
    calls = []
    monkeypatch.setattr(metadata, "_doi", lambda _: {"title": "Crossref title", "abstract": ""})
    def repository(pid):
        calls.append(pid)
        return {"title": "Repository title", "abstract": "A full repository abstract.", "source": "openaire"}
    monkeypatch.setattr(metadata.openaire, "get_paper", repository)
    paper = {"title": "Existing title", "authors": ["Existing author"], "external_ids": {"DOI": doi}}
    metadata.hydrate([paper, deepcopy(paper)], fields={"abstract"})
    assert calls == [doi]
    assert paper["title"] == "Existing title" and paper["authors"] == ["Existing author"]
    assert paper["abstract"] == "A full repository abstract."
    assert paper["_abstract_source"] == "openaire" and "_source_count" not in paper
    metadata.hydrate([paper], fields={"abstract"})
    assert calls == [doi]


def test_repository_abstract_survives_primary_catalog_failure(monkeypatch):
    monkeypatch.setattr(metadata, "_batch_dois", lambda _: {})
    def unavailable(_):
        raise httpx.ConnectError("Catalog unavailable")
    monkeypatch.setattr(metadata, "_doi", unavailable)
    monkeypatch.setattr(metadata.openaire, "get_paper", lambda _: {"title": "Paper", "abstract": "Repository text", "source": "openaire"})
    paper = {"title": "Paper", "external_ids": {"DOI": "10.1000/fallback"}}
    metadata.hydrate([paper], fields={"abstract"})
    assert paper["abstract"] == "Repository text"


def test_full_catalog_abstract_does_not_trigger_repository_requests(monkeypatch):
    monkeypatch.setattr(metadata, "_batch_dois", lambda _: {"10.1000/full": {"title": "Paper", "abstract": "Already complete"}})
    monkeypatch.setattr(metadata, "_doi", lambda _: pytest.fail("unnecessary lookup"))
    monkeypatch.setattr(metadata.openaire, "get_paper", lambda _: pytest.fail("unnecessary lookup"))
    paper = {"title": "Paper", "external_ids": {"DOI": "10.1000/full"}}
    metadata.hydrate([paper], fields={"abstract"})
    assert paper["abstract"] == "Already complete"


def test_openaire_exact_lookup_rejects_other_dois(monkeypatch):
    from scholar_mcp.sources import openaire as client
    monkeypatch.setattr(client, "_request", lambda _: [
        {"mainTitle": "Wrong paper", "pids": [{"scheme": "doi", "value": "10.1000/other"}]},
        {"mainTitle": "Matched paper", "pids": [{"scheme": "doi", "value": "https://doi.org/10.1000/exact"}], "descriptions": ["Exact abstract"]},
    ])
    assert client.get_paper("10.1000/exact")["abstract"] == "Exact abstract"
    assert client.get_paper("10.1000/missing") is None


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
    from scholar_mcp.sources import dblp, s2
    monkeypatch.setattr(cr, "match_reference", lambda *a: None)
    monkeypatch.setattr(cr, "reference_candidates", lambda text: [{"title": ["A distinctive scientific paper title"]}])
    monkeypatch.setattr(dblp, "search_papers", lambda *a, **kw: [])
    monkeypatch.setattr(s2, "is_healthy", lambda: True)
    monkeypatch.setattr(s2, "search_match", lambda title: {"title": title, "year": 2017})
    text = "A Author. A distinctive scientific paper title. (2012)."
    assert metadata._bibliographic(text, "", "A Author", "2012", "") is None
    monkeypatch.setattr(s2, "search_match", lambda title: {"title": title, "year": 2012})
    from scholar_mcp.cache import clear
    clear()  # a new provider response, beyond the short negative-cache window
    assert metadata._bibliographic(text, "", "A Author", "2012", "")["year"] == 2012


def test_crossref_same_title_does_not_override_the_cited_year(monkeypatch):
    title = "A book and its identically titled review"
    monkeypatch.setattr(cr, "reference_candidates", lambda _: [
        {"title": [title], "DOI": "10.1000/review", "published-print": {"date-parts": [[2009]]}},
        {"title": [title], "DOI": "10.1000/book", "published-print": {"date-parts": [[2008]]}},
    ])
    result = cr.match_reference("Book author. 2008.", title=title, year="2008")
    assert result["external_ids"]["DOI"] == "10.1000/book"
    assert cr.match_reference("Book author. 2007.", title=title, year="2007") is None


def test_bibliographic_fallback_does_not_probe_overloaded_s2(monkeypatch):
    from scholar_mcp.sources import dblp, s2
    monkeypatch.setattr(cr, "match_reference", lambda *a: None)
    monkeypatch.setattr(dblp, "search_papers", lambda *a, **kw: [])
    monkeypatch.setattr(s2, "is_healthy", lambda: False)
    monkeypatch.setattr(s2, "search_match", lambda *a: pytest.fail("must not consume S2 quota"))
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
    result = record_ops.deduplicate(records)
    assert len(result) == 1 and result[0]["year"] == 2017
    assert result[0]["external_ids"]["DOI"] == "10.1000/original"


def test_generic_annual_reviews_remain_distinct():
    records = [{"title": "Review of Particle Physics", "year": year,
                "authors": ["A Author", "B Author"], "external_ids": {"DOI": f"10.1000/{year}"}}
               for year in (2018, 2022)]
    assert len(record_ops.deduplicate(records)) == 2


def test_later_same_title_does_not_hide_an_earlier_compatible_group():
    records = [{"title": "A sufficiently specific repeated scientific title", "year": year,
                "authors": authors} for year, authors in [(2017, ["A Smith", "B Jones"]),
                                                          (2020, ["C Other", "D Person"]),
                                                          (2017, ["Alice Smith", "Bob Jones"])]]
    assert len(record_ops.deduplicate(records)) == 2


def test_arxiv_doi_and_arxiv_id_share_identity_even_with_corrupt_provider_title(monkeypatch):
    from scholar_mcp.sources import arxiv
    records = [{"title": "Actual original paper title", "external_ids": {"ArXiv": "2005.11401"}},
               {"title": "Incorrect unrelated provider title", "external_ids": {"DOI": "10.48550/arxiv.2005.11401"}}]
    result = record_ops.deduplicate(records)
    assert len(result) == 1 and result[0]["_title_conflict"]
    monkeypatch.setattr(metadata, "_batch_dois", lambda _: {})
    monkeypatch.setattr(arxiv, "get_papers", lambda ids: {i: {"title": "Actual original paper title", "source": "arxiv"} for i in ids})
    metadata.hydrate(result)
    assert result[0]["title"] == "Actual original paper title"
    assert not result[0].get("_title_conflict")


def test_native_title_repair_preserves_known_citations(monkeypatch):
    from scholar_mcp.sources import arxiv
    paper = {"title": "Corrupt title", "abstract": "Wrong abstract", "_title_conflict": True,
             "_needs_metadata": True, "citation_count": 1000, "_citation_count_known": True,
             "external_ids": {"ArXiv": "1706.03762"}}
    monkeypatch.setattr(metadata, "_batch_dois", lambda _: {})
    monkeypatch.setattr(arxiv, "get_papers", lambda ids: {i: {"title": "Correct title", "abstract": "Original abstract",
                        "citation_count": 0, "_citation_count_known": False, "source": "arxiv"} for i in ids})
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
    monkeypatch.setattr(sources, "search_sources", lambda: [sources.Source("fixture", search=lambda q, limit, **kw: papers)])
    seen = []
    def rerank(query, rows, top_n, **kw):
        seen.append(top_n)
        return sorted(rows, key=lambda p: -p["_rerank_score"])[:top_n]
    monkeypatch.setattr(relevance, "rerank", rerank)
    monkeypatch.setattr(relevance, "_load_rank_params", lambda model=None: {"gamma": 1, "alpha": .05, "beta": .02, "delta": .1})
    results, _ = server._pipeline("search", "query", 1, rerank_query="query", expand_citations=False)
    assert seen == [100]
    assert results[0]["title"] == "Target foundation paper"


def test_search_filters_full_pool_before_final_limit(monkeypatch):
    from scholar_mcp import server
    papers = [{"title": f"Earlier ranked paper {i}", "year": 2020} for i in range(10)]
    papers.append({"title": "Requested recent paper", "year": 2025})
    def pipeline(dispatch, query, limit, **kwargs):
        return (papers if kwargs.get("return_all") else papers[:limit]), []
    monkeypatch.setattr(server, "_pipeline", pipeline)
    result = server.search_papers("research question", year="2025", limit=1)
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
    # The close second read fails (no model in tests), so the first local pass stands.
    result = relevance.rerank("q", deepcopy([{"title": str(i)} for i in range(600)]), 5)
    assert len(local) == 600
    assert all(p["_reranker_provider"] == "local" for p in result)


def test_arxiv_records_are_completed_in_one_batched_request(monkeypatch):
    import httpx

    from scholar_mcp.sources import arxiv
    calls = []
    feed = """<feed xmlns="http://www.w3.org/2005/Atom">""" + "".join(
        f"<entry><id>http://arxiv.org/abs/{i}v2</id><title>Paper {i}</title><summary>A</summary>"
        f"<published>2017-06-12T00:00:00Z</published></entry>" for i in ("1706.03762", "2005.11401")) + "</feed>"
    def get(url, params=None, **kw):
        calls.append(params["id_list"])
        return httpx.Response(200, text=feed, request=httpx.Request("GET", url))
    monkeypatch.setattr(arxiv.httpx, "get", get)
    monkeypatch.setattr(metadata, "_batch_dois", lambda _: {})
    papers = [{"_needs_metadata": True, "external_ids": {"ArXiv": "1706.03762"}},
              {"_needs_metadata": True, "external_ids": {"ArXiv": "2005.11401v1"}}]
    metadata.hydrate(papers)
    assert calls == ["1706.03762,2005.11401"]
    assert [p["title"] for p in papers] == ["Paper 1706.03762", "Paper 2005.11401"]
