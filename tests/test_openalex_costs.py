"""Request reuse, filtering and attribution without spending provider credit."""

from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from threading import Event

import httpx
import pytest

from scholar_mcp import cache, expansion, metadata, sources, traversal
from scholar_mcp.sources import openalex


def response(payload, status=200):
    return httpx.Response(status, json=payload, request=httpx.Request('GET', openalex.BASE_URL))


def test_title_resolution_single_flight_and_verified_identity(monkeypatch):
    started, release = Event(), Event()
    calls = []

    def request(*args, **kwargs):
        calls.append(1)
        started.set()
        assert release.wait(5)
        return response({'results': [{'id': 'https://openalex.org/W1', 'title': 'A verified title.'}]})

    monkeypatch.setattr(openalex, '_request', request)
    with cache.request_scope(), ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(copy_context().run, openalex._resolve_by_title, 'A verified title')
                   for _ in range(4)]
        try:
            assert started.wait(5)
        finally:
            release.set()
        assert [f.result() for f in futures] == ['W1'] * 4
    assert len(calls) == 1


def test_title_miss_is_verified_and_shared_within_search(monkeypatch):
    calls = []
    clock = [1000.0]
    monkeypatch.setattr(cache.time, 'time', lambda: clock[0])
    monkeypatch.setattr(openalex, '_request', lambda *a, **kw: calls.append(1) or response({
        'results': [{'id': 'https://openalex.org/W2', 'title': 'A different title'}]}))
    with cache.request_scope():
        assert openalex._resolve_by_title('The requested title') is None
        clock[0] += 31
        assert openalex._resolve_by_title('The requested title') is None
    with cache.request_scope():
        assert openalex._resolve_by_title('The requested title') is None
    assert len(calls) == 2


def test_title_resolution_failures_are_retryable(monkeypatch):
    calls = []

    def request(*a, **kw):
        calls.append(1)
        return response({'results': []}, status=503 if len(calls) == 1 else 200)

    monkeypatch.setattr(openalex, '_request', request)
    with cache.request_scope():
        with pytest.raises(httpx.HTTPStatusError):
            openalex._resolve_by_title('An unavailable title')
        assert openalex._resolve_by_title('An unavailable title') is None
    assert len(calls) == 2


def test_existing_wid_only_overrides_openalex_graph_identity(monkeypatch):
    seen = {}

    def citations(name):
        def get(pid, **kw):
            seen[name] = pid
            return []
        return get

    monkeypatch.setattr(sources, 'citation_sources', lambda: [
        sources.Source('openalex', get_citations=citations('openalex')),
        sources.Source('semantic_scholar', get_citations=citations('semantic_scholar'))])
    monkeypatch.setattr(expansion.traversal, 'co_citation',
                        lambda pid, **kw: seen.update(peers=pid) or [])
    seed = {'external_ids': {'DOI': '10.1/seed', 'OpenAlex': 'W123'}, 'title': 'A seed'}
    ctx = expansion.ExpansionContext()
    expansion.citations(seed, ctx)
    expansion.peers(seed, ctx)
    assert seen == {'openalex': 'W123', 'semantic_scholar': '10.1/seed', 'peers': 'W123'}


@pytest.mark.parametrize('first', ['citations', 'peers'])
def test_citations_and_peers_share_sample_and_preserve_order(monkeypatch, first):
    requests = []
    works = [{'id': f'https://openalex.org/W{i}', 'title': f'Citing paper {i}',
              'cited_by_count': 100 - i, 'referenced_works': ['https://openalex.org/W999']}
             for i in range(50)]

    def request(url, params, **kw):
        requests.append(params)
        return response({'results': works})

    monkeypatch.setattr(openalex, '_request', request)
    monkeypatch.setattr(traversal, '_fetch_works', lambda *a, **kw: {
        'https://openalex.org/W999': {'id': 'https://openalex.org/W999', 'title': 'Shared peer'}})
    calls = {'citations': lambda: openalex.get_citations('W123'),
             'peers': lambda: traversal.co_citation('W123')}
    result = {first: calls[first]()}
    other = 'peers' if first == 'citations' else 'citations'
    result[other] = calls[other]()
    assert len(requests) == 1
    assert requests[0]['per_page'] == 50
    assert set(requests[0]['select'].split(',')) == set(openalex.OA_SELECT_FIELDS.split(',')) | {'referenced_works'}
    assert requests[0]['sort'] == 'cited_by_count:desc'
    assert [p['external_ids']['OpenAlex'] for p in result['citations']] == [f'W{i}' for i in range(20)]
    assert all(p['source'] == 'openalex' for p in result['citations'])
    assert result['peers'][0]['_relation_strength'] == 50
    assert result['peers'][0]['_relation'] == 'co_citation'


