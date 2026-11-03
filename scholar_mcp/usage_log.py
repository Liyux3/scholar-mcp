"""A bounded local record of tool calls, read by health.py.

One JSON line per MCP tool call goes to <data>/usage/calls.jsonl, rotated at
2 MB with one backup, so the record never grows past about 4 MB. By default it
holds no query text or paper ids: the tool, the client, the duration, the size
of the answer, how each source answered, which reranker ranked and where a PDF
came from. SCHOLAR_USAGE_LOG=full adds the arguments; off turns the record off.
"""

import contextvars
import json
import os
import time
import traceback as _traceback
from pathlib import Path

from filelock import FileLock

from . import config

MAX_BYTES = 2_000_000
# Arguments that carry no research content, recorded even without full mode.
PLAIN_ARGUMENTS = {"limit", "year", "intent", "sort", "include", "relation", "pages", "max_hops",
                   "max_papers", "direction", "min_citations", "open_access_only", "debug", "action"}

_current: contextvars.ContextVar[dict | None] = contextvars.ContextVar("scholar_call", default=None)


def mode() -> str:
    value = os.environ.get("SCHOLAR_USAGE_LOG", "on").strip().lower()
    return "off" if value in {"0", "off", "false", "no"} else "full" if value == "full" else "on"


def path() -> Path:
    return Path(config.DATA_DIR) / "usage" / "calls.jsonl"


def _append(line: str) -> None:
    """Append one record. Every MCP client runs its own server on the same file,
    so the size check, the rotation and the write share one lock across processes."""
    target = path()
    target.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(target) + ".lock", timeout=2):
        if target.exists() and target.stat().st_size >= MAX_BYTES:
            os.replace(target, target.with_name(target.name + ".1"))
        with target.open("a", encoding="utf-8") as stream:
            stream.write(line + "\n")


def note(**fields) -> None:
    """Add details to the record of the tool call in progress; a no-op outside one."""
    record = _current.get()
    if record is not None:
        record.update(fields)


FAILED = {"error", "timeout", "blocked"}


def note_source(name: str, status: str, ms: int, count: int, abstracts: float | None = None,
                error: str = "") -> None:
    """Add one source's answer as [status, ms, count, abstract share]; a later answer replaces it."""
    record = _current.get()
    if record is None:
        return
    record.setdefault("sources", {})[name] = [status, ms, count] + ([abstracts] if abstracts is not None else [])
    if error and status in FAILED:
        record.setdefault("errors", {})[name] = error[:120]


def _arguments(arguments: dict) -> dict:
    if mode() == "full":
        return {key: value for key, value in arguments.items() if value not in (None, "")}
    plain = {key: arguments[key] for key in PLAIN_ARGUMENTS & arguments.keys() if arguments[key] not in (None, "")}
    if isinstance(arguments.get("query"), str):
        plain["query_words"] = len(arguments["query"].split())
    return plain


def _answer(data) -> dict:
    if not isinstance(data, dict):
        return {}
    size = next((len(data[key]) for key in ("results", "papers", "authors", "nodes", "citations")
                 if isinstance(data.get(key), list)), None)
    return {key: value for key, value in (
        ("n", size), ("chars", len(data["content"]) if isinstance(data.get("content"), str) else None),
        ("failed", "error" in data or None)) if value is not None}


class call:
    """Record one tool call: `with usage_log.call(tool, client, arguments) as record:`."""

    def __init__(self, tool: str, client: str, arguments: dict):
        self.record = {"tool": tool, "client": client, "args": _arguments(arguments)}

    def __enter__(self) -> dict:
        self.started = time.monotonic()
        self.record["at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self.token = _current.set(self.record)
        return self.record

    def answered(self, data) -> None:
        self.record.update(_answer(data))

    def __exit__(self, exc_type, exc, traceback) -> bool:
        _current.reset(self.token)
        if mode() == "off":
            return False
        self.record["ms"] = int((time.monotonic() - self.started) * 1000)
        if exc_type is not None:
            self.record["exception"] = exc_type.__name__
            self.record["where"] = _where(traceback)
        try:
            _append(json.dumps(self.record, ensure_ascii=False, default=str))
        except Exception:  # the record must never break the call it describes
            pass
        return False


def _where(traceback) -> str:
    """The deepest frame inside this package, as module.py:line, which is where a fix starts."""
    frames = [frame for frame in _traceback.extract_tb(traceback) if "scholar_mcp" in frame.filename]
    return f"{Path(frames[-1].filename).name}:{frames[-1].lineno}" if frames else ""


def read(limit: int = 500) -> list[dict]:
    """The most recent records, oldest first, across the live file and its backup."""
    rows = []
    for part in (path().with_name(path().name + ".1"), path()):
        try:
            lines = part.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                rows.append(json.loads(line))
            except ValueError:  # a damaged line costs only itself
                continue
    return rows[-limit:]
