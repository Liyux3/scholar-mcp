"""Citation graph exploration and construction.

Builds paper relationship graphs via multi-hop citation/reference traversal.
Primarily uses OpenAlex for citation data (impact-ranked, generous rate limits).
"""

import heapq
import time
from . import relevance
from . import sources


def _expansion_priority(paper: dict) -> float:
    """Higher = expand first. Blends citation count with recency."""
    cites = paper.get("citation_count", 0) or 0
    year = paper.get("year")
    if not year or not cites:
        return float(cites)
    from datetime import datetime
    age = max(datetime.now().year - year, 1)
    return cites + (cites / age) * 2


def _get_openalex_id(paper: dict) -> str | None:
    """Extract OpenAlex-compatible ID from a paper dict.
    Prefers W-ID, falls back to DOI, then constructs DOI from ArXiv ID.
    """
    pid = paper.get("paper_id", "")
    if pid.startswith("W"):
        return pid
    ext = paper.get("external_ids") or {}
    oa_id = ext.get("OpenAlex", "")
    if oa_id:
        return oa_id
    doi = ext.get("DOI", "")
    if doi:
        return doi
    arxiv_id = ext.get("ArXiv", "")
    if arxiv_id:
        return f"10.48550/arXiv.{arxiv_id}"
    return None


def _paper_to_node(paper: dict, depth: int) -> dict:
    """Convert paper dict to graph node."""
    return {
        "id": paper.get("paper_id") or _get_openalex_id(paper) or "",
        "title": paper.get("title", ""),
        "year": paper.get("year"),
        "citation_count": paper.get("citation_count", 0),
        "venue": paper.get("venue", ""),
        "authors": [a[:40] if isinstance(a, str) else a for a in (paper.get("authors") or [])[:5]],
        "source": paper.get("source", ""),
        "depth": depth,
    }


def _get_any_id(paper: dict) -> str:
    """Pick the best ID for cross-source lookup."""
    ext = paper.get("external_ids") or {}
    for key in ("DOI", "OpenAlex", "ArXiv"):
        val = ext.get(key, "")
        if val:
            return val
    return paper.get("paper_id", "")


def _fetch_related(paper: dict, relation: str, limit: int, delay: float) -> list[dict]:
    """Fetch citations or references from all capable sources in parallel."""
    paper_id = _get_any_id(paper)
    if not paper_id:
        return []

    if relation == "citations":
        # Pass the title so OpenAlex can resolve arXiv papers, which it cannot
        # do from an id alone. Without it OpenAlex contributes nothing and the
        # graph is built from S2's recency-ordered citations only.
        source_results = sources.parallel_citations(
            paper_id, limit=limit, title=paper.get("title", ""))
    else:
        source_results = sources.parallel_references(paper_id, limit=limit)

    all_results = []
    seen_titles = set()
    for sr in source_results:
        for r in sr.results:
            nt = relevance._normalize_title(r.get("title", ""))
            if nt and nt not in seen_titles:
                seen_titles.add(nt)
                all_results.append(r)

    time.sleep(delay)
    return all_results[:limit]


def _matches_topic(paper: dict, topic_keywords: list[str]) -> bool:
    """Check if paper title/abstract contains any of the topic keywords."""
    if not topic_keywords:
        return True
    text = ((paper.get("title") or "") + " " + (paper.get("abstract") or "")).lower()
    return any(kw in text for kw in topic_keywords)