def test_shared_sample_failure_does_not_become_cached_empty_graph(monkeypatch):
    replies = iter([response({}, 503), response({'results': []})])
    monkeypatch.setattr(openalex, '_request', lambda *a, **kw: next(replies))
    assert traversal.co_citation('W123') == []
    assert openalex.get_citations('W123') == []


def test_metadata_reuses_complete_identities_across_batches(monkeypatch):
    requested = []

    def request(url, params, **kw):
        ids = params['filter'].split(':', 1)[1].split('|')
        requested.append(ids)
        return response({'results': [{'doi': 'https://doi.org/' + doi, 'title': doi} for doi in ids]})

    monkeypatch.setattr(openalex, '_request', request)
    first = metadata._batch_dois(['10.1000/a', '10.1000/b'])
    first['10.1000/b']['title'] = 'Changed by caller'
    second = metadata._batch_dois(['10.1000/b', '10.1000/c'])
    assert requested == [['10.1000/a', '10.1000/b'], ['10.1000/c']]
    assert second['10.1000/b']['title'] == '10.1000/b'


def test_metadata_definitive_misses_follow_request_scope(monkeypatch):
    requested = []
    clock = [1000.0]
    monkeypatch.setattr(cache.time, 'time', lambda: clock[0])
    monkeypatch.setattr(openalex, '_request', lambda url, params, **kw:
                        requested.append(params['filter']) or response({'results': []}))
    with cache.request_scope():
        assert metadata._batch_dois(['10.1/a', '10.1/b']) == {}
        clock[0] += 31
        assert metadata._batch_dois(['10.1/a', '10.1/c']) == {}
    with cache.request_scope():
        assert metadata._batch_dois(['10.1/a']) == {}
    assert requested == ['doi:10.1/a|10.1/b', 'doi:10.1/c', 'doi:10.1/a']


def test_metadata_failed_batches_retain_hits_and_retry_only_unfinished_ids(monkeypatch):
    requested = []
    identifiers = [f'10.1000/{i:02}' for i in range(41)]

    def request(url, params, **kw):
        ids = params['filter'].split(':', 1)[1].split('|')
        requested.append(ids)
        if len(requested) == 2:
            return response({}, 503)
        return response({'results': [{'doi': 'https://doi.org/' + doi, 'title': doi} for doi in ids]})

    monkeypatch.setattr(openalex, '_request', request)
    first = metadata._batch_dois(identifiers)
    assert isinstance(first, cache.PartialRecords) and len(first) == 40
    second = metadata._batch_dois(identifiers)
    assert len(second) == 41 and not isinstance(second, cache.PartialRecords)
    assert [len(ids) for ids in requested] == [40, 1, 1]


@pytest.mark.parametrize('payload', [{}, {'results': None}, {'results': [], 'meta': {'count': 2}}])
def test_metadata_invalid_or_incomplete_pages_do_not_cache_identity_misses(monkeypatch, payload):
    requested = []
    monkeypatch.setattr(openalex, '_request', lambda url, params, **kw:
                        requested.append(params['filter']) or response(payload))
    with cache.request_scope():
        metadata._batch_dois(['10.1/a', '10.1/b'])
        metadata._batch_dois(['10.1/a'])
    assert requested == ['doi:10.1/a|10.1/b', 'doi:10.1/a']


