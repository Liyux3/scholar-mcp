"""Tests for new source clients: Europe PMC, DBLP, INSPIRE-HEP."""

import pytest


@pytest.mark.parametrize('name', ['europepmc', 'doaj', 'openaire', 'hal', 'zenodo', 'core'])
def test_pdf_repository_results_must_identify_the_requested_paper(monkeypatch, name):
    from importlib import import_module
    module = import_module(f'scholar_mcp.sources.{name}')
    paper = {'title': 'Attention Is All You Need', 'year': 2017,
             'authors': ['Ashish Vaswani'], 'external_ids': {'ArXiv': '1706.03762'}}
    candidates = [
        ('Attention Is All You Need for Protein Prediction', 'Ashish Vaswani', 2017, 'near'),
        ('Attention Is All You Need', 'Other Author', 2026, 'homonym'),
        ('Attention Is All You Need', 'Ashish Vaswani', 2017, 'right'),
    ]
    def url(key):
        return f'https://files.example/{key}.pdf'
    if name == 'europepmc':
        rows = [{'title': title, 'authorString': author, 'pubYear': str(year),
                 'fullTextUrlList': {'fullTextUrl': [{'documentStyle': 'pdf', 'availabilityCode': 'OA', 'url': url(key)}]}}
                for title, author, year, key in candidates]
        monkeypatch.setattr(module, 'pmc_pdf_urls', lambda _: [])
    elif name == 'doaj':
        rows = [{'title': title, 'authors': [author], 'year': year, 'open_access_url': url(key)}
                for title, author, year, key in candidates]
    elif name == 'openaire':
        rows = [{'mainTitle': title, 'authors': [{'fullName': author}], 'publicationDate': str(year),
                 'instances': [{'accessRight': {'label': 'OPEN'}, 'urls': [url(key)]}]}
                for title, author, year, key in candidates]
    elif name == 'hal':
        rows = [{'title_s': [title], 'authFullName_s': [author], 'producedDateY_i': year, 'fileMain_s': url(key)}
                for title, author, year, key in candidates]
    elif name == 'zenodo':
        rows = [{'metadata': {'title': title, 'creators': [{'name': author}], 'publication_date': str(year),
                             'resource_type': {'type': 'publication'}},
                 'files': [{'key': key + '.pdf', 'links': {'self': url(key)}}]}
                for title, author, year, key in candidates]
    else:
        rows = [{'title': title, 'authors': [author], 'publishedDate': str(year), 'downloadUrl': url(key)}
                for title, author, year, key in candidates]
    request = '_search' if name == 'doaj' else '_get' if name == 'core' else '_request'
    monkeypatch.setattr(module, request, lambda *a, **kw: {'results': rows} if name == 'core' else rows)
    locations = module.resolve_pdf(paper)
    assert [item['url'] if isinstance(item, dict) else item for item in locations] == [url('right')]


