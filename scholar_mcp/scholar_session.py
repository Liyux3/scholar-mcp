"""Optional Google verification worker and private, route-bound HTTP sessions.

Browser dependencies are imported only by the short-lived recovery subprocess.
The MCP process retains cookies, never a browser or a speech-recognition model.
"""
from contextlib import ExitStack, redirect_stdout
from http.cookiejar import Cookie
import hashlib
import importlib.util
import io
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlencode, urlsplit

from filelock import FileLock, Timeout
import httpx

from . import config

RECOVERY_TIMEOUT = 120
SESSION_TTL = 6 * 3600
HOSTS = {"scholar.google.com", "scholar.google.co.uk"}
COOKIE_DOMAINS = {"google.com", "www.google.com", "scholar.google.com",
                  "google.co.uk", "www.google.co.uk", "scholar.google.co.uk"}


def proxy() -> str | None:
    return (config.GOOGLE_SCHOLAR_PROXY or os.environ.get("HTTPS_PROXY")
            or os.environ.get("https_proxy") or os.environ.get("ALL_PROXY")
            or os.environ.get("all_proxy") or None)


def _route() -> str:
    return hashlib.sha256((proxy() or "direct").encode()).hexdigest()


def _path() -> Path:
    return Path(config.DATA_DIR) / "sessions" / "google.json"


def _read() -> dict:
    try:
        path = _path()
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
            and importlib.util.find_spec("speech_recognition") is not None
            and bool(shutil.which("ffmpeg")) and bool(browser_path())
            and (mode != "headed" or sys.platform != "linux"
                 or bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))))


def recover(query: str, previous: dict) -> dict:
    if not recovery_available():
        raise PermissionError("Google Scholar needs verification; recovery requires scholar-mcp[google], Chrome/Edge/Chromium and ffmpeg, with SCHOLAR_GOOGLE_RECOVERY enabled")
    route = proxy()
    if route and urlsplit(route).username:
        raise PermissionError("Google verification requires a local unauthenticated proxy gateway")
    _path().parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        with FileLock(str(_path().with_suffix(".lock")), timeout=RECOVERY_TIMEOUT + 5):
            ready = current()
            if ready and ready.get("created") != previous.get("created"):
                return ready
            retry_after = _read().get("retry_after", 0)
            if isinstance(retry_after, (int, float)) and retry_after > time.time():
                raise PermissionError("Google verification recently failed; retry after a few minutes")
            try:
                process = subprocess.Popen(
                    [sys.executable, "-m", "scholar_mcp.scholar_session"],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    text=True, start_new_session=os.name == "posix",
                )
                try:
                    output, _ = process.communicate(json.dumps({
                        "query": query, "proxy": route,
                        "prefer_background": _read().get("recovery_mode") == "background",
                    }), timeout=RECOVERY_TIMEOUT)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    try:
                        process.communicate(timeout=8)
                    except subprocess.TimeoutExpired:
                        if os.name == "posix":
                            os.killpg(process.pid, signal.SIGKILL)
                        else:
                            process.kill()
                        process.communicate()
                    raise TimeoutError("Google verification exceeded its time budget") from None
                if process.returncode:
                    try:
                        reason = json.loads(output).get("error")
                    except (ValueError, AttributeError):
                        reason = None
                    allowed = {"verification_unavailable", "challenge_declined", "no_paper_results", "timeout"}
                    reason = reason if reason in allowed else "worker_error"
                    raise PermissionError(f"Google verification failed ({reason})")
                data = json.loads(output)
                data.update(route=_route(), created=time.time())
                _write(data)
                if not current():
                    raise ValueError("Invalid Google session returned by recovery worker")
                return data
            except Exception:
                _write({**previous, "route": _route(), "retry_after": time.time() + 180})
                raise
    except Timeout:
        raise TimeoutError("Google verification is already running") from None