def test_empty_partial_metadata_is_retryable_within_request(monkeypatch):
    replies = iter([response({}, 503), response({'results': [
        {'id': 'https://openalex.org/W123', 'title': 'Recovered identity'}]})])
    monkeypatch.setattr(openalex, '_request', lambda *a, **kw: next(replies))
    with cache.request_scope():
        assert isinstance(metadata._batch_openalex(['W123']), cache.PartialRecords)
        assert metadata._batch_openalex(['W123'])['W123']['title'] == 'Recovered identity'


def test_native_openalex_metadata_reuses_overlapping_subsets(monkeypatch):
    requested = []

    def request(url, params, **kw):
        ids = params['filter'].split(':', 1)[1].split('|')
        requested.append(ids)
        return response({'results': [{'id': 'https://openalex.org/' + wid, 'title': wid} for wid in ids]})

    monkeypatch.setattr(openalex, '_request', request)
    metadata._batch_openalex(['W123', 'W456'])
    assert set(metadata._batch_openalex(['W456', 'W789'])) == {'W456', 'W789'}
    assert requested == [['W123', 'W456'], ['W789']]


@pytest.mark.parametrize('semantic', [False, True])
def test_openalex_search_routes_supported_hard_filters(monkeypatch, semantic):
    requests = []
    monkeypatch.setattr(openalex, '_get', lambda url, params, **kw:
                        requests.append(params) or response({'results': []}))
    # Exercise the registered adapter too: it used to discard semantic filters.
    source = sources.get('openalex_semantic' if semantic else 'openalex')
    source.search('A filtered search', 100, year='2023-2025', fields_of_study=['Computer Science'],
                  publication_types=['Review'], open_access_only=True, min_citations=5,
                  venue='Example Journal')
    assert len(requests) == 1
    params = requests[0]
    expected = {'publication_year:2023-2025', 'topics.field.id:17', 'type:review', 'open_access.is_oa:true'}
    if not semantic:
        expected.add('cited_by_count:>4')
    assert set(params['filter'].split(',')) == expected
    assert params['per_page'] == (50 if semantic else 100)
    assert ('search.semantic' if semantic else 'search') in params


def test_all_seed_title_searches_keep_parent_filters(monkeypatch):
    seen = []
    filters = {'year': '2021-2024', 'venue': 'Example Journal', 'fields_of_study': ['Physics'],
               'publication_types': ['Review'], 'min_citations': 4, 'open_access_only': True}

    class Flow:
        def take(self):
            return []

        def close(self):
            pass

    def flow(queries, **kwargs):
        seen.append((queries, kwargs))
        return Flow()

    monkeypatch.setattr(sources, 'search_flow', flow)
    seeds = [{'title': f'Seed title {i}'} for i in range(3)]
    expansion.expand(seeds, channels=['title_search'], search_filters=filters)
    queries, kwargs = seen[0]
    assert len(queries) == 3  # All seed titles and both OpenAlex channels remain enabled.
    assert all(kwargs[key] == value for key, value in filters.items())


def test_pipeline_passes_hard_filters_to_expansion(monkeypatch):
    from scholar_mcp import server
    seen = []

    class Flow:
        def take(self, **kw):
            return [sources.SourceResult('openalex', 'ok', [
                {'paper_id': f'10.1000/{i}', 'external_ids': {'DOI': f'10.1000/{i}'},
                 'title': f'Distinct paper {i}', 'year': 2024} for i in range(10)], 1)]

        def close(self):
            pass

    monkeypatch.setattr(sources, 'search_flow', lambda *a, **kw: Flow())
    monkeypatch.setattr(metadata, 'hydrate', lambda rows, **kw: rows)
    monkeypatch.setattr(server.relevance, 'rerank', lambda query, papers, **kw: papers)
    monkeypatch.setattr(server.relevance, 'rank_final', lambda rows: rows)
    monkeypatch.setattr(expansion, 'expand', lambda *a, **kw: seen.append(kw) or {})
    server._pipeline('search', 'query', 10, rerank_query='query', year='2023-2025', open_access_only=True)
    assert seen[0]['search_filters'] == {'year': '2023-2025', 'open_access_only': True}