class TestRepositoryRecords:
    def test_hal_record_exposes_native_pdf(self):
        from scholar_mcp.sources import hal

        paper = hal._normalize({
            "halId_s": "hal-1",
            "title_s": ["HAL paper"],
            "authFullName_s": ["A. Author"],
            "fileMain_s": "https://hal.science/hal-1/document",
            "doiId_s": "10.1/hal",
            "producedDateY_i": 2025,
        })

        assert paper["open_access_url"].endswith("/document")
        assert paper["external_ids"]["HAL"] == "hal-1"

    def test_zenodo_admits_publications_and_selects_pdf(self):
        from scholar_mcp.sources import zenodo

        paper = zenodo._normalize({
            "id": 42,
            "metadata": {
                "title": "Zenodo paper",
                "doi": "10.5281/zenodo.42",
                "publication_date": "2025-01-02",
                "resource_type": {"type": "publication"},
                "creators": [{"name": "A. Author"}],
                "access_right": "open",
            },
            "files": [{
                "key": "paper.pdf",
                "links": {"self": "https://zenodo.org/api/records/42/files/paper.pdf/content"},
            }],
            "links": {"self_html": "https://zenodo.org/records/42"},
        })

        assert paper["open_access_url"].endswith("/content")
        assert paper["year"] == 2025

    def test_openaire_service_token_is_fetched_once_and_paced_under_its_own_limit(self, monkeypatch):
        import httpx
        from scholar_mcp import config
        from scholar_mcp.sources import openaire, pacing
        monkeypatch.setattr(config, "OPENAIRE_CLIENT_ID", "svc")
        monkeypatch.setattr(config, "OPENAIRE_CLIENT_SECRET", "secret")
        monkeypatch.setattr(openaire, "_token", None)
        posts, gets, waits = [], [], []
        monkeypatch.setattr(openaire.httpx, "post", lambda url, **kw: posts.append(kw["auth"]) or httpx.Response(
            200, json={"access_token": "tok", "expires_in": 3600}, request=httpx.Request("POST", url)))
        monkeypatch.setattr(openaire.httpx, "get", lambda url, **kw: gets.append(kw["headers"]) or httpx.Response(
            200, json={"results": []}, request=httpx.Request("GET", url)))
        monkeypatch.setattr(pacing, "wait", lambda provider, key=None, **kw: waits.append((provider, key)))
        openaire._request({"search": "a"})
        openaire._request({"search": "b"})
        assert posts == [("svc", "secret")] and gets == [{"Authorization": "Bearer tok"}] * 2
        assert waits == [("openaire", "svc")] * 2
        # A token that cannot be had leaves requests anonymous, not failing.
        monkeypatch.setattr(openaire, "_token", None)
        monkeypatch.setattr(openaire.httpx, "post", lambda url, **kw: httpx.Response(401, request=httpx.Request("POST", url)))
        openaire._request({"search": "c"})
        assert gets[-1] is None and waits[-1] == ("openaire", None)

    def test_openaire_only_uses_open_repository_instances(self):
        from scholar_mcp.sources import openaire

        item = {
            "mainTitle": "Repository paper",
            "id": "oa-1",
            "authors": [],
            "pids": [{"scheme": "doi", "value": "10.1/oa"}],
            "bestAccessRight": {"label": "OPEN ACCESS"},
            "instances": [
                {"accessRight": {"label": "CLOSED"}, "urls": ["https://closed.test/file"]},
                {"accessRight": {"label": "OPEN ACCESS"}, "urls": ["https://repo.test/file"]},
            ],
        }

        assert openaire._instance_urls(item, open_only=True) == ["https://repo.test/file"]


@pytest.mark.integration
class TestEuropePMC:
    def test_search_returns_results(self):
        from scholar_mcp.sources import europepmc
        results = europepmc.search_papers("CRISPR", limit=2)
        assert len(results) > 0
        p = results[0]
        assert "title" in p and p["title"]
        assert "source" in p and p["source"] == "europepmc"

    def test_result_has_citation_count(self):
        from scholar_mcp.sources import europepmc
        results = europepmc.search_papers("deep learning", limit=1)
        if results:
            assert "citation_count" in results[0]

    def test_result_format(self):
        from scholar_mcp.sources import europepmc
        results = europepmc.search_papers("transformer", limit=1)
        if results:
            p = results[0]
            assert isinstance(p.get("authors"), list)
            assert isinstance(p.get("year"), (int, type(None)))


@pytest.mark.integration
class TestINSPIRE:
    def test_search_returns_results(self):
        from scholar_mcp.sources import inspirehep
        results = inspirehep.search_papers("dark matter", limit=2)
        assert len(results) > 0
        p = results[0]
        assert "title" in p and p["title"]
        assert p["source"] == "inspirehep"

    def test_physics_domain(self):
        from scholar_mcp.sources import inspirehep
        results = inspirehep.search_papers("higgs boson", limit=1)
        if results:
            assert "Physics" in results[0].get("fields_of_study", [])