def build_graph(
    seed_papers: list[dict],
    max_hops: int = 2,
    max_papers: int = 50,
    direction: str = "both",
    min_citations: int = 0,
    citations_per_paper: int = 10,
    references_per_paper: int = 10,
    delay: float = 0.3,
    topic_filter: str = "",
) -> dict:
    """Build citation graph from seed papers via BFS expansion.

    Args:
        seed_papers: list of paper dicts (from search_papers or get_paper)
        max_hops: maximum citation/reference expansion depth
        max_papers: stop after collecting this many papers
        direction: "citations" (who cites this), "references" (what it cites), or "both"
        min_citations: skip papers below this citation count
        citations_per_paper: max citations to fetch per paper
        references_per_paper: max references to fetch per paper
        delay: seconds between API calls
        topic_filter: space-separated keywords to filter expanded papers (only keep relevant ones)

    Returns:
        dict with nodes, edges, and stats
    """
    topic_keywords = [w.lower() for w in topic_filter.split() if len(w) > 2] if topic_filter else []

    nodes = {}
    edges = []
    seen_titles = set()
    heap = []
    counter = 0

    for p in seed_papers:
        nt = relevance._normalize_title(p.get("title", ""))
        if not nt or nt in seen_titles:
            continue
        seen_titles.add(nt)
        node = _paper_to_node(p, depth=0)
        node_id = node["id"] or nt
        nodes[node_id] = node
        prio = _expansion_priority(p)
        heapq.heappush(heap, (-prio, counter, p, node_id, 0))
        counter += 1

    while heap and len(nodes) < max_papers:
        _, _, paper, parent_id, depth = heapq.heappop(heap)

        if depth >= max_hops:
            continue

        oa_id = _get_openalex_id(paper)

        if direction in ("citations", "both"):
            try:
                cites = _fetch_related(paper, "citations", citations_per_paper, delay)
                for c in cites:
                    if len(nodes) >= max_papers:
                        break
                    if min_citations > 0 and (c.get("citation_count", 0) or 0) < min_citations:
                        continue
                    if not _matches_topic(c, topic_keywords):
                        continue
                    ct = relevance._normalize_title(c.get("title", ""))
                    if not ct or ct in seen_titles:
                        cid = None
                        for nid, n in nodes.items():
                            if relevance._normalize_title(n["title"]) == ct:
                                cid = nid
                                break
                        if cid:
                            edges.append({"source": cid, "target": parent_id, "type": "cites"})
                        continue
                    seen_titles.add(ct)
                    node = _paper_to_node(c, depth=depth + 1)
                    cid = node["id"] or ct
                    nodes[cid] = node
                    edges.append({"source": cid, "target": parent_id, "type": "cites"})
                    heapq.heappush(heap, (-_expansion_priority(c), counter, c, cid, depth + 1))
                    counter += 1
            except Exception:
                pass

        if direction in ("references", "both"):
            try:
                refs = _fetch_related(paper, "references", references_per_paper, delay)
                for r in refs:
                    if len(nodes) >= max_papers:
                        break
                    if min_citations > 0 and (r.get("citation_count", 0) or 0) < min_citations:
                        continue
                    if not _matches_topic(r, topic_keywords):
                        continue
                    rt = relevance._normalize_title(r.get("title", ""))
                    if not rt or rt in seen_titles:
                        rid = None
                        for nid, n in nodes.items():
                            if relevance._normalize_title(n["title"]) == rt:
                                rid = nid
                                break
                        if rid:
                            edges.append({"source": parent_id, "target": rid, "type": "cites"})
                        continue
                    seen_titles.add(rt)
                    node = _paper_to_node(r, depth=depth + 1)
                    rid = node["id"] or rt
                    nodes[rid] = node
                    edges.append({"source": parent_id, "target": rid, "type": "cites"})
                    heapq.heappush(heap, (-_expansion_priority(r), counter, r, rid, depth + 1))
                    counter += 1
            except Exception:
                pass

    unique_edges = []
    edge_set = set()
    for e in edges:
        key = (e["source"], e["target"])
        if key not in edge_set:
            edge_set.add(key)
            unique_edges.append(e)

    node_list = list(nodes.values())
    stats = {
        "total_nodes": len(nodes),
        "total_edges": len(unique_edges),
        "max_depth": max(n["depth"] for n in node_list) if node_list else 0,
        "seed_count": sum(1 for n in node_list if n["depth"] == 0),
    }

    analytics = _analyze(node_list, unique_edges)
    summary = _summarize(node_list, unique_edges, stats, analytics)
    mermaid = _to_mermaid(node_list, unique_edges, analytics)

    return {
        "summary": summary,
        "mermaid": mermaid,
        "nodes": node_list,
        "edges": unique_edges,
        "stats": stats,
    }


def _simple_pagerank(G, alpha: float = 0.85, max_iter: int = 50) -> dict:
    """Power-iteration PageRank without scipy."""
    nodes = list(G.nodes())
    n = len(nodes)
    if n == 0:
        return {}
    pr = {node: 1.0 / n for node in nodes}
    for _ in range(max_iter):
        new_pr = {}
        for node in nodes:
            rank = (1 - alpha) / n
            for pred in G.predecessors(node):
                out_deg = G.out_degree(pred)
                if out_deg > 0:
                    rank += alpha * pr[pred] / out_deg
            new_pr[node] = rank
        pr = new_pr
    return pr


