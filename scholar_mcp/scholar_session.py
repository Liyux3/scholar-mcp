"""Optional Google verification worker and private, route-bound HTTP sessions.

Browser dependencies are imported only by the short-lived recovery subprocess.
Its private browser profile survives that worker; the MCP process keeps no browser.
"""
import hashlib
import importlib.util
import io
import json
import math
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from contextlib import ExitStack, redirect_stdout
from http.cookiejar import Cookie
from pathlib import Path
from urllib.parse import urlencode, urlsplit

import httpx
from filelock import FileLock, Timeout

from . import config

RECOVERY_TIMEOUT = 120
SESSION_TTL = 6 * 3600
HOSTS = {"scholar.google.com", "scholar.google.co.uk"}
COOKIE_DOMAINS = {"google.com", "www.google.com", "scholar.google.com",
                  "google.co.uk", "www.google.co.uk", "scholar.google.co.uk"}


def proxy() -> str | None:
    if (config.GOOGLE_SCHOLAR_PROXY or "").strip().casefold() == "direct":
        return None
    return (config.GOOGLE_SCHOLAR_PROXY or os.environ.get("HTTPS_PROXY")
            or os.environ.get("https_proxy") or os.environ.get("ALL_PROXY")
            or os.environ.get("all_proxy") or None)


def _route() -> str:
    return hashlib.sha256((proxy() or "direct").encode()).hexdigest()


def _path() -> Path:
    return Path(config.DATA_DIR) / 'sessions' / f'google-{_route()[:16]}.json'


def _profile(mode: str = 'auto') -> Path:
    identity = hashlib.sha256(f"{_route()}:{browser_path()}:{mode}".encode()).hexdigest()[:16]
    path = _path().parent / f"google-browser-{identity}"
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


def _read() -> dict:
    try:
        path = _path()
        if not path.exists() and not path.is_symlink():
            path = path.parent / 'google.json'  # A same-route legacy session can migrate naturally.
        if path.is_symlink() or path.stat().st_size > 262144:
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("route") != _route():
            return {}
        return data
    except (OSError, ValueError):
        return {}


def current() -> dict:
    data = _read()
    try:
        if (data.get("host") not in HOSTS or not isinstance(data.get("user_agent"), str)
                or not isinstance(data.get("cookies"), list)
                or not 0 <= time.time() - data["created"] < SESSION_TTL):
            return {}
        if (len(data["user_agent"]) > 1024 or "\r" in data["user_agent"] or "\n" in data["user_agent"]
                or any(not _valid_cookie(c) for c in data["cookies"])):
            return {}
        return data
    except (TypeError, KeyError):
        return {}


def cookie_jar(data: dict) -> httpx.Cookies:
    jar = httpx.Cookies()
    for c in data.get("cookies", []):
        domain = c["domain"]
        expires = int(c["expires"]) if c.get("expires", -1) > 0 else None
        jar.jar.set_cookie(Cookie(
            0, c["name"], c["value"], None, False, domain, domain.startswith("."),
            domain.startswith("."), c.get("path", "/"), True,
            bool(c.get("secure", True)), expires, expires is None, None, None, {},
        ))
    return jar


def _valid_cookie(cookie) -> bool:
    if not isinstance(cookie, dict):
        return False
    if not all(isinstance(cookie.get(k), str) for k in ("name", "value", "domain")):
        return False
    expires = cookie.get("expires", -1)
    return (cookie["domain"].lstrip(".") in COOKIE_DOMAINS
            and isinstance(cookie.get("path", "/"), str)
            and cookie.get("path", "/").startswith("/")
            and isinstance(expires, (int, float)) and math.isfinite(expires))


def _write(data: dict) -> None:
    path = _path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".google-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def browser_path() -> str | None:
    configured = os.environ.get("SCHOLAR_GOOGLE_BROWSER")
    if configured:
        path = Path(configured).expanduser()
        return str(path) if path.is_file() else shutil.which(configured)
    candidates = []
    if sys.platform == "darwin":
        for root in (Path("/Applications"), Path.home() / "Applications"):
            candidates.extend(str(root / app) for app in (
                "Google Chrome.app/Contents/MacOS/Google Chrome",
                "Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
                "Chromium.app/Contents/MacOS/Chromium",
            ))
    elif sys.platform == "win32":
        for variable in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            if root := os.environ.get(variable):
                candidates.extend(str(Path(root) / app) for app in (
                    "Google/Chrome/Application/chrome.exe",
                    "Microsoft/Edge/Application/msedge.exe",
                    "Chromium/Application/chrome.exe",
                ))
    candidates.extend(shutil.which(name) for name in (
        "google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "msedge",
    ))
    return next((p for p in candidates if p and Path(p).is_file()), None)


