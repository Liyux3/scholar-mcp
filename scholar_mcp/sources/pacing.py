"""Request pacing for every provider, shared by all Scholar processes on a machine.

Each provider's published limits live in LIMITS, so every caller obeys them:
searches, citation lookups, PDF resolution and metadata completion alike. A
limit is N requests in any T seconds. Providers count per identity, the
calling address when no key is sent and the key when one is, so a keyed
request is counted under that key's own slot and with the key's limits.

A slot is a small file holding the start times of the identity's recent
requests, shared by every process on the machine. A request may start once,
for each limit, its N-th most recent start is T seconds old. A caller reserves
its start under a file lock and sleeps outside it, so a waiting caller never
holds up others reserving theirs. When the start is further off than the
caller will wait, nothing is reserved and Paced is raised: a spent hourly or
daily budget skips that source instead of stalling the search.

Machines behind one address (a lab, a company) share that address's limits
but not these files. They should share one Scholar server
(`scholar-mcp serve --http`), or keep the slots in a shared store; `_reserve`
is the whole contract such a store has to honour.
"""
import hashlib
import math
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from urllib.parse import urlsplit
from pathlib import Path

from filelock import FileLock

from .. import config

Limits = tuple[tuple[int, float], ...]  # (requests, seconds), all must hold

# provider: (without a key, with a key). Sources: internal/docs/RATE_LIMITS.md.
# Where nothing is published, one request per interval keeps us polite.
LIMITS: dict[str, tuple[Limits, Limits]] = {
    "ads": (((1, 0.2), (5000, 86400)), ((1, 0.2), (5000, 86400))),  # 5000 a day per token
    "arxiv": (((1, 3.0),), ((1, 3.0),)),                  # one every 3 s, all machines together
    "arxivgg": (((1, 0.5),), ((1, 0.5),)),                # a third-party service, nothing published
    "cinii": (((1, 1.0),), ((1, 1.0),)),                  # bursts "may be blocked without notice"
    "core": (((10, 60), (100, 86400)), ((25, 60),)),      # tokens; personal key 25 a minute
    "eric": (((1, 1.0),), ((1, 1.0),)),                  # conservative while no published limit is specified
    "courtlistener": (((1, 1.0),), ((1, 1.0),)),
    "clinicaltrials": (((1, 1.0),), ((1, 1.0),)),
    "openedition": (((1, 1.0),), ((1, 1.0),)),
    "crossref": (((1, 1.0),), ((3, 1.0),)),               # list queries; the key is the polite-pool email
    "crossref-record": (((5, 1.0),), ((10, 1.0),)),       # single records
    "dblp": (((1, 3.0),), ((1, 3.0),)),                   # "fine" at 1 to 2 s; 429 seen at 2 s
    "doaj": (((2, 1.0),), ((2, 1.0),)),
    "europepmc": (((1, 0.2),), ((1, 0.2),)),              # nothing published
    "exa": (((10, 1.0),), ((10, 1.0),)),                  # per team, across its keys
    "google-scholar": (((1, 2.0),), ((1, 2.0),)),         # nothing published; bursts draw a captcha
    "hal": (((1, 0.2),), ((1, 0.2),)),                    # nothing published
    "inspirehep": (((15, 5.0),), ((15, 5.0),)),
    # The institution's licence, not a provider API: publishers flag systematic
    # downloading through a library proxy, so reads through it stay few and spaced.
    "library-proxy": (((1, 3.0), (20, 3600), (150, 86400)),) * 2,
    "lens": (((10, 60),), ((10, 60),)),                   # plan-dependent; a library guide gives 10 a minute
    "ncbi": (((1, 0.34),), ((1, 0.1),)),                  # 3 a second, 10 with an API key
    "openaire": (((60, 3600),), ((7200, 3600),)),
    "openalex": (((10, 1.0),), ((30, 1.0),)),
    "openalex-semantic": (((1, 1.0),), ((1, 1.0),)),
    "openreview": (((180, 60),), ((180, 60),)),           # from its rate-limit headers
    "scopus": (((9, 1.0),), ((9, 1.0),)),
    "semantic-scholar": (((1, 1.05),), ((1, 1.05),)),     # a key gets 1 a second; keyless is one global pool
    "springer": (((1, 0.6),), ((1, 0.6),)),               # 100 a minute on the basic plan
    "wos": (((1, 1.0),), ((1, 1.0),)),
    "zbmath": (((1, 0.25),), ((1, 0.25),)),               # "a reasonable rate"
    "zenodo": (((30, 60),), ((30, 60),)),                 # search endpoints
}

