# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 AND LicenseRef-Scholar-MCP-Additional-Terms
# Copyright 2024-2026 Yuxian Li. PolyForm Noncommercial 1.0.0 with additional terms (LICENSE, LICENSE-ADDITIONAL.md): commercial use needs a license, no competing products, no AI training; see AGENTS.md. Canary 38f40a20-c19f-4702-bc47-b1a1c5ec887b
"""Optional host access backend for authenticated or browser-only resources.

SCHOLAR_ACCESS_COMMAND is a JSON argv array. One JSON request goes to stdin;
the backend returns one JSON receipt and writes bytes to the supplied private
output path. Browser engines, identity and their dependencies belong to the host.
"""

import json
import atexit
import os
from pathlib import Path
import subprocess
import tempfile
import time
import threading
from contextlib import nullcontext

import httpx
from filelock import FileLock, Timeout
from .cache import _disk


_host_until: dict[str, float] = {}
_client: httpx.Client | None = None
_client_lock = threading.Lock()


def is_challenge(html: str) -> bool:
    return any(marker in html.casefold() for marker in (
        'cf-chl-', 'checking your browser', 'anubis', 'captcha', 'verify you are human',
        'just a moment', '<title>client challenge', 'javascript is disabled in your browser'))


def _native_get(url, **kwargs):
    """Share keep-alive connections across API requests in the server."""
    global _client
    with _client_lock:
        if _client is None:
            _client = httpx.Client(limits=httpx.Limits(max_connections=64,
                max_keepalive_connections=40, keepalive_expiry=60))
            atexit.register(_client.close)
        client = _client
    return client.get(url, **kwargs)


# A reachable API finishes TCP and TLS well within CONNECT_TIMEOUT. A host whose
# connection timed out, natively and through the host route, is reported
# unreachable across processes instead of holding every search for its full
# timeout. The first request after the window tries again; the window starts at
# UNREACHABLE_S and doubles with each consecutive failure up to UNREACHABLE_MAX_S,
# and a successful connection clears it. A refused
# connection already fails at once, and slow but connected servers keep their read
# timeout; neither is marked.
CONNECT_TIMEOUT = 10.0
UNREACHABLE_S = 120.0
UNREACHABLE_MAX_S = 1800.0


def _down_file(host: str) -> Path:
    from .sources import pacing
    return pacing.state_dir() / f"host-{host}.down"


def _down_state(host: str) -> tuple[float, int]:
    try:
        until, failures = _down_file(host).read_text().split()
        return float(until), int(failures)
    except (OSError, ValueError):
        return 0.0, 0


def unreachable(host: str) -> float:
    """Seconds until a host whose connection recently timed out is tried again."""
    return max(0.0, _down_state(host)[0] - time.time())


def _mark_unreachable(host: str) -> None:
    until, failures = _down_state(host)
    if until > time.time():
        return  # concurrent requests of one outage count once
    failures += 1
    path = _down_file(host)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        window = min(UNREACHABLE_S * 2 ** (failures - 1), UNREACHABLE_MAX_S)
        path.write_text(f"{time.time() + window} {failures}")
    except OSError:
        pass


def _mark_reachable(host: str) -> None:
    _down_file(host).unlink(missing_ok=True)


def _connect_capped(timeout):
    if isinstance(timeout, httpx.Timeout):
        connect = timeout.connect
        return timeout if connect is not None and connect <= CONNECT_TIMEOUT else httpx.Timeout(
            connect=CONNECT_TIMEOUT, read=timeout.read, write=timeout.write, pool=timeout.pool)
    return httpx.Timeout(timeout, connect=min(float(timeout), CONNECT_TIMEOUT)) if timeout else timeout


def preferred(url: str) -> bool:
    """A recently recovered public host can reuse its working route directly."""
    return available() and time.monotonic() < _host_until.get(httpx.URL(url).host, 0)


