import os

S2_API_KEY: str | None = os.environ.get("S2_API_KEY") or None
CORE_API_KEY: str | None = os.environ.get("CORE_API_KEY") or None
OPENREVIEW_USERNAME: str | None = os.environ.get("OPENREVIEW_USERNAME") or None
OPENREVIEW_PASSWORD: str | None = os.environ.get("OPENREVIEW_PASSWORD") or None
DOWNLOAD_DIR: str = os.environ.get("SCHOLAR_DOWNLOAD_DIR", "./downloads")
S2_TIMEOUT: int = int(os.environ.get("S2_TIMEOUT", "30"))
SCIHUB_ENABLED: bool = os.environ.get("SCIHUB_ENABLED", "").lower() in ("1", "true", "yes")
