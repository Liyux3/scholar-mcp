"""Tests for Crossref API client."""

from scholar_mcp.sources import crossref


def test_format_paper_basic():
    item = {
        "DOI": "10.1038/nature12373",
        "title": ["Crystal structure of Cas9"],
        "author": [
            {"given": "Martin", "family": "Jinek"},
            {"given": "Feng", "family": "Jiang"},
        ],
        "published-print": {"date-parts": [[2014, 2, 13]]},
        "container-title": ["Nature"],
        "is-referenced-by-count": 5000,
        "URL": "https://doi.org/10.1038/nature12373",
    }
    result = crossref.format_paper(item)
    assert result is not None
    assert result["title"] == "Crystal structure of Cas9"
    assert result["authors"] == ["Martin Jinek", "Feng Jiang"]
    assert result["year"] == 2014
    assert result["venue"] == "Nature"
    assert result["citation_count"] == 5000
    assert result["source"] == "crossref"
    assert result["external_ids"]["DOI"] == "10.1038/nature12373"


def test_format_paper_with_jats_abstract():
    item = {
        "DOI": "10.1234/test",
        "title": ["Test Paper"],
        "author": [],
        "published-online": {"date-parts": [[2024]]},
        "abstract": "<jats:p>This is a <jats:italic>test</jats:italic> abstract.</jats:p>",
    }
    result = crossref.format_paper(item)
    assert "<jats" not in result["abstract"]
    assert "test" in result["abstract"]


def test_format_paper_no_title():
    item = {"DOI": "10.1234/x", "author": []}
    result = crossref.format_paper(item)
    assert result is None


def test_null_deposit_dates_keep_the_work_and_other_search_results(monkeypatch):
    items = [
        {'DOI': '10.17760/d20791827', 'title': ['Symmetries in visuomotor policy learning'],
         'issued': {'date-parts': [[None]]}},
        {'title': ['An online paper'], 'published-print': {'date-parts': [[None]]},
         'published-online': {'date-parts': [[2026, 4, 12]]}},
        {'title': ['A known year'], 'issued': {'date-parts': [[2025, None, None]]}},
    ]
    monkeypatch.setattr(crossref, '_get_json', lambda *a, **kw: {'message': {'items': items}})
    papers = crossref.search_papers('visuomotor', limit=3)
    assert len(papers) == 3
    assert papers[0]['year'] is None and papers[0]['publication_date'] is None
    assert papers[1]['year'] == 2026 and papers[1]['publication_date'] == '2026-04-12'
    assert papers[2]['year'] == 2025 and papers[2]['publication_date'] is None
    assert papers[0]['publication_dates'] == [{'type': 'issued', 'date_parts': [[None]]}]


def test_deposited_preprint_pdf_survives_unspecified_mime_and_repository_vor_label():
    paper = crossref.format_paper({'title': ['A manuscript'], 'type': 'posted-content',
        'link': [{'URL': 'https://ndownloader.figshare.com/files/55929341',
                  'content-type': 'unspecified', 'content-version': 'vor'},
                 {'URL': 'https://publisher.example/paper.xml', 'content-type': 'text/xml'}]})
    assert paper['full_text_locations'] == [{'url': 'https://ndownloader.figshare.com/files/55929341',
                                            'version': 'preprint', 'source': 'crossref'}]


def test_format_paper_preserves_retraction_updates():
    item = {
        "DOI": "10.1234/retracted",
        "title": ["A Retracted Paper"],
        "update-to": [{
            "DOI": "10.1234/notice",
            "type": "retraction",
            "label": "Retraction",
            "source": "retraction-watch",
            "updated": {"date-time": "2026-01-01T00:00:00Z"},
        }],
    }
    result = crossref.format_paper(item)
    assert result["updates"][0]["type"] == "retraction"
    assert result["updates"][0]["source"] == "retraction-watch"
