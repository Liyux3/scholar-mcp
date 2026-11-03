"""Google Scholar page fields and source-specific routing."""
from bs4 import BeautifulSoup
import httpx

from scholar_mcp import config
from scholar_mcp.sources import google_scholar


def test_result_card_retains_bibliographic_and_pdf_evidence():
    soup = BeautifulSoup('''<div class="gs_r">
      <div class="gs_or_ggsm"><a href="https://example.org/paper.pdf">[PDF]</a></div>
      <div class="gs_ri"><h3 class="gs_rt"><a href="https://example.org/paper">A <b>useful</b> paper</a></h3>
      <div class="gs_a">A Author, B Author - Example Journal, 2024 - publisher.org</div>
      <div class="gs_rs">An abstract.</div><a href="/scholar?cites=123">Cited by 1,234</a>
      </div></div>''', "html.parser")
    paper = google_scholar._parse_paper(soup.select_one(".gs_ri"))
    assert paper["title"] == "A useful paper"
    assert paper["authors"] == ["A Author", "B Author"]
    assert paper["year"] == 2024
    assert paper["venue"] == "Example Journal"
    assert paper["citation_count"] == 1234 and paper["_citation_count_known"]
    assert paper["open_access_url"] == "https://example.org/paper.pdf"


def test_missing_citation_count_stays_unknown():
    soup = BeautifulSoup('<div class="gs_ri"><h3 class="gs_rt">A paper</h3><div class="gs_a">A Author - 2024</div></div>', "html.parser")
    paper = google_scholar._parse_paper(soup.div)
    assert not paper["_citation_count_known"]
    assert paper["venue"] == ""


def test_google_book_markers_keep_the_resource_type_and_clean_title():
    soup = BeautifulSoup('<div class="gs_ri"><h3 class="gs_rt">[BOOK] [B] Projection methods</h3></div>', "html.parser")
    paper = google_scholar._parse_paper(soup.div)
    assert paper["title"] == "Projection methods" and paper["publication_types"] == ["Book"]


def test_title_only_record_survives_and_native_ids_remain_reusable():
    soup = BeautifulSoup('<div class="gs_ri"><h3 class="gs_rt"><a href="https://arxiv.org/abs/1706.03762v5">Attention Is All You Need</a></h3></div>', "html.parser")
    paper = google_scholar._parse_paper(soup.div)
    assert paper["authors"] == [] and paper["year"] is None
    assert paper["external_ids"] == {"ArXiv": "1706.03762v5"}


def test_dedicated_proxy_does_not_change_global_route(monkeypatch):
    monkeypatch.setattr(config, "GOOGLE_SCHOLAR_PROXY", "http://local-proxy:9000")
    monkeypatch.setattr(google_scholar.pacing, "wait", lambda *a, **k: None)
    seen = {}

    class Client:
        def __init__(self, **kwargs):
            seen.update(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def close(self):
            pass

        def get(self, url, **kwargs):
            return httpx.Response(200, text="<html>Your search did not match any articles.</html>", request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "Client", Client)
    assert google_scholar.search_papers("query") == []
    assert seen["proxy"] == "http://local-proxy:9000"
    assert seen["follow_redirects"] is True


def test_paginated_request_retains_cookie_and_classifies_inline_challenge(monkeypatch):
    real_client = httpx.Client
    monkeypatch.setattr(google_scholar.pacing, "wait", lambda *a, **k: None)
    cookies = []

    def respond(request):
        cookies.append(request.headers.get("cookie", ""))
        if len(cookies) == 1:
            return httpx.Response(200, headers={"set-cookie": "scholar_session=example; Path=/"},
                                  text='<div class="gs_ri"><h3 class="gs_rt">A</h3><div class="gs_a">Author - 2024</div></div>')
        return httpx.Response(200, text='<form id="gs_captcha_f"></form>')

    def client(**kwargs):
        kwargs.pop("proxy", None)
        return real_client(**kwargs, transport=httpx.MockTransport(respond))

    monkeypatch.setattr(httpx, "Client", client)
    papers = google_scholar.search_papers("query", max_results=2)
    assert len(papers) == 1 and papers.partial_warning
    assert cookies == ["", "scholar_session=example"]


def test_explicit_google_direct_route_preserves_the_global_proxy(monkeypatch):
    from scholar_mcp import scholar_session
    monkeypatch.setattr(config, "GOOGLE_SCHOLAR_PROXY", "direct")
    monkeypatch.setenv("HTTPS_PROXY", "http://global-proxy:9000")
    assert scholar_session.proxy() is None
    import os
    assert os.environ["HTTPS_PROXY"] == "http://global-proxy:9000"


def test_native_google_queries_share_rotated_cookies_and_connections(monkeypatch):
    from scholar_mcp import scholar_session
    real_client = httpx.Client
    clients, cookies = [], []

    def respond(request):
        cookies.append(request.headers.get("cookie", ""))
        return httpx.Response(200, headers={"set-cookie": "GSP=rotated; Path=/"},
            text='<div class="gs_ri"><h3 class="gs_rt">A paper</h3></div>')

    def client(**kwargs):
        kwargs.pop("proxy", None)
        result = real_client(transport=httpx.MockTransport(respond), **kwargs)
        clients.append(result)
        return result

    monkeypatch.setattr(google_scholar.httpx, "Client", client)
    monkeypatch.setattr(scholar_session, "current", lambda: {})
    assert google_scholar.search_papers("first", max_results=1)
    assert google_scholar.search_papers("second", max_results=1)
    assert len(clients) == 1 and cookies == ["", "GSP=rotated"]