class TestDBLP:
    @pytest.mark.parametrize("carrier", ["meta", "header"])
    @pytest.mark.parametrize("after_verification_status", [200, 429])
    def test_metarefresh_preserves_session_and_returns_papers(self, monkeypatch, carrier, after_verification_status):
        import httpx
        from scholar_mcp import config
        from scholar_mcp.sources import dblp
        from pathlib import Path

        calls = []
        delays = []
        paced = []
        quota_returned = False
        def handle(request):
            nonlocal quota_returned
            calls.append(request)
            if request.url.path == "/search/publ/api" and "auth=verified" not in request.headers.get("cookie", ""):
                redirect = "2;url=/.within.website/x/cmd/anubis/api/pass-challenge?id=example"
                headers = {"content-type": "text/html", "set-cookie": "verification=test; Path=/; Secure"}
                body = '<script id="anubis_challenge"></script>'
                if carrier == "header":
                    headers["Refresh"] = redirect
                else:
                    body += '<meta http-equiv="refresh" content="' + redirect + '">'
                return httpx.Response(202, headers=headers, text=body)
            if request.url.path != "/search/publ/api":
                assert request.headers["cookie"] == "verification=test"
                return httpx.Response(302, headers={
                    "location": str(calls[0].url), "set-cookie": "auth=verified; Path=/; Secure"})
            if after_verification_status == 429 and not quota_returned:
                quota_returned = True
                return httpx.Response(429, headers={'retry-after': '60'})
            return httpx.Response(200, json={"result": {"hits": {"hit": [
                {"info": {"title": "Transformer.", "year": "2026", "key": "test/paper"}},
            ]}}})

        client = httpx.Client
        monkeypatch.setattr(dblp.httpx, "Client", lambda **kw: client(
            transport=httpx.MockTransport(handle), **kw))
        monkeypatch.setattr(dblp.time, "sleep", delays.append)
        monkeypatch.setattr(dblp.pacing, "wait", paced.append)
        if after_verification_status == 429:
            with pytest.raises(httpx.HTTPStatusError) as error:
                dblp.search_papers("transformer", limit=3)
            assert error.value.response.status_code == 429
        else:
            assert dblp.search_papers("transformer", limit=3)[0]["title"] == "Transformer"
        assert delays == [3]
        assert len(calls) == len(paced) == 3
        saved = Path(config.DATA_DIR) / "sessions" / "dblp.cookies"
        assert saved.stat().st_mode & 0o777 == 0o600
        # A new CLI/MCP process needs neither a new challenge nor a browser.
        dblp._client.close()
        monkeypatch.setattr(dblp, "_client", None)
        assert dblp.search_papers("another query", limit=3)[0]["title"] == "Transformer"
        assert len(calls) == len(paced) == 4 and delays == [3]
        assert calls[-1].url.params['q'] == 'another query'

    def test_verification_cookie_expires_and_corruption_recovers(self, monkeypatch):
        import httpx
        from pathlib import Path
        from scholar_mcp import config
        from scholar_mcp.sources import dblp

        path = Path(config.DATA_DIR) / "sessions" / "dblp.cookies"
        path.parent.mkdir(parents=True)
        seen = []
        def handle(request):
            seen.append(request.headers.get('cookie', ''))
            return httpx.Response(200, json={"result": {"hits": {"hit": []}}})
        client = httpx.Client
        monkeypatch.setattr(dblp.httpx, "Client", lambda **kw: client(
            transport=httpx.MockTransport(handle), **kw))
        for contents in ['incomplete cookie cache',
                         '#LWP-Cookies-2.0\nSet-Cookie3: auth=expired; path="/"; domain="dblp.org"; path_spec; secure; expires="2000-01-01 00:00:00Z"; version=0\n']:
            path.write_text(contents)
            assert dblp.search_papers('retry') == []
        assert seen == ['', '']

    def test_metarefresh_keeps_requests_on_dblp(self, monkeypatch):
        import httpx
        from scholar_mcp.sources import dblp

        calls = []
        def handle(request):
            calls.append(request)
            return httpx.Response(200, headers={"content-type": "text/html"},
                                  text='<script id="anubis_challenge"></script><meta http-equiv="refresh" '
                                       'content="0;url=https://other.invalid/.within.website/x/cmd/anubis/api/pass-challenge">')
        client = httpx.Client
        monkeypatch.setattr(dblp.httpx, "Client", lambda **kw: client(
            transport=httpx.MockTransport(handle), **kw))
        with pytest.raises(PermissionError):
            dblp.search_papers("transformer", limit=3)
        assert len(calls) == 1

    def test_verification_page_is_reported_as_blocked(self, monkeypatch):
        import httpx
        from scholar_mcp import sources
        from scholar_mcp.sources import dblp

        response = httpx.Response(200, headers={"content-type": "text/html"},
                                  text="<title>Making sure you're not a bot!</title>",
                                  request=httpx.Request("GET", dblp.BASE_URL))
        monkeypatch.setattr(dblp.httpx.Client, "get", lambda *a, **kw: response)
        result = sources._timed_call("dblp", dblp.search_papers, "transformer", 3)
        assert result.status == "blocked"
        assert "verification" in result.error
        assert "JSONDecodeError" not in result.error

    def test_successful_requests_reuse_the_verified_client(self, monkeypatch):
        import httpx
        from scholar_mcp.sources import dblp
        created = []
        real_client = httpx.Client

        def client(**kwargs):
            created.append(True)
            return real_client(transport=httpx.MockTransport(lambda request: httpx.Response(
                200, json={"result": {"hits": {"hit": []}}})), **kwargs)

        monkeypatch.setattr(dblp.httpx, "Client", client)
        assert dblp.search_papers("first") == []
        assert dblp.search_papers("second") == []
        assert len(created) == 1

    def test_valid_empty_response_remains_empty(self, monkeypatch):
        import httpx
        from scholar_mcp import sources
        from scholar_mcp.sources import dblp

        response = httpx.Response(200, json={"result": {"hits": {"@total": "0"}}},
                                  request=httpx.Request("GET", dblp.BASE_URL))
        monkeypatch.setattr(dblp.httpx.Client, "get", lambda *a, **kw: response)
        assert sources._timed_call("dblp", dblp.search_papers, "q", 3).status == "empty"

    @pytest.mark.integration
    def test_returns_list_for_query_with_no_matches(self):
        """A query with genuinely no CS matches returns an empty list. HTTP
        failures are a separate case and must raise, see test_propagates_errors.
        """
        from scholar_mcp.sources import dblp
        results = dblp.search_papers("nonexistent_query_xyz_12345", limit=1)
        assert isinstance(results, list)

    def test_propagates_errors(self, monkeypatch):
        """DBLP throttles with 429/503 rather than slowing down. Returning []
        would make throttling look like sparse CS coverage.
        """
        import httpx
        from scholar_mcp.sources import dblp

        response = httpx.Response(503, request=httpx.Request('GET', dblp.BASE_URL))
        monkeypatch.setattr(dblp.httpx.Client, "get", lambda *a, **kw: response)
        with pytest.raises(httpx.HTTPStatusError):
            dblp.search_papers("anything", limit=10)


