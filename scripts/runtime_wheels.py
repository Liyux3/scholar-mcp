#!/usr/bin/env python3
"""Create a hash-pinned wheel index; installers select their actual ABI."""
import argparse
import hashlib
from html import escape
from pathlib import Path
try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib

ROOT = Path(__file__).resolve().parents[1]


def wheel_index(wheel_dir: Path, version: str, *, local: bool = False) -> str:
    rows = []
    platforms = set()
    for path in sorted(wheel_dir.rglob("cryptography-50.0.1-*.whl")):
        if "macosx" in path.name and path.name.endswith("x86_64.whl"):
            platform = "darwin"
        elif path.name.endswith("win_arm64.whl"):
            platform = "win32"
        else:
            continue
        if platform in platforms:
            raise ValueError(f"Multiple compatibility wheels for {platform}")
        platforms.add(platform)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        url = path.resolve().as_uri() if local else f"https://github.com/Liyux3/scholar-mcp/releases/download/v{version}/{path.name}"
        rows.append(f'<a href="{escape(url)}#sha256={digest}">{escape(path.name)}</a><br>')
    if platforms != {"darwin", "win32"}:
        raise ValueError("Both Intel macOS and Windows ARM wheels are required")
    return '<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Scholar runtime wheels</title></head><body>\n' + "\n".join(rows) + "\n</body></html>\n"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--wheels", type=Path, default=ROOT / ".compat-wheels")
    parser.add_argument("--output", type=Path, default=ROOT / ".compat-wheels/wheel-index.html")
    parser.add_argument("--local", action="store_true", help="validate the same hashed wheels before release upload")
    args = parser.parse_args()
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    args.output.write_text(wheel_index(args.wheels, version, local=args.local), encoding="utf-8")


if __name__ == "__main__":
    main()
