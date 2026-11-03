"""The optional host boundary stays typed, private and independent of browsers."""

import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
import threading
import time

import httpx
import pytest

from scholar_mcp import access, sources
from scholar_mcp.sources import dblp, doaj, google_scholar


def test_host_json_pipe_reads_utf8_and_types_invalid_bytes(monkeypatch):
    command = [sys.executable, '-c',
               'import json,sys; data=json.loads(sys.stdin.buffer.read().decode("utf-8")); '
               'sys.stderr.buffer.write(bytes([255])); '
               'sys.stdout.buffer.write(json.dumps({"ok":True,"label":data["label"]},ensure_ascii=False).encode("utf-8"))']
    monkeypatch.setenv('SCHOLAR_ACCESS_COMMAND', json.dumps(command))
    assert access._run({'op': 'fixture', 'label': '研究 café'}, 5)['label'] == '研究 café'
    command[-1] = 'import sys; sys.stdout.buffer.write(bytes([255]))'
    monkeypatch.setenv('SCHOLAR_ACCESS_COMMAND', json.dumps(command))
    with pytest.raises(access.AccessError) as failure:
        access._run({'op': 'fixture'}, 5)
    assert failure.value.code == 'PROTOCOL'


def test_background_requests_coordinate_one_browser_owner(monkeypatch):
    monkeypatch.setenv('SCHOLAR_ACCESS_COMMAND', '["host"]')
    counts = {'active': 0, 'maximum': 0}
    guard = threading.Lock()
    def run(*_args, **kwargs):
        with guard:
            counts['active'] += 1
            counts['maximum'] = max(counts['maximum'], counts['active'])
        time.sleep(.04)
        with guard:
            counts['active'] -= 1
        assert json.loads(kwargs['input'])['timeout'] <= 2
        return SimpleNamespace(returncode=0, stdout=b'{"ok": true}')
    monkeypatch.setattr(access.subprocess, 'run', run)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: access._run({'engine': 'background'}, 2), range(2)))
    assert counts['maximum'] == 1 and all(r['ok'] for r in results)


@pytest.mark.parametrize('wall', ['<html>Just a moment cf-chl-</html>',
                                  '<html><title>Client Challenge</title>JavaScript is disabled in your browser</html>'])
def test_shared_get_recovers_a_public_challenge_and_reuses_the_working_route(monkeypatch, wall):
    monkeypatch.setenv('SCHOLAR_ACCESS_COMMAND', '["host"]')
    monkeypatch.setattr(access, '_host_until', {})
    native = []
    calls = host(monkeypatch, body=b'{"records": [1]}')
    def blocked(url, **kw):
        native.append(True)
        return httpx.Response(503, text=wall,
                              request=httpx.Request('GET', url))
    monkeypatch.setattr(httpx, 'get', blocked)
    assert access.get('https://public.test/search', params={'q': 'science'}).json()['records'] == [1]
    assert access.get('https://public.test/search', params={'q': 'another'}).json()['records'] == [1]
    assert len(native) == 1 and len(calls) == 2


@pytest.mark.parametrize('status,options', [(429, {}), (401, {}), (403, {'headers': {'Authorization': 'Bearer private'}}),
                                        (403, {'params': {'api_key': 'private'}})])
def test_shared_get_keeps_quotas_and_api_auth_on_the_native_path(monkeypatch, status, options):
    monkeypatch.setenv('SCHOLAR_ACCESS_COMMAND', '["host"]')
    monkeypatch.setattr(access, 'fetch', lambda *a, **kw: pytest.fail('must not retry quota or API credentials in a browser'))
    monkeypatch.setattr(httpx, 'get', lambda url, **kw: httpx.Response(status,
        text='<html>Just a moment cf-chl-</html>', request=httpx.Request('GET', url)))
    assert access.get('https://auth.test/search', **options).status_code == status


def test_preferred_host_failure_can_return_to_a_working_native_api(monkeypatch):
    import time
    monkeypatch.setenv('SCHOLAR_ACCESS_COMMAND', '["host"]')
    monkeypatch.setattr(access, '_host_until', {'recovered.test': time.monotonic() + 30})
    monkeypatch.setattr(access, 'fetch', lambda *a, **kw: (_ for _ in ()).throw(access.AccessError('UNAVAILABLE')))
    monkeypatch.setattr(httpx, 'get', lambda url, **kw: httpx.Response(200, json={'native': True}, request=httpx.Request('GET', url)))
    assert access.get('https://recovered.test/search').json() == {'native': True}
    assert not access.preferred('https://recovered.test/search')