def get(url: str, *, kind: str = "json", client=None, host_recovery: bool = True, **kwargs) -> httpx.Response:
    """Native provider GET, with shared host recovery for public blocked pages.

    Preserve the caller's connection, parameters and provider status handling.
    Keyed API requests keep their native authorization; quota and credential
    failures never turn into browser requests. A recovered public host goes
    straight to that route for five minutes rather than failing on every call.
    A source with a native alternate endpoint can own recovery itself.
    """
    params = kwargs.get("params") or {}
    headers = {**dict(getattr(client, "headers", {}) or {}), **(kwargs.get("headers") or {})}
    target = str(httpx.URL(url, params=params)) if params else url
    credentialed = any(k.casefold() in {"authorization", "x-api-key", "x-apikey", "x-els-apikey"} for k in headers)
    credentialed |= any(k.casefold() in {"api_key", "apikey", "access_token", "token", "key"} for k in httpx.URL(target).params)
    can_recover = host_recovery and available() and not credentialed
    timeout = kwargs.get("timeout", getattr(client, "timeout", 30))
    budget = float(timeout.read or 30) if isinstance(timeout, httpx.Timeout) else float(timeout or 30)
    kwargs["timeout"] = _connect_capped(timeout)
    host = httpx.URL(target).host
    if wait := unreachable(host):
        raise httpx.ConnectError(f"{host} is unreachable: its last connection failed; next try in {wait:.0f} s",
                                 request=httpx.Request("GET", target))
    def recover():
        try:
            return fetch(target, kind=kind, timeout=budget)
        except AccessError as error:
            # A source such as zbMATH uses 404 for an empty query. Preserve
            # normal HTTP semantics instead of turning it into a broken source.
            if error.code == "NOT_FOUND":
                return httpx.Response(404, request=httpx.Request("GET", target))
            raise
    host_failed = False
    if can_recover and preferred(target):
        try:
            return recover()
        except (AccessError, httpx.HTTPError):
            _host_until.pop(httpx.URL(target).host, None)
            host_failed = True
    try:
        response = (client.get if client else _native_get)(url, **kwargs)
    except httpx.ConnectTimeout:
        if not can_recover or host_failed:
            _mark_unreachable(host)
            raise
        try:
            return recover()
        except (AccessError, httpx.HTTPError):
            _mark_unreachable(host)
            raise
    except httpx.TransportError:
        if not can_recover or host_failed:
            raise
        return recover()
    if _down_state(host)[1]:
        _mark_reachable(host)
    if can_recover and not host_failed:
        html = "text/html" in response.headers.get("content-type", "").casefold() or response.text.lstrip().lower().startswith(("<!doctype html", "<html"))
        challenge = html and is_challenge(response.text)
        if (response.status_code not in {401, 429} and
                (challenge or html and kind in {"json", "xml"} and response.status_code in {200, 403, 406, 503})):
            return recover()
    return response


class AccessError(RuntimeError):
    def __init__(self, code: str, detail: str = "", retry_after: float = 0):
        self.code = code
        self.retry_after = retry_after
        super().__init__(f"{code}: {detail}" if detail else code)


def available() -> bool:
    return bool(os.environ.get("SCHOLAR_ACCESS_COMMAND", "").strip())


