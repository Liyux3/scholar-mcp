"""Native specialist records keep their meaning through lookup, ranking and storage."""

import httpx
import pytest

from scholar_mcp import knowledge_base as kb
from scholar_mcp import access, metadata, pdf_utils, relevance, server, sources
from scholar_mcp import records as record_ops
from scholar_mcp.sources import clinicaltrials, courtlistener, eric, openedition


def reply(monkeypatch, body, inspect=lambda kwargs: None):
    def get(url, **kwargs):
        inspect(kwargs)
        return httpx.Response(200, json=body, request=httpx.Request('GET', url))
    monkeypatch.setattr(httpx, 'get', get)


def test_eric_requests_venue_and_native_doi_fields(monkeypatch):
    def inspect(kwargs):
        assert {'source', 'url', 'author', 'publicationdateyear'} <= set(kwargs['params']['fields'].split(','))
        assert kwargs['params']['search'] == 'id:EJ1489919'
    reply(monkeypatch, {'response': {'numFound': 1, 'docs': [{
        'id': 'EJ1489919', 'title': 'Virtual tutoring', 'author': ['Carly Robinson'],
        'publicationdateyear': 2025, 'source': 'Educational Evaluation and Policy Analysis',
        'publicationtype': ['Journal Articles'], 'url': 'http://dx.doi.org/10.3102/01623737241288845',
        'fulltext': 'T', 'peerreviewed': 'T'}]}}, inspect)
    paper = eric.get_paper('https://eric.ed.gov/?id=EJ1489919')
    assert paper['external_ids']['DOI'] == '10.3102/01623737241288845'
    assert paper['venue'] and paper['year'] == 2025 and paper['authors'] == ['Carly Robinson']
    assert paper['open_access_url'] is None  # An availability flag is not a PDF URL.


def test_court_cluster_identity_and_judges_are_not_article_authors(monkeypatch):
    def inspect(kwargs):
        assert kwargs['params']['q'] == 'cluster_id:5010564'
    reply(monkeypatch, {'results': [{'cluster_id': 5010564, 'caseName': 'Kilgo v. Smith',
          'dateFiled': '2014-12-12', 'court': 'Supreme Court of Alabama', 'court_id': 'ala',
          'judge': 'Bolin', 'citation': ['177 So. 3d 884'], 'docketNumber': '1130840',
          'opinions': [{'id': 4822896, 'snippet': 'An <mark>opinion</mark> excerpt', 'type': 'lead-opinion'}]}]}, inspect)
    paper = server._find_paper('CourtListener:5010564')
    assert paper['authors'] == [] and paper['abstract'] == 'An opinion excerpt'
    assert paper['abstract_kind'] == 'search_excerpt' and paper['record_type'] == 'court_opinion'
    assert paper['details']['legal_citations'] == ['177 So. 3d 884']
    assert not metadata.needs_metadata(paper, {'authors', 'citation_count'})
    formatted = server._format_paper(paper, detailed=True)
    assert formatted['authors'] is None and formatted['details']['judges'] == 'Bolin'
    assert paper['paper_id'] == 'CourtListener:5010564'


def test_court_questions_use_the_native_semantic_engine_and_relevant_excerpts(monkeypatch):
    query = 'court decisions on COMPAS sentencing risk assessment'
    def inspect(kwargs):
        assert kwargs['params']['q'] == query
        assert kwargs['params']['semantic'] == 'true' and kwargs['params']['highlight'] == 'on'
    reply(monkeypatch, {'results': []}, inspect)
    assert courtlistener.search_papers(query, 3) == []
    assert sources.get('courtlistener').query_style == sources.QUERY_RAW


@pytest.mark.parametrize('failure', [httpx.ReadTimeout('semantic backend slow'),
                                   access.AccessError('TIMEOUT'), access.AccessError('NETWORK')])
