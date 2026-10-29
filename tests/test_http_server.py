"""Idle recycling covers workers, streamed replies and the real process lifecycle."""

import http.client
from contextlib import closing
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import pytest

from scholar_mcp import http_server, server

ROOT = Path(__file__).resolve().parents[1]


def test_idle_window_starts_after_last_concurrent_request_finishes(monkeypatch):
    now = 0.0
    monkeypatch.setattr(http_server.time, 'monotonic', lambda: now)
    idle = http_server.IdleExit(1)
    now = 59
    assert not idle.expired()
    with idle.request():
        now = 120
        with idle.request():
            now = 180
            assert not idle.expired()
        now = 240
        assert not idle.expired()
    now = 299
    assert not idle.expired()
    now = 300
    assert idle.expired()
    with pytest.raises(RuntimeError), idle.request():
        raise RuntimeError('request failed')
    assert idle.active == 0 and not idle.expired()


@pytest.mark.parametrize('minutes', [-1, float('inf'), float('-inf'), float('nan')])
def test_invalid_idle_window_is_rejected(minutes):
    with pytest.raises(ValueError, match='SCHOLAR_IDLE_EXIT'):
        http_server.IdleExit(minutes)


@pytest.mark.parametrize('transport', ['http', 'streamable-http', 'stdio'])
def test_zero_disables_http_recycling_and_stdio_keeps_its_lifecycle(monkeypatch, transport):
    calls = []
    monkeypatch.setenv('SCHOLAR_MCP_TRANSPORT', transport)
    monkeypatch.setenv('SCHOLAR_IDLE_EXIT', '0' if transport != 'stdio' else 'invalid-but-http-only')
    monkeypatch.setattr(server.mcp, 'run', lambda **kwargs: calls.append(kwargs))
    monkeypatch.setattr(server, '_http_idle', None)
    server.main()
    assert len(calls) == 1
    assert calls[0]['transport'] == ('stdio' if transport == 'stdio' else 'http')
    assert not http_server.IdleExit(0).expired()


def test_default_idle_window_and_http_options(monkeypatch):
    calls = []
    monkeypatch.setenv('SCHOLAR_MCP_TRANSPORT', 'http')
    monkeypatch.delenv('SCHOLAR_IDLE_EXIT', raising=False)
    monkeypatch.setenv('SCHOLAR_MCP_HOST', '127.0.0.1')
    monkeypatch.setenv('SCHOLAR_MCP_PORT', '8801')
    monkeypatch.setenv('SCHOLAR_MCP_PATH', '/papers')
    monkeypatch.setenv('SCHOLAR_MCP_STATELESS', 'false')
    monkeypatch.setattr(server, '_http_idle', None)
    monkeypatch.setattr(http_server, 'run_http', lambda mcp, idle, **kw: calls.append((idle, kw)))
    server.main()
    idle, options = calls[0]
    assert idle.seconds == 1200
    assert options == {'host': '127.0.0.1', 'port': 8801, 'path': '/papers', 'stateless_http': False}


PROCESS = '''
import asyncio
from pathlib import Path
import sys
import time
from starlette.responses import StreamingResponse
from scholar_mcp import server

directory = Path(sys.argv[1])

@server.mcp.custom_route('/idle-test', methods=['GET'])
async def stream(request):
    async def chunks():
        yield b'first'
        await asyncio.sleep(2)
        yield b'last'
    return StreamingResponse(chunks())

@server._tool()
def hold() -> dict:
    (directory / 'started').touch()
    time.sleep(2.5)
    (directory / 'finished').touch()
    return {'done': True}

server.main()
'''