class TestGoogleScholarBlocking:
    def test_http_200_challenge_is_blocked_in_fanout(self, monkeypatch):
        import httpx
        from scholar_mcp import sources
        from scholar_mcp.sources import google_scholar

        response = httpx.Response(200, text='<div id="gs_captcha_ccl">Verify</div>',
                                  request=httpx.Request("GET", google_scholar.SCHOLAR_URL))
        monkeypatch.setattr(google_scholar.httpx.Client, "get", lambda *a, **kw: response)
        monkeypatch.setattr(google_scholar.pacing, "wait", lambda *a, **k: None)
        result = sources._timed_call("google_scholar", google_scholar.search_papers, "q", 3)
        assert result.status == "blocked"
        assert result.results == []

    def test_redirect_to_sorry_page_raises(self, monkeypatch):
        """Google answers scraped requests with a 302 to /sorry/ rather than an
        error status, so a bare status check reads it as an ordinary empty
        page and the source silently reports 'no results' while blocked.
        """
        from scholar_mcp.sources import google_scholar

        class FakeResponse:
            status_code = 302
            url = "https://www.google.com/sorry/index?continue=..."
            text = ""
            request = None

        monkeypatch.setattr(google_scholar.httpx.Client, "get", lambda *a, **kw: FakeResponse())
        monkeypatch.setattr(google_scholar.pacing, "wait", lambda *a, **k: None)

        with pytest.raises(google_scholar.BlockedError):
            google_scholar.search_papers("anything", max_results=5)


