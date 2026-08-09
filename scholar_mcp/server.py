import re
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
from . import expansion
from . import knowledge_base as kb
from . import sources
from . import traversal
from . import vault

mcp = FastMCP("scholar-mcp")

INTERNAL_FETCH_LIMIT = 100


def _lookup_title(paper_id: str) -> str:
    """Fetch just the title of a paper, for id resolution.

    Costs one request against the first source that answers. Only called when
    the caller has not already fetched the paper.
    """
    for src in sources.all_sources():
        if not src.get_paper or not src.available():
            continue
        try:
            paper = src.get_paper(paper_id)
            if paper and paper.get("title"):
                return paper["title"]
        except Exception:
            continue
    return ""


def _get_best_id(paper: dict) -> str:
    ext = paper.get("external_ids") or {}
    for key in ("DOI", "OpenAlex", "ArXiv"):
        if ext.get(key):
            return ext[key]
    pid = paper.get("paper_id", "")
    if pid and not pid.startswith("W"):
        return pid
    return ""


# Seeds expanded from. Each costs a fan-out per channel, so this is the main
# lever on how much traffic expansion generates.
EXPANSION_SEEDS = 3

# Weight kept from the first reranking pass when a paper was scored twice.
# Both passes score the same paper against the same query, so disagreement is
# noise from a different candidate set rather than new information; averaging
# damps it.
PASS_BLEND = 0.2


def _smooth_across_passes(papers: list[dict]) -> None:
    """Average a paper's two rerank scores, where it has two.

    Only papers present before expansion have a first-pass score. Blending a
    missing one as zero would multiply every expanded paper by 1 - PASS_BLEND,
    a flat 20% penalty applied for no reason other than arriving late, which
    works directly against the point of expanding.
    """
    for paper in papers:
        first = paper.get("_iter1_score")
        if first is None:
            continue
        second = paper.get("_rerank_score", 0.0)
        paper["_rerank_score"] = PASS_BLEND * first + (1 - PASS_BLEND) * second


def _pipeline(
    dispatch: str,
    query_or_id: str,
    limit: int,
    rerank_query: str = "",
    raw_query: str = "",
    intent: str = "",
    paper_title: str = "",
    expand_citations: bool = True,
    expand_min_pool: int = 10,
    expand_limit: int = 20,
    expand_channels: list[str] | None = None,
    **kwargs,
) -> tuple[list[dict], list[dict]]:
    """Shared pipeline: parallel fetch -> dedup -> rerank -> (expand) -> rank -> truncate.

    Args:
        dispatch: "search" | "citations" | "references"
        query_or_id: search query (optimized) or paper ID
        limit: final output size
        rerank_query: if set, run reranker with this query
        raw_query: original unoptimized query, sent to semantic sources
        paper_title: title of the seed paper, for citation-source id resolution
        expand_citations: if True, expand the pool from the top results
        expand_min_pool: skip expansion when fewer results than this came back,
            since seeds picked from a thin pool are unlikely to be good ones
        expand_limit: how many papers each channel fetches per seed
        expand_channels: channel names to run; None means expansion.CHANNELS
        **kwargs: passed to source search (year, fields_of_study, etc.)
    Returns:
        (ranked_papers, source_reports)
    """
    if dispatch == "search":
        short_q = relevance.optimize_query_short(raw_query or query_or_id)
        source_results = sources.parallel_search(query_or_id, limit=INTERNAL_FETCH_LIMIT, raw_query=raw_query, short_query=short_q, **kwargs)
    elif dispatch == "citations":
        # title lets OpenAlex resolve arXiv papers, which it cannot do from an
        # id. Without it the results come from S2 alone, which orders
        # citations by recency rather than impact.
        source_results = sources.parallel_citations(
            query_or_id, limit=INTERNAL_FETCH_LIMIT, title=paper_title)
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

        for p in all_papers:
            p["_iter1_score"] = p.get("_rerank_score", 0.0)

        if expand_citations and len(all_papers) >= expand_min_pool:
            by_channel = expansion.expand(
                all_papers[:EXPANSION_SEEDS],
                intent=intent,
                per_seed_limit=expand_limit,
                channels=expand_channels,
            )
            found = [p for papers in by_channel.values() for p in papers]

            if found:
                for p in found:
                    relevance.tag_source_ranks([p], "expansion")
                all_papers.extend(found)
                all_papers = relevance.deduplicate(all_papers)
                if len(all_papers) > 500:
                    old = [p for p in all_papers if p.get("_rerank_score")]
                    new = [p for p in all_papers if not p.get("_rerank_score")]
                    old.sort(key=lambda p: -p.get("_rerank_score", 0))
                    old_keep = old[:100]
                    new_ranked = relevance.rank_final(new)
                    new_keep = new_ranked[:500 - len(old_keep)]
                    all_papers = old_keep + new_keep
                all_papers = relevance.rerank(rerank_query, all_papers, top_n=min(limit * 3, len(all_papers)), intent=intent)
                _smooth_across_passes(all_papers)
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


_DATE_PREFIX = re.compile(
    r"^\s*(\d{4})(?:-(\d{1,2})(?:-(\d{1,2}))?)?"
)


