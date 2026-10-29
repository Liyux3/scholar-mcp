"""Verified local-reader assets shared by runtime preparation and desktop builds."""

import hashlib
import os
from pathlib import Path
import shutil
import tempfile
import time
import zipfile

from filelock import FileLock

from . import config

CPU_MODEL = "ms-marco-MiniLM-L-12-v2"
REVISION = "858a1ac046a05663a35367eac852d7f76feeefdd"
ARCHIVE_BYTES = 22_696_961
ARCHIVE_SHA256 = "bdd3772b651ffc34f70e414049285bb55ccc6d1b8e29d0640f836d44f70ec77a"
FILES = {
    "flashrank-MiniLM-L-12-v2_Q.onnx": (34_004_051, "d3dd7b09fcf06b0c070081d6819b5effbb40b667bcad78b2d7543400271141d1"),
    "config.json": (787, "2144195e107cd7ea61556478e7add12986ebfbc3085f924fc0b90c2410604879"),
    "tokenizer_config.json": (525, "eaf913a687e8eed3010b6479d0d84631038212aa50c14d9b812e08cbc891d925"),
    "special_tokens_map.json": (112, "303df45a03609e4ead04bc3dc1536d0ab19b5358db685b6f3da123d05ec200e3"),
    "tokenizer.json": (466_062, "ce64fce797c24f68df90b40a3f74f579b336a493db14bd583fd520ea0d8c9a98"),
}
MODEL_NOTICE = f"""Local CPU reader weights: {CPU_MODEL}
Author: prithivida (https://huggingface.co/prithivida/flashrank)
Source revision: {REVISION}
License: Creative Commons Attribution-ShareAlike 4.0 International
https://creativecommons.org/licenses/by-sa/4.0/
The quantized weights and tokenizer are redistributed unchanged from the source
archive. Scholar MCP's software remains under its separate Apache-2.0 license.
"""


def _digest(path: Path) -> str:
    checksum = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1_048_576), b""):
            checksum.update(chunk)
    return checksum.hexdigest()


def ready(directory: Path) -> bool:
    """Check complete bytes, including older caches without a preparation receipt."""
    try:
        return all((directory / name).stat().st_size == size and _digest(directory / name) == digest
                   for name, (size, digest) in FILES.items())
    except OSError:
        return False


def cached_cpu_model_directory() -> Path | None:
    for base in (Path(config.DATA_DIR) / "models", Path(__file__).with_name("_models")):
        directory = base / CPU_MODEL
        if ready(directory):
            return directory
    return None


def _download(target: Path, deadline: float) -> None:
    import httpx

    configured = os.environ.get("HF_ENDPOINT", "").rstrip("/")
    endpoints = [configured] if configured else ["https://huggingface.co", "https://hf-mirror.com"]
    failure = "download deadline reached"
    for position, endpoint in enumerate(endpoints):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        route_deadline = time.monotonic() + remaining / (len(endpoints) - position)
        url = f"{endpoint}/prithivida/flashrank/resolve/{REVISION}/{CPU_MODEL}.zip"
        try:
            checksum, count = hashlib.sha256(), 0
            timeout = httpx.Timeout(min(15, remaining), connect=min(10, remaining))
            with httpx.stream("GET", url, timeout=timeout, follow_redirects=True) as response:
                response.raise_for_status()
                with target.open("wb") as output:
                    for chunk in response.iter_bytes(131_072):
                        if time.monotonic() >= route_deadline:
                            raise TimeoutError("model download deadline reached")
                        count += len(chunk)
                        if count > ARCHIVE_BYTES:
                            raise ValueError("model archive exceeds its declared size")
                        checksum.update(chunk)
                        output.write(chunk)
            if count != ARCHIVE_BYTES or checksum.hexdigest() != ARCHIVE_SHA256:
                raise ValueError("model archive checksum mismatch")
            return
        except (httpx.HTTPError, OSError, ValueError) as error:
            # Transport exceptions can include proxy or endpoint credentials.
            failure = type(error).__name__
    raise RuntimeError(f"Local reader preparation failed ({failure}); existing assets were preserved")


def prepare_cpu_model(cache: Path, *, archive: Path | None = None, timeout: float = 90) -> Path:
    """Serialize preparation and publish only verified, complete model directories."""
    cache = Path(cache)
    cache.mkdir(parents=True, exist_ok=True)
    destination = cache / CPU_MODEL
    deadline = time.monotonic() + timeout
    with FileLock(str(cache / f".{CPU_MODEL}.lock"), timeout=timeout):
        if ready(destination):
            return destination
        with tempfile.TemporaryDirectory(prefix=".reader-", dir=cache) as temporary:
            staging = Path(temporary)
            source = Path(archive) if archive else staging / "model.zip"
            if archive is None:
                _download(source, deadline)
            if source.stat().st_size != ARCHIVE_BYTES or _digest(source) != ARCHIVE_SHA256:
                raise ValueError("Local reader archive checksum mismatch")
            prepared = staging / CPU_MODEL
            prepared.mkdir()
            with zipfile.ZipFile(source) as bundle:
                # Select known members rather than extracting archive paths or metadata.
                for name in FILES:
                    with bundle.open(f"{CPU_MODEL}/{name}") as incoming, (prepared / name).open("wb") as output:
                        shutil.copyfileobj(incoming, output)
            if not ready(prepared):
                raise ValueError("Local reader files failed integrity validation")
            (prepared / "NOTICE").write_text(MODEL_NOTICE, encoding="utf-8")
            previous = staging / "previous"
            if destination.exists() or destination.is_symlink():
                os.replace(destination, previous)
            try:
                os.replace(prepared, destination)
            except OSError:
                if previous.exists() or previous.is_symlink():
                    os.replace(previous, destination)
                raise
    return destination


def cpu_model_directory() -> Path:
    return cached_cpu_model_directory() or prepare_cpu_model(Path(config.DATA_DIR) / "models")