class TestScopusPaging:
    """Free Elsevier keys cap `count` at 25 and reject larger values with
    HTTP 400 instead of clamping. Requesting limit=100 in one shot therefore
    returned zero results for every query; these tests pin the paging fix.
    """

    def _fake_entry(self, i):
        return {
            "dc:title": f"Paper {i}",
            "prism:doi": f"10.1000/{i}",
            "eid": f"2-s2.0-{i}",
            "citedby-count": str(100 - i),
            "prism:coverDate": "2023-01-01",
            "prism:publicationName": "Journal of Testing",
        }

    def test_pages_until_limit(self, monkeypatch):
        from scholar_mcp import config
        from scholar_mcp.sources import scopus
        monkeypatch.setattr(config, "SCOPUS_API_KEY", "test-key")

        calls = []

        def fake_fetch(query, key, start, count):
            calls.append({"start": start, "count": count})
            return [self._fake_entry(start + i) for i in range(count)]

        monkeypatch.setattr(scopus, "_fetch_page", fake_fetch)
        papers = scopus.search_papers("knowledge distillation", limit=60)

        assert len(papers) == 60
        assert all(c["count"] <= scopus.SCOPUS_MAX_COUNT for c in calls), \
            f"a page exceeded the service cap: {calls}"
        # Pages are fetched concurrently, so call order is not deterministic;
        # what matters is that the offsets tile the range exactly once.
        assert sorted(c["start"] for c in calls) == [0, 25, 50]
        by_start = {c["start"]: c["count"] for c in calls}
        assert by_start[50] == 10, "final page should request only the remainder"

    def test_preserves_api_sort_order(self, monkeypatch):
        """Scopus sorts by citation count server-side. Concurrent pages must be
        reassembled by offset, otherwise whichever page returns first wins.
        """
        from scholar_mcp import config
        from scholar_mcp.sources import scopus
        monkeypatch.setattr(config, "SCOPUS_API_KEY", "test-key")

        def fake_fetch(query, key, start, count):
            return [self._fake_entry(start + i) for i in range(count)]

        monkeypatch.setattr(scopus, "_fetch_page", fake_fetch)
        papers = scopus.search_papers("knowledge distillation", limit=75)

        titles = [p["title"] for p in papers]
        assert titles == [f"Paper {i}" for i in range(75)]

    def test_stops_on_empty_page(self, monkeypatch):
        """Exhausted result sets must terminate the loop, not spin forever."""
        from scholar_mcp import config
        from scholar_mcp.sources import scopus
        monkeypatch.setattr(config, "SCOPUS_API_KEY", "test-key")

        def fake_fetch(query, key, start, count):
            return [self._fake_entry(i) for i in range(5)] if start == 0 else []

        monkeypatch.setattr(scopus, "_fetch_page", fake_fetch)
        assert len(scopus.search_papers("rare topic", limit=100)) == 5

    def test_propagates_http_errors(self, monkeypatch):
        """Silent [] on failure is what hid this bug for two months. The
        registry layer records exceptions, so clients must let them through.
        """
        import httpx
        from scholar_mcp import config
        from scholar_mcp.sources import scopus
        monkeypatch.setattr(config, "SCOPUS_API_KEY", "test-key")

        def fake_fetch(query, key, start, count):
            raise httpx.HTTPStatusError("400", request=None, response=None)

        monkeypatch.setattr(scopus, "_fetch_page", fake_fetch)
        with pytest.raises(httpx.HTTPStatusError):
            scopus.search_papers("anything", limit=10)

    def test_no_key_returns_empty(self, monkeypatch):
        from scholar_mcp import config
        from scholar_mcp.sources import scopus
        monkeypatch.setattr(config, "SCOPUS_API_KEY", "")
        assert scopus.search_papers("anything", limit=10) == []


