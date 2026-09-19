"""Provider-native recovery and private browser-to-HTTP handoff contracts."""
import json
import os
import time
from unittest.mock import Mock
from types import SimpleNamespace
import sys

import httpx
import pytest

from scholar_mcp import arxiv_client, scholar_client, scholar_session, sources


@pytest.mark.parametrize("mode, headless", [(None, True), ("auto", True), ("headless", True), ("headed", False)])
def test_browser_window_requires_explicit_opt_in(monkeypatch, mode, headless):
    if mode is None:
        monkeypatch.delenv("SCHOLAR_GOOGLE_RECOVERY", raising=False)
    else:
        monkeypatch.setenv("SCHOLAR_GOOGLE_RECOVERY", mode)
    options = Mock()
    for name in ("set_browser_path", "set_tmp_path", "auto_port", "headless"):
        getattr(options, name).return_value = options
    page = Mock(url="https://scholar.google.co.uk/scholar")
    page.eles.return_value = [object()]
    page.cookies.return_value = []
    page.run_js.return_value = "Test browser"
    monkeypatch.setitem(sys.modules, "DrissionPage", SimpleNamespace(
        ChromiumOptions=Mock(return_value=options), ChromiumPage=Mock(return_value=page)))
    monkeypatch.setattr(scholar_session, "browser_path", lambda: "/fake/chrome")
    monkeypatch.setattr(scholar_session.time, "sleep", lambda *_: None)
    monkeypatch.setattr(scholar_session.signal, "signal", lambda *_: None)
    monkeypatch.setattr(scholar_session.signal, "alarm", lambda *_: None, raising=False)
    assert scholar_session._bootstrap("query", None)["user_agent"] == "Test browser"
    options.headless.assert_called_once_with(headless)
    page.quit.assert_called_once()


