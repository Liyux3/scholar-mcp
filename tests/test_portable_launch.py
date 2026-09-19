"""Launcher contracts without changing the developer's Python or uv installation."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(os.name == "nt" or not shutil.which("sh"), reason="POSIX launcher")
def test_launcher_preserves_arguments_and_uses_managed_python(tmp_path):
    stub = tmp_path / "fake uv"
    stub.write_text(f'#!{sys.executable}\nimport sys,json\nprint(json.dumps(sys.argv[1:]))\n')
    stub.chmod(0o700)
    result = subprocess.run(["sh", str(ROOT / "scripts/launch.sh"), "sources", "--query", "a query with spaces"],
                            env={**os.environ, "SCHOLAR_UV": str(stub), "SCHOLAR_PACKAGE": "scholar-mcp[rerank]==0.8.5"},
                            text=True, capture_output=True, check=True)
    assert json.loads(result.stdout) == ["tool", "run", "--no-config", "--managed-python", "--python", "3.12",
                                       "--from", "scholar-mcp[rerank]==0.8.5", "scholar-mcp", "sources", "--query", "a query with spaces"]


def test_windows_launcher_uses_the_same_runtime_contract():
    script = (ROOT / "scripts/launch.ps1").read_text()
    assert '--managed-python --python $runtimePython' in script
    assert 'cpython-3.12-windows-aarch64-none' in script
    assert "UV_UNMANAGED_INSTALL" in script
    assert "scholar-mcp @args" in script
    command = (ROOT / "scripts/launch.cmd").read_text()
    assert "-ExecutionPolicy Bypass" in command
    assert "Set-ExecutionPolicy" not in script + command


@pytest.mark.skipif(os.name == "nt" or not shutil.which("sh"), reason="POSIX bootstrap")
def test_missing_uv_bootstraps_once_without_polluting_mcp_stdout(tmp_path):
    commands = tmp_path / "commands"
    commands.mkdir()
    for command in ("uname", "mkdir", "mktemp", "rm", "sh", "cp", "chmod"):
        (commands / command).symlink_to(shutil.which(command))
    fake_uv = tmp_path / "uv fixture"
    fake_uv.write_text(f'#!{sys.executable}\nimport json,sys\nprint(json.dumps(sys.argv[1:]))\n')
    fake_uv.chmod(0o700)
    counter = tmp_path / "downloads"
    downloader = commands / "curl"
    installer = '#!/bin/sh\nmkdir -p "$UV_UNMANAGED_INSTALL"\ncp "$SCHOLAR_UV_FIXTURE" "$UV_UNMANAGED_INSTALL/uv"\nchmod +x "$UV_UNMANAGED_INSTALL/uv"\necho "installer output"\n'
    downloader.write_text(f'#!{sys.executable}\nimport pathlib,sys\npathlib.Path({str(counter)!r}).open("a").write("download\\n")\npathlib.Path(sys.argv[-1]).write_text({installer!r})\n')
    downloader.chmod(0o700)
    environment = {**os.environ, "PATH": str(commands), "SCHOLAR_UV": "",
                   "SCHOLAR_RUNTIME_DIR": str(tmp_path / "runtime with spaces"), "SCHOLAR_UV_FIXTURE": str(fake_uv),
                   "SCHOLAR_PACKAGE": "scholar-mcp[rerank]"}
    for _ in range(2):
        result = subprocess.run([shutil.which("sh"), str(ROOT / "scripts/launch.sh"), "--help"],
                                env=environment, text=True, capture_output=True, check=True)
        assert json.loads(result.stdout)[-2:] == ["scholar-mcp", "--help"]
    assert counter.read_text().splitlines() == ["download"]