def test_pmc_pdfs_come_from_the_open_dataset_newest_version_first(monkeypatch):
    from scholar_mcp.sources import europepmc

    listing = ("<ListBucketResult><Key>PMC123.1/PMC123.1.pdf</Key><Key>PMC123.1/PMC123.1.xml</Key>"
               "<Key>PMC123.2/PMC123.2.pdf</Key><Key>PMC123.2/article_sm.pdf</Key></ListBucketResult>")
    seen = {}

    def fake_get(url, params=None, timeout=None):
        import httpx
        if params:
            seen.update(params)
        else:
            return httpx.Response(404, request=httpx.Request('GET', url))
        return httpx.Response(200, text=listing, request=httpx.Request('GET', url))

    monkeypatch.setattr(europepmc.httpx, "get", fake_get)
    urls = europepmc.pmc_pdf_urls("123")
    assert seen["prefix"] == "PMC123."
    assert urls[0].endswith("/PMC123.2/PMC123.2.pdf")
    assert urls[-1] == "https://europepmc.org/articles/PMC123?pdf=render"
    assert europepmc.pmc_pdf_urls("not-a-pmcid") == []


def test_formatting_a_search_page_makes_no_requests(monkeypatch):
    """The dataset lookup once ran per search result and held every search."""
    from scholar_mcp.sources import europepmc

    monkeypatch.setattr(europepmc.httpx, "get", lambda *a, **kw: pytest.fail("format_paper went online"))
    paper = europepmc.format_paper({"id": "1", "title": "T", "pmcid": "PMC9"})
    assert paper["open_access_url"] == "https://europepmc.org/articles/PMC9?pdf=render"


def test_failed_answers_are_failures_not_empty_searches(monkeypatch):
    """arxiv.gg's success flag and Exa's errors (out of credits) used to come back as no papers."""
    import httpx
    import pytest
    from scholar_mcp import sources
    from scholar_mcp.sources import arxivgg, exa
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: httpx.Response(
        200, json={"success": False, "error": "index rebuilding"}, request=httpx.Request("GET", "https://x")))
    assert sources._timed_call("arxivgg", arxivgg.search_papers, "q", 5).status == "error"

    class Broke:
        def search(self, *a, **kw):
            raise RuntimeError("402 out of credits")
    monkeypatch.setattr(exa, "_get_exa", lambda: Broke())
    with pytest.raises(RuntimeError):
        exa.search_papers("q", limit=5)