def test_headless_recovery_needs_no_display_but_still_needs_a_browser(monkeypatch):
    monkeypatch.setattr(scholar_session.sys, "platform", "linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setenv("SCHOLAR_GOOGLE_RECOVERY", "auto")
    monkeypatch.setattr(scholar_session.importlib.util, "find_spec", lambda _: object())
    monkeypatch.setattr(scholar_session.shutil, "which", lambda _: "/fake/ffmpeg")
    monkeypatch.setattr(scholar_session, "browser_path", lambda: "/fake/chrome")
    assert scholar_session.recovery_available()
    monkeypatch.setenv("SCHOLAR_GOOGLE_RECOVERY", "headed")
    assert not scholar_session.recovery_available()
    monkeypatch.setenv("SCHOLAR_GOOGLE_RECOVERY", "off")
    assert not scholar_session.recovery_available()
    monkeypatch.setenv("SCHOLAR_GOOGLE_RECOVERY", "headless")
    monkeypatch.setattr(scholar_session, "browser_path", lambda: None)
    assert not scholar_session.recovery_available()


def test_browser_override_and_windows_user_install(monkeypatch, tmp_path):
    browser = tmp_path / "Microsoft/Edge/Application/msedge.exe"
    browser.parent.mkdir(parents=True)
    browser.touch()
    monkeypatch.setenv("SCHOLAR_GOOGLE_BROWSER", str(browser))
    assert scholar_session.browser_path() == str(browser)
    monkeypatch.delenv("SCHOLAR_GOOGLE_BROWSER")
    monkeypatch.setattr(scholar_session.sys, "platform", "win32")
    monkeypatch.delenv("PROGRAMFILES", raising=False)
    monkeypatch.delenv("PROGRAMFILES(X86)", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(scholar_session.shutil, "which", lambda _: None)
    assert scholar_session.browser_path() == str(browser)


def session():
    return {"host": "scholar.google.co.uk", "user_agent": "Chrome/test",
            "created": time.time(), "route": scholar_session._route(),
            "cookies": [{"name": "session", "value": "fixture", "domain": ".google.co.uk",
                         "path": "/", "secure": True, "expires": time.time() + 3600}]}


def test_session_is_private_and_route_bound(monkeypatch):
    scholar_session._write(session())
    assert scholar_session.current()
    if os.name == "posix":
        assert scholar_session._path().stat().st_mode & 0o777 == 0o600
    monkeypatch.setattr(scholar_session, "proxy", lambda: "http://another-route:8080")
    assert scholar_session.current() == {}


@pytest.mark.parametrize("update", [
    {"host": "attacker.invalid"}, {"created": 0}, {"created": "bad"},
    {"cookies": [{"name": "x", "value": "y", "domain": None}]},
    {"cookies": [{"name": "x", "value": "y", "domain": ".google.com", "expires": "bad"}]},
    {"user_agent": "header\r\ninjection"},
])
def test_invalid_session_is_ignored(update):
    scholar_session._write({**session(), **update})
    assert scholar_session.current() == {}


def test_cookie_scope_secure_and_expiry_are_preserved():
    with httpx.Client(cookies=scholar_session.cookie_jar(session())) as client:
        assert "session=fixture" in client.build_request("GET", "https://scholar.google.co.uk/scholar").headers["cookie"]
        assert "cookie" not in client.build_request("GET", "http://scholar.google.co.uk/scholar").headers
        assert "cookie" not in client.build_request("GET", "https://unrelated.invalid/").headers


def test_recovery_reuses_another_workers_session(monkeypatch):
    scholar_session._write(session())
    monkeypatch.setattr(scholar_session, "recovery_available", lambda: True)
    launch = Mock(side_effect=AssertionError("must reuse cached session"))
    monkeypatch.setattr(scholar_session.subprocess, "Popen", launch)
    assert scholar_session.recover("query", {})["host"] == "scholar.google.co.uk"
    launch.assert_not_called()


def test_failed_recovery_sets_bounded_retry_cooldown(monkeypatch):
    monkeypatch.setattr(scholar_session, "recovery_available", lambda: True)
    process = Mock(returncode=1)
    process.communicate.return_value = ("", None)
    launch = Mock(return_value=process)
    monkeypatch.setattr(scholar_session.subprocess, "Popen", launch)
    with pytest.raises(PermissionError):
        scholar_session.recover("query", {})
    with pytest.raises(PermissionError, match="recently failed"):
        scholar_session.recover("query", {})
    assert launch.call_count == 1
    assert "retry_after" in json.loads(scholar_session._path().read_text())


def test_search_recovers_once_and_reuses_session_across_calls(monkeypatch):
    real_client = httpx.Client
    monkeypatch.setattr(scholar_client.time, "sleep", lambda *_: None)
    seen = []

    def handle(request):
        seen.append(request)
        if "session=fixture" not in request.headers.get("cookie", ""):
            return httpx.Response(200, text='<form id="gs_captcha_f"></form>')
        return httpx.Response(200, text='<div class="gs_ri"><h3 class="gs_rt">Paper</h3><div class="gs_a">Author - 2024</div></div>')

    def recover(*_):
        data = session()
        scholar_session._write(data)
        return data

    bootstrap = Mock(side_effect=recover)
    monkeypatch.setattr(scholar_session, "recover", bootstrap)
    def client(**kwargs):
        kwargs.pop("proxy", None)
        return real_client(**kwargs, transport=httpx.MockTransport(handle))
    monkeypatch.setattr(httpx, "Client", client)
    assert scholar_client.search_papers("query", 1)[0]["title"] == "Paper"
    assert scholar_client.search_papers("second", 1)[0]["title"] == "Paper"
    assert bootstrap.call_count == 1
    assert seen[-1].url.host == "scholar.google.co.uk"
    assert seen[-1].headers["User-Agent"] == "Chrome/test"


def test_rejected_recovered_session_does_not_loop(monkeypatch):
    monkeypatch.setattr(scholar_client.time, "sleep", lambda *_: None)
    recover = Mock(return_value=session())
    monkeypatch.setattr(scholar_session, "recover", recover)
    monkeypatch.setattr(httpx.Client, "get", lambda *a, **kw: httpx.Response(
        429, request=httpx.Request("GET", "https://scholar.google.co.uk/scholar")))
    with pytest.raises(scholar_client.BlockedError, match="rejected"):
        scholar_client.search_papers("query", 1)
    assert recover.call_count == 1


def test_cold_recovery_budget_preserves_explicit_limit(monkeypatch):
    monkeypatch.setattr(sources, "search_sources", lambda: [sources.Source("google_scholar", search=lambda *a, **kw: [])])
    monkeypatch.setattr(scholar_session, "recovery_available", lambda: True)
    budgets = []
    original = sources.as_completed
    def completed(fs, timeout):
        budgets.append(timeout)
        return original(fs, timeout=timeout)
    monkeypatch.setattr(sources, "as_completed", completed)
    sources.parallel_search("q")
    sources.parallel_search("q", budget_s=2)
    assert budgets == [150, 2]


def test_arxiv_web_fallback_retains_identity_and_full_abstract(monkeypatch):
    page = '''<li class="arxiv-result"><p class="list-title"><a href="https://arxiv.org/abs/1706.03762">arXiv</a></p>
    <p class="title">Attention Is All You Need</p><p class="authors"><a>A Author</a></p>
    <span class="abstract-full">Full abstract.<a>Less</a></span>
    <p class="is-size-7">Submitted 2 August, 2023; originally announced June 2017.</p></li>'''
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: httpx.Response(
        429, request=httpx.Request("GET", arxiv_client.ARXIV_API_URL)))
    monkeypatch.setattr(httpx.Client, "get", lambda *a, **kw: httpx.Response(
        200, text=page, request=httpx.Request("GET", "https://arxiv.org/search/")))
    result = arxiv_client.search_papers("attention", 1)[0]
    assert result["year"] == 2017 and result["publication_date"] == "2017-06"
    assert result["abstract"] == "Full abstract."
    assert result["external_ids"] == {"ArXiv": "1706.03762"}
    assert result["source"] == "arxiv"


def test_arxiv_does_not_mask_invalid_api_queries(monkeypatch):
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: httpx.Response(
        400, request=httpx.Request("GET", arxiv_client.ARXIV_API_URL)))
    with pytest.raises(httpx.HTTPStatusError):
        arxiv_client.search_papers("invalid", 1)


