"""Python API: the MCP tools as plain functions returning dicts.

The same implementation serves MCP clients, the command line and Python code,
so results carry the fields of the tools' structured output::

    from scholar_mcp import api

    hits = api.search("diffusion policy for dexterous manipulation", year="2024-2026")
    paper = api.info(hits["results"][0]["id"], include="detail,citations")
    text = api.read(paper["paper"]["id"], pages="1-4")["content"]
"""

from __future__ import annotations

from collections.abc import Iterable

from . import server

__all__ = ["search", "info", "related", "authors", "read", "download", "graph", "library"]

search = server.search_papers
info = server.paper_info
related = server.recommend_papers
authors = server.search_authors
read = server.read_paper
download = server.download_paper
library = server.paper_library


def graph(paper_ids: str | Iterable[str], **options) -> dict:
    """Citation graph from one or more seed identifiers (see build_paper_graph)."""
    seeds = paper_ids if isinstance(paper_ids, str) else ",".join(paper_ids)
    return server.build_paper_graph(seeds, **options)
