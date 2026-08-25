"""Release metadata and distribution contracts."""

import json
from pathlib import Path
import re
import tomllib
from urllib.parse import parse_qs, urlparse

import yaml

from scholar_mcp import __version__

ROOT = Path(__file__).resolve().parents[1]


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
    assert yaml.safe_load((ROOT / "CITATION.cff").read_text())["version"] == version

    for path in (
        "plugins/scholar-mcp/.mcp.json",
        "plugins/scholar-mcp/mcp.json",
    ):
        args = _json(path)["mcpServers"]["scholar"]["args"]
        assert f"scholar-mcp=={version}" in args


def test_registry_and_release_workflows_are_wired():
    server = _json("server.json")
    package = server["packages"][0]
    assert server["$schema"].endswith("2025-12-11/server.schema.json")
    assert server["name"] == "io.github.liyux3/scholar-mcp"
    assert package["registryType"] == "pypi"
    assert package["runtimeHint"] == "uvx"
    assert package["runtimeArguments"] == [
        {"type": "positional", "value": "scholar-mcp"}
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


def test_readme_contains_valid_one_click_install_urls():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "<!-- mcp-name: io.github.liyux3/scholar-mcp -->" in readme

    vscode_match = re.search(r"https://vscode\.dev/redirect/mcp/install\?[^\"]+", readme)
    assert vscode_match is not None
    vscode = parse_qs(urlparse(vscode_match.group().replace("&amp;", "&")).query)
    assert vscode["name"] == ["scholar-mcp"]
    assert json.loads(vscode["config"][0]) == {
        "type": "stdio",
        "command": "uvx",
        "args": ["scholar-mcp"],
    }

    kiro_match = re.search(r"https://kiro\.dev/launch/mcp/add\?[^\"]+", readme)
    assert kiro_match is not None
    kiro = parse_qs(urlparse(kiro_match.group().replace("&amp;", "&")).query)
    assert kiro["name"] == ["scholar-mcp"]
    assert json.loads(kiro["config"][0]) == {
        "command": "uvx",
        "args": ["scholar-mcp"],
        "disabled": False,
        "autoApprove": [],
    }