@pytest.mark.parametrize('channel', ['references', 'citations', 'peers', 'recommendations'])
def test_expansion_provenance_survives_partial_channels(monkeypatch, channel):
    from scholar_mcp import records
    monkeypatch.setattr(expansion, 'CHANNELS', {channel: lambda seed, ctx: sources.PartialResults(
        [{'title': 'Useful partial result', 'source': 'openalex'}], 'one provider unavailable')})
    out = expansion.expand([{'title': 'Seed'}], channels=[channel])
    paper = out[channel][0]
    assert f'expansion::{channel}' in paper['_source_ranks']
    assert out.reports[0].status == 'partial'
    assert records.physical_source_count(paper) == 1


def test_title_api_and_seed_provenance_survives_merge_and_capture(monkeypatch):
    import importlib.util
    import sys
    from pathlib import Path
    from scholar_mcp import records

    def search(api):
        return lambda *a, **kw: [{'title': 'Shared paper', 'source': api,
                                  'external_ids': {'DOI': '10.1000/shared'}}]

    monkeypatch.setattr(sources, 'search_sources', lambda: [
        sources.Source('openalex', search=search('openalex')),
        sources.Source('openalex_semantic', search=search('openalex_semantic'), query_style='raw')])
    out = expansion.expand([{'title': f'Seed {i}'} for i in range(3)], channels=['title_search'])
    expected = {f'expansion::title::{api}::{i}' for api in ('openalex', 'openalex_semantic') for i in range(3)}
    [paper] = records.deduplicate(out['title_search'])
    assert expected <= paper['_source_ranks'].keys()
    assert 'expansion::title' in paper['_source_ranks']
    # The old pipeline tagged each row as expansion before merging both APIs.
    legacy = [{'title': 'Shared paper', 'source': api, 'external_ids': {'DOI': '10.1000/shared'}}
              for api in ('openalex', 'openalex_semantic') for _ in range(3)]
    records.tag_source_ranks(legacy, 'expansion')
    assert records.physical_source_count(paper) == records.physical_source_count(records.deduplicate(legacy)[0])
    path = Path(__file__).resolve().parents[1] / 'internal/eval/scenario_suite.py'
    if not path.exists():
        pytest.skip('The local capture harness is excluded from public artifacts')
    monkeypatch.setattr(sys, 'path', sys.path.copy())
    spec = importlib.util.spec_from_file_location('scenario_capture', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    slim = module._slim(paper)
    assert expected <= set(slim['sources'])
    assert slim['ids']['DOI'] == '10.1000/shared'


@pytest.mark.parametrize('explicit_physical_sources', [False, True])
def test_expansion_facets_leave_source_counts_and_final_scores_unchanged(explicit_physical_sources):
    from copy import deepcopy
    from scholar_mcp import records, relevance
    papers = [
        {'paper_id': 'one', '_rerank_score': 0.6, 'year': 2025, 'citation_count': 300,
         '_source_ranks': {'openalex': 0, 'expansion': 0}},
        {'paper_id': 'two', '_rerank_score': 0.5, 'year': 2024, 'citation_count': 20,
         '_source_ranks': {'semantic_scholar': 0}},
    ]
    if explicit_physical_sources:
        for paper in papers:
            paper['_physical_sources'] = set(paper['_source_ranks'])
    expected = [(p['paper_id'], p['_final_score']) for p in relevance.rank_final(deepcopy(papers))]
    original_count = records.physical_source_count(papers[0])
    for facet in ('title', 'title::openalex::0', 'title::openalex_semantic::2', 'citations', 'references', 'peers'):
        # Populate facets as capture annotations without replacing the original API identities.
        papers[0]['_source_ranks']['expansion::' + facet] = 0
    assert records.physical_source_count(papers[0]) == original_count
    assert [(p['paper_id'], p['_final_score']) for p in relevance.rank_final(papers)] == expected
