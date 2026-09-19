#!/usr/bin/env python3
"""Extract and exercise the exact Scholar MCPB artifact."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import stat
import subprocess
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
        for member in archive.infolist():
            # zipfile extracts bytes but does not restore executable bits.
            # Never restore setuid/setgid or permissions on a symlink.
            mode = (member.external_attr >> 16) & 0o777
            if mode:
                (destination / member.filename).chmod(mode)


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
        launch = manifest["server"]["mcp_config"]
        defaults = {name: value.get("default", "") for name, value in manifest.get("user_config", {}).items()}
        def expand(value):
            value = value.replace("${__dirname}", str(unpacked)).replace("${HOME}", str(home))
            for name, default in defaults.items():
                value = value.replace("${user_config." + name + "}", str(default).replace("${HOME}", str(home)))
            return value
        environment.update({key: expand(value) for key, value in launch.get("env", {}).items()})
        command = Path(expand(launch["command"]))
        if not command.is_relative_to(unpacked) or not command.is_file():
            raise RuntimeError("MCPB must launch its bundled interpreter")
        # Prove both relocation and native PDF/reranker imports, without
        # downloading or loading any model weights.
        subprocess.run([str(command), "-c", "import sys,onnxruntime,pypdfium2; print(sys.prefix)"],
                       cwd=root, env=environment, check=True, capture_output=True, timeout=60)
        protocol = await inspect_server(
            command,
            environment,
            [expand(arg) for arg in launch.get("args", [])],
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
