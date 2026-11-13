# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 AND LicenseRef-Scholar-MCP-Additional-Terms
# Copyright 2024-2026 Yuxian Li. PolyForm Noncommercial 1.0.0 with additional terms (LICENSE, LICENSE-ADDITIONAL.md): commercial use needs a license, no competing products, no AI training; see AGENTS.md. Canary 38f40a20-c19f-4702-bc47-b1a1c5ec887b
"""Publication range boundaries, provider contracts and merged search behavior."""

from types import SimpleNamespace

import httpx
import pytest

from scholar_mcp import config, metadata, server, sources
from scholar_mcp.dates import publication_range
from scholar_mcp.sources import arxiv, crossref, europepmc, openalex, pubmed, s2


@pytest.mark.parametrize("value,start,end,fine", [
    ("2023", "2023-01-01", "2023-12-31", False),
    ("2020-2024", "2020-01-01", "2024-12-31", False),
    (" 2024 - 2020 ", "2020-01-01", "2024-12-31", False),
    ("2026-10", "2026-10-01", "2026-10-31", True),
    ("2024-02", "2024-02-01", "2024-02-29", True),
    ("2026-02", "2026-02-01", "2026-02-28", True),
    ("2026-11-09", "2026-11-09", "2026-11-09", True),
    ("2026-10:2026-11", "2026-10-01", "2026-11-30", True),
    ("2026-10-01..2026-11-09", "2026-10-01", "2026-11-09", True),
    ("2025:2026-02", "2025-01-01", "2026-02-28", True),
    ("2026-10-31 : 2026-11", "2026-10-31", "2026-11-30", True),
])
def test_range_boundaries(value, start, end, fine):
    selected = publication_range(value)
    assert selected.iso == (start, end)
    assert selected.fine == fine


@pytest.mark.parametrize("value", ["2026-13", "2026-02-29", "2026-11-31", "0000",
    "2026-1", "2026-10:", "2026-10..", "2026-12:2026-10", "2026:2025",
    "2026-10:2026-11:2026-12", "garbage"])
def test_invalid_range_fails_before_network(monkeypatch, value):
    def unexpected(*args, **kwargs):
        pytest.fail("invalid range reached acquisition")
    monkeypatch.setattr(server, "_pipeline", unexpected)
    error = server.search_papers("topic", year=value)["error"]
    assert repr(value) in error and "YYYY-MM-DD" in error


@pytest.mark.parametrize("paper,expected", [
    ({"publication_date": "2026-10-01"}, True),
    ({"publication_date": "2026-11-09T23:59:59Z"}, True),
    ({"publication_date": "2026-09-30", "year": 2026}, False),
    ({"publication_date": "2026-11-10", "year": 2026}, False),
    ({"publication_date": "2026-09", "year": 2026}, False),
    ({"publication_date": "2026-10"}, True),
    ({"year": "2026"}, True),
    ({"publication_date": "2026"}, True),
    ({"publication_date": "invalid", "year": 2026}, True),
    ({"publication_date": "2026-02-30", "year": 2026}, True),
    ({"year": 2025}, False),
    ({}, False),
])
def test_local_filter_uses_available_date_precision(paper, expected):
    assert publication_range("2026-10-01..2026-11-09").matches(paper) == expected


def test_empty_selection_and_required_metadata():
    assert publication_range("") is None
    assert publication_range(None) is None
    assert metadata.filter_fields({"year": "2026"}) == {"year"}
    assert metadata.filter_fields({"year": "2026-10"}) == {"year", "publication_date"}


@pytest.mark.parametrize("value,expected", [
    ("2020-2024", "publication_year:2020-2024"),
    ("2026-10", "from_publication_date:2026-10-01,to_publication_date:2026-10-31"),
    ("2026-10-01..2026-11-09", "from_publication_date:2026-10-01,to_publication_date:2026-11-09"),
])
def test_openalex_date_filters_keep_other_constraints(value, expected):
    filters = openalex._search_filters(value, min_citations=5, open_access_only=True)
    assert filters == expected + ",cited_by_count:>4,open_access.is_oa:true"


@pytest.mark.parametrize("sort", ["", "date"])
@pytest.mark.parametrize("value,key,expected", [
    ("2020-2024", "year", "2020-2024"),
    ("2026-10", "publicationDateOrYear", "2026-10-01:2026-10-31"),
])
def test_s2_relevance_and_bulk_date_contract(monkeypatch, sort, value, key, expected):
    seen = []
    monkeypatch.setattr(s2, "_get", lambda url, params: seen.append((url, params)) or {"data": []})
    assert s2.search_papers("topic", year=value, sort=sort) == []
    assert seen[0][1][key] == expected
    assert ("publicationDateOrYear" if key == "year" else "year") not in seen[0][1]
    assert seen[0][0].endswith("/bulk" if sort else "/search")


