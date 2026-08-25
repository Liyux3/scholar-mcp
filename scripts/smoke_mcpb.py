#!/usr/bin/env python3
"""Extract and exercise the exact Scholar MCPB artifact."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import zipfile

from smoke_release import clean_environment, inspect_server

ROOT = Path(__file__).resolve().parents[1]


def find_bundle(bundle_dir: Path) -> Path:
    bundles = sorted(bundle_dir.glob("*.mcpb"))
    if len(bundles) != 1:
        raise RuntimeError(f"Expected one MCPB in {bundle_dir}, found {bundles}")
    return bundles[0]


def safe_extract(bundle: Path, destination: Path) -> None:
    root = destination.resolve()
    with zipfile.ZipFile(bundle) as archive:
        if corrupt := archive.testzip():
            raise RuntimeError(f"Corrupt MCPB member: {corrupt}")
        for member in archive.infolist():
            target = (destination / member.filename).resolve()
            if not target.is_relative_to(root):
                raise RuntimeError(f"Unsafe MCPB member: {member.filename}")
            mode = (member.external_attr >> 16) & 0o170000
            if mode == stat.S_IFLNK:
                raise RuntimeError(f"MCPB symlink member: {member.filename}")
        archive.extractall(destination)


async def smoke(bundle: Path) -> dict:
    with tempfile.TemporaryDirectory(prefix="scholar-mcpb-smoke-") as temporary:
        root = Path(temporary)
        unpacked = root / "unpacked"
        safe_extract(bundle, unpacked)
        manifest = json.loads((unpacked / "manifest.json").read_text(encoding="utf-8"))
        source = unpacked / "server"
        vendor = source / "vendor"
        if not (source / "scholar_mcp" / "__main__.py").is_file() or not vendor.is_dir():
            raise RuntimeError("MCPB is missing server source or vendored dependencies")
        home = root / "home"
        home.mkdir()
        environment = clean_environment(home)
        environment["PYTHONPATH"] = os.pathsep.join((str(source), str(vendor)))
        protocol = await inspect_server(
            Path(sys.executable),
            environment,
            ["-m", "scholar_mcp"],
        )
        if protocol["server_version"] != manifest["version"]:
            raise RuntimeError("MCPB manifest and server versions differ")
        return {"bundle": bundle.name, "manifest": manifest["version"], **protocol}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle-dir", type=Path, default=ROOT / "mcpb-build")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(smoke(find_bundle(args.bundle_dir))), sort_keys=True))


if __name__ == "__main__":
    main()
