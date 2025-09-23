import json
import yaml
from fastmcp import FastMCP
from . import config
from . import s2_client
from . import openalex_client
from . import openreview_client
from . import pdf_utils
from . import relevance
from . import graph
from . import discovery
from . import knowledge_base as kb
from . import sources

mcp = FastMCP("scholar-mcp")

INTERNAL_FETCH_LIMIT = 100


def _pipeline(
    dispatch: str,
    query_or_id: str,
    limit: int,
    rerank_query: str = "",
    intent: str = "",
    **kwargs,
) -> tuple[list[dict], list[dict]]:
    """Shared pipeline: parallel fetch -> dedup -> rerank -> rank -> truncate.

    Args:
        dispatch: "search" | "citations" | "references"
        query_or_id: search query or paper ID
        limit: final output size
        rerank_query: if set, run reranker with this query
        **kwargs: passed to source search (year, fields_of_study, etc.)
    Returns:
        (ranked_papers, source_reports)
    """
    if dispatch == "search":
        source_results = sources.parallel_search(query_or_id, limit=INTERNAL_FETCH_LIMIT, **kwargs)
    elif dispatch == "citations":
        source_results = sources.parallel_citations(query_or_id, limit=INTERNAL_FETCH_LIMIT)
    elif dispatch == "references":
        source_results = sources.parallel_references(query_or_id, limit=INTERNAL_FETCH_LIMIT)
    else:
        return [], []

    all_papers = []
    source_reports = []
    for sr in source_results:
        source_reports.append({
            "source": sr.source,
            "status": sr.status,
            "count": len(sr.results),
            "latency_ms": sr.latency_ms,
            "error": sr.error,
        })
        if sr.results:
            relevance.tag_source_ranks(sr.results, sr.source)
            all_papers.extend(sr.results)

    if not all_papers:
        return [], source_reports

    all_papers = relevance.deduplicate(all_papers)

    if rerank_query:
        all_papers = relevance.rerank(rerank_query, all_papers, top_n=min(limit * 3, len(all_papers)), intent=intent)
        all_papers = relevance.rank_final(all_papers)
    else:
        all_papers.sort(key=lambda p: -(p.get("citation_count", 0) or 0))

    return all_papers[:limit], source_reports


def _format_paper(p: dict) -> dict:
    doi = (p.get("external_ids") or {}).get("DOI", "")
    abstract = p.get("abstract") or ""
    if len(abstract) > 300:
        abstract = abstract[:300] + "..."
    out = {
        "title": p.get("title", ""),
        "authors": (p.get("authors") or [])[:5],
        "year": p.get("year"),
        "venue": p.get("venue", ""),
        "citations": p.get("citation_count", 0),
        "abstract": abstract,
    }
    if doi:
        out["doi"] = doi
    if p.get("url"):
        out["url"] = p["url"]
    if p.get("tldr"):
        out["tldr"] = p["tldr"]
    if p.get("_final_score"):
        out["score"] = round(p["_final_score"], 2)
    return out


def _format_compact(p: dict) -> dict:
    out = {
        "title": p.get("title", ""),
        "authors": (p.get("authors") or [])[:3],
        "year": p.get("year"),
        "citations": p.get("citation_count", 0),
    }
    doi = (p.get("external_ids") or {}).get("DOI", "")
    if doi:
        out["doi"] = doi
    return out


def _yaml(data: dict) -> str:
    return yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False)


def _meta_block(source_reports: list[dict], **extra) -> dict:
    used = [r["source"] for r in source_reports if r["status"] == "ok"]
    return {"sources_used": used, "source_details": source_reports, **extra}


@mcp.tool()
def search_papers(
    query: str,
    limit: int = 10,
    year: str = "",
    venue: str = "",
    fields_of_study: str = "",
    min_citations: int = 0,
    open_access_only: bool = False,
    sort: str = "",
    intent: str = "",
) -> str:
    """Search for academic papers across multiple sources (Semantic Scholar, arXiv, OpenAlex).
    Results are ranked using LLM-based reranking for better relevance.

    Args:
        query: Search query (e.g., "attention is all you need", "CRISPR gene editing")
        limit: Maximum results to return (1-100, default 10)
        year: Filter by year or range (e.g., "2023", "2020-2024")
        venue: Filter by venue (e.g., "NeurIPS", "Nature")
        fields_of_study: Comma-separated fields (e.g., "Computer Science,Mathematics")
        min_citations: Minimum citation count filter (default 0)
        open_access_only: Only return papers with free PDF access
        sort: Sort results by "citations" (most cited first) or "date" (newest first). Default: relevance.
        intent: Ranking preference. "foundational" for seminal papers, "recent" for latest work, "survey" for reviews, "method" for specific techniques. Default: balanced relevance.
    """
    fos_list = [f.strip() for f in fields_of_study.split(",") if f.strip()] if fields_of_study else None
    search_query = relevance.optimize_query(query)

    results, reports = _pipeline("search", search_query, limit * 3, rerank_query=query, intent=intent)

    if fos_list:
        results = relevance.filter_by_fields(results, fos_list)
    if min_citations > 0:
        results = [p for p in results if (p.get("citation_count") or 0) >= min_citations]

    if sort == "citations":
        results.sort(key=lambda p: -(p.get("citation_count", 0) or 0))
    elif sort == "date":
        results.sort(key=lambda p: p.get("publication_date") or p.get("year") or 0, reverse=True)

    results = results[:limit]

    if not results:
        return _yaml({"error": "No relevant results found.", "_meta": _meta_block(reports)})

    return _yaml({
        "results": [_format_paper(p) for p in results],
        "_meta": _meta_block(reports, total=len(results)),
    })