def test_court_semantic_outage_recovers_through_its_native_keyword_search(monkeypatch, failure):
    requests = []
    def get(url, **kwargs):
        params = kwargs['params']
        requests.append(dict(params))
        if params.get('semantic'):
            raise failure
        return httpx.Response(200, json={'results': [{'cluster_id': 1, 'caseName': 'State v. Example'}]},
                              request=httpx.Request('GET', url))
    monkeypatch.setattr(httpx, 'get', get)
    rows = courtlistener.search_papers('COMPAS risk assessment', 1)
    assert rows[0]['record_type'] == 'court_opinion' and rows.partial_warning
    assert len(requests) == 2 and requests[1]['q'].casefold() == '"compas" and "risk" and "assessment"'


def test_openedition_native_chapter_keeps_its_publication_year_and_own_identity(monkeypatch):
    import json
    url = 'https://books.openedition.org/psorbonne/128981'
    native = {'url': url, 'platformID': 'OB', 'type': 'chapitre', 'title': 'Action et causalité',
              'authors': ['Djordjevic, Élodie'], 'anneedatepubli': '2021',
              'datemisenligne': '2024-07-05T00:00:00Z', 'ep_isbnelec': '979-10-351-0943-1',
              'access': True, 'access_type': 'freemium'}
    original = httpx.Client
    def handle(request):
        body = json.loads(request.content)
        assert body['pagination']['currentPage'] == 1
        assert body['facets']['platform']['options'] == ['OJ', 'OB']
        assert body['q'] == 'url:' + json.dumps(url)
        return httpx.Response(200, json={'documents': [native], 'pagination': {'pageCount': 1}})
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: original(transport=httpx.MockTransport(handle), **kwargs))
    paper = server._find_paper('OpenEdition:' + url)
    assert paper['year'] == 2021 and paper['publication_types'] == ['BookChapter']
    assert paper['external_ids'] == {'OpenEdition': url} and paper.get('open_access_url') is None
    assert paper['abstract_kind'] == 'search_excerpt'  # The native overview is truncated.
    assert openedition.format_paper({**native, 'platformID': 'HO'}) is None
    kb.add_papers([paper], notes='Human chapter notes')
    assert kb.get_paper(paper['paper_id'])['notes'] == 'Human chapter notes'


def test_explicit_registration_filter_routes_a_native_source_without_extra_query_words(monkeypatch):
    source = sources.get('clinicaltrials')
    monkeypatch.setattr(relevance, 'rerank', lambda query, papers, **kwargs: papers)
    def unrelated(*args, **kwargs):
        pytest.fail('an article index cannot serve a registration-only request')
    generic = sources.Source(name='article_index', search=unrelated)
    monkeypatch.setattr(sources, '_registry', {source.name: source, generic.name: generic})
    monkeypatch.setattr(source, 'search', lambda *args, **kwargs: [clinicaltrials.format_paper(study())])
    rows = sources.parallel_search('heart failure', limit=1, publication_types=['ClinicalTrialRegistration'])
    assert len(rows) == 1 and rows[0].source == 'clinicaltrials' and rows[0].results
    result = server.search_papers('heart failure', limit=1, paper_types='ClinicalTrialRegistration')
    assert result['results'][0]['record_type'] == 'clinical_trial_registration'


def study():
    return {'protocolSection': {'identificationModule': {'nctId': 'NCT03036124', 'officialTitle': 'DAPA-HF'},
            'statusModule': {'studyFirstPostDateStruct': {'date': '2017-01-30'}, 'overallStatus': 'COMPLETED'},
            'outcomesModule': {'primaryOutcomes': [{'measure': 'Cardiovascular death'}]},
            'referencesModule': {'references': [{'pmid': '31535829', 'type': 'RESULT', 'citation': 'Result paper'}]}},
            'hasResults': True, 'documentSection': {'largeDocumentModule': {'largeDocs': [
                {'filename': 'Prot_000.pdf', 'label': 'Study Protocol', 'hasProtocol': True},
                {'filename': 'SAP_001.pdf', 'label': 'Statistical Analysis Plan', 'hasSap': True}]}}}


