# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 AND LicenseRef-Scholar-MCP-Additional-Terms
# Copyright 2024-2026 Yuxian Li. PolyForm Noncommercial 1.0.0 with additional terms (LICENSE, LICENSE-ADDITIONAL.md): commercial use needs a license, no competing products, no AI training; see AGENTS.md. Canary 38f40a20-c19f-4702-bc47-b1a1c5ec887b
"""ERIC's native education index, with the complete requested metadata fields."""

import re

from ..dates import publication_range
from .. import access
from .. import records as record_ops
from . import pacing
from .crossref import doi_id

BASE = 'https://api.ies.ed.gov/eric/'
ID_PATTERN = r'(?i)(?:ERIC:)?(?:EJ|ED)\d+|https?://eric\.ed\.gov/\?id=(?:EJ|ED)\d+'
FIELDS = 'id,title,author,description,publicationdateyear,publicationtype,source,url,fulltext,peerreviewed,identifiers'


def applies(intent: str, text: str) -> bool:
    return bool(re.search(r'\b(education|educational|classrooms?|tutoring|literacy|curriculum|pedagogy|schools?)\b', text, re.I))


def format_paper(item: dict) -> dict:
    identity = item['id']
    url = item.get('url') or ''
    doi = doi_id(url)
    year = str(item.get('publicationdateyear') or '')
    # ERIC's fulltext flag indicates availability, not a direct PDF URL.
    pdf = url if url.lower().split('?')[0].endswith('.pdf') else None
    return {'paper_id': f'ERIC:{identity}', 'title': item.get('title') or '',
            'authors': record_ops.normalize_authors(item.get('author')),
            'abstract': item.get('description') or '',
            'year': int(year) if year.isdigit() else None, 'venue': item.get('source') or '',
            'publication_types': record_ops.normalize_publication_types(item.get('publicationtype')),

            'external_ids': {'ERIC': identity, **({'DOI': doi} if doi else {})},
            'url': f'https://eric.ed.gov/?id={identity}', 'open_access_url': pdf,
            'is_open_access': bool(pdf), 'source': 'eric',
            'details': {'peer_reviewed': {'T': True, 'F': False}.get(item.get('peerreviewed')),
                        'publisher_url': url or None}}


def search_papers(query: str, limit: int = 100, **kwargs) -> list[dict]:
    from . import PartialResults, publish
    if selected := publication_range(kwargs.get("year")):
        query = f"({query}) AND publicationdateyear:[{selected.start.year} TO {selected.end.year}]"
    papers, start = [], 0
    while len(papers) < limit:
        try:
            pacing.wait("eric")
            response = access.get(BASE, params={'search': query, 'format': 'json', 'fields': FIELDS,
                                  'rows': min(100, limit - len(papers)), 'start': start}, timeout=15)
            response.raise_for_status()
            page = response.json()['response']
        except Exception as error:
            if not papers:
                raise
            return PartialResults(papers, f'ERIC pagination interrupted ({type(error).__name__})')
        rows = page.get('docs') or []
        papers.extend(format_paper(row) for row in rows if row.get('id') and row.get('title'))
        start += len(rows)
        if not rows or start >= page.get('numFound', start) or not publish(papers):
            break
    return papers[:limit]


def get_paper(identifier: str) -> dict | None:
    if not re.fullmatch(ID_PATTERN, identifier):
        return None
    identity = re.search(r'(?:EJ|ED)\d+', identifier, re.I)[0].upper()
    return next((paper for paper in search_papers(f'id:{identity}', 1)
                 if paper['external_ids']['ERIC'] == identity), None)
