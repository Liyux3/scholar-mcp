"""Command line entry point for MCP serving and Research Library connectors."""

from __future__ import annotations

import argparse
import json
import sys

from . import knowledge_base as kb
from .library_connectors import (
    connector_status,
    export_obsidian,
    publish_notion,
    sync_zotero,
)
from . import sources


def _emit(payload: dict, compact: bool) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=None if compact else 2))


def _library_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="scholar-mcp library")
    parser.add_argument("--json", action="store_true", help="emit compact JSON")
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("doctor", help="inspect SQLite and connector configuration")
    commands.add_parser("migrate", help="initialize SQLite and import legacy JSONL")

    list_parser = commands.add_parser("list", help="list papers in a collection")
    list_parser.add_argument("--collection", default="default")
    list_parser.add_argument("--limit", type=int, default=20)

    search_parser = commands.add_parser("search", help="search the persistent FTS5 index")
    search_parser.add_argument("query")
    search_parser.add_argument("--collection", default="default")
    search_parser.add_argument("--limit", type=int, default=20)

    export_parser = commands.add_parser("export", help="export a library projection")
    export_parser.add_argument("target", choices=["obsidian"])
    export_parser.add_argument("--collection", default="default")
    export_parser.add_argument("--path")
    export_parser.add_argument("--link-citations", action="store_true")

    sync_parser = commands.add_parser("sync", help="sync a bibliographic connector")
    sync_parser.add_argument("target", choices=["zotero"])
    sync_parser.add_argument("--collection", default="default")
    sync_parser.add_argument("--zotero-collection-key", default="")
    sync_parser.add_argument(
        "--apply",
        action="store_true",
        help="perform writes; otherwise emit a dry-run plan",
    )

    publish_parser = commands.add_parser("publish", help="publish a human-facing view")
    publish_parser.add_argument("target", choices=["notion"])
    publish_parser.add_argument("--collection", default="default")
    publish_parser.add_argument(
        "--apply",
        action="store_true",
        help="perform writes; otherwise emit a dry-run plan",
    )
    return parser


def _run_library(argv: list[str]) -> int:
    parser = _library_parser()
    args = parser.parse_args(argv)
    store = kb.get_store()
    try:
        if args.command in {"doctor", "migrate"}:
            result = connector_status(store)
        elif args.command == "list":
            result = {
                "collection": args.collection,
                "papers": store.list_records(args.collection, max(args.limit, 1)),
            }
        elif args.command == "search":
            result = {
                "collection": args.collection,
                "papers": kb.search_kb(
                    args.query,
                    collection=args.collection,
                    limit=max(args.limit, 1),
                ),
            }
        elif args.command == "export":
            result = export_obsidian(
                store,
                args.collection,
                base_dir=args.path,
                link_citations=args.link_citations,
            )
        elif args.command == "sync":
            result = sync_zotero(
                store,
                args.collection,
                apply=args.apply,
                collection_key=args.zotero_collection_key,
            )
        elif args.command == "publish":
            result = publish_notion(store, args.collection, apply=args.apply)
        else:
            parser.error(f"unknown command {args.command}")
            return 2
        _emit({"ok": True, "result": result}, args.json)
        return 0
    except Exception as error:  # noqa: BLE001 - CLI boundary returns stable JSON errors.
        _emit(
            {
                "ok": False,
                "error": {
                    "type": type(error).__name__,
                    "message": str(error),
                },
            },
            args.json,
        )
        return 1


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments:
        from .server import main as serve_mcp

        serve_mcp()
        return 0
    if arguments[0] == "library":
        return _run_library(arguments[1:])
    if arguments[0] == "sources":
        return _run_sources(arguments[1:])
    if arguments[0] in {"-h", "--help"}:
        print(
            "usage: scholar-mcp [library ... | sources]\n\n"
            "Without arguments, starts the Scholar MCP server.\n"
            "Use 'scholar-mcp sources' for the source capability matrix or "
            "'scholar-mcp sources --check' to test configured providers.\n"
            "'scholar-mcp library --help' for local library and connectors."
        )
        return 0
    raise SystemExit(f"unknown command: {arguments[0]}")


def _run_sources(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="scholar-mcp sources")
    parser.add_argument("--check", action="store_true", help="send a small live search to configured sources")
    parser.add_argument("--query", default="machine learning", help="query for the live check")
    parser.add_argument("--source", help="check one named provider")
    args = parser.parse_args(argv)
    if args.source and not sources.get(args.source):
        parser.error(f"unknown source: {args.source}")
    rows = [r for r in sources.capabilities() if not args.source or r["name"] == args.source]
    if args.check:
        from concurrent.futures import ThreadPoolExecutor
        def check(row):
            if not row["search"]:
                return {**row, "status": "not_searchable"}
            if not row["available"]:
                return {**row, "status": "not_configured"}
            source = sources.get(row["name"])
            result = sources._timed_call(source.name, source.search, args.query, 1)
            return {**row, "status": result.status, "count": len(result.results),
                    "latency_ms": result.latency_ms,
                    **({"error_type": result.error.split(":", 1)[0]} if result.error else {})}
        with ThreadPoolExecutor(max_workers=3) as pool:
            rows = list(pool.map(check, rows))
    _emit({"sources": rows}, compact=False)
    return 1 if any(r.get("status") in {"error", "blocked", "timeout"} for r in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