def _audio_answer(url: str, route: str | None) -> str:
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
               windowless: bool = False) -> dict:
    from DrissionPage import ChromiumOptions, ChromiumPage

    def interrupted(*_):
        raise TimeoutError("Verification worker stopped")

    signal.signal(signal.SIGTERM, interrupted)
    if hasattr(signal, "SIGALRM"):
        signal.signal(signal.SIGALRM, interrupted)
        signal.alarm(RECOVERY_TIMEOUT)
    with tempfile.TemporaryDirectory(prefix="scholar-verify-", ignore_cleanup_errors=True) as directory, ExitStack() as cleanup:
        if hasattr(signal, "SIGALRM"):
            cleanup.callback(signal.alarm, 0)
        options = ChromiumOptions(read_file=False).set_browser_path(browser_path())
        options.set_tmp_path(directory).auto_port().headless(
            _recovery_mode() != "headed" if headless is None else headless)
        options.set_timeouts(base=4, page_load=20, script=5)
        if route:
            options.set_proxy(route)
        if windowless:
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                port = listener.getsockname()[1]
            options.auto_port(False).set_local_port(port).set_user_data_path(directory)
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
            url = "https://scholar.google.co.uk/scholar?" + urlencode({"q": query, "hl": "en"})
            page.get(url, retry=0, timeout=20)
            results = "css:.gs_ri .gs_rt"
            anchor_selector = 'css:iframe[src*="/anchor"]'
            challenge_selector = 'css:iframe[src*="/bframe"]'
            for _ in range(2):
                state = _wait_visible(page, (results, anchor_selector))
                if state == results:
                    break
                anchor = page.get_frame(anchor_selector, timeout=1) if state else None
                if not anchor:
                    raise PermissionError("Google verification widget unavailable")
                anchor.ele("#recaptcha-anchor", timeout=4).click()
                state = _wait_visible(page, (results, challenge_selector))
                if state == results:
                    break
                challenge = page.get_frame(challenge_selector, timeout=1) if state else None
                if not challenge:
                    continue
                challenge.ele("#recaptcha-audio-button", timeout=4).click()
                audio = challenge.ele("#audio-source", timeout=5)
                if not audio:
                    raise PermissionError("Google declined the audio verification")
                answer = _audio_answer(audio.attr("src"), route)
                challenge.ele("#audio-response", timeout=3).input(answer)
                challenge.ele("#recaptcha-verify-button", timeout=3).click()
                if _wait_visible(page, (results,), timeout=4):
                    break
            host = urlsplit(page.url).hostname
            if host not in HOSTS or not page.eles("css:.gs_ri .gs_rt", timeout=3):
                raise PermissionError("Google verification did not return paper results")
            return {"host": host, "user_agent": page.run_js("return navigator.userAgent"),
                    "cookies": [c for c in page.cookies(all_domains=True)
                                if c.get("domain", "").lstrip(".") in COOKIE_DOMAINS]}
        finally:
            page.quit(timeout=5)


def _background_bootstrap(query: str, route: str | None) -> dict:
    """Use the native engine without creating a desktop window or visible tab."""
    return _bootstrap(query, route, headless=False, windowless=True)


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
        connection.send(json.dumps({"id": 1, "method": "Target.createTarget",
                                    "params": {"url": "about:blank", "hidden": True, "background": True}}))
        reply = json.loads(connection.recv())
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
    """LaunchServices children are limited to this recovery's unique profile."""
    try:
        found = subprocess.run(["pgrep", "-f", "--", re.escape("--user-data-dir=" + profile)],
                               capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.TimeoutExpired):
        return
    for value in found.stdout.split():
        try:
            os.kill(int(value), signal.SIGTERM)
        except (OSError, ValueError):
            pass


def _establish_session(query: str, route: str | None, *, prefer_background: bool = False) -> dict:
    mode = _recovery_mode()
    if mode == "auto" and sys.platform == "darwin" and prefer_background:
        try:
            result = _background_bootstrap(query, route)
            result["recovery_mode"] = "background"
        except PermissionError:
            result = _bootstrap(query, route)
            result["recovery_mode"] = "headless"
        return result
    try:
        result = _bootstrap(query, route)
        result["recovery_mode"] = "headed" if mode == "headed" else "headless"
        return result
    except PermissionError:
        if mode != "auto" or sys.platform != "darwin":
            raise
        result = _background_bootstrap(query, route)
        result["recovery_mode"] = "background"
        return result


if __name__ == "__main__":
    try:
        request = json.loads(sys.stdin.read(16384))
        with redirect_stdout(sys.stderr):
            result = _establish_session(request["query"], request.get("proxy"),
                                        prefer_background=request.get("prefer_background", False))
        print(json.dumps(result))
    except Exception as error:
        reasons = {"Google verification widget unavailable": "verification_unavailable",
                   "Google declined the audio verification": "challenge_declined",
                   "Google verification did not return paper results": "no_paper_results"}
        reason = "timeout" if isinstance(error, TimeoutError) else reasons.get(str(error), "worker_error")
        print(json.dumps({"error": reason}))
        sys.exit(1)