def _analyze(nodes: list[dict], edges: list[dict]) -> dict:
    """Compute graph analytics using networkx if available."""
    try:
        import networkx as nx
    except ImportError:
        return {}

    if len(nodes) < 3:
        return {}

    G = nx.DiGraph()
    for n in nodes:
        G.add_node(n["id"])
    for e in edges:
        if e["source"] in G and e["target"] in G:
            G.add_edge(e["source"], e["target"])

    try:
        pagerank = nx.pagerank(G)
    except Exception:
        pagerank = _simple_pagerank(G)
    betweenness = nx.betweenness_centrality(G)

    id_to_node = {n["id"]: n for n in nodes}
    pivots = []
    if betweenness:
        mean_btw = sum(betweenness.values()) / len(betweenness)
        for nid, btw in betweenness.items():
            if btw > mean_btw * 2 and nid in id_to_node:
                pivots.append(nid)

    top_pr = sorted(pagerank.items(), key=lambda x: x[1], reverse=True)[:5]

    return {
        "pagerank": {nid: round(pr, 4) for nid, pr in top_pr},
        "pivots": pivots,
        "betweenness": betweenness,
    }


def _summarize(nodes: list[dict], edges: list[dict], stats: dict,
               analytics: dict = None) -> str:
    """Compact summary with stats, structural insights, and analytics."""
    lines = []
    lines.append(f"Graph: {stats['total_nodes']} papers, "
                 f"{stats['total_edges']} connections, depth {stats['max_depth']}")

    years = [n["year"] for n in nodes if n.get("year")]
    if years:
        lines.append(f"Timeline: {min(years)}-{max(years)}")

    out_degree = {}
    for e in edges:
        out_degree[e["source"]] = out_degree.get(e["source"], 0) + 1
    id_to_title = {n["id"]: n["title"][:45] for n in nodes}
    hubs = sorted(out_degree.items(), key=lambda x: x[1], reverse=True)[:3]
    if hubs:
        hub_strs = [f"{id_to_title.get(nid, '?')[:30]} ({deg})" for nid, deg in hubs]
        lines.append(f"Hubs: {', '.join(hub_strs)}")

    if analytics:
        pivots = analytics.get("pivots", [])
        if pivots:
            pivot_names = [id_to_title.get(p, "?")[:35] for p in pivots[:3]]
            lines.append(f"Bridge papers: {', '.join(pivot_names)}")

        pr = analytics.get("pagerank", {})
        if pr:
            top = list(pr.items())[:3]
            pr_strs = [f"{id_to_title.get(nid, '?')[:25]}" for nid, _ in top]
            lines.append(f"Most central: {', '.join(pr_strs)}")

    return "\n".join(lines)


def _short_label(title: str, max_len: int = 30) -> str:
    """Shorten title for graph labels."""
    title = title.replace('"', "'")
    if len(title) <= max_len:
        return title
    return title[:max_len - 3] + "..."


def _to_mermaid(nodes: list[dict], edges: list[dict],
                analytics: dict = None) -> str:
    """Generate Mermaid flowchart from graph data."""
    lines = ["graph TD"]
    pivot_ids = set(analytics.get("pivots", [])) if analytics else set()

    id_map = {}
    for i, n in enumerate(nodes):
        safe_id = f"n{i}"
        id_map[n["id"]] = safe_id
        label = _short_label(n["title"], max_len=35)
        year = n.get("year", "")
        cites = n.get("citation_count", 0)
        cite_str = f"{cites:,}" if cites else "0"
        tag = f"{year}, {cite_str}c" if year else f"{cite_str}c"
        if n["depth"] == 0:
            lines.append(f'    {safe_id}["{label}<br/>{tag}"]')
        elif n["id"] in pivot_ids:
            lines.append(f'    {safe_id}{{{{"{label}<br/>{tag}"}}}}')
        else:
            lines.append(f'    {safe_id}("{label}<br/>{tag}")')

    for e in edges:
        src = id_map.get(e["source"])
        tgt = id_map.get(e["target"])
        if src and tgt:
            lines.append(f"    {src} --> {tgt}")

    max_cites = max((n.get("citation_count", 0) for n in nodes), default=1) or 1
    for i, n in enumerate(nodes):
        cites = n.get("citation_count", 0) or 0
        if n["depth"] == 0:
            lines.append(f"    style n{i} fill:#e1f5fe,stroke:#01579b,stroke-width:3px")
        elif n["id"] in pivot_ids:
            lines.append(f"    style n{i} fill:#c8e6c9,stroke:#2e7d32,stroke-width:2px")
        elif cites > max_cites * 0.5:
            lines.append(f"    style n{i} fill:#fff3e0,stroke:#e65100")
        elif cites > max_cites * 0.1:
            lines.append(f"    style n{i} fill:#f3e5f5,stroke:#7b1fa2")

    return "\n".join(lines)
