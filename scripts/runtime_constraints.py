#!/usr/bin/env python3
"""Create hash-pinned release constraints for the native compatibility wheels."""
import argparse
import hashlib
from pathlib import Path
try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib

ROOT = Path(__file__).resolve().parents[1]


def constraints(wheel_dir: Path, version: str, *, local: bool = False) -> str:
    rows = []
    platforms = set()
    for path in sorted(wheel_dir.rglob("cryptography-50.0.1-*.whl")):
        if "macosx" in path.name and path.name.endswith("x86_64.whl"):
            platform = "darwin"
            marker = 'sys_platform == "darwin" and platform_machine == "x86_64"'
        elif path.name.endswith("win_arm64.whl"):
            platform = "win32"
            marker = 'sys_platform == "win32" and (platform_machine == "ARM64" or platform_machine == "aarch64")'
        else:
            continue
        if platform in platforms:
            raise ValueError(f"Multiple compatibility wheels for {platform}")
        platforms.add(platform)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        url = path.resolve().as_uri() if local else f"https://github.com/Liyux3/scholar-mcp/releases/download/v{version}/{path.name}"
        rows.append(f"cryptography @ {url}#sha256={digest} ; {marker}")
    if platforms != {"darwin", "win32"}:
        raise ValueError("Both Intel macOS and Windows ARM wheels are required")
    return "# Built from unmodified upstream cryptography 50.0.1 in release CI.\n" + "\n".join(rows) + "\n"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--wheels", type=Path, default=ROOT / ".compat-wheels")
    parser.add_argument("--output", type=Path, default=ROOT / ".compat-wheels/runtime-constraints.txt")
    parser.add_argument("--local", action="store_true", help="validate the same hashed wheels before release upload")
    args = parser.parse_args()
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    args.output.write_text(constraints(args.wheels, version, local=args.local), encoding="utf-8")


if __name__ == "__main__":
    main()
