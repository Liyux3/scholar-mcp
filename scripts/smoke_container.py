#!/usr/bin/env python3
"""Exercise the Scholar MCP container over stdio."""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

from smoke_release import inspect_server


async def smoke(image: str) -> dict:
    return await inspect_server(
        Path("docker"),
        dict(os.environ),
        [
            "run",
            "--rm",
            "-i",
            "-e",
            "SCHOLAR_MCP_EXTENSIONS=research",
            image,
        ],
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default="scholar-mcp:release-smoke")
    args = parser.parse_args()
    print(asyncio.run(smoke(args.image)))


if __name__ == "__main__":
    main()