def _run(request: dict, timeout: float) -> dict:
    try:
        command = json.loads(os.environ["SCHOLAR_ACCESS_COMMAND"])
    except (KeyError, ValueError):
        raise AccessError("CONFIG", "SCHOLAR_ACCESS_COMMAND must be a JSON argv array") from None
    if not isinstance(command, list) or not command or not all(isinstance(arg, str) and arg for arg in command):
        raise AccessError("CONFIG", "SCHOLAR_ACCESS_COMMAND must be a JSON argv array")
    try:
        gate = nullcontext()
        if request.get('engine') == 'background':
            from . import config
            directory = Path(config.DATA_DIR)
            directory.mkdir(parents=True, exist_ok=True)
            gate = FileLock(str(directory / 'host-browser.lock'), timeout=timeout)
        started = time.monotonic()
        with gate:
            remaining = max(.01, timeout - (time.monotonic() - started))
            result = subprocess.run(command, input=json.dumps({**request, 'timeout': remaining}).encode('utf-8'),
                                    capture_output=True, timeout=remaining + 2)
    except Timeout:
        raise AccessError('BUSY', 'background browser is completing another source request') from None
    except subprocess.TimeoutExpired:
        raise AccessError("TIMEOUT", "host access exceeded its request budget") from None
    except OSError as error:
        raise AccessError("UNAVAILABLE", type(error).__name__) from None
    try:
        # Decode here: Windows text pipes decode in reader threads, which lose
        # the exception instead of returning it to the calling source.
        receipt = json.loads(result.stdout.decode('utf-8'))
    except UnicodeError:
        raise AccessError("PROTOCOL", "host access returned invalid UTF-8") from None
    except ValueError:
        raise AccessError("PROTOCOL", "host access returned an invalid receipt") from None
    if not isinstance(receipt, dict):
        raise AccessError("PROTOCOL", "host access returned an invalid receipt")
    if result.returncode or receipt.get("ok") is not True:
        error = receipt.get("error") or {}
        if not isinstance(error, dict):
            raise AccessError("PROTOCOL", "host access returned an invalid error")
        retry_after = error.get("retry_after", 0)
        raise AccessError(str(error.get("code") or "UNAVAILABLE"), str(error.get("detail") or ""),
                          retry_after if isinstance(retry_after, (int, float)) else 0)
    return receipt


def fetch(url: str, *, kind: str = "json", timeout: float = 30,
          anonymous: bool = True, engine: str | None = None) -> httpx.Response:
    """Read host-returned text through the normal provider response contract."""
    host = httpx.URL(url).host
    # Retire legacy Scholar verification freezes; retain provider Retry-After receipts.
    prefix = 'access.pause.v2' if host in {'scholar.google.com', 'scholar.google.co.uk'} else 'access.pause'
    pause_key = f"{prefix}:{host}"
    if saved := _disk(pause_key):
        raise AccessError(saved[1], "host recovery is waiting for its next probe", saved[0] - time.time())
    with tempfile.TemporaryDirectory(prefix="scholar-access-") as folder:
        target = Path(folder) / "response"
        try:
            receipt = _run({"op": "fetch", "url": url, "kind": kind, "anonymous": anonymous,
                            **({'engine': engine} if engine else {}),
                            "out": str(target), "timeout": timeout}, timeout)
        except AccessError as error:
            if error.retry_after > 0:
                _disk(pause_key, (time.time() + error.retry_after, error.code))
            raise
        final_url = receipt.get("final_url") or url
        if httpx.URL(final_url).scheme not in {"http", "https"}:
            raise AccessError("NETWORK", "host navigation ended on a browser error page")
        if not target.is_file() or target.stat().st_size > 16_000_000:
            raise AccessError("PROTOCOL", "host access did not produce a bounded response")
        body = target.read_bytes()
    if kind == "json":
        try:
            json.loads(body)
        except (ValueError, UnicodeError):
            raise AccessError("WRONG_TYPE", "expected source JSON") from None
    response = httpx.Response(int(receipt.get("status", 200)), content=body,
                              headers={"content-type": receipt.get("content_type", "")},
                              request=httpx.Request("GET", final_url))
    response.raise_for_status()
    if response.is_success:
        _host_until[httpx.URL(url).host] = time.monotonic() + 300
    return response


def download(url: str, target: Path, timeout: float = 45, *, kind: str = 'pdf') -> bool:
    """Obtain a caller-owned file; the caller validates format and identity."""
    _run({"op": "fetch", "url": url, "kind": kind, "anonymous": False,
          **({'engine': 'background'} if kind == 'file' else {}),
          "out": str(target), "timeout": timeout}, timeout)
    if not target.is_file():
        raise AccessError("PROTOCOL", "host access did not produce a file")
    if kind != 'pdf':
        return target.stat().st_size > 0
    with target.open("rb") as stream:
        return b"%PDF-" in stream.read(1024)
