import os
import random

S2_API_KEY: str | None = os.environ.get("S2_API_KEY") or None
S2_API_KEYS: list[str] = [k.strip() for k in (os.environ.get("S2_API_KEYS") or "").split(",") if k.strip()]
CORE_API_KEY: str | None = os.environ.get("CORE_API_KEY") or None
OPENALEX_API_KEY: str | None = os.environ.get("OPENALEX_API_KEY") or None
OPENALEX_EMAIL: str | None = os.environ.get("OPENALEX_EMAIL") or None
OPENREVIEW_USERNAME: str | None = os.environ.get("OPENREVIEW_USERNAME") or None
OPENREVIEW_PASSWORD: str | None = os.environ.get("OPENREVIEW_PASSWORD") or None
DOWNLOAD_DIR: str = os.environ.get("SCHOLAR_DOWNLOAD_DIR", "./downloads")
S2_TIMEOUT: int = int(os.environ.get("S2_TIMEOUT", "30"))
SCIHUB_ENABLED: bool = os.environ.get("SCIHUB_ENABLED", "").lower() in ("1", "true", "yes")


def get_s2_api_key() -> str | None:
    """Get an S2 API key, rotating through pool if multiple are configured."""
    if S2_API_KEYS:
        return random.choice(S2_API_KEYS)
    return S2_API_KEY