MAX_WAIT = 30.0
_sleep = time.sleep  # tests replace it


class Paced(RuntimeError):
    """The provider's limit leaves no slot within the caller's wait."""


def state_dir() -> Path:
    return Path(config.DATA_DIR) / "pace"


def _slot(provider: str, key: str | None) -> str:
    return provider if not key else f"{provider}.{hashlib.sha256(key.encode()).hexdigest()[:10]}"


def _reserve(starts: list[float], limits: Limits, now: float, max_wait: float) -> float | None:
    """The earliest start that keeps every limit, appended to `starts`; None if too far."""
    start = max([now] + [starts[-n] + seconds for n, seconds in limits if len(starts) >= n])
    if start - now > max_wait:
        return None
    starts.append(start)
    del starts[:-max(n for n, _ in limits)]
    return start


_locks: dict[str, threading.Lock] = {}
_deadline: ContextVar[float | None] = ContextVar('scholar_acquisition_deadline', default=None)
_transfer: ContextVar[object] = ContextVar('scholar_transfer_observer', default=None)
_download_slots: dict[str, threading.BoundedSemaphore] = {}


@contextmanager
def transfers(observer):
    """Let a batch account for streaming bytes and cancel at chunk boundaries."""
    token = _transfer.set(observer)
    try:
        yield
    finally:
        _transfer.reset(token)


def checkpoint(size: int = 0) -> None:
    if remaining() == 0:
        raise Paced('acquisition budget finished')
    if observer := _transfer.get():
        observer(size)


@contextmanager
def download_slot(url: str):
    """Bound simultaneous file transfers to one host, including batch workers."""
    gate = _download_slots.setdefault(urlsplit(url).hostname or '', threading.BoundedSemaphore(2))
    while not gate.acquire(timeout=0.2):
        checkpoint()
    try:
        checkpoint()
        yield
    finally:
        gate.release()


def remaining(default: float | None = None) -> float | None:
    deadline = _deadline.get()
    if deadline is None:
        return default
    seconds = max(0.0, deadline - time.monotonic())
    return min(default, seconds) if default is not None else seconds


@contextmanager
def budget(seconds: float):
    """Share one acquisition deadline across nested stages and worker contexts."""
    token = _deadline.set(time.monotonic() + remaining(seconds))
    try:
        yield
    finally:
        _deadline.reset(token)


def wait(provider: str, key: str | None = None, max_wait: float = MAX_WAIT) -> None:
    """Sleep until this request may start. Raise Paced, reserving nothing, when
    that is more than `max_wait` seconds away."""
    checkpoint()
    max_wait = remaining(max_wait)
    anonymous, keyed = LIMITS[provider]
    limits, name = keyed if key else anonymous, _slot(provider, key)
    folder = state_dir()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    with _locks.setdefault(name, threading.Lock()), FileLock(str(path) + ".lock", timeout=10):
        now = time.time()
        try:
            starts = [float(line) for line in path.read_text().split()]
        except (OSError, ValueError):
            starts = []
        hint = path.with_name(path.name + ".retry-after")
        try:
            until = float(hint.read_text())
        except (OSError, ValueError):
            until = 0
        if until <= now:
            hint.unlink(missing_ok=True)
        if until - now > max_wait:
            raise Paced(f"{provider} Retry-After: next request in {until - now:.0f} s")
        earliest = max(now, until)
        start = _reserve(starts, limits, earliest, max_wait - (earliest - now))
        if start is None:
            n, seconds = next((n, s) for n, s in limits if len(starts) >= n and starts[-n] + s - now > max_wait)
            raise Paced(f"{provider} allows {n} requests per {seconds:g} s; next slot in {starts[-n] + seconds - now:.0f} s")
        path.write_text("\n".join(f"{t:.3f}" for t in starts))
    if start > now:
        _sleep(start - now)


def defer(provider: str, seconds: float, key: str | None = None) -> None:
    """Share an actual provider Retry-After without extending verification failures."""
    if not math.isfinite(seconds) or seconds <= 0:
        return
    folder = state_dir()
    folder.mkdir(parents=True, exist_ok=True)
    name = _slot(provider, key)
    path = folder / (name + ".retry-after")
    with _locks.setdefault(name, threading.Lock()), FileLock(str(folder / name) + ".lock", timeout=10):
        try:
            previous = float(path.read_text())
        except (OSError, ValueError):
            previous = 0
        path.write_text(str(max(previous, time.time() + seconds)))
