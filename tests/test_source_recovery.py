"""Provider-native recovery and private browser-to-HTTP handoff contracts."""
import json
import os
import sys
import time
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest

from scholar_mcp import scholar_session, sources
from scholar_mcp.sources import arxiv, google_scholar


def test_openreview_search_uses_public_submission_contract(monkeypatch):
    from scholar_mcp.sources import openreview
    seen = {}

    def get(url, **kwargs):
        seen.update(url=url, **kwargs)
        return httpx.Response(200, json={"notes": [{"id": "paper", "content": {
            "title": {"value": "A transformer paper"}, "authors": {"value": ["A Author"]},
        }}]}, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", get)
    papers = openreview.search_papers("transformer", 150, "ICLR")
    assert seen["params"] == {"term": "transformer", "source": "forum", "limit": 150, "group": "ICLR"}
    assert "headers" not in seen
    assert papers[0]["title"] == "A transformer paper"
    assert sources.get("openreview").available()


def test_ready_page_does_not_pay_a_fixed_verification_sleep(monkeypatch):
    page = Mock()
    page.ele.return_value = SimpleNamespace(states=SimpleNamespace(is_displayed=True))
    sleep = Mock(side_effect=AssertionError("ready pages should proceed immediately"))
    monkeypatch.setattr(scholar_session.time, "sleep", sleep)
    assert scholar_session._wait_visible(page, ("results", "challenge")) == "results"
    sleep.assert_not_called()


def test_waits_for_visible_state_and_stops_at_deadline(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(scholar_session.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(scholar_session.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    page = Mock()
    page.ele.side_effect = lambda selector, timeout: SimpleNamespace(
        states=SimpleNamespace(is_displayed=clock[0] >= .4))
    assert scholar_session._wait_visible(page, ("results",), timeout=2) == "results"
    assert clock[0] == pytest.approx(.4)
    page.ele.return_value = None
    page.ele.side_effect = None
    assert scholar_session._wait_visible(page, ("results",), timeout=.5) is None
    assert clock[0] == pytest.approx(.9)


def test_background_page_has_no_startup_window_and_keeps_hidden_target_owner(monkeypatch):
    options = Mock(browser_path="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                   user_data_path="/tmp/scholar-private-fixture", address="127.0.0.1:9231",
                   arguments=["--user-data-dir=/tmp/scholar-private-fixture"])
    ws_url = "ws://127.0.0.1:9231/devtools/browser/test"
    client = Mock()
    client.__enter__ = Mock(return_value=client)
    client.__exit__ = Mock(return_value=False)
    client.get.return_value.json.return_value = {"webSocketDebuggerUrl": ws_url}
    monkeypatch.setattr(httpx, "Client", Mock(return_value=client))
    connection = Mock()
    connection.recv.return_value = '{"id":1,"result":{"targetId":"hidden-target"}}'
    monkeypatch.setitem(sys.modules, "websocket", SimpleNamespace(create_connection=Mock(return_value=connection)))
    page = Mock()
    constructor = Mock(return_value=page)
    monkeypatch.setitem(sys.modules, "DrissionPage", SimpleNamespace(ChromiumPage=constructor))
    launch = Mock()
    monkeypatch.setattr(scholar_session.subprocess, "Popen", launch)
    close = Mock()
    monkeypatch.setattr(scholar_session, "_close_background_profile", close)
    with ExitStack() as cleanup:
        assert scholar_session._background_page(options, cleanup) is page
        command = launch.call_args.args[0]
        assert "--no-startup-window" in command and "-g" in command and "-j" in command
        body = json.loads(connection.send.call_args.args[0])
        assert body["params"] == {"url": "about:blank", "hidden": True, "background": True}
        constructor.assert_called_once_with(options, tab_id="hidden-target")
        options.set_address.assert_called_once_with(ws_url)
        page.run_cdp.assert_called_once_with("Emulation.setDeviceMetricsOverride", width=1280, height=900,
                                            deviceScaleFactor=1, mobile=False)
        connection.close.assert_not_called()
    connection.close.assert_called_once()
    close.assert_called_once_with("/tmp/scholar-private-fixture")


def test_background_hidden_target_failure_never_opens_a_visible_retry(monkeypatch):
    options = Mock(browser_path="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                   user_data_path="/tmp/scholar-private-fixture", address="127.0.0.1:9231", arguments=[])
    client = Mock()
    client.__enter__ = Mock(return_value=client)
    client.__exit__ = Mock(return_value=False)
    client.get.return_value.json.return_value = {"webSocketDebuggerUrl": "ws://127.0.0.1:9231/devtools/browser/test"}
    monkeypatch.setattr(httpx, "Client", Mock(return_value=client))
    connection = Mock()
    connection.recv.return_value = '{"id":1,"error":{"message":"unsupported hidden targets"}}'
    monkeypatch.setitem(sys.modules, "websocket", SimpleNamespace(create_connection=Mock(return_value=connection)))
    constructor = Mock(side_effect=AssertionError("must not open a regular page"))
    monkeypatch.setitem(sys.modules, "DrissionPage", SimpleNamespace(ChromiumPage=constructor))
    launch, close = Mock(), Mock()
    monkeypatch.setattr(scholar_session.subprocess, "Popen", launch)
    monkeypatch.setattr(scholar_session, "_close_background_profile", close)
    with ExitStack() as cleanup, pytest.raises(PermissionError, match="Hidden browser"):
        scholar_session._background_page(options, cleanup)
    assert launch.call_count == 1
    connection.close.assert_called_once()
    close.assert_called_once()


@pytest.mark.parametrize('supported,success', [(True, True), (True, False), (False, False)])
def test_empty_browser_bootstrap_is_minimized_and_retired(monkeypatch, supported, success):
    options = Mock(browser_path='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
                   user_data_path='/tmp/dedicated', address='127.0.0.1:9231', arguments=[])
    client = Mock()
    client.__enter__ = Mock(return_value=client)
    client.__exit__ = Mock(return_value=False)
    client.get.side_effect = lambda url: SimpleNamespace(json=lambda: (
        {'domains': [{'domain': 'Target', 'commands': [{'name': 'createTarget',
          'parameters': [{'name': 'windowState'}] if supported else []}]}]}
        if url.endswith('/json/protocol') else {'webSocketDebuggerUrl': 'ws://127.0.0.1:9231/browser'}))
    monkeypatch.setattr(httpx, 'Client', Mock(return_value=client))
    connection = Mock()
    connection.recv.side_effect = [json.dumps(message) for message in [
        {'method': 'Target.targetCreated'},
        {'id': 1, 'error': {'message': 'Hidden target can be created only when remote debugging is enabled'}},
        {'id': 2, 'result': {'targetId': 'bootstrap'}},
        {'id': 3, **({'result': {'targetId': 'hidden'}} if success else {'error': {'message': 'failed'}})},
        {'id': 4, 'result': {'success': True}},
    ]]
    monkeypatch.setitem(sys.modules, 'websocket', SimpleNamespace(create_connection=Mock(return_value=connection)))
    monkeypatch.setitem(sys.modules, 'DrissionPage', SimpleNamespace(ChromiumPage=Mock(return_value=Mock())))
    monkeypatch.setattr(scholar_session.subprocess, 'Popen', Mock())
    monkeypatch.setattr(scholar_session, '_close_background_profile', Mock())
    with ExitStack() as cleanup:
        if success:
            scholar_session._background_page(options, cleanup)
        else:
            with pytest.raises(PermissionError):
                scholar_session._background_page(options, cleanup)
    requests = [json.loads(call.args[0]) for call in connection.send.call_args_list]
    if supported:
        assert requests[1]['params']['windowState'] == 'minimized'
        assert requests[1]['params']['background'] is True
        assert requests[-1] == {'id': 4, 'method': 'Target.closeTarget', 'params': {'targetId': 'bootstrap'}}
    else:
        assert len(requests) == 1


def test_acquisition_budget_is_shared_and_nested_without_extending_it(monkeypatch):
    from contextvars import copy_context

    from scholar_mcp.sources import pacing
    now = [1000]
    monkeypatch.setattr(pacing.time, 'monotonic', lambda: now[0])
    assert pacing.remaining() is None
    with pacing.budget(10):
        now[0] += 4
        with pacing.budget(20):
            assert copy_context().run(pacing.remaining, 30) == 6
            now[0] += 7
            with pytest.raises(pacing.Paced, match='acquisition budget'):
                pacing.wait('arxiv')
    assert pacing.remaining() is None


def test_pacing_spaces_bursts_and_budgets(monkeypatch):
    from scholar_mcp.sources import pacing
    now, sleeps = [10.0], []
    monkeypatch.setattr(pacing.time, "time", lambda: now[0])
    def sleep(seconds):
        sleeps.append(seconds)
        now[0] += seconds
    monkeypatch.setattr(pacing, "_sleep", sleep)
    monkeypatch.setitem(pacing.LIMITS, "spaced", (((1, 2.0),), ((1, 2.0),)))
    pacing.wait("spaced")
    now[0] += .5
    pacing.wait("spaced")
    now[0] += 3
    pacing.wait("spaced")
    assert sleeps == [1.5]  # no delay on the first request, network time counts toward the next
    # Three a second plus five a minute: a burst of three, then the minute budget decides.
    monkeypatch.setitem(pacing.LIMITS, "budget", (((3, 1.0), (5, 60.0)), ((3, 1.0), (5, 60.0))))
    sleeps.clear()
    for _ in range(5):
        pacing.wait("budget")
    assert sleeps == [1.0]
    # A caller that will not wait that long reserves nothing and learns why.
    with pytest.raises(pacing.Paced, match="5 requests per 60 s"):
        pacing.wait("budget", max_wait=10)
    pacing.wait("budget", max_wait=60)
    assert sleeps[-1] == pytest.approx(59.0)
    # A key has its own slot and its own limits.
    monkeypatch.setitem(pacing.LIMITS, "keyed", (((1, 3600),), ((10, 1.0),)))
    pacing.wait("keyed")
    with pytest.raises(pacing.Paced):
        pacing.wait("keyed", max_wait=1)
    for _ in range(10):
        pacing.wait("keyed", key="secret", max_wait=0)
    assert pacing.LIMITS["google-scholar"] == (((1, 2.0),), ((1, 2.0),))


def test_pacing_is_shared_between_processes(tmp_path, monkeypatch):
    """A slot reserved by another Scholar process holds this one back."""
    import subprocess
    import sys
    from pathlib import Path

    from scholar_mcp.sources import pacing
    # This checks the shared slot, not every provider's cold import. Loading
    # the actual leaf module avoids unrelated HDD/SDK startup in the child;
    # full-package startup is covered by the MCP and CLI acceptance gates.
    script = ("import sys,pathlib,types;"
              "package=types.ModuleType('scholar_mcp.sources');"
              "package.__path__=[sys.argv[2]];sys.modules['scholar_mcp.sources']=package;"
              "from scholar_mcp.sources import pacing;"
              "pacing.state_dir=lambda: pathlib.Path(sys.argv[1]);"
              "pacing.LIMITS['shared']=(((1, 30),),)*2; pacing.wait('shared');"
              "pacing.defer('hinted',60)")
    subprocess.run([sys.executable, "-c", script, str(tmp_path), str(Path(pacing.__file__).parent)],
                   check=True, timeout=30)
    monkeypatch.setattr(pacing, "state_dir", lambda: tmp_path)
    monkeypatch.setitem(pacing.LIMITS, "shared", (((1, 30),),) * 2)
    monkeypatch.setitem(pacing.LIMITS, "other", (((1, 30),),) * 2)
    monkeypatch.setitem(pacing.LIMITS, "hinted", (((1, 3),),) * 2)
    with pytest.raises(pacing.Paced):
        pacing.wait("shared", max_wait=5)
    with pytest.raises(pacing.Paced, match='Retry-After'):
        pacing.wait("hinted", max_wait=5)
    pacing.wait("other", max_wait=5)


def test_provider_retry_after_expires_and_respects_the_call_budget(monkeypatch):
    from scholar_mcp.sources import pacing
    now, sleeps = [10.0], []
    monkeypatch.setattr(pacing.time, 'time', lambda: now[0])
    def sleep(seconds):
        sleeps.append(seconds)
        now[0] += seconds
    monkeypatch.setattr(pacing, '_sleep', sleep)
    monkeypatch.setitem(pacing.LIMITS, 'hinted', (((1, 3),),) * 2)
    pacing.defer('hinted', 60)
    pacing.defer('hinted', 5)  # Another worker cannot shorten the server's interval.
    with pytest.raises(pacing.Paced, match='Retry-After'):
        pacing.wait('hinted', max_wait=20)
    assert sleeps == []  # A short interactive call reserves nothing.
    pacing.wait('hinted', key='another-identity', max_wait=0)
    pacing.wait('hinted', max_wait=60)
    assert sleeps == [60]
    pacing.wait('hinted', max_wait=5)
    assert sleeps == [60, 3]
    assert not (pacing.state_dir() / 'hinted.retry-after').exists()


@pytest.mark.parametrize("mode, headless", [(None, True), ("auto", True), ("headless", True), ("headed", False)])
def test_browser_window_requires_explicit_opt_in(monkeypatch, mode, headless):
    if mode is None:
        monkeypatch.delenv("SCHOLAR_GOOGLE_RECOVERY", raising=False)
    else:
        monkeypatch.setenv("SCHOLAR_GOOGLE_RECOVERY", mode)
    options = Mock()
    for name in ("set_browser_path", "set_tmp_path", "set_local_port", "set_user_data_path", "headless"):
        getattr(options, name).return_value = options
    page = Mock(url="https://scholar.google.co.uk/scholar", html='<div class="gs_ri">Paper</div>')
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


def test_headless_browser_and_http_handoff_use_the_same_native_version(monkeypatch):
    options = Mock()
    for name in ("set_browser_path", "set_tmp_path", "set_local_port", "set_user_data_path", "headless"):
        getattr(options, name).return_value = options
    page = Mock(url="https://scholar.google.co.uk/scholar", html='<div class="gs_ri">Paper</div>')
    page.eles.return_value = [object()]
    page.cookies.return_value = []
    page.run_js.side_effect = ["NativePlatform HeadlessChrome/153.0.0.0", "NativePlatform Chrome/153.0.0.0"]
    monkeypatch.setitem(sys.modules, "DrissionPage", SimpleNamespace(
        ChromiumOptions=Mock(return_value=options), ChromiumPage=Mock(return_value=page)))
    monkeypatch.setattr(scholar_session, "browser_path", lambda: "/fake/chrome")
    monkeypatch.setattr(scholar_session.time, "sleep", lambda *_: None)
    monkeypatch.setattr(scholar_session.signal, "signal", lambda *_: None)
    monkeypatch.setattr(scholar_session.signal, "alarm", lambda *_: None, raising=False)
    result = scholar_session._bootstrap("query", None)
    page.run_cdp.assert_called_once_with("Network.setUserAgentOverride",
        userAgent="NativePlatform Chrome/153.0.0.0", acceptLanguage="en-US,en;q=0.9")
    assert result["user_agent"] == "NativePlatform Chrome/153.0.0.0"


def test_headless_recovery_needs_no_display_but_still_needs_a_browser(monkeypatch):
    monkeypatch.setattr(scholar_session.sys, "platform", "linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setenv("SCHOLAR_GOOGLE_RECOVERY", "auto")
    monkeypatch.setattr(scholar_session.importlib.util, "find_spec", lambda _: object())
    monkeypatch.setattr(scholar_session.shutil, "which", lambda _: "/fake/ffmpeg")
    monkeypatch.setattr(scholar_session, "browser_path", lambda: "/fake/chrome")
    assert scholar_session.recovery_available()
    monkeypatch.setattr(scholar_session.shutil, 'which', lambda _: None)
    assert scholar_session.recovery_available()  # Plain browser reading needs no audio stack.
    monkeypatch.setenv("SCHOLAR_GOOGLE_RECOVERY", "headed")
    assert not scholar_session.recovery_available()
    monkeypatch.setenv("SCHOLAR_GOOGLE_RECOVERY", "off")
    assert not scholar_session.recovery_available()
    monkeypatch.setenv("SCHOLAR_GOOGLE_RECOVERY", "headless")
    monkeypatch.setattr(scholar_session, "browser_path", lambda: None)
    assert not scholar_session.recovery_available()


def test_auto_retry_is_hidden_and_explicit_headless_never_uses_it(monkeypatch):
    monkeypatch.setenv("SCHOLAR_GOOGLE_RECOVERY", "auto")
    monkeypatch.setattr(scholar_session.sys, "platform", "darwin")
    monkeypatch.setattr(scholar_session, "_bootstrap", Mock(side_effect=PermissionError("challenge declined")))
    hidden = Mock(return_value={"host": "scholar.google.co.uk"})
    monkeypatch.setattr(scholar_session, "_background_bootstrap", hidden)
    assert scholar_session._establish_session("q", None)["recovery_mode"] == "background"
    assert hidden.call_count == 1
    monkeypatch.setenv("SCHOLAR_GOOGLE_RECOVERY", "headless")
    with pytest.raises(PermissionError):
        scholar_session._establish_session("q", None)
    assert hidden.call_count == 1


def test_auto_remembers_the_quiet_mode_that_worked_on_this_route(monkeypatch):
    monkeypatch.setenv("SCHOLAR_GOOGLE_RECOVERY", "auto")
    monkeypatch.setattr(scholar_session.sys, "platform", "darwin")
    monkeypatch.setattr(scholar_session, "_bootstrap", lambda *a, **kw: pytest.fail("do not repeat a known-declined path"))
    monkeypatch.setattr(scholar_session, "_background_bootstrap", lambda *a: {"host": "scholar.google.co.uk"})
    assert scholar_session._establish_session("q", None, prefer_background=True)["recovery_mode"] == "background"


def test_remembered_desktop_mode_can_fall_back_to_headless(monkeypatch):
    monkeypatch.setenv("SCHOLAR_GOOGLE_RECOVERY", "auto")
    monkeypatch.setattr(scholar_session.sys, "platform", "darwin")
    monkeypatch.setattr(scholar_session, "_background_bootstrap", Mock(side_effect=PermissionError("desktop unavailable")))
    monkeypatch.setattr(scholar_session, "_bootstrap", lambda *a: {"host": "scholar.google.co.uk"})
    assert scholar_session._establish_session("q", None, prefer_background=True)["recovery_mode"] == "headless"


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


def test_failed_recovery_does_not_freeze_subsequent_attempts(monkeypatch):
    monkeypatch.setattr(scholar_session, "recovery_available", lambda: True)
    process = Mock(returncode=1)
    process.communicate.return_value = ("", None)
    launch = Mock(return_value=process)
    monkeypatch.setattr(scholar_session.subprocess, "Popen", launch)
    with pytest.raises(PermissionError):
        scholar_session.recover("query", {})
    with pytest.raises(PermissionError):
        scholar_session.recover("query", {})
    assert launch.call_count == 2
    state = json.loads(scholar_session._path().read_text())
    assert 'retry_after' not in state and state['recovery_failures'] == 2


def test_legacy_verification_freeze_is_retired_without_discarding_the_session(monkeypatch):
    monkeypatch.setattr(scholar_session, "recovery_available", lambda: True)
    process = Mock(returncode=1)
    process.communicate.return_value = ("", None)
    monkeypatch.setattr(scholar_session.subprocess, "Popen", Mock(return_value=process))
    previous = {**session(), 'retry_after': time.time() + 3600}
    scholar_session._write(previous)
    with pytest.raises(PermissionError):
        scholar_session.recover('query', previous)
    state = scholar_session._read()
    assert 'retry_after' not in state
    assert scholar_session.current()['cookies'] == previous['cookies']


def test_browser_profile_survives_worker_restarts_and_is_bound_to_the_route(monkeypatch):
    monkeypatch.setattr(scholar_session, 'browser_path', lambda: '/dedicated/chrome')
    monkeypatch.setattr(scholar_session, 'proxy', lambda: 'http://local:1234')
    first = scholar_session._profile()
    marker = first / 'verified-storage'
    marker.write_text('retained')
    assert (scholar_session._profile() / marker.name).read_text() == 'retained'
    monkeypatch.setattr(scholar_session, 'proxy', lambda: 'http://local:5678')
    assert scholar_session._profile() != first
    assert marker.read_text() == 'retained'


def test_failed_recovery_does_not_destroy_the_last_verified_session(monkeypatch):
    previous = session()
    scholar_session._write(previous)
    monkeypatch.setattr(scholar_session, "recovery_available", lambda: True)
    process = Mock(returncode=1)
    process.communicate.return_value = ('{"error":"challenge_declined"}', None)
    monkeypatch.setattr(scholar_session.subprocess, "Popen", Mock(return_value=process))
    with pytest.raises(PermissionError, match="challenge_declined"):
        scholar_session.recover("q", previous)
    assert scholar_session.current()["cookies"] == previous["cookies"]


def test_host_page_recovery_remains_retryable_and_preserves_the_session(monkeypatch, tmp_path):
    previous = session()
    scholar_session._write(previous)
    bootstrap = Mock(side_effect=PermissionError('Google declined the audio verification'))
    monkeypatch.setattr(scholar_session, '_establish_session', bootstrap)
    with pytest.raises(PermissionError, match='declined'):
        scholar_session.fetch_page('q', 'https://scholar.google.co.uk/scholar?q=q', str(tmp_path / 'page'))
    with pytest.raises(PermissionError, match='declined'):
        scholar_session.fetch_page('q2', 'https://scholar.google.co.uk/scholar?q=q2', str(tmp_path / 'page2'))
    assert bootstrap.call_count == 2
    assert scholar_session.current()['cookies'] == previous['cookies']


def test_successful_host_page_recovery_saves_the_verified_session(monkeypatch, tmp_path):
    final_url = 'https://scholar.google.com/scholar?q=private-query'
    monkeypatch.setattr(scholar_session, '_establish_session', lambda *a, **kw: {**session(), '_final_url': final_url})
    result = scholar_session.fetch_page('q', 'https://scholar.google.co.uk/scholar?q=q', str(tmp_path / 'page'))
    assert scholar_session.current()['host'] == 'scholar.google.co.uk'
    assert not scholar_session._read().get('retry_after')
    assert result['_final_url'] == final_url
    assert 'private-query' not in scholar_session._path().read_text()


def test_failed_host_worker_receipt_does_not_create_a_verification_freeze(tmp_path):
    import subprocess
    environment = {**os.environ, 'SCHOLAR_DATA_DIR': str(tmp_path), 'SCHOLAR_GOOGLE_RECOVERY': 'headless'}
    # Reject an invalid origin before launching a browser or making requests.
    result = subprocess.run([sys.executable, '-m', 'scholar_mcp.scholar_session'],
        input=json.dumps({'query': 'q', 'target': 'https://invalid.test/scholar', 'out': str(tmp_path / 'page')}),
        capture_output=True, text=True, env=environment, timeout=15)
    receipt = json.loads(result.stdout)
    assert result.returncode == 1 and 'retry_after' not in receipt


def test_google_retries_partial_search_after_preserving_completed_pages(monkeypatch):
    monkeypatch.setattr(google_scholar.pacing, "wait", lambda *a, **k: None)
    calls = []
    def get(client, url, **kwargs):
        calls.append(1)
        response = ('<div class="gs_ri"><h3 class="gs_rt">A recovered paper</h3></div>' if len(calls) == 1
                    else '<form id="gs_captcha_f"></form>' if len(calls) == 2 else '<p>Your search did not match any articles.</p>')
        return httpx.Response(200, text=response, request=httpx.Request("GET", url))
    monkeypatch.setattr(httpx.Client, "get", get)
    monkeypatch.setattr(scholar_session, "recover", Mock(side_effect=PermissionError("verification unavailable")))
    first = sources._timed_call("google_scholar", google_scholar.search_papers, "q", 20)
    assert first.status == "partial" and len(first.results) == 1
    second = sources._timed_call("google_scholar", google_scholar.search_papers, "q", 20)
    assert second.status == "ok" and len(second.results) == 1
    assert len(calls) == 3  # the completed first page was replayed, not fetched again


def test_google_larger_request_reuses_all_cards_from_a_cached_small_request(monkeypatch):
    starts = []
    def get(client, url, **kwargs):
        start = int(kwargs['params'].get('start', 0))
        starts.append(start)
        html = ''.join(f'<div class="gs_ri"><h3 class="gs_rt"><a href="https://paper.test/{i}">Paper {i}</a></h3></div>'
                       for i in range(start, start + 10))
        return httpx.Response(200, text=html, request=httpx.Request('GET', url))
    monkeypatch.setattr(httpx.Client, 'get', get)
    assert len(google_scholar.search_papers('cached-small', max_results=3)) == 3
    assert len(google_scholar.search_papers('cached-small', max_results=20)) == 20
    assert starts == [0, 10]


def test_google_completed_page_survives_a_process_restart(monkeypatch):
    import os
    import subprocess
    import sys

    from scholar_mcp import config
    def get(client, url, **kwargs):
        return httpx.Response(200, text='<div class="gs_ri"><h3 class="gs_rt">Durable result</h3></div>',
                              request=httpx.Request('GET', url))
    monkeypatch.setattr(httpx.Client, 'get', get)
    assert google_scholar.search_papers('restart-safe page', max_results=1)[0]['title'] == 'Durable result'
    code = """from scholar_mcp.sources import google_scholar as google
def forbidden(*a, **kw):
    raise AssertionError('completed page was fetched again')
google._page = forbidden
assert google.search_papers('restart-safe page', max_results=1)[0]['title'] == 'Durable result'
"""
    run = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True,
        env={**os.environ, 'SCHOLAR_DATA_DIR': config.DATA_DIR}, timeout=15)
    assert run.returncode == 0, run.stderr


def test_browser_pages_are_read_without_replaying_their_cookies_in_http(monkeypatch):
    from urllib.parse import parse_qs
    real_client = httpx.Client
    monkeypatch.setattr(google_scholar.pacing, 'wait', lambda *a, **k: None)
    monkeypatch.setattr(google_scholar.access, 'available', lambda: False)
    seen = []
    def handle(request):
        seen.append(request)
        return httpx.Response(403, text='<form id="gs_captcha_f"></form>')
    def recover(query, previous, *, target):
        offset = int(parse_qs(httpx.URL(target).query.decode()).get('start', ['0'])[0])
        body = ''.join(f'<div class="gs_ri"><h3 class="gs_rt">Paper {i}</h3><div class="gs_a">Author - 2024</div></div>'
                       for i in range(offset, offset + 10))
        return {**session(), '_html': body}
    bootstrap = Mock(side_effect=recover)
    monkeypatch.setattr(scholar_session, 'recover', bootstrap)
    def client(**kwargs):
        kwargs.pop('proxy', None)
        return real_client(transport=httpx.MockTransport(handle), **kwargs)
    monkeypatch.setattr(google_scholar.httpx, 'Client', client)
    result = google_scholar.search_papers('query with browser-only verification', 20)
    assert len(result) == 20 and len(seen) == 1
    assert bootstrap.call_count == 2
    assert all('session=fixture' not in request.headers.get('cookie', '') for request in seen)


def test_rejected_recovered_session_does_not_loop(monkeypatch):
    monkeypatch.setattr(google_scholar.pacing, "wait", lambda *a, **k: None)
    recover = Mock(return_value={**session(), "_html": '<form id="gs_captcha_f"></form>'})
    monkeypatch.setattr(scholar_session, "recover", recover)
    monkeypatch.setattr(httpx.Client, "get", lambda *a, **kw: httpx.Response(
        429, request=httpx.Request("GET", "https://scholar.google.co.uk/scholar")))
    with pytest.raises(google_scholar.BlockedError, match="rejected"):
        google_scholar.search_papers("query", 1)
    assert recover.call_count == 1


def test_cold_recovery_budget_preserves_explicit_limit(monkeypatch):
    monkeypatch.setattr(sources, "search_sources", lambda: [sources.Source("google_scholar", search=lambda *a, **kw: [])])
    monkeypatch.setattr(scholar_session, "recovery_available", lambda: True)
    budgets = []
    for explicit in (None, 2):
        flow = sources.search_flow([("q", "", "")], budget_s=explicit)
        budgets.append(round(flow.deadline - flow.started))
        flow.close()
    assert budgets == [150, 2]


@pytest.mark.parametrize("status", [403, 406, 429, 503])
def test_arxiv_web_fallback_retains_identity_and_full_abstract(monkeypatch, status):
    page = '''<li class="arxiv-result"><p class="list-title"><a href="https://arxiv.org/abs/1706.03762">arXiv</a></p>
    <p class="title">Attention Is All You Need</p><p class="authors"><a>A Author</a></p>
    <span class="abstract-full">Full abstract.<a>Less</a></span>
    <p class="is-size-7">Submitted 2 August, 2023; originally announced June 2017.</p></li>'''
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: httpx.Response(
        status, request=httpx.Request("GET", arxiv.ARXIV_API_URL)))
    monkeypatch.setattr(httpx.Client, "get", lambda *a, **kw: httpx.Response(
        200, text=page, request=httpx.Request("GET", "https://arxiv.org/search/")))
    result = arxiv.search_papers("attention", 1)[0]
    assert result["year"] == 2017 and result["publication_date"] == "2017-06"
    assert result["abstract"] == "Full abstract."
    assert result["external_ids"] == {"ArXiv": "1706.03762"}
    assert result["source"] == "arxiv"


def test_arxiv_unavailable_atom_service_does_not_repeat_or_preempt_native_website(monkeypatch):
    from scholar_mcp import access
    from scholar_mcp.sources import arxiv
    calls = []
    def get(url, **kwargs):
        calls.append(url)
        return httpx.Response(503, request=httpx.Request('GET', url))
    monkeypatch.setattr(httpx, 'get', get)
    monkeypatch.setattr(access, 'available', lambda: True)
    monkeypatch.setattr(access, 'fetch', lambda *a, **kw: pytest.fail('Atom must use its native website fallback first'))
    monkeypatch.setattr(arxiv, '_search_web', lambda query, limit: [{'title': query}])
    assert arxiv.search_papers('first question')[0]['title'] == 'first question'
    assert arxiv.search_papers('second question')[0]['title'] == 'second question'
    assert calls == [arxiv.ARXIV_API_URL]


def test_arxiv_does_not_mask_invalid_api_queries(monkeypatch):
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: httpx.Response(
        400, request=httpx.Request("GET", arxiv.ARXIV_API_URL)))
    with pytest.raises(httpx.HTTPStatusError):
        arxiv.search_papers("invalid", 1)


def test_scholar_nonbreaking_spaces_do_not_leak_venue_into_authors():
    from bs4 import BeautifulSoup
    card = BeautifulSoup('''<div class="gs_ri"><h3 class="gs_rt">Paper</h3>
    <div class="gs_a">A Author, B Author\u00a0- Conference, 2017 - publisher.example</div></div>''', "html.parser").div
    paper = google_scholar._parse_paper(card)
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
def test_shared_record_output_and_worker_modules_stay_independent_of_the_source_fleet():
    import subprocess
    script = ('import sys; from scholar_mcp import records, output, scholar_session;'
              'assert not {"scholar_mcp.sources", "scholar_mcp.server", "scholar_mcp.relevance"} & sys.modules.keys();'
              'assert records.best_paper_id({"external_ids":{"DOI":"10.1234/example"}})=="10.1234/example";'
              'assert output.yaml_text({"content":"first\\nsecond"}).startswith("content: |")')
    subprocess.run([sys.executable, '-c', script], check=True, timeout=30)