@mcp.tool()
def paper_info(
    paper_id: str,
    include: str = "detail",
    limit: int = 20,
) -> str:
    """Get information about a specific paper. Can include detail, citations, and/or references.

    Args:
        paper_id: Paper identifier (S2 ID, DOI, ArXiv:ID, OpenAlex W-ID, etc.)
        include: Comma-separated: "detail", "citations", "references" (default: "detail")
        limit: Max citations/references to return (default 20)
    """
    parts = [p.strip() for p in include.split(",")]
    output = {}

    if "detail" in parts:
        for src in sources.all_sources():
            if not src.get_paper or not src.available():
                continue
            try:
                result = src.get_paper(paper_id)
                if result:
                    output["paper"] = _format_paper(result)
                    break
            except Exception:
                continue

    if "citations" in parts:
        cites, reports = _pipeline("citations", paper_id, limit)
        output["citations"] = [_format_compact(p) for p in cites]
        output["_citations_meta"] = _meta_block(reports, total=len(cites))

    if "references" in parts:
        refs, reports = _pipeline("references", paper_id, limit)
        output["references"] = [_format_compact(p) for p in refs]
        output["_references_meta"] = _meta_block(reports, total=len(refs))

    if not output:
        return _yaml({"error": f"Could not find paper '{paper_id}'"})

    return _yaml(output)


@mcp.tool()
def recommend_papers(paper_id: str, limit: int = 10) -> str:
    """Find similar/related papers using Semantic Scholar's recommendation engine.

    Args:
        paper_id: Paper identifier (S2 ID, DOI, ArXiv:ID, OpenAlex ID, etc.)
        limit: Maximum recommendations (1-500, default 10)
    """
    ids_to_try = [paper_id]
    if paper_id.startswith("W"):
        oa_paper = openalex_client.get_paper_by_id(paper_id)
        if oa_paper:
            doi = (oa_paper.get("external_ids") or {}).get("DOI", "")
            if doi:
                ids_to_try.insert(0, doi)

    for pid in ids_to_try:
        try:
            results = s2_client.get_recommendations(pid, limit=limit)
            if results:
                return _yaml({
                    "recommendations": [_format_compact(p) for p in results],
                    "total": len(results),
                })
        except Exception:
            continue
    return _yaml({"error": f"Could not get recommendations for '{paper_id}'"})


@mcp.tool()
def search_authors(query: str, limit: int = 5) -> str:
    """Search for academic authors/researchers.

    Args:
        query: Author name to search for
        limit: Maximum results (1-1000, default 5)
    """
    try:
        results = s2_client.search_authors(query, limit=limit)
        return _yaml(results)
    except Exception as e:
        return _yaml({"error": f"Author search failed: {e}"})


@mcp.tool()
def read_paper(paper_id: str, save_dir: str = "", max_pages: int = 0, extract_text: bool = True) -> str:
    """Download a paper's PDF and optionally extract its text content.

    Args:
        paper_id: Paper identifier (S2 ID, DOI, ArXiv:ID, etc.)
        save_dir: Directory to save PDF (default: configured download directory)
        max_pages: Maximum pages to extract (0 = all pages)
        extract_text: If True, extract and return text. If False, just download PDF.
    """
    save_path = save_dir or config.DOWNLOAD_DIR
    try:
        paper_info_data = s2_client.get_paper(paper_id)
    except Exception as e:
        return _yaml({"error": f"Could not find paper '{paper_id}': {e}"})

    dl_result = pdf_utils.download_paper(paper_info_data, save_path)
    if not dl_result.get("success"):
        return _yaml(dl_result)

    if dl_result.get("file_path"):
        paper_info_data["pdf_path"] = dl_result["file_path"]
        kb.add_papers([paper_info_data], collection="downloads")

    if not extract_text:
        return _yaml(dl_result)

    try:
        text = pdf_utils.extract_text(dl_result["file_path"], max_pages=max_pages)
        return text
    except Exception as e:
        return _yaml({"error": f"PDF downloaded but text extraction failed: {e}", "file_path": dl_result.get("file_path")})