def test_host_not_found_keeps_normal_source_http_semantics(monkeypatch):
    monkeypatch.setenv('SCHOLAR_ACCESS_COMMAND', '["host"]')
    monkeypatch.setattr(httpx, 'get', lambda *a, **kw: (_ for _ in ()).throw(httpx.ReadTimeout('unavailable')))
    monkeypatch.setattr(access, 'fetch', lambda *a, **kw: (_ for _ in ()).throw(access.AccessError('NOT_FOUND')))
    assert access.get('https://index.test/missing').status_code == 404


def test_host_retry_hint_is_shared_instead_of_restarting_verification(monkeypatch):
    calls = host(monkeypatch, receipt={'ok': False, 'error': {'code': 'RATE_LIMITED', 'retry_after': 60}})
    for _ in range(2):
        with pytest.raises(access.AccessError) as failure:
            access.fetch('https://verification.test/search')
        assert failure.value.code == 'RATE_LIMITED'
    assert len(calls) == 1


def test_legacy_google_freeze_does_not_block_a_repaired_browser(monkeypatch):
    import time
    from scholar_mcp.cache import _disk
    url = 'https://scholar.google.co.uk/scholar?q=research'
    _disk('access.pause:scholar.google.co.uk', (time.time() + 3600, 'RATE_LIMITED'))
    calls = host(monkeypatch, body=b'<div class="gs_ri">Recovered paper</div>')
    assert access.fetch(url, kind='html').status_code == 200
    assert len(calls) == 1


def test_browser_error_page_is_not_a_successful_host_response(monkeypatch):
    host(monkeypatch, body=b'<html>ERR_TIMED_OUT</html>', receipt={
        'ok': True, 'status': 200, 'final_url': 'chrome-error://chromewebdata/'})
    with pytest.raises(access.AccessError) as failure:
        access.fetch('https://unreachable.test/search', kind='html')
    assert failure.value.code == 'NETWORK'


def test_host_configuration_does_not_preempt_dblp_native_verification(monkeypatch):
    monkeypatch.setenv('SCHOLAR_ACCESS_COMMAND', '["host"]')
    monkeypatch.setattr(access, 'fetch', lambda *a, **kw: pytest.fail('native metarefresh must run before host recovery'))
    monkeypatch.setattr(dblp.time, 'sleep', lambda delay: None)
    calls = []
    def get(client, url, **kwargs):
        calls.append(str(url))
        body = ('<html><script id="anubis_challenge">{}</script>'
                '<meta http-equiv="refresh" content="0; url=/.within.website/x/cmd/anubis/api/pass-challenge?id=test"></html>')
        if len(calls) == 1:
            return httpx.Response(202, text=body, headers={'content-type': 'text/html'}, request=httpx.Request('GET', dblp.BASE_URL))
        return httpx.Response(200, json={'result': {'hits': {'hit': [{'info': {'title': 'Native result', 'key': 'test'}}]}}}, request=httpx.Request('GET', url))
    monkeypatch.setattr(httpx.Client, 'get', get)
    assert dblp.search_papers('native-first', limit=1)[0]['title'] == 'Native result'
    assert len(calls) == 2



def host(monkeypatch, body=b'{"records": []}', receipt=None):
    seen = []
    monkeypatch.setenv("SCHOLAR_ACCESS_COMMAND", '["host-access", "--stdio"]')

    def run(command, **kwargs):
        request = json.loads(kwargs["input"])
        seen.append((command, request))
        Path(request["out"]).write_bytes(body)
        return SimpleNamespace(returncode=0, stdout=json.dumps(receipt or {
            "ok": True, "status": 200, "content_type": "application/json", "engine": "http"}).encode('utf-8'))

    monkeypatch.setattr(access.subprocess, "run", run)
    return seen


def test_fetch_passes_request_on_stdin_and_validates_bytes(monkeypatch):
    seen = host(monkeypatch)
    response = access.fetch("https://source.test/api?query=private")
    assert response.json() == {"records": []}
    command, request = seen[0]
    assert command == ["host-access", "--stdio"]
    assert request["url"] == "https://source.test/api?query=private"
    assert request["kind"] == "json" and request["anonymous"] is True
    assert Path(request["out"]).is_absolute()
    assert not Path(request["out"]).exists()


