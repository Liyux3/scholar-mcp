"""Native OpenEdition journals and books, excluding blogs and event notices."""

import json
import re

import httpx
from bs4 import BeautifulSoup

from .. import access
from .. import records as record_ops
from . import pacing

BASE = 'https://search-api.openedition.org/documents'
ID_PATTERN = r'(?i)(?:OpenEdition:)?https://(?:journals|books)\.openedition\.org/[^\s?#"<>]+'


def applies(intent: str, text: str) -> bool:
    return bool(re.search(r'\b(OpenEdition|humanities|philosoph\w*|anthropolog\w*|archaeolog\w*|sociolog\w*|historiograph\w*|histoire|littérature)\b', text, re.I))


def format_paper(item: dict) -> dict | None:
    url = str(item.get('url') or '')
    if item.get('platformID') not in {'OJ', 'OB'} or not re.fullmatch(ID_PATTERN, url):
        return None
    year = str(item.get('anneedatepubli') or '')
    title = BeautifulSoup(item.get('naked_titre') or item.get('title') or '', 'html.parser').get_text(' ', strip=True)
    if not title:
        return None
    kind = {'article': 'JournalArticle', 'chapitre': 'BookChapter', 'livre': 'Book'}.get(item.get('type'), 'Other')
    return {'paper_id': 'OpenEdition:' + url, 'title': title,
            'authors': record_ops.normalize_authors(item.get('authors')),
            'year': int(year) if year.isdigit() else None, 'publication_date': year or None,
            'venue': item.get('site_title') or '', 'publication_types': [kind],
            'abstract': BeautifulSoup(item.get('overview') or '', 'html.parser').get_text(' ', strip=True),
            'abstract_kind': 'search_excerpt',
            'source': 'openedition', 'url': url, 'external_ids': {'OpenEdition': url},
            'is_open_access': False,
            # A book's ISBN identifies its container, never one of its chapters.
            'details': {'native_type': item.get('type'), 'text_access': item.get('access_type'),
                        'subtitle': item.get('subtitle'),
                        'container_isbn': item.get('ep_isbnelec') or item.get('ep_isbnprint')}}


def search_papers(query: str, limit: int = 100, **kwargs) -> list[dict]:
    from . import PartialResults, publish
    payload = {'lang': 'fr_FR', 'locale': 'fr_FR', 'q': query, 'adv': False, 'mm': '100',
               'pagination': {'currentPage': 1, 'documentsPerPage': min(limit, 100)},
               'facets': {key: {'options': []} for key in ('platform', 'access', 'type', 'author',
                            'translations', 'journals-publication', 'books-publication', 'hypotheses-publication', 'subscribers')}}
    payload['facets'].update(platform={'options': ['OJ', 'OB']}, date={})
    papers = []
    with httpx.Client(timeout=15) as client:
        while len(papers) < limit:
            try:
                pacing.wait("openedition")
                response = client.post(BASE, json=payload)
                response.raise_for_status()
                page = response.json()
                papers.extend(paper for row in page.get('documents') or [] if (paper := format_paper(row)))
            except Exception as error:
                if not papers:
                    raise
                return PartialResults(papers, f'OpenEdition pagination interrupted ({type(error).__name__})')
            pagination = page.get('pagination') or {}
            if not page.get('documents') or payload['pagination']['currentPage'] >= pagination.get('pageCount', 1) or not publish(papers):
                break
            payload['pagination']['currentPage'] += 1
    return papers[:limit]


def get_paper(identifier: str) -> dict | None:
    if not re.fullmatch(ID_PATTERN, identifier):
        return None
    url = re.sub(r'^OpenEdition:', '', identifier, flags=re.I)
    return next((paper for paper in search_papers('url:' + json.dumps(url), 1) if paper['url'] == url), None)


def resolve_pdf(paper: dict) -> list[dict]:
    url = str((paper.get('external_ids') or {}).get('OpenEdition') or '')
    if not re.fullmatch(ID_PATTERN, url):
        return []
    pacing.wait("openedition")
    response = access.get(url, kind='html', timeout=15)
    response.raise_for_status()
    meta = BeautifulSoup(response.text, 'html.parser').select_one('meta[name="citation_pdf_url"]')
    return [{'url': str(httpx.URL(url).join(meta['content'])), 'version': 'published'}] if meta and meta.get('content') else []
