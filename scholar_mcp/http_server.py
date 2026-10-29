"""Gracefully recycle an idle HTTP process so model runtimes release their memory."""

import asyncio
from contextlib import contextmanager
import logging
import math
import threading
import time


class IdleExit:
    def __init__(self, minutes: float):
        if not math.isfinite(minutes) or minutes < 0:
            raise ValueError("SCHOLAR_IDLE_EXIT must be finite minutes >= 0 (0 disables)")
        self.seconds = minutes * 60
        self.active = 0
        self.last_activity = time.monotonic()
        self._lock = threading.Lock()

    @contextmanager
    def request(self):
        # Tool workers can outlive a disconnected HTTP request. Both hold the
        # same counter, through the end of the worker or the entire ASGI reply.
        with self._lock:
            self.active += 1
            self.last_activity = time.monotonic()
        try:
            yield
        finally:
            with self._lock:
                self.active -= 1
                self.last_activity = time.monotonic()

    def expired(self) -> bool:
        with self._lock:
            return bool(self.seconds and not self.active
                        and time.monotonic() - self.last_activity >= self.seconds)


def run_http(mcp, idle: IdleExit, *, host: str, port: int, path: str, stateless_http: bool):
    import fastmcp
    import uvicorn

    app = mcp.http_app(path=path, stateless_http=stateless_http)

    async def tracked_app(scope, receive, send):
        if scope["type"] == "http":
            with idle.request():
                await app(scope, receive, send)
        elif scope["type"] == "lifespan":
            async def lifespan_send(message):
                if message["type"] == "lifespan.startup.complete":
                    # Count idle time from readiness, excluding app startup.
                    with idle.request():
                        await send(message)
                else:
                    await send(message)
            await app(scope, receive, lifespan_send)
        else:
            await app(scope, receive, send)

    async def check_idle():
        if idle.expired():
            logging.getLogger("uvicorn.error").info("Scholar exiting after %.1f idle minutes", idle.seconds / 60)
            http_server.should_exit = True

    # Use Uvicorn's own tick and graceful drain. Returning from serve exits 0;
    # a supervisor such as launchd KeepAlive can then start a light process.
    # No shutdown deadline: a request admitted at the idle boundary must finish.
    http_server = uvicorn.Server(uvicorn.Config(
        tracked_app, host=host, port=port, lifespan="on", ws="websockets-sansio",
        log_level=fastmcp.settings.log_level.lower(), timeout_graceful_shutdown=None,
        callback_notify=check_idle, timeout_notify=1,
    ))
    asyncio.run(http_server.serve())
