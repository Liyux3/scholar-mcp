"""Release metadata and distribution contracts."""

import json
from pathlib import Path
import re
from urllib.parse import parse_qs, urlparse
import base64
import importlib.util

import pytest

import yaml

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10; provided by pytest's dependencies.
    import tomli as tomllib

from scholar_mcp import __version__

ROOT = Path(__file__).resolve().parents[1]
CONSTRAINTS_URL = f"https://github.com/Liyux3/scholar-mcp/releases/download/v{__version__}/runtime-constraints.txt"
INSTALL_ARGS = ["--python", "3.12", "--constraints", CONSTRAINTS_URL,
                "--from", f"scholar-mcp[rerank]=={__version__}", "scholar-mcp"]


def _json(path: str):
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def test_release_versions_are_synchronized():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]
    version = project["version"]
    assert version == __version__
    assert _json("server.json")["version"] == version
    assert _json("server.json")["packages"][0]["version"] == version
    assert _json("manifest.json")["version"] == version
    assert _json("plugins/scholar-mcp/.codex-plugin/plugin.json")["version"] == version
    assert _json("plugins/scholar-mcp/.claude-plugin/plugin.json")["version"] == version
    assert _json("plugins/scholar-mcp/plugin.json")["version"] == version
    assert _json(".claude-plugin/marketplace.json")["plugins"][0]["version"] == version
    cursor = _json(".cursor-plugin/marketplace.json")
    assert cursor["metadata"]["version"] == version
    assert cursor["plugins"][0]["version"] == version
    assert yaml.safe_load((ROOT / "CITATION.cff").read_text())["version"] == version
    smithery = yaml.safe_load((ROOT / "smithery.yaml").read_text())
    assert f"scholar-mcp[rerank]=={version}" in smithery["startCommand"]["commandFunction"]

    for path in (
        "plugins/scholar-mcp/.mcp.json",
        "plugins/scholar-mcp/mcp.json",
    ):
        args = _json(path)["mcpServers"]["scholar"]["args"]
        assert f"scholar-mcp[rerank]=={version}" in args
        assert args[:2] == ["--python", "3.12"]
        assert args == INSTALL_ARGS


def test_registry_and_release_workflows_are_wired():
    server = _json("server.json")
    package = server["packages"][0]
    assert server["$schema"].endswith("2025-12-11/server.schema.json")
    assert server["name"] == "io.github.Liyux3/scholar-mcp"
    assert package["registryType"] == "pypi"
    assert package["runtimeHint"] == "uvx"
    assert package["runtimeArguments"] == [
        {"type": "named", "name": "--constraints", "value": CONSTRAINTS_URL},
        {"type": "named", "name": "--python", "value": "3.12"},
        {"type": "named", "name": "--with", "value": f"scholar-mcp[rerank]=={__version__}"},
    ]

    publish = (ROOT / ".github/workflows/publish.yml").read_text(encoding="utf-8")
    assert "release:" in publish
    assert "scripts/smoke_release.py" in publish
    assert "mcp-publisher validate server.json" in publish
    assert "mcp-publisher publish server.json" in publish
    assert "pypa/gh-action-pypi-publish" in publish

    docker = (ROOT / ".github/workflows/docker.yml").read_text(encoding="utf-8")
    assert "linux/amd64,linux/arm64" in docker
    assert "ghcr.io/liyux3/scholar-mcp" in docker
    assert "scripts/smoke_container.py" in docker

    mcpb = (ROOT / ".github/workflows/mcpb.yml").read_text(encoding="utf-8")
    assert "scripts/build_mcpb.sh" in mcpb
    assert "scripts/smoke_mcpb.py" in mcpb
    bundle = (ROOT / "scripts/build_mcpb.sh").read_text(encoding="utf-8")
    assert "--extra rerank" in bundle
    manifest = _json("manifest.json")
    assert manifest["server"]["type"] == "binary"
    assert manifest["server"]["mcp_config"]["command"] == "${__dirname}/runtime/bin/python3.12"
    assert "runtimes" not in manifest["compatibility"]
    assert "UV_PYTHON_INSTALL_DIR" in bundle and "symlinks=False" in bundle


def test_readme_contains_valid_one_click_install_urls():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "<!-- mcp-name: io.github.Liyux3/scholar-mcp -->" in readme

    vscode_match = re.search(r"https://vscode\.dev/redirect/mcp/install\?[^\"]+", readme)
    assert vscode_match is not None
    vscode = parse_qs(urlparse(vscode_match.group().replace("&amp;", "&")).query)
    assert vscode["name"] == ["scholar-mcp"]
    assert json.loads(vscode["config"][0]) == {
        "type": "stdio",
        "command": "uvx",
        "args": INSTALL_ARGS,
    }

    cursor_match = re.search(r"cursor://anysphere\.cursor-deeplink/mcp/install\?[^\"]+", readme)
    assert cursor_match is not None
    cursor = parse_qs(urlparse(cursor_match.group().replace("&amp;", "&")).query)
    assert cursor["name"] == ["scholar"]
    assert json.loads(base64.b64decode(cursor["config"][0])) == {
        "scholar": {"command": "uvx", "args": INSTALL_ARGS}
    }

    kiro_match = re.search(r"https://kiro\.dev/launch/mcp/add\?[^\"]+", readme)
    assert kiro_match is not None
    kiro = parse_qs(urlparse(kiro_match.group().replace("&amp;", "&")).query)
    assert kiro["name"] == ["scholar-mcp"]
    assert json.loads(kiro["config"][0]) == {
        "command": "uvx",
        "args": INSTALL_ARGS,
        "disabled": False,
        "autoApprove": [],
    }


def test_runtime_constraints_require_both_native_wheels_and_pin_hashes(tmp_path):
    from packaging.requirements import Requirement
    spec = importlib.util.spec_from_file_location("runtime_constraints", ROOT / "scripts/runtime_constraints.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with pytest.raises(ValueError, match="Both"):
        module.constraints(tmp_path, __version__)
    for platform in ("macosx_11_0_x86_64", "win_arm64"):
        (tmp_path / f"cryptography-50.0.1-cp311-abi3-{platform}.whl").write_bytes(b"fixture")
    lines = module.constraints(tmp_path, __version__).splitlines()[1:]
    assert len(lines) == 2
    for line in lines:
        requirement = Requirement(line)
        assert requirement.name == "cryptography" and "#sha256=" in requirement.url
        assert not requirement.marker.evaluate({"sys_platform": "linux", "platform_machine": "x86_64"})