def _browser_major(path: str | None) -> int | None:
    if not path:
        return None
    try:
        if sys.platform == "darwin":
            import plistlib
            with open(Path(path).parents[1] / "Info.plist", "rb") as stream:
                version = plistlib.load(stream).get("CFBundleShortVersionString", "")
        elif sys.platform == "win32":
            # Chrome and Edge keep each installed version in a folder beside the executable.
            version = max((d.name for d in Path(path).parent.iterdir()
                           if re.fullmatch(r"\d+(?:\.\d+){3}", d.name)),
                          key=lambda name: [int(part) for part in name.split(".")], default="")
        else:
            version = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=5).stdout
        match = re.search(r"(\d{2,3})\.\d+", version)
        return int(match[1]) if match else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


_user_agent: str | None = None


def browser_user_agent() -> str:
    """The user agent of the installed Chromium browser, for publisher and proxy requests.

    Bot walls bounce old browser versions (Radware turns away Chrome 120 and
    passes 131+), so a hard-coded agent goes stale. Without a browser, Chrome's
    four-week release cadence since 131 (2024-11-12) gives a current version.
    """
    global _user_agent
    if _user_agent is None:
        path = browser_path()
        import datetime
        major = _browser_major(path) or 130 + (datetime.date.today() - datetime.date(2024, 11, 12)).days // 28
        platform = {"darwin": "Macintosh; Intel Mac OS X 10_15_7",
                    "win32": "Windows NT 10.0; Win64; x64"}.get(sys.platform, "X11; Linux x86_64")
        edge = f" Edg/{major}.0.0.0" if path and "edge" in path.lower() else ""
        _user_agent = (f"Mozilla/5.0 ({platform}) AppleWebKit/537.36 (KHTML, like Gecko) "
                       f"Chrome/{major}.0.0.0 Safari/537.36{edge}")
    return _user_agent


def _recovery_mode() -> str:
    # Auto remains quiet: only the macOS no-activation launcher is eligible
    # for a hidden retry. A visible window always requires explicit opt-in.
    mode = os.environ.get("SCHOLAR_GOOGLE_RECOVERY", "auto").strip().lower()
    if mode in {"0", "false", "off"}:
        return "off"
    return mode if mode in {"auto", "headed", "headless"} else "headless"


def recovery_available() -> bool:
    mode = _recovery_mode()
    return (mode != "off"
            and importlib.util.find_spec("DrissionPage") is not None
            and bool(browser_path())
            and (mode != "headed" or sys.platform != "linux"
                 or bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))))


