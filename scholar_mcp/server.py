import json
from fastmcp import FastMCP
from . import config
from . import s2_client
from . import arxiv_client
from . import openreview_client
from . import core_client
from . import pubmed_client
from . import scholar_client
from . import pdf_utils

mcp = FastMCP("scholar-mcp")


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
    """Search for academic papers across 214M+ papers in Semantic Scholar.
    Falls back to arXiv, CORE, PubMed, then Google Scholar if Semantic Scholar is unavailable.

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

    sources_tried = []

    # Primary: Semantic Scholar
    try:
        results = s2_client.search_papers(
            query, limit=limit,
            year=year or None,
            venue=venue or None,
            fields_of_study=fos_list,
            min_citations=min_citations,
            open_access_only=open_access_only,
        )
        if results:
            return json.dumps(results, indent=2, default=str)
    except Exception as e:
        sources_tried.append(f"semantic_scholar: {type(e).__name__}")

    # Fallback 1: arXiv (sorted by relevance)
    try:
        results = arxiv_client.search_papers(query, max_results=limit)
        if results:
            return json.dumps(results, indent=2, default=str)
    except Exception as e:
        sources_tried.append(f"arxiv: {type(e).__name__}")

    # Fallback 2: OpenReview (only if credentials configured)
    if openreview_client.is_configured():
        try:
            results = openreview_client.search_papers(query, max_results=limit)
            if results:
                return json.dumps(results, indent=2, default=str)
        except Exception as e:
            sources_tried.append(f"openreview: {type(e).__name__}")

    # Fallback 3: CORE
    try:
        results = core_client.search_papers(query, limit=limit)
        if results:
            return json.dumps(results, indent=2, default=str)
    except Exception as e:
        sources_tried.append(f"core: {type(e).__name__}")

    # Fallback 4: PubMed
    try:
        results = pubmed_client.search_papers(query, max_results=limit)
        if results:
            return json.dumps(results, indent=2, default=str)
    except Exception as e:
        sources_tried.append(f"pubmed: {type(e).__name__}")

    # Fallback 5: Google Scholar
    try:
        results = scholar_client.search_papers(query, max_results=limit)
        if results:
            return json.dumps(results, indent=2, default=str)
    except Exception as e:
        sources_tried.append(f"google_scholar: {type(e).__name__}")

    return json.dumps({"error": "Search failed on all sources.", "sources_tried": sources_tried})


@mcp.tool()
def get_paper(paper_id: str) -> str:
    """Get detailed information about a specific paper.
    Accepts: Semantic Scholar ID, DOI, ArXiv ID (prefix with "ArXiv:"),
    PMID (prefix with "PMID:"), or a Semantic Scholar URL.

    Args:
        paper_id: Paper identifier (e.g., "649def34f8be52c8b66281af98ae884c09aef38b",
                  "10.1038/nature12373", "ArXiv:2106.09685", "PMID:19872477")
    """
    try:
        result = s2_client.get_paper(paper_id)
        return json.dumps(result, indent=2, default=str)
    except Exception as e:
        return json.dumps({"error": f"Could not find paper '{paper_id}': {e}"})


@mcp.tool()
def get_citations(paper_id: str, limit: int = 20) -> str:
    """Get papers that cite a given paper.

    Args:
        paper_id: Paper identifier (S2 ID, DOI, ArXiv:ID, etc.)
        limit: Maximum number of citing papers (1-1000, default 20)
    """
    try:
        results = s2_client.get_citations(paper_id, limit=limit)
        return json.dumps({
            "total_returned": len(results),
            "citations": results,
        }, indent=2, default=str)
    except Exception as e:
        return json.dumps({"error": f"Could not get citations: {e}"})


@mcp.tool()
def get_references(paper_id: str, limit: int = 20) -> str:
    """Get papers referenced by a given paper.

    Args:
        paper_id: Paper identifier (S2 ID, DOI, ArXiv:ID, etc.)
        limit: Maximum number of referenced papers (1-1000, default 20)
    """
    try:
        results = s2_client.get_references(paper_id, limit=limit)
        return json.dumps({
            "total_returned": len(results),
            "references": results,
        }, indent=2, default=str)
    except Exception as e:
        return json.dumps({"error": f"Could not get references: {e}"})


@mcp.tool()
def recommend_papers(paper_id: str, limit: int = 10) -> str:
    """Find similar/related papers using Semantic Scholar's recommendation engine.

    Args:
        paper_id: Paper identifier (S2 ID, DOI, ArXiv:ID, etc.)
        limit: Maximum recommendations (1-500, default 10)
    """
    try:
        results = s2_client.get_recommendations(paper_id, limit=limit)
        return json.dumps(results, indent=2, default=str)
    except Exception as e:
        return json.dumps({"error": f"Could not get recommendations: {e}"})


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


def main():
    mcp.run(transport="stdio", show_banner=False)


if __name__ == "__main__":
    main()
