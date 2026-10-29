"""Native US case-law records, kept distinct from scholarly articles."""
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

import httpx
from .. import access, relevance
from . import pacing

BASE = 'https://www.courtlistener.com'
SEARCH = BASE + '/api/rest/v4/search/'
ID_PATTERN = r'(?i)CourtListener:\d+|https?://(?:www\.)?courtlistener\.com/opinion/\d+(?:/[^?#]*)?'


def applies(intent: str, text: str) -> bool:
    return bool(re.search(r'\b(case law|court (?:cases?|decisions?|opinions?|rulings?)|judicial (?:decisions?|opinions?)|CourtDecision|CourtListener)\b|\bv\.\s', text, re.I))


def format_paper(item: dict) -> dict:
    date = item.get('dateFiled') or ''
    opinions = item.get('opinions') or []
    main = next((row for row in opinions if row.get('type') == 'lead-opinion'), opinions[0] if opinions else {})
    pdf = main.get('download_url')
    return {'paper_id': f'CourtListener:{item["cluster_id"]}', 'record_type': 'court_opinion',
            'title': item.get('caseName') or item.get('caseNameFull') or '', 'authors': [],
            'year': int(date[:4]) if date[:4].isdigit() else None, 'publication_date': date or None,
            'venue': item.get('court') or '', 'publication_types': ['CourtDecision'],
            'abstract': BeautifulSoup(item.get('syllabus') or main.get('snippet') or '', 'html.parser').get_text(' ', strip=True),
            'abstract_kind': 'syllabus' if item.get('syllabus') else 'search_excerpt',
            'citation_count': item.get('citeCount') or 0, '_citation_count_known': item.get('citeCount') is not None,
            'external_ids': {'CourtListener': str(item['cluster_id'])},
            'url': urljoin(BASE, item.get('absolute_url') or f'/opinion/{item["cluster_id"]}/'),
            'open_access_url': pdf, 'is_open_access': bool(pdf), 'source': 'courtlistener',
            'details': {'jurisdiction': item.get('court_jurisdiction'), 'country': 'US',
                        'court_id': item.get('court_id'), 'docket': item.get('docketNumber'),
                        'legal_citations': item.get('citation') or [], 'judges': item.get('judge') or None}}


def search_papers(query: str, limit: int = 100, **kwargs) -> list[dict]:
    papers, url, params = [], SEARCH, {'q': query, 'type': 'o', 'order_by': 'score desc', 'highlight': 'on'}
    warning = ''
    # Plain questions use Citegeist's native semantic search. Exact cluster
    # lookups and explicit legal query operators retain keyword semantics.
    if not re.search(r'[:"()]|\b(?:AND|OR|NOT)\b', query):
        params['semantic'] = 'true'
    while url and len(papers) < limit:
        try:
            pacing.wait("courtlistener")
            response = access.get(url, params=params, timeout=15)
            response.raise_for_status()
            page = response.json()
        except Exception as error:
            transient = isinstance(error, httpx.TransportError) or (
                isinstance(error, access.AccessError) and error.code in {'NETWORK', 'TIMEOUT', 'UNAVAILABLE'}) or (
                isinstance(error, httpx.HTTPStatusError) and error.response.status_code >= 500)
            if not papers and params and params.get('semantic') and transient:
                params.pop('semantic')
                words = relevance.optimize_query(query).split()
                params['q'] = ' AND '.join('"' + word.replace('"', '\\"') + '"' for word in words)
                warning = f'CourtListener semantic search unavailable ({type(error).__name__}); used keyword search'
                continue
            if not papers:
                raise
            from . import PartialResults
            return PartialResults(papers, f'CourtListener pagination interrupted ({type(error).__name__})')
        papers.extend(format_paper(row) for row in page.get('results') or [] if row.get('cluster_id'))
        from . import publish
        if not publish(papers):
            break
        url, params = page.get('next'), None
        if url and not url.startswith(SEARCH):
            break
    if warning:
        from . import PartialResults
        return PartialResults(papers[:limit], warning)
    return papers[:limit]


def get_paper(identifier: str) -> dict | None:
    if not re.fullmatch(ID_PATTERN, identifier):
        return None
    identity = re.search(r'(?:CourtListener:|/opinion/)(\d+)', identifier, re.I)[1]
    return next((paper for paper in search_papers(f'cluster_id:{identity}', 1)
                 if paper['external_ids']['CourtListener'] == identity), None)