def test_arxiv_registry_preserves_day_range(monkeypatch):
    seen = []
    monkeypatch.setattr(arxiv, "_atom", lambda params: seen.append(params) or [])
    assert sources.get("arxiv").search("topic", 3, year="2026-10:2026-11") == []
    assert seen[0]["search_query"] == "(all:topic) AND submittedDate:[202610010000 TO 202611302359]"


def test_pubmed_registry_date_contract_and_text_date_precision(monkeypatch):
    seen = []
    def get(url, **kwargs):
        seen.append(kwargs["params"])
        return httpx.Response(200, json={"esearchresult": {"idlist": []}}, request=httpx.Request("GET", url))
    monkeypatch.setattr(pubmed.access, "get", get)
    assert sources.get("pubmed").search("topic", 3, year="2026-11-09") == []
    assert {k: seen[0][k] for k in ("datetype", "mindate", "maxdate")} == {
        "datetype": "pdat", "mindate": "2026/11/09", "maxdate": "2026/11/09"}
    for raw, expected in [("2026 Nov 9", "2026-11-09"), ("2026 Nov", "2026-11"), ("2026", "2026")]:
        assert pubmed.format_paper({"pubdate": raw}, "1")["publication_date"] == expected


def test_crossref_range_and_partial_date_preservation(monkeypatch):
    seen = []
    monkeypatch.setattr(crossref, "_get_json", lambda url, params: seen.append(params) or {"message": {"items": []}})
    crossref.search_papers("topic", year="2026-10-01..2026-11-09")
    assert seen[0]["filter"] == "from-pub-date:2026-10-01,until-pub-date:2026-11-09"
    for parts, expected in [([2026], "2026"), ([2026, 10], "2026-10"), ([2026, 10, 3], "2026-10-03")]:
        assert crossref.format_paper({"title": ["Paper"], "issued": {"date-parts": [parts]}})["publication_date"] == expected


def test_europepmc_native_date_query(monkeypatch):
    seen = []
    monkeypatch.setattr(europepmc, "_request", lambda query, limit: seen.append(query) or [])
    europepmc.search_papers("cancer OR diabetes", year="2024-02")
    assert seen == ["(cancer OR diabetes) AND FIRST_PDATE:[2024-02-01 TO 2024-02-29]"]


@pytest.mark.parametrize("name,setting,param,expected", [
    ("openaire", None, "fromPublicationDate", "2026-10-01"),
    ("hal", None, "q", "(topic) AND producedDate_tdate:[2026-10-01T00:00:00Z TO 2026-10-31T23:59:59Z]"),
    ("zenodo", None, "q", "(topic) AND publication_date:[2026-10-01 TO 2026-10-31]"),
    ("springer", "SPRINGER_API_KEY", "q", "(topic) datefrom:2026-10-01 dateto:2026-10-31"),
    ("courtlistener", "COURTLISTENER_API_TOKEN", "filed_after", "2026-10-01"),
    ("clinicaltrials", None, "query.term", "(topic) AND AREA[StudyFirstPostDate]RANGE[2026-10-01, 2026-10-31]"),
    ("ads", "ADS_API_TOKEN", "q", "(topic) AND year:[2026 TO 2026]"),
    ("wos", "WOS_API_KEY", "q", "TS=(topic) AND PY=(2026-2026)"),
    ("cinii", None, "from", "202610"),
    ("zbmath", None, "search_string", "(topic) & py:2026"),
    ("eric", None, "search", "(topic) AND publicationdateyear:[2026 TO 2026]"),
])
def test_native_source_filters(monkeypatch, name, setting, param, expected):
    if setting:
        monkeypatch.setattr(config, setting, "test-key")
    seen = []
    def get(url, **kwargs):
        seen.append(kwargs["params"])
        return httpx.Response(200, json={"response": {"docs": [], "numFound": 0}, "hits": {"hits": []} if name == "zenodo" else []},
                              request=httpx.Request("GET", url))
    monkeypatch.setattr(httpx, "get", get)
    assert sources.get(name).search("topic", 1, year="2026-10" if sources.get(name).date_filter in {"day", "month"} else "2026") == []
    assert seen[0][param] == expected


