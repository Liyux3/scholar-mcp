"""Command line: paper research, client installation and the MCP server.

``scholar-mcp`` without arguments serves MCP over stdio, which every existing
client configuration relies on. Paper commands call the same functions as the
MCP tools and print YAML for people or JSON with ``--json``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

HELP = """usage: scholar-mcp [command] [options]

Without a command, runs the Scholar MCP server over stdio.

Research
  search QUERY          find papers across all sources
  info ID               paper details, citations and references
  related ID            semantic neighbours, co-cited peers or shared references
  authors NAME          author profiles
  data QUERY            search scientific data (--kind protein, gene, ...)
  data-info ID          read a native scientific record
  read ID               read pages, figures or tables of a paper
  download ID           keep a PDF in your library
  graph ID[,ID...]      citation graph around seed papers
  library ...           local library, Obsidian, Zotero and Notion

Setup
  install [CLIENT...]   register Scholar in agent clients (--all, --dry-run)
  uninstall [CLIENT...] remove what install added
  doctor                version, install method, clients and providers
  keys                  list, set or unset source keys for every client
  update [--check]      upgrade through the install method
  sources [--check]     source capability matrix and live checks
  serve [--http]        run the MCP server explicitly
  version

Run 'scholar-mcp COMMAND --help' for options."""


def _emit(payload, compact: bool) -> None:
    if compact:
        print(json.dumps(payload, ensure_ascii=False, default=str))
        return
    from .output import yaml_text
    print(yaml_text(payload), end="")


def _failed(result) -> bool:
    return isinstance(result, dict) and (bool(result.get("error")) or result.get("success") is False)


# ---------- research commands ----------

def _research_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="scholar-mcp", add_help=False)
    sub = parser.add_subparsers(dest="command", required=True)

    def command(name: str, help: str) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=help, description=help)
        p.add_argument("--json", action="store_true", help="print JSON instead of YAML")
        return p

    p = command("search", "find papers across all sources")
    p.add_argument("query")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--year", default="", help='a year or range such as "2024-2026"')
    p.add_argument("--venue", default="")
    p.add_argument("--fields", default="", help="comma-separated fields of study")
    p.add_argument("--types", default="", help="comma-separated publication types")
    p.add_argument("--min-citations", type=int, default=0)
    p.add_argument("--open-access", action="store_true")
    p.add_argument("--sort", choices=["relevance", "citations", "date"], default="relevance")
    p.add_argument("--intent", default="balanced",
                   choices=["balanced", "foundational", "recent", "survey", "method", "dataset"])
    p.add_argument("--debug", action="store_true", help="per-source status and ranking provenance")

    p = command("info", "paper details, citations and references")
    p.add_argument("paper_id")
    p.add_argument("--citations", action="store_true")
    p.add_argument("--references", action="store_true")
    p.add_argument("--data", action="store_true", help="explicit scientific database associations")
    p.add_argument("--limit", type=int, default=20)

    p = command("related", "related work around a paper")
    p.add_argument("paper_id")
    p.add_argument("--relation", choices=["similar", "peers", "kin"], default="similar")
    p.add_argument("--limit", type=int, default=10)

    p = command("authors", "author profiles")
    p.add_argument("name")
    p.add_argument("--limit", type=int, default=5)

    p = command("data", "search scientific data without downloading files")
    p.add_argument("query")
    p.add_argument("--kind", choices=["dataset", "sequence", "project", "gene", "protein", "compound"], default="dataset")
    p.add_argument("--source", default="")
    p.add_argument("--limit", type=int, default=10)

    p = command("data-info", "read a native scientific record")
    p.add_argument("data_id")
    p.add_argument("--limit", type=int, default=20)

    p = command("read", "read a paper's pages, figures or tables")
    p.add_argument("paper_id")
    p.add_argument("--pages", default="1-10")
    p.add_argument("--visual", default="", help='a selector such as "Figure 3", "Table 2" or "page 5"')
    p.add_argument("--image", type=Path, help="save the selected visual as a PNG file")

    p = command("download", "keep a PDF in your library")
    p.add_argument("paper_id")
    p.add_argument("--dir", default="", help="target directory (default: the library papers folder)")
    p.add_argument("--collection", default="downloads", help='library collection, "" to skip indexing')

    p = command("graph", "citation graph around seed papers")
    p.add_argument("paper_ids", help="comma-separated identifiers or exact titles")
    p.add_argument("--hops", type=int, default=1)
    p.add_argument("--max-papers", type=int, default=30)
    p.add_argument("--direction", choices=["both", "citations", "references"], default="both")
    p.add_argument("--min-citations", type=int, default=0)
    p.add_argument("--topic", default="", help="topic phrase that keeps the graph on track")
    return parser


def _run_research(argv: list[str]) -> int:
    args = _research_parser().parse_args(argv)
    from . import api

    if args.command == "search":
        result = api.search(args.query, limit=args.limit, year=args.year, venue=args.venue,
                            fields_of_study=args.fields, paper_types=args.types,
                            min_citations=args.min_citations, open_access_only=args.open_access,
                            sort=args.sort, intent=args.intent, debug=args.debug)
    elif args.command == "info":
        parts = ["detail"] + (["citations"] if args.citations else []) + (["references"] if args.references else []) + (["data"] if args.data else [])
        result = api.info(args.paper_id, include=",".join(parts), limit=args.limit)
    elif args.command == "related":
        result = api.related(args.paper_id, relation=args.relation, limit=args.limit)
    elif args.command == "authors":
        result = api.authors(args.name, limit=args.limit)
    elif args.command == "data":
        result = api.search_data(args.query, kind=args.kind, source=args.source, limit=args.limit)
    elif args.command == "data-info":
        result = api.data_info(args.data_id, limit=args.limit)
    elif args.command == "read":
        result = api.read(args.paper_id, pages=args.pages, visual=args.visual)
        image = result.pop("image_png", None) if isinstance(result, dict) else None
        if image is not None and args.image:
            args.image.write_bytes(image)
            result["image_saved"] = str(args.image.resolve())
        if not args.json and isinstance(result, dict) and isinstance(result.get("content"), str):
            content = result.pop("content")
            _emit(result, compact=False)
            print("---\n" + content)
            return int(_failed(result))
    elif args.command == "download":
        result = api.download(args.paper_id, save_dir=args.dir, collection=args.collection)
    else:
        result = api.graph(args.paper_ids, max_hops=args.hops, max_papers=args.max_papers,
                           direction=args.direction, min_citations=args.min_citations,
                           topic_filter=args.topic)
    _emit(result, args.json)
    return int(_failed(result))


# ---------- setup commands ----------

def _run_install(argv: list[str], remove: bool) -> int:
    from . import install

    name = "uninstall" if remove else "install"
    parser = argparse.ArgumentParser(
        prog=f"scholar-mcp {name}",
        description=("Remove the Scholar entries this installer added." if remove else
                     "Register Scholar in agent clients. Without client names, lists detected clients."))
    parser.add_argument("clients", nargs="*", metavar="CLIENT", help=", ".join(install.CLIENTS))
    parser.add_argument("--all", action="store_true", help="every detected client")
    parser.add_argument("--project", action="store_true", help="write the project file in this directory")
    parser.add_argument("--dry-run", action="store_true", help="show the planned change without writing")
    parser.add_argument("--force", action="store_true", help="replace or remove an entry this installer did not create")
    parser.add_argument("--json", action="store_true")
    if not remove:
        parser.add_argument("--profile", choices=["core", "research"], default="core",
                            help="research adds the citation graph and paper library tools")
        parser.add_argument("--mode", choices=["auto", "tool", "uvx"], default="auto",
                            help="tool registers this installation's executable; uvx a pinned uvx command")
    args = parser.parse_args(argv)
    unknown = sorted(set(args.clients) - set(install.CLIENTS))
    if unknown:
        parser.error(f"unknown client {', '.join(unknown)}; choose from {', '.join(install.CLIENTS)}")
    names = list(install.CLIENTS) if args.all else args.clients
    if args.all:
        names = [n for n in names if install.CLIENTS[n].detected()]
    if not names:
        _emit({"clients": install.status(),
               "hint": f"scholar-mcp {name} CLIENT [CLIENT...] or --all; add --dry-run to preview"}, args.json)
        return 0
    spec = None if remove else install.launch_spec(args.mode, args.profile)
    reports = []
    for client in names:
        try:
            if remove:
                reports.append(install.uninstall(client, project=args.project, dry_run=args.dry_run, force=args.force))
            else:
                reports.append(install.install(client, spec, project=args.project, dry_run=args.dry_run,
                                               force=args.force))
        except (OSError, ValueError, RuntimeError) as error:
            reports.append({"client": client, "status": "failed", "error": str(error)[:300]})
    result = {"results": reports}
    if spec is not None:
        result["server"] = {"command": spec.command, "args": spec.args, **({"env": spec.env} if spec.env else {})}
    if not remove and any(r["status"] in {"installed", "updated"} for r in reports):
        result["next"] = "Restart or reload those clients to load Scholar."
    _emit(result, args.json)
    return int(any(r["status"] == "failed" for r in reports))


def _latest_version(timeout: float = 5.0) -> str | None:
    import httpx

    try:
        response = httpx.get("https://pypi.org/pypi/scholar-mcp/json", timeout=timeout)
        return response.json()["info"]["version"]
    except Exception:  # noqa: BLE001 - an unreachable index is reported as unknown
        return None


def _upgrade_command(method: str) -> list[str] | None:
    return {"uv-tool": ["uv", "tool", "upgrade", "scholar-mcp"],
            "pipx": ["pipx", "upgrade", "scholar-mcp"],
            "pip": [sys.executable, "-m", "pip", "install", "--upgrade", "scholar-mcp"]}.get(method)


def _run_update(argv: list[str]) -> int:
    from . import __version__
    from .install import install_method

    parser = argparse.ArgumentParser(prog="scholar-mcp update",
                                     description="Upgrade Scholar through the way it was installed.")
    parser.add_argument("--check", action="store_true", help="only compare with the latest release")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    method = install_method()
    latest = _latest_version()
    result = {"installed": __version__, "latest": latest, "install_method": method}
    command = _upgrade_command(method)
    if args.check or latest == __version__:
        result["up_to_date"] = latest == __version__ if latest else None
        _emit(result, args.json)
        return 0
    if command is None:
        result["note"] = ("Pinned uvx configurations change with the version in each client's command; "
                          "re-run 'uvx scholar-mcp@latest install CLIENT'." if method == "uvx"
                          else "Development checkouts update through git.")
        _emit(result, args.json)
        return 0
    import subprocess

    completed = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', errors='replace')
    result.update(command=" ".join(command), returncode=completed.returncode,
                  output=(completed.stdout + completed.stderr).strip()[-600:],
                  next="Restart MCP clients so they start the upgraded server.")
    _emit(result, args.json)
    return completed.returncode


def _run_doctor(argv: list[str]) -> int:
    from . import __version__, config, health, install, sources

    parser = argparse.ArgumentParser(prog="scholar-mcp doctor",
                                     description="Version, install, clients, providers and the health of every link.")
    parser.add_argument("--network", action="store_true",
                        help="also probe every source, the reranker and the PDF chain live; exit 1 on a problem")
    parser.add_argument("--skip", default="", help="comma-separated sources the probe leaves out")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    caps = sources.capabilities()
    reranker = ("custom endpoint" if config.RERANK_URL else
                "dashscope" if config.DASHSCOPE_API_KEY else "local flashrank (no cloud key configured)")
    result = {
        "version": __version__,
        "install_method": install.install_method(),
        "executable": install.launch_spec("tool").command,
        "python": sys.version.split()[0],
        "data_dir": config.DATA_DIR,
        "profile": os.environ.get("SCHOLAR_MCP_EXTENSIONS") or "core",
        "sources": {"available": sum(c["available"] for c in caps if c["search"]),
                    "searchable": sum(1 for c in caps if c["search"]),
                    "missing_keys": [c["name"] for c in caps if c["search"] and not c["available"]]},
        "reranker": reranker,
        "clients": [row for row in install.status() if row.get("detected") or row.get("registered")],
    }
    # How real calls went lately, and what changed for the worse.
    if recent := health.report():
        result["recent_calls"] = recent
    if args.network:
        result["probe"] = health.probe({name.strip() for name in args.skip.split(",") if name.strip()})
    _emit(result, args.json)
    return 1 if result.get("probe", {}).get("problems") else 0


def _run_serve(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="scholar-mcp serve", description="Run the MCP server.")
    parser.add_argument("--http", action="store_true", help="Streamable HTTP instead of stdio")
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--path")
    args = parser.parse_args(argv)
    if args.http:
        os.environ["SCHOLAR_MCP_TRANSPORT"] = "http"
    for name, value in (("HOST", args.host), ("PORT", args.port), ("PATH", args.path)):
        if value is not None:
            os.environ[f"SCHOLAR_MCP_{name}"] = str(value)
    from .server import main as serve_mcp

    serve_mcp()
    return 0


# ---------- library, sources and access ----------

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
    from . import knowledge_base as kb
    from .library_connectors import (
        connector_status,
        export_obsidian,
        publish_notion,
        pull_linked_notes,
        pull_obsidian_notes,
        sync_zotero,
    )

    parser = _library_parser()
    args = parser.parse_args(argv)
    store = kb.get_store()
    try:
        note_sync = pull_linked_notes(store, args.collection) if args.command in {"list", "search", "sync", "export"} else pull_obsidian_notes(store, args.collection) if args.command == "publish" else {}
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
        if note_sync.get("note_conflicts"):
            result["note_conflicts"] = note_sync["note_conflicts"]
        if note_sync.get("note_errors"):
            result["note_errors"] = note_sync["note_errors"]
        print(json.dumps({"ok": True, "result": result}, ensure_ascii=False, indent=None if args.json else 2))
        return 0
    except Exception as error:  # noqa: BLE001 - CLI boundary returns stable JSON errors.
        print(json.dumps({"ok": False, "error": {"type": type(error).__name__, "message": str(error)}},
                         ensure_ascii=False, indent=None if args.json else 2))
        return 1


def _run_sources(argv: list[str]) -> int:
    from . import sources
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
                    **({("warning" if result.status == "partial" else "error_type"):
                        result.error if result.status == "partial" else result.error.split(":", 1)[0]}
                       if result.error else {})}
        with ThreadPoolExecutor(max_workers=3) as pool:
            rows = list(pool.map(check, rows))
    print(json.dumps({"sources": rows}, ensure_ascii=False, indent=2))
    return 1 if any(r.get("status") in {"error", "blocked", "timeout", "paced"} for r in rows) else 0


RESEARCH = {"search", "info", "related", "authors", "read", "download", "graph", "data", "data-info"}


def _run_keys(argv: list[str]) -> int:
    """List, set or remove keys in the global key file, without printing values."""
    import getpass
    import tempfile

    from filelock import FileLock

    from . import config

    parser = argparse.ArgumentParser(prog="scholar-mcp keys",
                                     description="Keys in <data>/keys.env apply to every client that starts Scholar.")
    parser.add_argument("action", nargs="?", choices=["list", "set", "unset"], default="list")
    parser.add_argument("name", nargs="?")
    args = parser.parse_args(argv)
    path = config.key_file()

    def read() -> list[str]:
        return path.read_text(encoding="utf-8").splitlines() if path.exists() else []

    if args.action == "list":
        saved = {config.key_name(line) for line in read()} - {""}
        print(json.dumps({"file": str(path), "saved": sorted(saved)}, indent=2))
        return 0
    if not args.name or not args.name.replace("_", "").isalnum():
        parser.error("give a variable name, for example OPENALEX_API_KEY")
    value = ""
    if args.action == "set":
        value = getpass.getpass(f"{args.name}: ").strip() if sys.stdin.isatty() else sys.stdin.readline().strip()
        if not value:
            parser.error("empty value")
    path.parent.mkdir(parents=True, exist_ok=True)
    # Read after the prompt and under a lock, so keys saved meanwhile survive;
    # the new file is written whole, owner-only, then moved into place.
    with FileLock(str(path) + ".lock"):
        kept = [line for line in read() if config.key_name(line) != args.name]
        if value:
            kept.append(f"{args.name}={value}")
        fd, temp = tempfile.mkstemp(dir=path.parent, prefix=".keys.", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write("\n".join(kept) + ("\n" if kept else ""))
        os.replace(temp, path)
    print(f"{'saved' if args.action == 'set' else 'removed'} {args.name} in {path}; restart your MCP clients")
    return 0


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments:
        return _run_serve([])
    command, rest = arguments[0], arguments[1:]
    if command in RESEARCH:
        return _run_research(arguments)
    handlers = {
        "library": _run_library,
        "sources": _run_sources,
        "install": lambda a: _run_install(a, remove=False),
        "uninstall": lambda a: _run_install(a, remove=True),
        "update": _run_update,
        "doctor": _run_doctor,
        "serve": _run_serve,
        "keys": _run_keys,
    }
    if command in handlers:
        return handlers[command](rest)
    if command in {"version", "--version", "-V"}:
        from . import __version__
        print(__version__)
        return 0
    if command in {"-h", "--help", "help"}:
        print(HELP)
        return 0
    print(f"unknown command: {command}\n\n{HELP}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