def test_doaj_natural_language_escapes_lucene_punctuation(monkeypatch):
    import httpx
    from urllib.parse import unquote
    from scholar_mcp.sources import doaj

    def get(url, **kwargs):
        assert unquote(url.rsplit("/articles/", 1)[1]) == r"C\+\+ agents\?"
        return httpx.Response(200, json={"results": []}, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", get)
    assert doaj.search_papers("C++ agents?") == []


def test_semantic_fallback_is_partial_and_recovers_its_query_route(monkeypatch):
    import httpx
    from scholar_mcp import sources
    from scholar_mcp.sources import arxivgg
    answers = iter([{"used": True}, {"used": False}])

    def get(url, **kwargs):
        return httpx.Response(200, json={"success": True, "data": {
            "papers": [{"ID": "2601.00001", "Title": "A paper"}], "fallback": next(answers)}},
            request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", get)
    monkeypatch.setattr(arxivgg, "_degraded_warning_shown", False)
    first = sources._timed_call("arxivgg_semantic", arxivgg.search_papers, "query", 3)
    assert first.status == "partial" and len(first.results) == 1 and not arxivgg.semantic_available()
    second = sources._timed_call("arxivgg_semantic", arxivgg.search_papers, "query", 3)
    assert second.status == "ok" and arxivgg.semantic_available()


def test_pmc_lookup_uses_pmcid_and_preserves_manuscript_status(monkeypatch):
    from scholar_mcp.sources import europepmc
    seen = []
    def request(query, limit):
        seen.append(query)
        return [{'id': '12345', 'pmcid': 'PMC4040241', 'source': 'MED', 'authMan': 'Y'}]
    monkeypatch.setattr(europepmc, '_request', request)
    assert europepmc._identity('PMC4040241')['id'] == '12345'
    assert seen == ['PMCID:PMC4040241']
    assert europepmc._copy_version({'authMan': 'Y'}) == 'accepted'
    assert europepmc._copy_version({'authMan': 'N'}) == 'published'
    assert europepmc._copy_version({'authMan': 'N', 'source': 'PPR'}) == 'preprint'
    assert europepmc._copy_version({}) == 'unknown'
    monkeypatch.setattr(europepmc, 'pmc_pdf_urls', lambda _: ['https://example.com/article.pdf'])
    assert europepmc.resolve_pdf({'external_ids': {'PMC': '4040241'}})[0]['version'] == 'accepted'


def test_native_pmc_document_binds_doi_and_declared_image_bytes(monkeypatch):
    import hashlib
    from io import BytesIO
    import httpx
    from PIL import Image
    from scholar_mcp.sources import europepmc
    xml = b'<article><front><article-meta><article-id pub-id-type="pmc">PMC123</article-id><article-id pub-id-type="doi">10.1234/trial</article-id></article-meta></front><body><p>Methods.</p></body></article>'
    image = BytesIO()
    Image.new('RGB', (11, 7), color='red').save(image, format='JPEG')
    record = {'pmcid': 'PMC123', 'version': 1, 'document_id': 'PMC123.1',
              'doi': '10.1234/trial', 'is_manuscript': True, 'license_code': 'TDM',
              'xml_url': 's3://pmc-oa-opendata/PMC123.1/PMC123.1.xml',
              'media_urls': ['s3://pmc-oa-opendata/PMC123.1/figure.jpg']}
    monkeypatch.setattr(europepmc, '_pmc_record', lambda _: record)
    def get(url, **kwargs):
        content = xml if '.xml' in url else image.getvalue()
        return httpx.Response(200, content=content, request=httpx.Request('GET', url))
    monkeypatch.setattr(europepmc.access, 'get', get)
    document = europepmc.structured_text({'external_ids': {'PMC': '123', 'DOI': '10.1234/trial'}})
    assert document['version'] == 'accepted' and document['document']['sha256'] == hashlib.sha256(xml).hexdigest()
    original = europepmc.structured_visual(document, {'xml_id': 'F1', 'asset': 'figure.jpg'})
    assert original['_image_bytes'].startswith(b'\x89PNG') and original['image_source']['width'] == 11
    with pytest.raises(ValueError, match='DOI differs'):
        europepmc.structured_text({'external_ids': {'PMC': '123', 'DOI': '10.1234/other'}})
    assert europepmc._dataset_url('s3://unrelated/PMC123.1/figure.jpg', 'PMC123.1') == ''
    assert europepmc._dataset_url('s3://pmc-oa-opendata/PMC123.2/figure.jpg', 'PMC123.1') == ''
    with pytest.raises(ValueError, match='checksum'):
        europepmc._dataset_bytes('https://pmc-oa-opendata.s3.amazonaws.com/PMC123.1/figure.jpg?md5=wrong', kind='image')


def test_dataset_ingestion_order_does_not_override_publication_version(monkeypatch):
    from scholar_mcp.sources import europepmc
    from scholar_mcp.records import paper_identity_keys
    keys = [f'PMC42.{n}/PMC42.{n}.{extension}' for n in (1, 2) for extension in ('json', 'pdf')]
    monkeypatch.setattr(europepmc, '_pmc_keys', lambda _: keys)
    monkeypatch.setattr(europepmc, '_pmc_version_record', lambda pmc, n: {
        'pmcid': pmc, 'version': n, 'document_id': f'{pmc}.{n}', 'is_manuscript': n == 2})
    record = europepmc._pmc_record('PMC42')
    assert record['version'] == 1
    urls = europepmc.pmc_pdf_urls('42')
    assert urls[0].endswith('PMC42.1/PMC42.1.pdf')
    assert europepmc._pmc_locations('42', {'authMan': 'Y'})[0]['version'] == 'published'
    assert paper_identity_keys({'paper_id': 'PMC:42'}) & paper_identity_keys({'external_ids': {'PMC': 'pmc42.2'}})