def recover(query: str, previous: dict, *, target: str = "") -> dict:
    """Recover in the browser's own transport; optionally return the page it read."""
    from .sources import pacing

    if not recovery_available():
        raise PermissionError("Google Scholar needs browser recovery; install scholar-mcp[access] and Chrome/Edge/Chromium, with SCHOLAR_GOOGLE_RECOVERY enabled")
    route = proxy()
    if route and urlsplit(route).username:
        raise PermissionError("Google verification requires a local unauthenticated proxy gateway")
    ready = current()
    if not target and ready and ready.get('created') != previous.get('created'):
        return ready
    failures_before = _read().get('recovery_failures', 0)
    budget = max(1, pacing.remaining(RECOVERY_TIMEOUT))
    with tempfile.TemporaryDirectory(prefix='scholar-page-') as folder:
        out = str(Path(folder) / 'page') if target else ''
        request = {'query': query, 'proxy': route, 'timeout': budget,
                   'previous_created': previous.get('created'), 'target': target, 'out': out}
        try:
            process = subprocess.Popen(
                [sys.executable, '-m', 'scholar_mcp.scholar_session'],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, encoding='utf-8', start_new_session=os.name == 'posix')
            try:
                output, _ = process.communicate(json.dumps(request), timeout=budget)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.communicate(timeout=8)
                except subprocess.TimeoutExpired:
                    if os.name == 'posix':
                        os.killpg(process.pid, signal.SIGKILL)
                    else:
                        process.kill()
                    process.communicate()
                raise TimeoutError('Google verification exceeded its time budget') from None
            try:
                data = json.loads(output)
            except ValueError:
                data = {}
            if process.returncode:
                allowed = {'verification_unavailable', 'challenge_declined', 'no_paper_results',
                           'timeout', 'traffic_denied', 'network_error', 'busy'}
                reason = data.get('error')
                raise PermissionError(f'Google verification failed ({reason if reason in allowed else "worker_error"})')
            # The worker serializes its own profile and saves the verified state.
            # Retain compatibility with a session-only worker, without persisting HTML.
            if not target:
                data.update(route=_route(), created=time.time())
                _write(data)
                if not current():
                    raise ValueError('Invalid Google session returned by recovery worker')
                return data
            page = Path(out)
            if not page.is_file() or page.stat().st_size > 16_000_000:
                raise ValueError('Google browser did not return a bounded page')
            return {**current(), '_html': page.read_text(encoding='utf-8'), '_final_url': data.get('final_url', target)}
        except Exception as error:
            if _read().get('recovery_failures', 0) == failures_before:
                _record_failure(current() or previous, error)
            raise


def _record_failure(previous: dict, error: Exception) -> None:
    failures = int(_read().get("recovery_failures", 0) or 0) + 1
    state = {key: value for key, value in previous.items() if key != "retry_after"}
    _write({**state, "route": _route(), "recovery_failures": failures,
            "failed_at": time.time(), "failure_type": type(error).__name__})


def fetch_page(query: str, target: str, out: str, previous_created=None) -> dict:
    """Serialize this private profile's work and retain its verified browser state."""
    _path().parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        with FileLock(str(_path().parent / 'google.lock'), timeout=RECOVERY_TIMEOUT):
            previous = _read()
            if not target and (ready := current()) and ready.get('created') != previous_created:
                return ready
            try:
                data = _establish_session(query, proxy(), target=target, out=out,
                                           prefer_background=previous.get("recovery_mode") == "background")
                final_url = data.pop('_final_url', target)
                data.update(route=_route(), created=time.time())
                _write(data)
                if not current():
                    raise ValueError("Invalid Google session returned by recovery worker")
                return {**data, '_final_url': final_url} if out else data
            except Exception as error:
                _record_failure(previous, error)
                raise
    except Timeout:
        raise PermissionError("Google verification is already running") from None


def _audio_answer(url: str, route: str | None) -> str:
    if importlib.util.find_spec('speech_recognition') is None or not shutil.which('ffmpeg'):
        raise PermissionError('Google audio verification needs the google extra and ffmpeg')
    import speech_recognition as sr

    # Challenge URLs come from the Google iframe, but still enforce the host boundary.
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname not in COOKIE_DOMAINS:
        raise ValueError("Unexpected verification audio origin")
    with httpx.Client(proxy=route, trust_env=False, timeout=15) as client:
        response = client.get(url)
        response.raise_for_status()
    converted = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-i", "pipe:0", "-f", "wav", "pipe:1"],
        input=response.content, capture_output=True, timeout=15, check=True,
    )
    recognizer = sr.Recognizer()
    recognizer.operation_timeout = 15
    with sr.AudioFile(io.BytesIO(converted.stdout)) as source:
        audio = recognizer.record(source)
    return recognizer.recognize_google(audio)


def _wait_visible(page, selectors: tuple[str, ...], timeout: float = 8) -> str | None:
    """Advance as soon as a result or verification state is ready."""
    deadline = time.monotonic() + timeout
    while True:
        for selector in selectors:
            element = page.ele(selector, timeout=0)
            if element and element.states.is_displayed:
                return selector
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        time.sleep(min(.2, remaining))