@pytest.mark.parametrize("code,status", [("AUTH_REQUIRED", "blocked"), ("CHALLENGE", "blocked"),
                                         ("RATE_LIMITED", "paced"), ("TIMEOUT", "timeout")])
def test_host_failures_keep_their_source_status(monkeypatch, code, status):
    host(monkeypatch, receipt={"ok": False, "error": {"code": code, "detail": "unavailable"}})
    result = sources._timed_call("source", access.fetch, "https://source.test/api")
    assert result.status == status and result.results == []


def test_html_is_never_a_successful_json_reply(monkeypatch):
    host(monkeypatch, body=b"<html>Login</html>")
    with pytest.raises(access.AccessError, match="WRONG_TYPE"):
        access.fetch("https://source.test/api")


def test_invalid_adapter_configuration_and_receipt_are_explicit(monkeypatch):
    monkeypatch.setenv("SCHOLAR_ACCESS_COMMAND", '"host-access --stdio"')
    with pytest.raises(access.AccessError, match="CONFIG"):
        access.fetch("https://source.test/api")
    host(monkeypatch)
    monkeypatch.setattr(access.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(
        returncode=0, stdout=b"not JSON"))
    with pytest.raises(access.AccessError, match="PROTOCOL"):
        access.fetch("https://source.test/api")


def test_adapter_timeout_keeps_a_typed_failure(monkeypatch):
    host(monkeypatch)

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("host-access", kwargs["timeout"])

    monkeypatch.setattr(access.subprocess, "run", timeout)
    with pytest.raises(access.AccessError, match="TIMEOUT"):
        access.fetch("https://source.test/api", timeout=1)


def test_dblp_reuses_the_host_search_and_preserves_metadata(monkeypatch):
    rows = {"result": {"hits": {"hit": [{"info": {
        "title": "A paper.", "year": "2026", "key": "journals/test/A26",
        "authors": {"author": [{"text": "First Author"}, {"text": "Second Author"}]},
        "doi": "10.1234/example", "venue": "Test Journal"}}]}}}
    seen = host(monkeypatch, body=json.dumps(rows).encode())
    monkeypatch.setattr(dblp, "_api_search", lambda *args: (_ for _ in ()).throw(PermissionError("verification")))
    papers = dblp.search_papers("a paper", limit=3)
    assert papers[0]["title"] == "A paper" and papers[0]["year"] == 2026
    assert papers[0]["authors"] == ["First Author", "Second Author"]
    assert papers[0]["external_ids"]["DOI"] == "10.1234/example"
    assert "a+paper" in seen[0][1]["url"]
    assert seen[0][1]["anonymous"] is False


def test_doaj_uses_the_host_only_after_the_native_path_fails(monkeypatch):
    seen = host(monkeypatch, body=json.dumps({"results": [{"id": "a", "bibjson": {
        "title": "A paper", "year": "2026", "month": "1", "author": [{"name": "A"}]}}]}).encode())
    monkeypatch.setattr(httpx, "get", lambda *args, **kwargs: (_ for _ in ()).throw(httpx.ReadTimeout("late")))
    assert doaj.search_papers("a paper", limit=2)[0]["title"] == "A paper"
    assert "pageSize=2" in seen[0][1]["url"]


def test_google_keeps_the_host_session_for_following_pages(monkeypatch):
    monkeypatch.setenv("SCHOLAR_ACCESS_COMMAND", '["host-access"]')
    denied = httpx.Response(403, request=httpx.Request("GET", google_scholar.SCHOLAR_URL))
    monkeypatch.setattr(google_scholar.httpx.Client, "get", lambda *args, **kwargs: denied)
    urls = []

    def fetch(url, **kwargs):
        urls.append(url)
        index = len(urls)
        body = f'<div class="gs_ri"><h3 class="gs_rt"><a href="https://paper.test/{index}">Paper {index}</a></h3>'
        body += '<div class="gs_a">Author - Venue, 2026</div></div>'
        return httpx.Response(200, text=body, request=httpx.Request("GET", url))

    monkeypatch.setattr(google_scholar.access, "fetch", fetch)
    papers = google_scholar.search_papers("example", max_results=2)
    assert [p["title"] for p in papers] == ["Paper 1", "Paper 2"]
    assert "start=" not in urls[0] and "start=1" in urls[1]
    assert all("num=" not in url for url in urls)


