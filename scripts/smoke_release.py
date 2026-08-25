#!/usr/bin/env python3
"""Install the candidate wheel in isolation and exercise its MCP discovery surface."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import timedelta
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_CORE_TOOLS = {
    "download_paper",
    "paper_info",
    "read_paper",
    "recommend_papers",
    "search_authors",
    "search_papers",
}
EXPECTED_RESEARCH_TOOLS = EXPECTED_CORE_TOOLS | {
    "build_paper_graph",
    "paper_library",
}


def project_version() -> str:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return data["project"]["version"]


def find_wheel(dist_dir: Path) -> Path:
    wheels = sorted(dist_dir.glob("*.whl"))
    if len(wheels) != 1:
        raise RuntimeError(f"Expected one wheel in {dist_dir}, found {wheels}")
    return wheels[0]


def venv_executable(venv: Path, name: str) -> Path:
    directory = "Scripts" if os.name == "nt" else "bin"
    suffix = ".exe" if os.name == "nt" else ""
    return venv / directory / f"{name}{suffix}"


def clean_environment(home: Path) -> dict[str, str]:
    blocked = {
        "PYTHONHOME",
        "PYTHONPATH",
        "VIRTUAL_ENV",
        "UV_CONSTRAINT",
        "UV_OVERRIDE",
    }
    environment = {key: value for key, value in os.environ.items() if key not in blocked}
    environment.update(
        {
            "HOME": str(home),
            "USERPROFILE": str(home),
            "NO_COLOR": "1",
            "SCHOLAR_MCP_EXTENSIONS": "research",
        }
    )
    return environment


async def inspect_server(
    entrypoint: Path,
    environment: dict[str, str],
    args: list[str] | None = None,
) -> dict:
    parameters = StdioServerParameters(
        command=str(entrypoint),
        args=args or [],
        env=environment,
    )
    async with asyncio.timeout(90):
        async with stdio_client(parameters) as (read_stream, write_stream):
            async with ClientSession(
                read_stream,
                write_stream,
                read_timeout_seconds=timedelta(seconds=30),
            ) as session:
                initialized = await session.initialize()
                tools = await session.list_tools()
                names = {tool.name for tool in tools.tools}
                if names != EXPECTED_RESEARCH_TOOLS:
                    raise RuntimeError(
                        "Unexpected MCP tools: "
                        f"missing={sorted(EXPECTED_RESEARCH_TOOLS - names)}, "
                        f"extra={sorted(names - EXPECTED_RESEARCH_TOOLS)}"
                    )
                return {
                    "server_version": initialized.serverInfo.version,
                    "tools": sorted(names),
                }


async def smoke(wheel: Path) -> dict:
    with tempfile.TemporaryDirectory(prefix="scholar-release-smoke-") as temporary:
        root = Path(temporary)
        venv = root / "venv"
        home = root / "home"
        home.mkdir()
        environment = clean_environment(home)
        subprocess.run(
            ["uv", "venv", "--python", sys.executable, str(venv)],
            check=True,
            cwd=root,
            env=environment,
        )
        python = venv_executable(venv, "python")
        subprocess.run(
            ["uv", "pip", "install", "--python", str(python), str(wheel)],
            check=True,
            cwd=root,
            env=environment,
        )
        entrypoint = venv_executable(venv, "scholar-mcp")
        if not entrypoint.is_file():
            raise RuntimeError(f"Installed wheel is missing {entrypoint}")
        protocol = await inspect_server(entrypoint, environment)
        expected = project_version()
        if protocol["server_version"] != expected:
            raise RuntimeError(
                f"MCP version {protocol['server_version']} does not match {expected}"
            )
        return {"wheel": wheel.name, "version": expected, **protocol}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dist-dir", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(smoke(find_wheel(args.dist_dir))), sort_keys=True))


if __name__ == "__main__":
    main()