@mcp.tool()
def search_openreview(query: str, venue: str = "", limit: int = 10) -> str:
    """Search OpenReview for conference papers (ICLR, NeurIPS, ICML, etc.).
    No API key required. Returns papers with PDFs and review links.

    Args:
        query: Search query (e.g., "vision language action robot")
        venue: OpenReview venue ID filter (e.g., "ICLR.cc/2026/Conference")
        limit: Maximum results (1-50, default 10)
    """
    try:
        results = openreview_client.search_papers(query, max_results=limit, venue=venue or None)
        if results:
            return _yaml(results)
        return _yaml({"message": "No results found on OpenReview.", "query": query, "venue": venue})
    except Exception as e:
        return _yaml({"error": f"OpenReview search failed: {e}"})


@mcp.tool()
def build_paper_graph(
    paper_ids: str,
    max_hops: int = 2,
    max_papers: int = 50,
    direction: str = "both",
    min_citations: int = 0,
    topic_filter: str = "",
) -> str:
    """Build a citation graph starting from seed papers.
    Recursively expands citations and references to map the research landscape.

    Args:
        paper_ids: Comma-separated paper identifiers (S2 IDs, DOIs, or paper titles to search)
        max_hops: How many citation/reference hops to expand (1-3, default 2)
        max_papers: Maximum total papers in the graph (10-200, default 50)
        direction: "citations" (who cites this), "references" (what it cites), or "both"
        min_citations: Skip papers with fewer citations than this
        topic_filter: Space-separated keywords to keep graph focused on a topic
    """
    max_hops = min(max(max_hops, 1), 3)
    max_papers = min(max(max_papers, 10), 200)

    seeds = [pid.strip() for pid in paper_ids.split(",") if pid.strip()]
    if not seeds:
        return _yaml({"error": "No paper IDs provided"})

    seed_papers = []
    for seed in seeds:
        if len(seed) > 30 and " " not in seed:
            try:
                p = s2_client.get_paper(seed)
                if p:
                    seed_papers.append(p)
                    continue
            except Exception:
                pass
        results, _ = _pipeline("search", seed, 1)
        if results:
            seed_papers.append(results[0])

    if not seed_papers:
        return _yaml({"error": "Could not find any of the specified papers"})

    result = graph.build_graph(
        seed_papers, max_hops=max_hops, max_papers=max_papers,
        direction=direction, min_citations=min_citations, topic_filter=topic_filter,
    )

    return result["summary"] + "\n\n```mermaid\n" + result["mermaid"] + "\n```"


@mcp.tool()
def knowledge_base(
    action: str = "list",
    paper_titles: str = "",
    collection: str = "default",
    query: str = "",
    notes: str = "",
    limit: int = 20,
) -> str:
    """Manage saved papers in the knowledge base.

    Args:
        action: "save" to save papers, "list" to list papers, "search" to search, "collections" to list all collections
        paper_titles: Comma-separated paper titles or DOIs (for action="save")
        collection: Collection name (default: "default")
        query: Search query (for action="search")
        notes: Notes to attach to saved papers (for action="save")
        limit: Maximum papers to return
    """
    if action == "collections":
        return _yaml({"collections": kb.list_collections()})

    if action == "save":
        titles = [t.strip() for t in paper_titles.split(",") if t.strip()]
        if not titles:
            return _yaml({"error": "No paper titles provided"})
        papers_to_save = []
        for title in titles:
            results, _ = _pipeline("search", title, 1)
            if results:
                papers_to_save.append(results[0])
        if not papers_to_save:
            return _yaml({"error": "Could not find any of the specified papers"})
        result = kb.add_papers(papers_to_save, collection=collection, notes=notes)
        return _yaml(result)

    if action == "search" and query:
        papers = kb.search_kb(query, collection=collection, limit=limit)
    else:
        papers = kb.list_papers(collection=collection, limit=limit)

    return _yaml({
        "collection": collection,
        "total": len(papers),
        "papers": [{"title": p.get("title", ""), "year": p.get("year"), "citations": p.get("citation_count", 0), "notes": p.get("notes", "")} for p in papers],
    })


@mcp.tool()
def discover_field(topic: str, max_papers: int = 30) -> str:
    """Map a research field: find surveys, foundational papers, and recent advances.

    Args:
        topic: Research topic to explore (e.g., "RLHF language model alignment")
        max_papers: Maximum papers to collect (10-50, default 30)
    """
    max_papers = min(max(max_papers, 10), 50)
    result = discovery.discover_field(topic, max_papers=max_papers)
    return result["summary"] + "\n\n```mermaid\n" + result["mermaid"] + "\n```"


@mcp.tool()
def scholar_status() -> str:
    """Check scholar-mcp server version, available sources, and KB collections."""
    available = [s.name for s in sources.search_sources()]
    cite_sources = [s.name for s in sources.citation_sources()]
    colls = kb.list_collections()
    return _yaml({
        "version": "0.7.0",
        "tools": 10,
        "search_sources": available,
        "citation_sources": cite_sources,
        "kb_collections": [{"name": c["name"], "papers": c["papers"]} for c in colls],
        "s2_key": bool(config.get_s2_api_key()),
        "dashscope_key": bool(config.DASHSCOPE_API_KEY),
        "cache_enabled": True,
    })


def main():
    mcp.run(transport="stdio", show_banner=False)


if __name__ == "__main__":
    main()