def test_scholar_nonbreaking_spaces_do_not_leak_venue_into_authors():
    from bs4 import BeautifulSoup
    card = BeautifulSoup('''<div class="gs_ri"><h3 class="gs_rt">Paper</h3>
    <div class="gs_a">A Author, B Author\u00a0- Conference, 2017 - publisher.example</div></div>''', "html.parser").div
    paper = scholar_client._parse_paper(card)
    assert paper["authors"] == ["A Author", "B Author"]
    assert paper["venue"] == "Conference" and paper["year"] == 2017


def test_source_check_reports_types_not_credential_bearing_errors(monkeypatch, capsys):
    from scholar_mcp.cli import main
    def fail(*a, **kw):
        raise ValueError("https://provider.invalid/?api_key=fixture-sensitive")
    monkeypatch.setattr(sources, "_registry", {"fixture": sources.Source("fixture", search=fail)})
    assert main(["sources", "--check"]) == 1
    output = capsys.readouterr().out
    assert "fixture-sensitive" not in output
    assert json.loads(output)["sources"][0]["error_type"] == "ValueError"


def test_source_check_missing_credentials_are_not_network_failures(monkeypatch, capsys):
    from scholar_mcp.cli import main
    source = sources.Source("fixture", search=Mock(), requires_key=True, key_available=lambda: False)
    monkeypatch.setattr(sources, "_registry", {"fixture": source})
    assert main(["sources", "--check"]) == 0
    assert json.loads(capsys.readouterr().out)["sources"][0]["status"] == "not_configured"
    source.search.assert_not_called()