def test_host_access_replaces_the_legacy_cold_bootstrap_budget(monkeypatch):
    from scholar_mcp import config, scholar_session
    registered = [sources.Source(name="google_scholar")]
    monkeypatch.setattr(config, "SOURCE_BUDGET_S", 30)
    monkeypatch.setattr(scholar_session, "current", lambda: {})
    monkeypatch.setattr(scholar_session, "recovery_available", lambda: False)
    ordinary = sources._search_budget(registered, 1)
    monkeypatch.setattr(scholar_session, "recovery_available", lambda: True)
    monkeypatch.setattr(access, "available", lambda: True)
    assert sources._search_budget(registered, 1) == ordinary
    monkeypatch.setattr(access, "available", lambda: False)
    assert sources._search_budget(registered, 1) >= scholar_session.RECOVERY_TIMEOUT + 30






def test_google_repeated_page_retains_unique_records_without_looping(monkeypatch):
    from scholar_mcp import scholar_session
    body = '<div class="gs_ri"><h3 class="gs_rt"><a href="https://paper.test/a">Paper A</a></h3></div>'
    calls = []

    def get(client, endpoint, params):
        calls.append(params)
        return httpx.Response(200, text=body, request=httpx.Request("GET", endpoint))

    monkeypatch.setattr(google_scholar.httpx.Client, "get", get)
    monkeypatch.setattr(scholar_session, "current", lambda: {})
    result = sources._timed_call("google_scholar", google_scholar.search_papers, "repeat", 3)
    assert result.status == "partial" and len(result.results) == 1 and len(calls) == 2
    assert calls[1]["start"] == 1


def test_google_unreadable_cards_are_a_parser_failure_not_zero_matches(monkeypatch):
    body = '<div class="gs_ri">The result format changed.</div>'
    monkeypatch.setattr(google_scholar.httpx.Client, "get", lambda *args, **kwargs: httpx.Response(
        200, text=body, request=httpx.Request("GET", google_scholar.SCHOLAR_URL)))
    result = sources._timed_call("google_scholar", google_scholar.search_papers, "changed", 3)
    assert result.status == "error" and "could not be parsed" in result.error


def test_quota_recovery_does_not_repeat_the_same_dblp_endpoint(monkeypatch):
    from scholar_mcp.sources import pacing
    monkeypatch.setenv("SCHOLAR_ACCESS_COMMAND", '["host-access"]')
    monkeypatch.setattr(dblp, "_api_search", lambda *args: (_ for _ in ()).throw(pacing.Paced("quota")))
    monkeypatch.setattr(access, "fetch", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("same quota")))
    assert sources._timed_call("dblp", dblp.search_papers, "limited", 3).status == "paced"




def test_host_pdf_uses_the_normal_atomic_store_and_cache(monkeypatch, tmp_path):
    from pypdf import PdfWriter, PdfReader
    from scholar_mcp import config, pdf_utils
    monkeypatch.setattr(access, "available", lambda: True)
    monkeypatch.setattr(pdf_utils, "_try_download", lambda *args: None)
    monkeypatch.setattr(pdf_utils.sources, "resolve_pdf_candidates", lambda *args: [])
    monkeypatch.setattr(config, "SCIHUB_ENABLED", False)
    calls = []

    def download(url, target, **kwargs):
        calls.append(url)
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        with target.open("wb") as stream:
            writer.write(stream)
        return True

    monkeypatch.setattr(access, "download", download)
    paper = {"paper_id": "fixture", "title": "A paper", "open_access_url": "https://paper.test/a"}
    result = pdf_utils.download_paper(paper, str(tmp_path))
    assert result["success"] and result["source"] == "host_access"
    assert len(PdfReader(result["file_path"]).pages) == 1
    assert not list(tmp_path.glob("*.part"))
    assert pdf_utils.download_paper(paper, str(tmp_path))["source"] == "cache" and len(calls) == 1


