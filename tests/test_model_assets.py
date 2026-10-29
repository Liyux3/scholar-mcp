"""Integrity, recovery and deployment boundaries for the shared local reader."""

from contextlib import contextmanager
import hashlib
from pathlib import Path
import time
import zipfile

import httpx
import pytest

from scholar_mcp import config, model_assets as assets


@pytest.fixture
def archive(tmp_path, monkeypatch):
    contents = {"model.onnx": b"model bytes", "tokenizer.json": b'{"test": true}'}
    source = tmp_path / "model.zip"
    with zipfile.ZipFile(source, "w") as bundle:
        for name, value in contents.items():
            bundle.writestr(f"test-reader/{name}", value)
        bundle.writestr("../outside", b"not a model asset")
    monkeypatch.setattr(assets, "CPU_MODEL", "test-reader")
    monkeypatch.setattr(assets, "FILES", {name: (len(value), hashlib.sha256(value).hexdigest())
                                          for name, value in contents.items()})
    monkeypatch.setattr(assets, "ARCHIVE_BYTES", source.stat().st_size)
    monkeypatch.setattr(assets, "ARCHIVE_SHA256", hashlib.sha256(source.read_bytes()).hexdigest())
    monkeypatch.delenv("HF_ENDPOINT", raising=False)
    return source


def test_incomplete_cache_is_repaired_and_complete_cache_is_reused(tmp_path, monkeypatch, archive):
    cache = tmp_path / "cache"
    broken = cache / assets.CPU_MODEL
    broken.mkdir(parents=True)
    (broken / "model.onnx").write_bytes(b"truncated")
    directory = assets.prepare_cpu_model(cache, archive=archive)
    assert assets.ready(directory)
    assert (directory / "NOTICE").is_file() and not (tmp_path / "outside").exists()
    monkeypatch.setattr(assets, "_download", lambda *a: pytest.fail("complete cache attempted a download"))
    assert assets.prepare_cpu_model(cache) == directory
    assert not list(cache.glob('.reader-*'))


def test_bad_archive_and_failed_promotion_preserve_previous_assets(tmp_path, monkeypatch, archive):
    cache = tmp_path / "cache"
    previous = cache / assets.CPU_MODEL
    previous.mkdir(parents=True)
    marker = previous / "model.onnx"
    marker.write_bytes(b"previous incomplete model")
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"interrupted download")
    with pytest.raises(ValueError, match="checksum"):
        assets.prepare_cpu_model(cache, archive=bad)
    assert marker.read_bytes() == b"previous incomplete model"
    replace = assets.os.replace
    def fail_publish(source, target):
        if Path(source).name == assets.CPU_MODEL and Path(source).parent.name.startswith('.reader-'):
            raise OSError("publish failed")
        replace(source, target)
    monkeypatch.setattr(assets.os, "replace", fail_publish)
    with pytest.raises(OSError, match="publish failed"):
        assets.prepare_cpu_model(cache, archive=archive)
    assert marker.read_bytes() == b"previous incomplete model"
    assert not list(cache.glob('.reader-*'))


def test_download_checks_bytes_and_respects_an_explicit_endpoint(tmp_path, monkeypatch, archive):
    calls = []
    @contextmanager
    def stream(method, url, **kwargs):
        calls.append((url, kwargs['timeout']))
        status = 503 if len(calls) == 1 else 200
        yield httpx.Response(status, content=archive.read_bytes(), request=httpx.Request(method, url))
    monkeypatch.setattr(httpx, "stream", stream)
    target = tmp_path / "download.zip"
    assets._download(target, time.monotonic() + 30)
    assert target.read_bytes() == archive.read_bytes()
    assert [httpx.URL(url).host for url, _ in calls] == ['huggingface.co', 'hf-mirror.com']
    assert all(timeout.connect and timeout.read for _, timeout in calls)
    calls.clear()
    monkeypatch.setenv("HF_ENDPOINT", "https://private-value@configured.test")
    with pytest.raises(RuntimeError) as failure:
        assets._download(target, time.monotonic() + 30)
    assert 'private-value' not in str(failure.value)
    assert len(calls) == 1 and httpx.URL(calls[0][0]).host == 'configured.test'


def test_bundled_reader_is_available_without_writing_the_bundle(tmp_path, monkeypatch, archive):
    package = tmp_path / 'package'
    package.mkdir()
    monkeypatch.setattr(assets, '__file__', str(package / 'model_assets.py'))
    monkeypatch.setattr(config, 'DATA_DIR', str(tmp_path / 'userdata'))
    bundled = assets.prepare_cpu_model(package / '_models', archive=archive)
    monkeypatch.setattr(assets, '_download', lambda *a: pytest.fail('bundled reader attempted a download'))
    assert assets.cpu_model_directory() == bundled
    assert not (Path(config.DATA_DIR) / 'models').exists()
    user_copy = assets.prepare_cpu_model(Path(config.DATA_DIR) / 'models', archive=archive)
    assert assets.cpu_model_directory() == user_copy