def _publication_sort_key(paper: dict) -> tuple[int, int, int]:
    """Return a comparable newest-first key across heterogeneous sources.

    Source clients normally expose an ISO ``publication_date`` string, but
    some records only have ``year`` and older/cached records may store that
    year as either an int or a string.  Sorting the raw values mixes ``str``
    and ``int``, which Python 3 cannot compare.
    """
    for raw in (paper.get("publication_date"), paper.get("year")):
        if raw is None or raw == "":
            continue
        match = _DATE_PREFIX.match(str(raw))
        if not match:
            continue
        year = int(match.group(1))
        month = int(match.group(2) or 0)
        day = int(match.group(3) or 0)
        if not 1 <= month <= 12:
            month = 0
        if not 1 <= day <= 31:
            day = 0
        return year, month, day
    return 0, 0, 0


def _yaml(data: dict) -> str:
    return yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False)


ERROR_MAX_CHARS = 160


def _clean_error(message: str) -> str:
    """Trim an exception message for display.

    httpx embeds the full request URL, which for several sources carries the
    API key as a query parameter. That must not reach the caller's context or
    any log. The status line is the useful part; the URL is not.
    """
    if not message:
        return message
    message = re.sub(r"https?://\S+", "<url>", message)
    message = " ".join(message.split())
    if len(message) > ERROR_MAX_CHARS:
        message = message[:ERROR_MAX_CHARS].rstrip() + "..."
    return message


def _meta_block(source_reports: list[dict], **extra) -> dict:
    """Summarise which sources answered, expanding only the ones that did not.

    Listing all 13 in full costs ~60 lines of the caller's context per search,
    almost all of it `error: null`. Healthy sources are reduced to
    "name (count)"; anything not ok keeps its status, latency and a trimmed
    error, since that is the case worth reading.
    """
    healthy = [r for r in source_reports if r["status"] == "ok"]
    degraded = [r for r in source_reports if r["status"] != "ok"]

    meta = {
        "sources_used": [f"{r['source']} ({r['count']})" for r in
                         sorted(healthy, key=lambda r: -r["count"])],
    }
    if degraded:
        meta["sources_unavailable"] = [
            {"source": r["source"], "status": r["status"],
             "error": _clean_error(r.get("error") or "")}
            for r in degraded
        ]
    return {**meta, **extra}