def test_host_pdf_failure_remains_visible_and_leaves_no_file(monkeypatch, tmp_path):
    from scholar_mcp import config, pdf_utils
    monkeypatch.setattr(access, "available", lambda: True)
    monkeypatch.setattr(pdf_utils, "_try_download", lambda *args: None)
    monkeypatch.setattr(pdf_utils.sources, "resolve_pdf_candidates", lambda *args: [])
    monkeypatch.setattr(config, "SCIHUB_ENABLED", False)
    monkeypatch.setattr(access, "download", lambda *args: (_ for _ in ()).throw(access.AccessError("AUTH_REQUIRED")))
    result = pdf_utils.download_paper({"title": "A paper", "open_access_url": "https://paper.test/a"}, str(tmp_path))
    assert not result["success"] and "AUTH_REQUIRED" in result["message"]
    assert not list(tmp_path.iterdir())


def test_google_preserves_completed_pages_when_pacing_runs_out(monkeypatch):
    from scholar_mcp.sources import pacing
    calls = iter([None, pacing.Paced("quota")])

    def wait(*args):
        outcome = next(calls)
        if outcome:
            raise outcome

    monkeypatch.setattr(google_scholar.pacing, "wait", wait)
    monkeypatch.setattr(google_scholar.httpx.Client, "get", lambda *args, **kwargs: httpx.Response(
        200, text='<div class="gs_ri"><h3 class="gs_rt">A retained paper</h3></div>',
        request=httpx.Request("GET", google_scholar.SCHOLAR_URL)))
    result = sources._timed_call("google_scholar", google_scholar.search_papers, "quota", 3)
    assert result.status == "partial" and len(result.results) == 1
    assert "Paced" in result.error


def test_doi_only_paper_can_use_authorized_host_download(monkeypatch, tmp_path):
    from scholar_mcp import config, pdf_utils
    monkeypatch.setattr(access, "available", lambda: True)
    monkeypatch.setattr(pdf_utils.sources, "resolve_pdf_candidates", lambda *args: [])
    monkeypatch.setattr(pdf_utils, "_unpaywall_locations", lambda *args: [])
    monkeypatch.setattr(pdf_utils, "_try_elsevier_api", lambda *args: None)
    monkeypatch.setattr(pdf_utils, "_try_ezproxy", lambda *args, **kwargs: None)
    monkeypatch.setattr(config, "SCIHUB_ENABLED", False)
    urls = []
    monkeypatch.setattr(access, "download", lambda url, *args: urls.append(url) or False)
    assert not pdf_utils.download_paper({"title": "Fixture", "external_ids": {"DOI": "10.1234/a"}}, str(tmp_path))["success"]
    assert urls == ["https://doi.org/10.1234/a"]


def test_google_empty_shell_is_not_a_successful_zero_result_search(monkeypatch):
    monkeypatch.setattr(google_scholar.httpx.Client, "get", lambda *args, **kwargs: httpx.Response(
        200, text="<html><body></body></html>", request=httpx.Request("GET", google_scholar.SCHOLAR_URL)))
    result = sources._timed_call("google_scholar", google_scholar.search_papers, "pending page", 3)
    assert result.status == "error" and "unrecognized" in result.error


def test_google_unrecognized_http_shell_uses_the_host_recovery(monkeypatch):
    monkeypatch.setenv("SCHOLAR_ACCESS_COMMAND", '["host-access"]')
    monkeypatch.setattr(google_scholar.httpx.Client, "get", lambda *args, **kwargs: httpx.Response(
        200, text="<html><body></body></html>", request=httpx.Request("GET", google_scholar.SCHOLAR_URL)))
    monkeypatch.setattr(access, "fetch", lambda *args, **kwargs: httpx.Response(
        200, text='<div class="gs_ri"><h3 class="gs_rt">Recovered paper</h3></div>',
        request=httpx.Request("GET", google_scholar.SCHOLAR_URL)))
    assert google_scholar.search_papers("shell", max_results=1)[0]["title"] == "Recovered paper"


def test_google_browser_output_rejects_other_origins_and_existing_paths(tmp_path):
    from scholar_mcp import scholar_session
    with pytest.raises(ValueError, match="origin"):
        scholar_session._bootstrap("query", None, target="https://elsewhere.test/scholar")
    existing = tmp_path / "existing"
    existing.touch()
    with pytest.raises(ValueError, match="new absolute"):
        scholar_session._bootstrap("query", None, target=google_scholar.SCHOLAR_URL, out=str(existing))
