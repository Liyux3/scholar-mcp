import os
import random

S2_API_KEY: str | None = os.environ.get("S2_API_KEY") or None
S2_API_KEYS: list[str] = [k.strip() for k in (os.environ.get("S2_API_KEYS") or "").split(",") if k.strip()]
CORE_API_KEY: str | None = os.environ.get("CORE_API_KEY") or None
OPENALEX_API_KEY: str | None = os.environ.get("OPENALEX_API_KEY") or None
OPENALEX_EMAIL: str | None = os.environ.get("OPENALEX_EMAIL") or None
OPENALEX_EMAILS: list[str] = [e.strip() for e in (os.environ.get("OPENALEX_EMAILS") or "").split(",") if e.strip()]
OPENREVIEW_USERNAME: str | None = os.environ.get("OPENREVIEW_USERNAME") or None
OPENREVIEW_PASSWORD: str | None = os.environ.get("OPENREVIEW_PASSWORD") or None
DASHSCOPE_API_KEY: str | None = os.environ.get("DASHSCOPE_API_KEY") or None
EXA_API_KEY: str | None = os.environ.get("EXA_API_KEY") or None
SCOPUS_API_KEY: str | None = os.environ.get("SCOPUS_API_KEY") or None
DOWNLOAD_DIR: str = os.environ.get("SCHOLAR_DOWNLOAD_DIR", "./downloads")
S2_TIMEOUT: int = int(os.environ.get("S2_TIMEOUT", "30"))
SCIHUB_ENABLED: bool = os.environ.get("SCIHUB_ENABLED", "").lower() in ("1", "true", "yes")
RANK_PARAMS_PATH: str = os.path.expanduser("~/.scholar-mcp/rank_params.json")


def get_s2_api_key() -> str | None:
    if S2_API_KEYS:
        return random.choice(S2_API_KEYS)
    return S2_API_KEY


def get_openalex_email() -> str | None:
    if OPENALEX_EMAILS:
        return random.choice(OPENALEX_EMAILS)
    return OPENALEX_EMAIL
