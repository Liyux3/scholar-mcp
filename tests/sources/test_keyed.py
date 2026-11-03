"""Opt-in sources: silent without a key, mapped to the shared paper format with one."""
import httpx
import pytest

from scholar_mcp import config, sources
from scholar_mcp.sources import ads, lens, springer, wos

ADS = {"response": {"docs": [{"title": ["Dark matter halos"], "author": ["Doe, J."], "year": "2021", "pub": "ApJ",
                              "abstract": "Halo profiles.", "doi": ["10.3847/x"], "bibcode": "2021ApJ...1D",
                              "identifier": ["arXiv:2101.00001", "10.3847/x"], "citation_count": 12}]}}
SPRINGER = {"records": [{"title": "Graph learning", "creators": [{"creator": "Li, X."}], "publicationDate": "2022-05-01",
                         "publicationName": "Machine Learning", "abstract": {"h1": "Abstract", "p": "We learn graphs."},
                         "doi": "10.1007/x", "openaccess": "true",
                         "url": [{"format": "pdf", "value": "https://link.springer.com/x.pdf"}]}]}
WOS = {"hits": [{"uid": "WOS:000123", "title": "Memory in agents", "names": {"authors": [{"displayName": "Kim, S"}]},
                 "source": {"sourceTitle": "Nature", "publishYear": 2024}, "identifiers": {"doi": "10.1038/x"},
                 "citations": [{"db": "WOS", "count": 7}]}]}
LENS = {"data": [{"lens_id": "001-002", "title": "CRISPR off-target", "authors": [{"first_name": "Ana", "last_name": "Ruiz"}],
                  "year_published": 2020, "source": {"title": "Cell"}, "abstract": "Detection.",
                  "external_ids": [{"type": "doi", "value": "10.1016/x"}, {"type": "pmid", "value": "123"}],
                  "scholarly_citations_count": 40,
                  "open_access": {"locations": [{"pdf_urls": ["https://x.org/a.pdf"]}]}}]}

CASES = [
    (ads, "ADS_API_TOKEN", ADS, "authorization", {"DOI": "10.3847/x", "ADS": "2021ApJ...1D", "ArXiv": "2101.00001"}),
    (springer, "SPRINGER_API_KEY", SPRINGER, None, {"DOI": "10.1007/x"}),
    (wos, "WOS_API_KEY", WOS, "x-apikey", {"DOI": "10.1038/x", "WoS": "WOS:000123"}),
    (lens, "LENS_API_TOKEN", LENS, "authorization", {"DOI": "10.1016/x", "PMID": "123", "Lens": "001-002"}),
]


@pytest.mark.parametrize("client,key,payload,header,ids", CASES)
def test_keyed_source_maps_records_and_stays_silent_without_a_key(monkeypatch, client, key, payload, header, ids):
    monkeypatch.setattr(config, key, None)
    assert client.search_papers("anything", limit=5) == []
    monkeypatch.setattr(config, key, "secret")
    seen = {}
    def reply(request):
        seen["request"] = request
        return httpx.Response(200, json=payload)
    real = httpx.Client
    transport = httpx.MockTransport(reply)
    monkeypatch.setattr(httpx, "get", lambda url, **kw: real(transport=transport).get(url, **kw))
    monkeypatch.setattr(httpx, "post", lambda url, **kw: real(transport=transport).post(url, **kw), raising=False)
    [paper] = client.search_papers("dark matter halo profiles", limit=5)
    assert paper["title"] and paper["year"] and paper["authors"] and paper["external_ids"] == ids
    if header:
        assert "secret" in seen["request"].headers[header]
    else:
        assert seen["request"].url.params["api_key"] == "secret"
    assert "secret" not in str(paper)


def test_opt_in_sources_need_their_key_and_leave_the_default_fleet_alone(monkeypatch):
    for name in ("ads", "springer", "wos", "lens"):
        source = sources.get(name)
        assert source.opt_in and source.requires_key
    monkeypatch.setattr(config, "ADS_API_TOKEN", None)
    assert "ads" not in {s.name for s in sources.search_sources()}
    monkeypatch.setattr(config, "ADS_API_TOKEN", "t")
    assert "ads" in {s.name for s in sources.search_sources()}
