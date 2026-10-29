"""Study registrations, protocols and linked papers from ClinicalTrials.gov v2."""
import re
from urllib.parse import urlparse

from .. import access
from ..cache import cached
from . import pacing

BASE = 'https://clinicaltrials.gov/api/v2/studies'
ID_PATTERN = r'(?i)(?:ClinicalTrials:)?NCT\d{8}|https?://clinicaltrials\.gov/study/NCT\d{8}|https://cdn\.clinicaltrials\.gov/large-docs/\d{2}/NCT\d{8}/[A-Za-z0-9_-]+\.pdf'


def applies(intent: str, text: str) -> bool:
    return bool(re.search(r'\b(ClinicalTrialRegistration|ClinicalTrials|registered (?:clinical )?trials?|ongoing (?:clinical )?trials?|trial (?:registrations?|registr(?:y|ies)|protocols?))\b', text, re.I))


@cached(ttl=3600, miss_ttl=30)
def _study(identity: str) -> dict:
    pacing.wait("clinicaltrials")
    response = access.get(f'{BASE}/{identity}', timeout=15)
    if response.status_code == 404:
        return {}
    response.raise_for_status()
    return response.json()


def format_paper(item: dict) -> dict:
    protocol = item.get('protocolSection') or {}
    identification = protocol.get('identificationModule') or {}
    identity = identification['nctId']
    status = protocol.get('statusModule') or {}
    description = protocol.get('descriptionModule') or {}
    date = (status.get('studyFirstPostDateStruct') or {}).get('date') or ''
    design = protocol.get('designModule') or {}
    sponsor = (protocol.get('sponsorCollaboratorsModule') or {}).get('leadSponsor') or {}
    documents = []
    for row in (item.get('documentSection') or {}).get('largeDocumentModule', {}).get('largeDocs') or []:
        filename = row.get('filename') or ''
        if not re.fullmatch(r'[A-Za-z0-9_-]+\.pdf', filename):
            continue
        documents.append({'label': row.get('label') or row.get('typeAbbrev'),
                          'url': f'https://cdn.clinicaltrials.gov/large-docs/{identity[-2:]}/{identity}/{filename}',
                          'has_protocol': bool(row.get('hasProtocol')), 'date': row.get('date')})
    preferred = next((row for row in documents if row['has_protocol']), documents[0] if documents else {})
    return {'paper_id': f'ClinicalTrials:{identity}', 'record_type': 'clinical_trial_registration',
            'title': identification.get('officialTitle') or identification.get('briefTitle') or '',
            'authors': [], 'year': int(date[:4]) if date[:4].isdigit() else None,
            'publication_date': date or None, 'venue': 'ClinicalTrials.gov',
            'abstract': description.get('briefSummary') or description.get('detailedDescription') or '',
            'abstract_kind': 'study_summary',
            'publication_types': ['ClinicalTrialRegistration'], 'external_ids': {'ClinicalTrials': identity},
            'source': 'clinicaltrials', 'url': f'https://clinicaltrials.gov/study/{identity}',
            'open_access_url': preferred.get('url'), 'is_open_access': bool(preferred),
            'details': {'status': status.get('overallStatus'), 'last_update': (status.get('lastUpdatePostDateStruct') or {}).get('date'),
                        'sponsor': sponsor.get('name'), 'phases': design.get('phases') or [],
                        'study_type': design.get('studyType'), 'design': design.get('designInfo'),
                        'conditions': (protocol.get('conditionsModule') or {}).get('conditions') or [],
                        'planned_primary_outcomes': (protocol.get('outcomesModule') or {}).get('primaryOutcomes') or [],
                        'has_posted_results': bool(item.get('hasResults')), 'documents': documents}}


def search_papers(query: str, limit: int = 100, **kwargs) -> list[dict]:
    params, papers = {'query.term': query, 'pageSize': min(limit, 100), 'format': 'json'}, []
    while len(papers) < limit:
        try:
            pacing.wait("clinicaltrials")
            response = access.get(BASE, params=params, timeout=15)
            response.raise_for_status()
            page = response.json()
        except Exception as error:
            if not papers:
                raise
            from . import PartialResults
            return PartialResults(papers, f'ClinicalTrials pagination interrupted ({type(error).__name__})')
        papers.extend(format_paper(row) for row in page.get('studies') or [])
        from . import publish
        if not page.get('nextPageToken') or not publish(papers):
            break
        params['pageToken'] = page['nextPageToken']
    return papers[:limit]


def get_paper(identifier: str) -> dict | None:
    if not re.fullmatch(ID_PATTERN, identifier):
        return None
    identity = re.search(r'NCT\d{8}', identifier, re.I)[0].upper()
    item = _study(identity)
    if not item:
        return None
    paper = format_paper(item)
    if urlparse(identifier).hostname == 'cdn.clinicaltrials.gov':
        if identifier not in {row['url'] for row in paper['details']['documents']}:
            return None
        paper.update(open_access_url=identifier, _requested_copy_url=identifier)
    return paper


def get_references(identifier: str, limit: int = 20, **kwargs) -> list[dict]:
    if not re.fullmatch(ID_PATTERN, identifier):
        return []
    item = _study(re.search(r'NCT\d{8}', identifier, re.I)[0].upper())
    refs = (item.get('protocolSection') or {}).get('referencesModule', {}).get('references') or []
    refs = sorted(refs, key=lambda row: row.get('type') != 'RESULT')
    return [{'paper_id': 'PMID:' + str(row['pmid']), 'external_ids': {'PMID': str(row['pmid'])},
             'title': '', '_needs_metadata': True, '_reference': {'unstructured': row.get('citation') or ''},
             'source': 'clinicaltrials'} for row in refs if str(row.get('pmid') or '').isdigit()][:limit]
