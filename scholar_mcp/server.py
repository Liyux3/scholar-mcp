import json
from fastmcp import FastMCP
from . import config
from . import s2_client
from . import arxiv_client
from . import openalex_client
from . import crossref_client
from . import openreview_client
from . import core_client
from . import pubmed_client
from . import scholar_client
from . import pdf_utils
from . import relevance
from . import graph
from . import discovery
from . import knowledge_base as kb
from . import sources

mcp = FastMCP("scholar-mcp")


def _normalize_paper_id(paper_id: str) -> str:
    """Convert arXiv DOI format to ArXiv: prefix that S2 understands."""
    import re
    m = re.match(r"10\.48550/arXiv\.(\d+\.\d+)", paper_id, re.IGNORECASE)
    if m:
        return f"ArXiv:{m.group(1)}"
    return paper_id


def _compact_papers(papers: list[dict]) -> list[dict]:
    """Slim down paper list for citation/reference output."""
    compact = []
    for p in papers:
        c = {
            "paper_id": p.get("paper_id", ""),
            "title": p.get("title", ""),
            "authors": (p.get("authors") or [])[:3],
            "year": p.get("year"),
            "citation_count": p.get("citation_count", 0),
        }
        doi = (p.get("external_ids") or {}).get("DOI", "")
        if doi:
            c["doi"] = doi
        compact.append(c)
    return compact