def _bootstrap(query: str, route: str | None, *, headless: bool | None = None,
               windowless: bool = False, target: str = "", out: str = "") -> dict:
    if target:
        parsed = urlsplit(target)
        if parsed.scheme != "https" or parsed.hostname not in HOSTS or parsed.path != "/scholar" or parsed.port not in (None, 443):
            raise ValueError("Unexpected Scholar result origin")
    if out and (not Path(out).is_absolute() or Path(out).exists()):
        raise ValueError("Output must be a new absolute path")
    from DrissionPage import ChromiumOptions, ChromiumPage

    def interrupted(*_):
        raise TimeoutError("Verification worker stopped")

    signal.signal(signal.SIGTERM, interrupted)
    if hasattr(signal, "SIGALRM"):
        signal.signal(signal.SIGALRM, interrupted)
        signal.alarm(math.ceil(RECOVERY_TIMEOUT))
    with tempfile.TemporaryDirectory(prefix="scholar-verify-", ignore_cleanup_errors=True) as directory, ExitStack() as cleanup:
        if hasattr(signal, "SIGALRM"):
            cleanup.callback(signal.alarm, 0)
        actual_headless = _recovery_mode() != 'headed' if headless is None else headless
        profile = _profile('background' if windowless else 'headless' if actual_headless else 'headed')
        _close_background_profile(str(profile))
        if not windowless:
            cleanup.callback(_close_background_profile, str(profile))
        options = ChromiumOptions(read_file=False).set_browser_path(browser_path())
        options.set_tmp_path(directory).headless(actual_headless)
        options.set_timeouts(base=4, page_load=20, script=5)
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        options.set_local_port(port).set_user_data_path(str(profile))
        options.set_argument('--disk-cache-size', '16777216')
        options.set_argument('--no-first-run')
        options.set_argument('--no-default-browser-check')
        if route:
            options.set_proxy(route)
        page = _background_page(options, cleanup) if windowless else ChromiumPage(options)
        try:
            # Unified Chromium uses the same engine in both modes, but its
            # headless UA can receive a different, non-interactive Scholar
            # gate. Keep the installed browser version/platform and use the
            # same UA for browser recovery and the subsequent HTTP session.
            user_agent = page.run_js("return navigator.userAgent")
            if "HeadlessChrome/" in user_agent:
                page.run_cdp("Network.setUserAgentOverride",
                             userAgent=user_agent.replace("HeadlessChrome/", "Chrome/"),
                             acceptLanguage="en-US,en;q=0.9")
            from urllib.parse import urlunsplit
            requested = urlsplit(target or 'https://scholar.google.co.uk/scholar?' + urlencode({'q': query, 'hl': 'en'}))
            known = current().get('host')
            hosts = list(dict.fromkeys([known or requested.hostname, requested.hostname,
                                       'scholar.google.co.uk', 'scholar.google.com']))
            for index, host in enumerate(hosts):
                url = urlunsplit(requested._replace(netloc=host))
                loaded = page.get(url, retry=0, timeout=20)
                if loaded is False or urlsplit(page.url).scheme not in {'http', 'https'}:
                    raise ConnectionError('Google browser navigation failed')
                try:
                    _verify_browser_page(page, route)
                    break
                except PermissionError:
                    if index == len(hosts) - 1:
                        raise
            host = urlsplit(page.url).hostname
            if host not in HOSTS:
                raise PermissionError('Google verification did not return paper results')
            if out:
                Path(out).write_text(page.html, encoding="utf-8")
            return {"host": host, "_final_url": page.url, "user_agent": page.run_js("return navigator.userAgent"),
                    "cookies": [c for c in page.cookies(all_domains=True)
                                if c.get("domain", "").lstrip(".") in COOKIE_DOMAINS]}
        finally:
            page.quit(timeout=5)


def _verify_browser_page(page, route):
    results = 'css:.gs_ri .gs_rt'
    anchor_selector = 'css:iframe[src*="/anchor"]'
    challenge_selector = 'css:iframe[src*="/bframe"]'
    empty_selector = 'text:did not match any articles'
    for _ in range(2):
        body = page.html.casefold()
        if ('unusual traffic' in body or 'automated queries' in body) and 'recaptcha' not in body:
            raise PermissionError('Google rejected this network without a verification challenge')
        state = _wait_visible(page, (results, anchor_selector, empty_selector))
        if state in (results, empty_selector):
            return
        anchor = page.get_frame(anchor_selector, timeout=1) if state else None
        if not anchor:
            raise PermissionError('Google verification widget unavailable')
        anchor.ele('#recaptcha-anchor', timeout=4).click()
        state = _wait_visible(page, (results, challenge_selector))
        if state == results:
            return
        challenge = page.get_frame(challenge_selector, timeout=1) if state else None
        if not challenge:
            continue
        challenge.ele('#recaptcha-audio-button', timeout=4).click()
        audio = challenge.ele('#audio-source', timeout=5)
        if not audio:
            raise PermissionError('Google declined the audio verification')
        challenge.ele('#audio-response', timeout=3).input(_audio_answer(audio.attr('src'), route))
        challenge.ele('#recaptcha-verify-button', timeout=3).click()
        if _wait_visible(page, (results,), timeout=4):
            return
    raise PermissionError('Google verification did not return paper results')