@mcp.tool()
def search_papers(
    query: str,
    limit: int = 10,
    year: str = "",
    venue: str = "",
    fields_of_study: str = "",
    paper_types: str = "",
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
        paper_types: Comma-separated types (e.g., "JournalArticle,Conference,Review,Book,Dataset"). Default: all types.
        min_citations: Minimum citation count filter (default 0)
        open_access_only: Only return papers with free PDF access
        sort: Sort results by "citations" (most cited first) or "date" (newest first). Default: relevance.
        intent: Ranking preference. "foundational" for seminal papers, "recent" for latest work, "survey" for reviews, "method" for specific techniques. Default: balanced relevance.
    """
    fos_list = [f.strip() for f in fields_of_study.split(",") if f.strip()] if fields_of_study else None
    type_list = [t.strip() for t in paper_types.split(",") if t.strip()] if paper_types else None
    search_query = relevance.optimize_query(query)

    search_kwargs = {}
    if year:
        search_kwargs["year"] = year
    if fos_list:
        search_kwargs["fields_of_study"] = fos_list
    if type_list:
        search_kwargs["publication_types"] = type_list
    if min_citations > 0:
        search_kwargs["min_citations"] = min_citations
    if open_access_only:
        search_kwargs["open_access_only"] = True

    results, reports = _pipeline("search", search_query, limit * 3, rerank_query=query, raw_query=query, intent=intent, expand_citations=True, **search_kwargs)

    if fos_list:
        results = relevance.filter_by_fields(results, fos_list)
    if min_citations > 0:
        results = [p for p in results if (p.get("citation_count") or 0) >= min_citations]

    if sort == "citations":
        results.sort(key=lambda p: -(p.get("citation_count", 0) or 0))
    elif sort == "date":
        results.sort(key=_publication_sort_key, reverse=True)

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
        # OpenAlex needs a title to resolve arXiv ids. Fetch one if the caller
        # did not ask for detail, otherwise citations for arXiv papers come
        # from S2 alone and arrive ordered by recency rather than impact.
        title = (output.get("paper") or {}).get("title", "")
        if not title:
            title = _lookup_title(paper_id)
        cites, reports = _pipeline("citations", paper_id, limit, paper_title=title)
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
def recommend_papers(paper_id: str, relation: str = "similar", limit: int = 10) -> str:
    """Find related papers by a chosen citation-graph relation.

    "Related" is several different questions, and which one you want depends
    on what you are doing:

        similar     embedding neighbours (SPECTER2). Same topic, possibly
                    different vocabulary. Good default.
        foundations what this paper cites. Where the ideas came from.
        descendants what cites this paper, impact-ordered. What came after.
        peers       what is cited alongside this paper. Its intellectual
                    cohort, which is usually what "related work" means.
        kin         what cites the same works this paper does. Shared method
                    rather than shared topic, so this is the relation that
                    crosses field boundaries: two papers can be coupled
                    without sharing any vocabulary.

    Args:
        paper_id: Paper identifier (S2 ID, DOI, ArXiv:ID, OpenAlex ID, etc.)
        relation: similar | foundations | descendants | peers | kin
        limit: Maximum results (default 10)
    """
    title = _lookup_title(paper_id)

    if relation == "peers":
        results = traversal.co_citation(paper_id, title=title, limit=limit)
    elif relation == "kin":
        results = traversal.bibliographic_coupling(paper_id, title=title, limit=limit)
    elif relation == "foundations":
        results = [p for sr in sources.parallel_references(paper_id, limit=limit)
                   for p in sr.results][:limit]
    elif relation == "descendants":
        results = [p for sr in sources.parallel_citations(paper_id, limit=limit, title=title)
                   for p in sr.results][:limit]
    elif relation == "similar":
        results = []
        for pid in _id_variants(paper_id):
            try:
                results = s2_client.get_recommendations(pid, limit=limit)
                if results:
                    break
            except Exception:
                continue
    else:
        return _yaml({"error": f"Unknown relation '{relation}'. Use one of: "
                               "similar, foundations, descendants, peers, kin"})

    if not results:
        return _yaml({"error": f"No '{relation}' results for '{paper_id}'",
                      "seed_title": title or None})

    return _yaml({
        "relation": relation,
        "seed": title or paper_id,
        "papers": [_format_compact(p) for p in results],
        "total": len(results),
    })


def _id_variants(paper_id: str) -> list[str]:
    """IDs to try for S2, which will not accept an OpenAlex W-id."""
    variants = [paper_id]
    if paper_id.startswith("W"):
        oa_paper = openalex_client.get_paper_by_id(paper_id)
        if oa_paper:
            doi = (oa_paper.get("external_ids") or {}).get("DOI", "")
            if doi:
                variants.insert(0, doi)
    return variants


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
    link_citations: bool = False,
) -> str:
    """Manage saved papers in the knowledge base.

    Args:
        action: "save", "list", "search", "collections", or "export"
            ("export" writes an Obsidian-compatible markdown vault)
        paper_titles: Comma-separated paper titles or DOIs (for action="save")
        collection: Collection name (default: "default")
        query: Search query (for action="search")
        notes: Notes to attach to saved papers (for action="save")
        limit: Maximum papers to return
        link_citations: for action="export", resolve each paper's reference
            list so notes link along real citations. Costs one request per
            paper, and is what actually connects the graph: on a 71-paper
            collection, stored metadata yields 26 links and 13% of notes
            connected, while reference lists yield 342 links and 73%.
    """
    if action == "collections":
        return _yaml({"collections": kb.list_collections()})

    if action == "export":
        # Markdown mirror of the JSONL store. The JSONL stays authoritative;
        # the vault exists so the collection can be browsed, annotated and
        # linked in Obsidian, and walked by following wikilinks.
        papers = kb.list_papers(collection=collection, limit=10000)
        if not papers:
            return _yaml({"error": f"Collection '{collection}' is empty"})
        return _yaml(vault.export_collection(papers, collection,
                                             link_citations=link_citations))

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

    output = {
        "collection": collection,
        "total": len(papers),
        "papers": [{"title": p.get("title", ""), "year": p.get("year"), "citations": p.get("citation_count", 0), "notes": p.get("notes", "")} for p in papers],
    }

    # An empty result from the default collection reads as "nothing saved",
    # even when other collections are full. discover_field and download both
    # write to named collections, so this is the common case.
    if not papers:
        others = [c for c in kb.list_collections()
                  if c["name"] != collection and c.get("papers")]
        if others:
            output["other_collections"] = [
                f"{c['name']} ({c['papers']})" for c in others
            ]

    return _yaml(output)


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
def scholar_status(check_reranker: bool = False) -> str:
    """Check scholar-mcp server version, available sources, and KB collections.

    Args:
        check_reranker: probe the DashScope API instead of only checking that a
            key is configured. Costs one request but catches an expired or
            unfunded account, which otherwise shows up as slow, lower-quality
            results with no other signal.
    """
    available = [s.name for s in sources.search_sources()]
    cite_sources = [s.name for s in sources.citation_sources()]
    colls = kb.list_collections()

    if config.DASHSCOPE_API_KEY:
        reranker = "configured (not probed)"
        if check_reranker:
            probe = relevance._rerank_dashscope(
                "test", [{"title": "test paper", "abstract": ""}], top_n=1)
            reranker = "ok" if probe is not None else "unavailable, falling back to FlashRank"
    else:
        reranker = "no key, using FlashRank"

    return _yaml({
        "version": "0.7.0",
        "tools": 10,
        "search_sources": available,
        "citation_sources": cite_sources,
        "kb_collections": [{"name": c["name"], "papers": c["papers"]} for c in colls],
        "s2_key": bool(config.get_s2_api_key()),
        "reranker": reranker,
        "cache_enabled": True,
    })


def main():
    mcp.run(transport="stdio", show_banner=False)


if __name__ == "__main__":
    main()