def test_trial_protocol_and_result_papers_have_distinct_identities(monkeypatch):
    reply(monkeypatch, study())
    monkeypatch.setattr(metadata, 'hydrate', lambda rows, fields=None: [dict(row, title='Result paper') for row in rows])
    paper = clinicaltrials.get_paper('NCT03036124')
    assert paper['record_type'] == 'clinical_trial_registration'
    assert paper['open_access_url'].endswith('/24/NCT03036124/Prot_000.pdf')
    assert paper['details']['has_posted_results']
    monkeypatch.setattr(sources, 'resolve_pdf_candidates', lambda paper: pytest.fail('protocol has a native URL'))
    assert pdf_utils._locate(paper, '') == [('clinicaltrials', paper['open_access_url'], 'unknown')]
    assert paper['details']['planned_primary_outcomes'][0]['measure'] == 'Cardiovascular death'
    sap = clinicaltrials.get_paper('https://cdn.clinicaltrials.gov/large-docs/24/NCT03036124/SAP_001.pdf')
    assert sap['_requested_copy_url'].endswith('SAP_001.pdf')
    assert clinicaltrials.get_paper('https://cdn.clinicaltrials.gov/large-docs/24/NCT03036124/Invented.pdf') is None
    refs = sources.parallel_references('NCT03036124', metadata_fields=set())
    # Unhydrated references are available directly; the shared pipeline hydrates real PMID records.
    assert clinicaltrials.get_references('NCT03036124')[0]['paper_id'] == 'PMID:31535829'
    assert {row.source for row in refs} == {'clinicaltrials'}


@pytest.mark.parametrize('name,text,unrelated', [
    ('eric', 'literacy tutoring in primary schools', 'teacher student distillation'),
    ('courtlistener', 'court decisions on sentencing algorithms', 'legal language models'),
    ('clinicaltrials', 'registered clinical trials for diabetes', 'diabetes treatment papers'),
])
def test_specialist_sources_route_only_to_their_research_task(name, text, unrelated):
    source = sources.get(name)
    assert source.applies('', text) and not source.applies('', unrelated)
    assert source.probe_query and source.probe_id and source.id_pattern


def test_native_lookup_failure_is_visible_and_never_queries_unrelated_indexes(monkeypatch):
    source = sources.get('courtlistener')
    def unavailable(identifier):
        raise httpx.ConnectError('provider unavailable')
    monkeypatch.setattr(source, 'get_paper', unavailable)
    monkeypatch.setattr(server.s2, 'get_paper', lambda *args: pytest.fail('invalid S2 namespace'))
    out = server.paper_info('CourtListener:5010564')
    assert out['temporary'] and out['source'] == 'courtlistener'
    assert 'ConnectError' in out['error']


def test_primary_record_recommendations_do_not_spend_paper_api_quota(monkeypatch):
    monkeypatch.setattr(server, '_lookup_title', lambda *args: pytest.fail('native capability is already known'))
    out = server.recommend_papers('NCT03036124')
    assert 'publication record' in out['error'] and 'native references' in out['hint']


def test_different_primary_records_do_not_absorb_article_identity_or_human_notes():
    trial = clinicaltrials.format_paper(study())
    article = {'title': trial['title'], 'paper_id': 'PMID:31535829', 'external_ids': {'PMID': '31535829'},
               'publication_types': ['JournalArticle'], 'year': trial['year']}
    assert len(record_ops.deduplicate([trial, article])) == 2
    kb.add_papers([trial], notes='Human trial notes')
    kb.add_papers([article], notes='Human article notes')
    assert len(kb.list_papers()) == 2
    assert kb.get_paper('ClinicalTrials:NCT03036124')['notes'] == 'Human trial notes'
    assert kb.get_paper('PMID:31535829')['notes'] == 'Human article notes'
    assert kb.get_paper(trial['title']) is None  # An ambiguous title cannot select whose notes to edit.