def _background_bootstrap(query: str, route: str | None, **page_options) -> dict:
    """Use the native engine without creating a desktop window or visible tab."""
    return _bootstrap(query, route, headless=False, windowless=True, **page_options)


def _background_page(options, cleanup):
    """Create one CDP hidden page; its owner connection lives through recovery."""
    from DrissionPage import ChromiumPage
    from websocket import create_connection

    app = Path(options.browser_path).parents[2]
    if app.suffix != ".app":
        raise PermissionError("No non-activating launcher for this browser")
    profile = str(options.user_data_path)
    port = options.address.rsplit(":", 1)[1]
    cleanup.callback(_close_background_profile, profile)
    subprocess.Popen(["/usr/bin/open", "-g", "-j", "-n", "-a", str(app), "--args",
                      f"--remote-debugging-port={port}", "--no-startup-window", *options.arguments],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + 15
    try:
        with httpx.Client(trust_env=False, timeout=1) as client:
            while True:
                try:
                    info = client.get(f"http://127.0.0.1:{port}/json/version").json()
                    break
                except (httpx.HTTPError, ValueError):
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Hidden browser startup exceeded its budget") from None
                    time.sleep(.1)
        connection = create_connection(info["webSocketDebuggerUrl"], timeout=5,
                                       suppress_origin=True, http_no_proxy=["127.0.0.1", "localhost"])
        cleanup.callback(connection.close)
        request_id = 0
        def command(method, params):
            nonlocal request_id
            request_id += 1
            connection.send(json.dumps({'id': request_id, 'method': method, 'params': params}))
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                reply = json.loads(connection.recv())
                if reply.get('id') == request_id:
                    return reply
            raise TimeoutError('Browser command did not answer')

        hidden = {'url': 'about:blank', 'hidden': True, 'background': True}
        reply = command('Target.createTarget', hidden)
        if reply.get('error', {}).get('message') == 'Hidden target can be created only when remote debugging is enabled':
            # Chromium's check actually needs an existing frame, even with CDP
            # enabled. Bootstrap an already-minimized background window and
            # close it immediately after the hidden target owns its frame.
            # See chromium/src change 49293c82de9615d3232d673dcd3aa7ea573ca1f2.
            with httpx.Client(trust_env=False, timeout=2) as client:
                protocol = client.get(f'http://127.0.0.1:{port}/json/protocol').json()
            params = next((c.get('parameters', []) for d in protocol.get('domains', []) if d['domain'] == 'Target'
                           for c in d.get('commands', []) if c['name'] == 'createTarget'), [])
            if not any(p['name'] == 'windowState' for p in params):
                raise PermissionError('This browser cannot bootstrap a minimized background target')
            bootstrap = command('Target.createTarget', {'url': 'about:blank', 'newWindow': True,
                                                       'background': True, 'windowState': 'minimized'})
            if bootstrap.get('error'):
                raise RuntimeError(bootstrap['error'].get('message', 'Background bootstrap failed'))
            try:
                reply = command('Target.createTarget', hidden)
            finally:
                command('Target.closeTarget', {'targetId': bootstrap['result']['targetId']})
        if reply.get('error'):
            raise RuntimeError(reply['error'].get('message', 'Hidden target creation failed'))
        target = reply["result"]["targetId"]
        # Hidden targets are deliberately absent from /json. Attach by the
        # browser WebSocket and exact target ID rather than opening a new tab.
        options.set_address(info["webSocketDebuggerUrl"])
        page = ChromiumPage(options, tab_id=target)
        # A hidden target has no OS window to supply a viewport. Give its DOM
        # normal desktop geometry without creating or resizing a real window.
        page.run_cdp("Emulation.setDeviceMetricsOverride", width=1280, height=900,
                     deviceScaleFactor=1, mobile=False)
        return page
    except Exception as error:
        raise PermissionError("Hidden browser recovery unavailable") from error


def _close_background_profile(profile: str) -> None:
    """Retire only this dedicated profile's browser children, across platforms."""
    try:
        import psutil
    except ImportError:
        return
    owned = []
    for process in psutil.process_iter(['cmdline']):
        try:
            if f'--user-data-dir={profile}' in (process.info['cmdline'] or []):
                process.terminate()
                owned.append(process)
        except (psutil.Error, OSError):
            continue
    _, pending = psutil.wait_procs(owned, timeout=2)
    for process in pending:
        try:
            process.kill()
        except psutil.Error:
            pass


def _establish_session(query: str, route: str | None, *, prefer_background: bool = False,
                       target: str = "", out: str = "") -> dict:
    page_options = {"target": target, "out": out} if target or out else {}
    mode = _recovery_mode()
    if mode == "auto" and sys.platform == "darwin" and prefer_background:
        try:
            result = _background_bootstrap(query, route, **page_options)
            result["recovery_mode"] = "background"
        except PermissionError:
            result = _bootstrap(query, route, **page_options)
            result["recovery_mode"] = "headless"
        return result
    try:
        result = _bootstrap(query, route, **page_options)
        result["recovery_mode"] = "headed" if mode == "headed" else "headless"
        return result
    except PermissionError:
        if mode != "auto" or sys.platform != "darwin":
            raise
        result = _background_bootstrap(query, route, **page_options)
        result["recovery_mode"] = "background"
        return result


def http_client(headers: dict):
    """One HTTP transport policy for source queries and the isolated worker."""
    import httpx
    return httpx.Client(headers=headers, timeout=15, follow_redirects=True,
                        trust_env=False, proxy=proxy())


def _http_page(target: str, out: str) -> dict:
    """Native HTTP in the same runtime as Scholar, with its own cookie scope."""
    parsed = urlsplit(target)
    if parsed.scheme != 'https' or parsed.hostname not in HOSTS or parsed.port not in (None, 443) or parsed.path != '/scholar':
        raise ValueError('Unexpected Scholar result origin')
    path = Path(out)
    if not path.is_absolute() or path.exists():
        raise ValueError('Output must be a new absolute path')
    with http_client({'User-Agent': browser_user_agent(), 'Accept-Language': 'en-US,en;q=0.9'}) as client:
        response = client.get(target)
    if len(response.content) > 16_000_000:
        raise ValueError('Google returned an oversized page')
    path.write_bytes(response.content)
    return {'ok': True, 'status': response.status_code, 'final_url': str(response.url),
            'content_type': response.headers.get('content-type', ''), 'engine': 'scholar-http'}


if __name__ == "__main__":
    try:
        request = json.loads(sys.stdin.read(16384))
        RECOVERY_TIMEOUT = min(RECOVERY_TIMEOUT, max(1, float(request.get('timeout', RECOVERY_TIMEOUT))))
        with redirect_stdout(sys.stderr):
            if request.get('engine') == 'http':
                result = _http_page(request['target'], request['out'])
            else:
                result = fetch_page(request['query'], request.get('target', ''), request.get('out', ''),
                                    request.get('previous_created'))
        if request.get("out") and request.get('engine') != 'http':
            result = {"ok": True, "status": 200, "content_type": "text/html",
                      "final_url": result.get('_final_url', request['target']), "engine": "scholar-browser"}
        print(json.dumps(result))
    except Exception as error:
        reasons = {"Google verification widget unavailable": "verification_unavailable",
                   "Google declined the audio verification": "challenge_declined",
                   "Google verification did not return paper results": "no_paper_results",
                   "Google verification is already running": "busy",
                   "Google rejected this network without a verification challenge": "traffic_denied",
                   "Google browser navigation failed": "network_error"}
        reasons['Google audio verification needs the google extra and ffmpeg'] = 'verification_unavailable'
        reason = ('timeout' if isinstance(error, TimeoutError) else
                  'network_error' if isinstance(error, (ConnectionError, httpx.TransportError)) else
                  reasons.get(str(error), 'worker_error'))
        print(json.dumps({"error": reason}))
        sys.exit(1)
