import os
import random

S2_API_KEY: str | None = os.environ.get("S2_API_KEY") or None
S2_API_KEYS: list[str] = [k.strip() for k in (os.environ.get("S2_API_KEYS") or "").split(",") if k.strip()]
CORE_API_KEY: str | None = os.environ.get("CORE_API_KEY") or None
OPENALEX_API_KEY: str | None = os.environ.get("OPENALEX_API_KEY") or None
OPENALEX_API_KEYS: list[str] = [k.strip() for k in (os.environ.get("OPENALEX_API_KEYS") or "").split(",") if k.strip()]
OPENALEX_EMAIL: str | None = os.environ.get("OPENALEX_EMAIL") or None
OPENALEX_EMAILS: list[str] = [e.strip() for e in (os.environ.get("OPENALEX_EMAILS") or "").split(",") if e.strip()]
OPENREVIEW_USERNAME: str | None = os.environ.get("OPENREVIEW_USERNAME") or None
OPENREVIEW_PASSWORD: str | None = os.environ.get("OPENREVIEW_PASSWORD") or None
DASHSCOPE_API_KEY: str | None = os.environ.get("DASHSCOPE_API_KEY") or None
EXA_API_KEY: str | None = os.environ.get("EXA_API_KEY") or None
SCOPUS_API_KEY: str | None = os.environ.get("SCOPUS_API_KEY") or None
DATA_DIR: str = os.path.expanduser(
    os.environ.get("SCHOLAR_DATA_DIR", "~/.scholar-mcp")
)
DOWNLOAD_DIR: str = os.path.expanduser(
    os.environ.get("SCHOLAR_DOWNLOAD_DIR", os.path.join(DATA_DIR, "papers"))
)
KB_DIR: str = os.path.expanduser(
    os.environ.get("SCHOLAR_KB_DIR", os.path.join(DATA_DIR, "kb"))
)
VAULT_DIR: str = os.path.expanduser(
    os.environ.get("SCHOLAR_VAULT_DIR", os.path.join(DATA_DIR, "vault"))
)
S2_TIMEOUT: int = int(os.environ.get("S2_TIMEOUT", "30"))
# Wall-clock budget for the whole parallel source fan-out. Sources still
# answering when it expires are dropped and reported as timed out, so one slow
# API cannot set the latency for the rest of the fleet.
SOURCE_BUDGET_S: float = float(os.environ.get("SCHOLAR_SOURCE_BUDGET_S", "8"))
SCIHUB_ENABLED: bool = os.environ.get("SCIHUB_ENABLED", "").lower() in ("1", "true", "yes")
RANK_PARAMS_PATH: str = os.path.expanduser("~/.scholar-mcp/rank_params.json")
# S2 recommendation candidate pool. "recent" restricts to papers from the last
# 60 days, which returns nothing for any seed older than that: a 2017 paper
# gets 0 recommendations. "all-cs" covers computer science across all time and
# is the right default for a literature tool. Non-CS users can set "recent",
# the only other value the API accepts.
S2_RECOMMEND_POOL: str = os.environ.get("S2_RECOMMEND_POOL", "all-cs")


def get_s2_api_key() -> str | None:
    if S2_API_KEYS:
        return random.choice(S2_API_KEYS)
    return S2_API_KEY


def get_openalex_email() -> str | None:
    if OPENALEX_EMAILS:
        return random.choice(OPENALEX_EMAILS)
    return OPENALEX_EMAIL


# Keys observed to be out of credit, with the time their quota resets.
# OpenAlex bills per request against a per-key daily allowance that refills on
# a rolling window, so an exhausted key is temporary, not dead.
_openalex_exhausted: dict[str, float] = {}


def mark_openalex_exhausted(key: str, reset_after_s: float = 0) -> None:
    """Take a key out of rotation until its quota refills.

    Without this, random selection keeps picking keys that are known to be
    empty, and each pick costs a wasted round trip before the retry finds a
    working one.
    """
    import time
    _openalex_exhausted[key] = time.time() + (reset_after_s or 3600)


def get_openalex_api_key() -> str | None:
    if not OPENALEX_API_KEYS:
        return OPENALEX_API_KEY

    import time
    now = time.time()
    usable = [k for k in OPENALEX_API_KEYS if _openalex_exhausted.get(k, 0) <= now]
    # If everything is exhausted, try anyway: the reset estimate may be stale
    # and a 429 is no worse than refusing to make the request.
    return random.choice(usable or OPENALEX_API_KEYS)