def _collect_primary(search_query, limit, year, venue, fos_list,
                     min_citations, open_access_only):
    """Query S2, arXiv, and OpenAlex in parallel via threads.
    Each paper is tagged with its rank position from the source for RRF fusion.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    all_papers = []
    sources_used = []
    sources_failed = []

    def _search_s2():
        return "semantic_scholar", s2_client.search_papers(
            search_query, limit=limit,
            year=year or None, venue=venue or None,
            fields_of_study=fos_list,
            min_citations=min_citations, open_access_only=open_access_only,
        )

    def _search_arxiv():
        return "arxiv", arxiv_client.search_papers(search_query, max_results=limit)

    def _search_oa():
        return "openalex", openalex_client.search_papers(
            search_query, limit=limit,
            year=year or None, fields_of_study=fos_list,
        )

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(fn) for fn in [_search_s2, _search_arxiv, _search_oa]]
        for future in as_completed(futures):
            try:
                name, results = future.result()
                if results:
                    relevance.tag_source_ranks(results, name)
                    all_papers.extend(results)
                    sources_used.append(name)
            except Exception as e:
                sources_failed.append(f"{type(e).__name__}")

    return all_papers, sources_used, sources_failed


def _collect_fallback(query, limit, sources_failed):
    """Try secondary sources as fallback. Returns (papers, sources_used)."""
    all_papers = []
    sources_used = []

    secondary = [s for s in sources.search_sources()
                 if s.name not in ("semantic_scholar", "openalex", "arxiv")]

    for src in secondary:
        try:
            results = src.search(query, limit=limit)
            if results:
                relevance.tag_source_ranks(results, src.name)
                all_papers.extend(results)
                sources_used.append(src.name)
                if len(all_papers) >= limit:
                    break
        except Exception as e:
            sources_failed.append(f"{src.name}: {type(e).__name__}")

    if not all_papers:
        try:
            results = scholar_client.search_papers(query, max_results=limit)
            if results:
                relevance.tag_source_ranks(results, "google_scholar")
                all_papers.extend(results)
                sources_used.append("google_scholar")
        except Exception as e:
            sources_failed.append(f"google_scholar: {type(e).__name__}")

    return all_papers, sources_used


@mcp.tool()
def search_papers(
    query: str,
    limit: int = 10,
    year: str = "",
    venue: str = "",
    fields_of_study: str = "",
    min_citations: int = 0,
    open_access_only: bool = False,
) -> str:
    """Search for academic papers across multiple sources (Semantic Scholar, arXiv, OpenAlex).
    Results are fused using Reciprocal Rank Fusion for better ranking quality.
    Falls back to Crossref, CORE, PubMed if primary sources are unavailable.

    Tips for best results:
    - Use specific technical terms, method names, or paper titles as query
    - Short focused queries (5-15 words) work better than long paragraphs
    - Use year filter to narrow recent work (e.g., "2024-2025")
    - Use fields_of_study for cross-domain queries to reduce noise

    Args:
        query: Search query (e.g., "attention is all you need", "CRISPR gene editing")
        limit: Maximum results to return (1-100, default 10)
        year: Filter by year or range (e.g., "2023", "2020-2024")
        venue: Filter by venue (e.g., "NeurIPS", "Nature")
        fields_of_study: Comma-separated fields (e.g., "Computer Science,Mathematics")
        min_citations: Minimum citation count filter (default 0)
        open_access_only: Only return papers with free PDF access
    """
    fos_list = [f.strip() for f in fields_of_study.split(",") if f.strip()] if fields_of_study else None
    search_query = relevance.optimize_query(query)

    all_papers, sources_used, sources_failed = _collect_primary(
        search_query, limit, year, venue, fos_list,
        min_citations, open_access_only,
    )

    if not all_papers:
        fallback_papers, fb_sources = _collect_fallback(search_query, limit, sources_failed)
        all_papers = fallback_papers
        sources_used.extend(fb_sources)

    total_before = len(all_papers)
    all_papers = relevance.deduplicate(all_papers)

    if len(sources_used) > 1:
        all_papers = relevance.rrf_fuse(all_papers, method="consensus")

    if fos_list:
        all_papers = relevance.filter_by_fields(all_papers, fos_list)

    if min_citations > 0:
        all_papers = [p for p in all_papers if (p.get("citation_count") or 0) >= min_citations]

    scored = relevance.score_results(query, all_papers, min_score=0.05)
    results = relevance.rerank(query, scored, top_n=limit)

    if not results:
        return json.dumps({
            "error": "No relevant results found.",
            "_meta": {"sources_used": sources_used, "sources_failed": sources_failed},
        })

    compact = []
    for r in results:
        doi = (r.get("external_ids") or {}).get("DOI", "")
        abstract = r.get("abstract") or ""
        if len(abstract) > 300:
            abstract = abstract[:300] + "..."
        p = {
            "paper_id": r.get("paper_id", ""),
            "title": r.get("title", ""),
            "authors": (r.get("authors") or [])[:5],
            "year": r.get("year"),
            "venue": r.get("venue", ""),
            "citation_count": r.get("citation_count", 0),
            "abstract": abstract,
            "url": r.get("url", ""),
        }
        if doi:
            p["doi"] = doi
        if r.get("tldr"):
            p["tldr"] = r["tldr"]
        if r.get("publication_date"):
            p["publication_date"] = r["publication_date"]
        src = r.get("source", "")
        if "+" in src:
            p["found_in"] = src.split("+")
        compact.append(p)

    return json.dumps({
        "results": compact,
        "_meta": {
            "sources_used": sources_used,
            "total": len(compact),
        },
    }, indent=2, default=str)


@mcp.tool()
def get_paper(paper_id: str) -> str:
    """Get detailed information about a specific paper.
    Accepts: Semantic Scholar ID, DOI, ArXiv ID (prefix with "ArXiv:"),
    PMID (prefix with "PMID:"), OpenAlex ID (W...), or a URL.

    Args:
        paper_id: Paper identifier (e.g., "649def34f8be52c8b66281af98ae884c09aef38b",
                  "10.1038/nature12373", "ArXiv:2106.09685", "W2626778328")
    """
    paper_id = _normalize_paper_id(paper_id)
    for src in sources.all_sources():
        if not src.get_paper or not src.available():
            continue
        try:
            result = src.get_paper(paper_id)
            if result:
                return json.dumps(result, indent=2, default=str)
        except Exception:
            continue
    return json.dumps({"error": f"Could not find paper '{paper_id}'"})


@mcp.tool()
def get_citations(paper_id: str, limit: int = 20) -> str:
    """Get papers that cite a given paper.
    Uses S2 first (recent citations, sorted by impact), falls back to OpenAlex
    (sorted by citation count, better for finding influential follow-up work).

    Args:
        paper_id: Paper identifier (S2 ID, DOI, ArXiv:ID, OpenAlex ID, etc.)
        limit: Maximum number of citing papers (1-1000, default 20)
    """
    paper_id = _normalize_paper_id(paper_id)
    for src in sources.citation_sources():
        try:
            results = src.get_citations(paper_id, limit=limit)
            if results:
                return json.dumps({
                    "total": len(results),
                    "citations": _compact_papers(results),
                }, indent=2, default=str)
        except Exception:
            continue
    return json.dumps({"error": f"Could not get citations for '{paper_id}'"})


@mcp.tool()
def get_references(paper_id: str, limit: int = 20) -> str:
    """Get papers referenced by a given paper.

    Args:
        paper_id: Paper identifier (S2 ID, DOI, ArXiv:ID, OpenAlex ID, etc.)
        limit: Maximum number of referenced papers (1-1000, default 20)
    """
    paper_id = _normalize_paper_id(paper_id)
    for src in sources.reference_sources():
        try:
            results = src.get_references(paper_id, limit=limit)
            if results:
                return json.dumps({
                    "total": len(results),
                    "references": _compact_papers(results),
                }, indent=2, default=str)
        except Exception:
            continue
    return json.dumps({"error": f"Could not get references for '{paper_id}'"})


@mcp.tool()
def recommend_papers(paper_id: str, limit: int = 10) -> str:
    """Find similar/related papers using Semantic Scholar's recommendation engine.

    Args:
        paper_id: Paper identifier (S2 ID, DOI, ArXiv:ID, OpenAlex ID, etc.)
        limit: Maximum recommendations (1-500, default 10)
    """
    paper_id = _normalize_paper_id(paper_id)
    ids_to_try = [paper_id]
    if "arxiv" in paper_id.lower():
        arxiv_id = paper_id.split("arxiv.")[-1] if "arxiv." in paper_id.lower() else ""
        if arxiv_id:
            ids_to_try.insert(0, f"ArXiv:{arxiv_id}")
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
                return json.dumps({
                    "total": len(results),
                    "recommendations": _compact_papers(results),
                }, indent=2, default=str)
        except Exception:
            continue
    return json.dumps({"error": f"Could not get recommendations for '{paper_id}'"})


@mcp.tool()
def search_authors(query: str, limit: int = 5) -> str:
    """Search for academic authors/researchers.

    Args:
        query: Author name to search for
        limit: Maximum results (1-1000, default 5)
    """
    try:
        results = s2_client.search_authors(query, limit=limit)
        return json.dumps(results, indent=2, default=str)
    except Exception as e:
        return json.dumps({"error": f"Author search failed: {e}"})


@mcp.tool()
def download_paper(paper_id: str, save_dir: str = "") -> str:
    """Download a paper's PDF. Tries: Semantic Scholar open access, arXiv, bioRxiv/medRxiv.

    Args:
        paper_id: Paper identifier (S2 ID, DOI, ArXiv:ID, etc.)
        save_dir: Directory to save PDF (default: configured download directory)
    """
    save_path = save_dir or config.DOWNLOAD_DIR
    try:
        paper_info = s2_client.get_paper(paper_id)
    except Exception as e:
        return json.dumps({"error": f"Could not find paper '{paper_id}': {e}"})

    result = pdf_utils.download_paper(paper_info, save_path)
    return json.dumps(result, indent=2)


@mcp.tool()
def read_paper(paper_id: str, save_dir: str = "", max_pages: int = 0) -> str:
    """Download a paper's PDF and extract its text content.

    Args:
        paper_id: Paper identifier (S2 ID, DOI, ArXiv:ID, etc.)
        save_dir: Directory to save PDF (default: configured download directory)
        max_pages: Maximum pages to extract (0 = all pages)
    """
    save_path = save_dir or config.DOWNLOAD_DIR
    try:
        paper_info = s2_client.get_paper(paper_id)
    except Exception as e:
        return json.dumps({"error": f"Could not find paper '{paper_id}': {e}"})

    dl_result = pdf_utils.download_paper(paper_info, save_path)
    if not dl_result["success"]:
        return json.dumps(dl_result, indent=2)

    try:
        text = pdf_utils.extract_text(dl_result["file_path"], max_pages=max_pages)
        return text
    except Exception as e:
        return json.dumps({"error": f"PDF downloaded but text extraction failed: {e}"})


@mcp.tool()
def search_openreview(
    query: str,
    venue: str = "",
    limit: int = 10,
) -> str:
    """Search OpenReview for conference papers (ICLR, NeurIPS, ICML, etc.).
    No API key required. Returns papers with PDFs and review links.

    Args:
        query: Search query (e.g., "vision language action robot")
        venue: OpenReview venue ID filter (e.g., "ICLR.cc/2026/Conference",
               "NeurIPS.cc/2025/Conference"). Leave empty to search all venues.
        limit: Maximum results (1-50, default 10)
    """
    try:
        results = openreview_client.search_papers(
            query, max_results=limit,
            venue=venue or None,
        )
        if results:
            return json.dumps(results, indent=2, default=str)
        return json.dumps({"message": "No results found on OpenReview.", "query": query, "venue": venue})
    except Exception as e:
        return json.dumps({"error": f"OpenReview search failed: {e}"})


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
    Uses OpenAlex for citation data (impact-ranked, comprehensive coverage).

    Best for: understanding a research area's structure, finding related work,
    discovering influential papers, and mapping method evolution.

    Args:
        paper_ids: Comma-separated paper identifiers (S2 IDs, DOIs, or paper titles to search)
        max_hops: How many citation/reference hops to expand (1-3, default 2)
        max_papers: Maximum total papers in the graph (10-200, default 50)
        direction: "citations" (who cites this), "references" (what it cites), or "both"
        min_citations: Skip papers with fewer citations than this (helps focus on impactful work)
        topic_filter: Space-separated keywords to keep graph focused on a topic (e.g., "attention transformer")
    """
    max_hops = min(max(max_hops, 1), 3)
    max_papers = min(max(max_papers, 10), 200)

    seeds = [pid.strip() for pid in paper_ids.split(",") if pid.strip()]
    if not seeds:
        return json.dumps({"error": "No paper IDs provided"})

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
        results = s2_client.search_papers(seed, limit=1) if config.get_s2_api_key() else []
        if not results:
            results = openalex_client.search_papers(seed, limit=1)
        if results:
            seed_papers.append(results[0])

    if not seed_papers:
        return json.dumps({"error": "Could not find any of the specified papers"})

    result = graph.build_graph(
        seed_papers,
        max_hops=max_hops,
        max_papers=max_papers,
        direction=direction,
        min_citations=min_citations,
        topic_filter=topic_filter,
    )

    output = (
        result["summary"] + "\n\n"
        "```mermaid\n" + result["mermaid"] + "\n```"
    )
    return output


