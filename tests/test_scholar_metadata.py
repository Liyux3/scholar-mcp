"""Google Scholar page fields and source-specific routing."""
from bs4 import BeautifulSoup
import httpx

from scholar_mcp import config, scholar_client


def test_result_card_retains_bibliographic_and_pdf_evidence():
    soup = BeautifulSoup('''<div class="gs_r">
      <div class="gs_or_ggsm"><a href="https://example.org/paper.pdf">[PDF]</a></div>
      <div class="gs_ri"><h3 class="gs_rt"><a href="https://example.org/paper">A <b>useful</b> paper</a></h3>
      <div class="gs_a">A Author, B Author - Example Journal, 2024 - publisher.org</div>
      <div class="gs_rs">An abstract.</div><a href="/scholar?cites=123">Cited by 1,234</a>
      </div></div>''', "html.parser")
    paper = scholar_client._parse_paper(soup.select_one(".gs_ri"))
    assert paper["title"] == "A useful paper"
    assert paper["authors"] == ["A Author", "B Author"]
    assert paper["year"] == 2024
    assert paper["venue"] == "Example Journal"
    assert paper["citation_count"] == 1234 and paper["_citation_count_known"]
    assert paper["open_access_url"] == "https://example.org/paper.pdf"


def test_missing_citation_count_stays_unknown():
    soup = BeautifulSoup('<div class="gs_ri"><h3 class="gs_rt">A paper</h3><div class="gs_a">A Author - 2024</div></div>', "html.parser")
    paper = scholar_client._parse_paper(soup.div)
    assert not paper["_citation_count_known"]
    assert paper["venue"] == ""


def test_dedicated_proxy_does_not_change_global_route(monkeypatch):
    monkeypatch.setattr(config, "GOOGLE_SCHOLAR_PROXY", "http://local-proxy:9000")
    monkeypatch.setattr(scholar_client.time, "sleep", lambda *_: None)
    seen = {}

    class Client:
        def __init__(self, **kwargs):
            seen.update(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def get(self, url, **kwargs):
            return httpx.Response(200, text="<html></html>", request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "Client", Client)
    assert scholar_client.search_papers("query") == []
    assert seen["proxy"] == "http://local-proxy:9000"
    assert seen["follow_redirects"] is True


def test_paginated_request_retains_cookie_and_classifies_inline_challenge(monkeypatch):
    import pytest
    real_client = httpx.Client
    monkeypatch.setattr(scholar_client.time, "sleep", lambda *_: None)
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
    with pytest.raises(scholar_client.BlockedError):
        scholar_client.search_papers("query", max_results=2)
    assert cookies == ["", "scholar_session=example"]