@pytest.fixture
def http_process(tmp_path):
    # Every connection belongs to this test process; no shared service is used.
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    env = {**os.environ, 'SCHOLAR_MCP_TRANSPORT': 'http', 'SCHOLAR_MCP_HOST': '127.0.0.1',
           'SCHOLAR_MCP_PORT': str(port), 'SCHOLAR_MCP_PATH': '/mcp', 'SCHOLAR_MCP_STATELESS': '1',
           'SCHOLAR_IDLE_EXIT': '0.005', 'SCHOLAR_DATA_DIR': str(tmp_path), 'SCHOLAR_USAGE_LOG': 'off'}
    with (tmp_path / 'server.log').open('w+') as log:
        process = subprocess.Popen([sys.executable, '-c', PROCESS, str(tmp_path)], cwd=ROOT,
                                   env=env, stdout=log, stderr=log)
        try:
            deadline = time.monotonic() + 15
            while True:
                try:
                    with socket.create_connection(('127.0.0.1', port), timeout=.1):
                        break
                except OSError:
                    if process.poll() is not None or time.monotonic() > deadline:
                        log.seek(0)
                        pytest.fail(log.read())
                    time.sleep(.02)
            yield process, port, tmp_path
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=15)


def test_unused_http_process_exits_cleanly(http_process):
    process, _, _ = http_process
    assert process.wait(timeout=10) == 0


def test_streamed_reply_holds_process_until_the_last_chunk(http_process):
    process, port, _ = http_process
    with closing(http.client.HTTPConnection('127.0.0.1', port, timeout=10)) as client:
        client.request('GET', '/idle-test')
        response = client.getresponse()
        assert response.status == 200 and response.read(5) == b'first'
        time.sleep(1.2)  # Cross an actual Uvicorn idle check while streaming.
        assert process.poll() is None
        assert response.read() == b'last'
    finished = time.monotonic()
    assert process.wait(timeout=10) == 0
    assert time.monotonic() - finished >= .3


@pytest.mark.parametrize('disconnect', [False, True])
def test_mcp_request_waits_for_its_tool_worker_and_reply(http_process, disconnect):
    process, port, directory = http_process
    client = http.client.HTTPConnection('127.0.0.1', port, timeout=10)
    client.request('POST', '/mcp', json.dumps({
        'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {'name': 'hold'}}),
        {'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream'})
    response = client.getresponse()
    assert response.status == 200
    deadline = time.monotonic() + 2
    while not (directory / 'started').exists():
        assert time.monotonic() < deadline
        time.sleep(.01)
    if disconnect:
        response.close()
        client.close()
    time.sleep(1.2)
    assert process.poll() is None and not (directory / 'finished').exists()
    if not disconnect:
        assert b'"done":true' in response.read()
        response.close()
        client.close()
    assert process.wait(timeout=10) == 0
    assert (directory / 'finished').exists()


def test_startup_keeps_models_lazy_and_first_use_imports_them():
    code = '''
import asyncio
import importlib.abc
import sys
from types import SimpleNamespace

attempts = []
heavy = {'torch', 'transformers', 'flashrank', 'keybert', 'model2vec', 'sentence_transformers'}
class Guard(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in heavy:
            attempts.append(fullname)
            raise ImportError('model import blocked by lazy-import test')
sys.meta_path.insert(0, Guard())

from scholar_mcp import cli, server
from fastmcp import Client
async def handshake():
    async with Client(server.mcp) as client:
        assert await client.list_tools()
asyncio.run(handshake())
server.mcp.http_app()
assert not attempts, attempts
assert not heavy.intersection(sys.modules), heavy.intersection(sys.modules)

r = server.relevance
r._gpu_device = None
r._gpu()
sys.modules['torch'] = SimpleNamespace()
try:
    r._gpu_scores('query', [{'title': 'paper'}], 128)
except ImportError:
    pass
del sys.modules['torch']
r._gpu_device = ''
try:
    r._flashrank_scores('query', [{'title': 'paper'}], 128)
except ImportError:
    pass
r._load_keybert()
assert attempts == ['torch', 'transformers', 'flashrank', 'keybert'], attempts
'''
    result = subprocess.run([sys.executable, '-c', code], cwd=ROOT, text=True,
                            capture_output=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