@mcp.tool()
def save_papers(
    paper_titles: str,
    collection: str = "default",
    notes: str = "",
) -> str:
    """Save papers to a persistent knowledge base collection for later reference.
    Papers persist across sessions. Use to build a reading list or track important papers.

    Args:
        paper_titles: Comma-separated paper titles or DOIs to save (searches and saves best match)
        collection: Collection name to save to (e.g., "rlhf-survey", "my-thesis")
        notes: Optional notes to attach to saved papers
    """
    titles = [t.strip() for t in paper_titles.split(",") if t.strip()]
    if not titles:
        return json.dumps({"error": "No paper titles provided"})

    papers_to_save = []
    for title in titles:
        try:
            results = openalex_client.search_papers(title, limit=1)
            if results:
                papers_to_save.append(results[0])
        except Exception:
            pass

    if not papers_to_save:
        return json.dumps({"error": "Could not find any of the specified papers"})

    result = kb.add_papers(papers_to_save, collection=collection, notes=notes)
    return json.dumps(result)


@mcp.tool()
def list_saved_papers(
    collection: str = "default",
    query: str = "",
    limit: int = 20,
) -> str:
    """List or search papers in a knowledge base collection.

    Args:
        collection: Collection name (default: "default"). Use "all" to list all collections.
        query: Optional search query to filter papers by keywords in title/abstract
        limit: Maximum papers to return
    """
    if collection == "all":
        collections = kb.list_collections()
        return json.dumps({"collections": collections})

    if query:
        papers = kb.search_kb(query, collection=collection, limit=limit)
    else:
        papers = kb.list_papers(collection=collection, limit=limit)

    compact = []
    for p in papers:
        compact.append({
            "title": p.get("title", ""),
            "year": p.get("year"),
            "citations": p.get("citation_count", 0),
            "notes": p.get("notes", ""),
        })

    return json.dumps({"collection": collection, "total": len(compact), "papers": compact}, indent=2)


@mcp.tool()
def discover_field(
    topic: str,
    max_papers: int = 30,
) -> str:
    """Map a research field: find surveys, foundational papers, and recent advances.
    Automatically searches for survey papers, expands references to find foundations,
    traces citations to find recent trends, and builds a citation graph.

    Best for: getting up to speed on a new field, understanding the landscape,
    finding key papers you should read, identifying research trends.

    Args:
        topic: Research topic to explore (e.g., "RLHF language model alignment")
        max_papers: Maximum papers to collect (10-50, default 30)
    """
    max_papers = min(max(max_papers, 10), 50)
    result = discovery.discover_field(topic, max_papers=max_papers)

    output = result["summary"] + "\n\n```mermaid\n" + result["mermaid"] + "\n```"
    return output


def main():
    mcp.run(transport="stdio", show_banner=False)


if __name__ == "__main__":
    main()