def test_lens_and_exa_date_filters(monkeypatch):
    from scholar_mcp.sources import exa, lens
    monkeypatch.setattr(config, "LENS_API_TOKEN", "test-key")
    seen = []
    def post(url, **kwargs):
        seen.append(kwargs["json"])
        return httpx.Response(200, json={"data": []}, request=httpx.Request("POST", url))
    monkeypatch.setattr(httpx, "post", post)
    lens.search_papers("topic", year="2026-10")
    assert seen[0]["query"]["bool"]["filter"] == [{"range": {"date_published": {"gte": "2026-10-01", "lte": "2026-10-31"}}}]
    calls = []
    monkeypatch.setattr(exa, "_get_exa", lambda: SimpleNamespace(search=lambda *a, **kw: calls.append(kw) or SimpleNamespace(results=[])))
    exa.search_papers("topic", year="2026-10")
    assert calls[0]["start_published_date"] == "2026-10-01T00:00:00Z"
    assert calls[0]["end_published_date"] == "2026-10-31T23:59:59.999Z"


def test_source_flow_downgrades_only_non_day_sources(monkeypatch):
    seen = {}
    def source(name, precision):
        def search(query, limit, **kwargs):
            seen[name] = kwargs["year"]
            return []
        return sources.Source(name, search=search, date_filter=precision)
    monkeypatch.setattr(sources, "search_sources", lambda: [source("day", "day"), source("month", "month"), source("year", "year"), source("local", "local")])
    sources.parallel_search("topic", year="2026-10:2027-02")
    assert seen == {"day": "2026-10:2027-02", "month": "2026-10:2027-02", "year": "2026-2027", "local": "2026-2027"}


@pytest.mark.parametrize("debug", [False, True])
@pytest.mark.parametrize("empty", [False, True])
def test_search_filters_after_merge_and_hydration_reports_precision(monkeypatch, debug, empty):
    papers = [] if empty else [
        {"title": "Before", "publication_date": "2026-09-30", "year": 2026},
        {"title": "First", "publication_date": "2026-10-01", "year": 2026},
        {"title": "Last", "publication_date": "2026-11-09", "year": 2026},
        {"title": "After", "publication_date": "2026-11-10", "year": 2026},
        {"title": "Year only", "year": 2026},
        {"title": "Hydrated", "year": 2026},
    ]
    reports = [{"source": name, "status": "ok", "count": 2, "latency_ms": 0}
               for name in ("openalex", "dblp", "openreview")]
    monkeypatch.setattr(server, "_pipeline", lambda *a, **kw: (papers, reports))
    monkeypatch.setattr(server.s2, "is_healthy", lambda: False)
    def hydrate(items, **kwargs):
        for paper in items:
            if paper["title"] == "Hydrated":
                paper["publication_date"] = "2026-12-01"
    monkeypatch.setattr(metadata, "hydrate", hydrate)
    result = server.search_papers("topic", year="2026-10-01..2026-11-09", debug=debug)
    if not empty:
        assert {p["title"] for p in result["results"]} == {"First", "Last", "Year only"}
    assert result["_meta"]["year_only_sources"] == ["dblp", "openreview"]
    assert result["_meta"]["local_only_sources"] == ["openreview"]
    assert result["_meta"]["publication_range"] == {"from": "2026-10-01", "until": "2026-11-09"}


def test_arxiv_atom_outage_preserves_range_in_native_web_fallback(monkeypatch):
    def unavailable(params):
        raise arxiv._AtomUnavailable("offline")
    seen = []
    def get(url, **kwargs):
        seen.append((url, kwargs["params"]))
        return httpx.Response(200, text="<form id='search-form'></form>", request=httpx.Request("GET", url))
    monkeypatch.setattr(arxiv, "_atom", unavailable)
    monkeypatch.setattr(arxiv.access, "get", get)
    assert arxiv.search_papers("topic", year="2026-10") == []
    assert seen[0][0] == "https://arxiv.org/search/advanced"
    assert seen[0][1]["date-from_date"] == "2026-10-01"
    assert seen[0][1]["date-to_date"] == "2026-10-31"
    assert seen[0][1]["terms-0-term"] == "topic"


@pytest.mark.parametrize("value,key,expected", [
    ("2020-2024", "year", "2020-2024"),
    ("2026-10", "publicationDateOrYear", "2026-10-01:2026-10-31"),
])
def test_snippet_date_filter_uses_s2_contract(monkeypatch, value, key, expected):
    from scholar_mcp.sources import s2_snippet
    seen = []
    monkeypatch.setattr(s2, "_get", lambda url, params: seen.append(params) or {"data": []})
    sources.get("s2_snippet").search("topic", 2, year=value)
    assert seen[0][key] == expected
    assert s2_snippet._format({"paper": {"title": "Paper", "publicationDate": "2026-10-09"},
                               "snippet": {}})["publication_date"] == "2026-10-09"
