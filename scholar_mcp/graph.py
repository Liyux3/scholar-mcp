"""Citation graph exploration and construction.

Builds paper relationship graphs via multi-hop citation/reference traversal.
Primarily uses OpenAlex for citation data (impact-ranked, generous rate limits).
"""

import time
from collections import deque
from . import config
from . import openalex_client
from . import s2_client
from . import relevance


def _get_openalex_id(paper: dict) -> str | None:
    """Extract OpenAlex-compatible ID from a paper dict.
    Prefers W-ID (works with citation filters), falls back to DOI.
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


def _fetch_related(paper: dict, relation: str, limit: int, delay: float) -> list[dict]:
    """Fetch citations or references, trying OpenAlex first then S2."""
    oa_id = _get_openalex_id(paper)
    results = []
    if oa_id:
        try:
            fn = openalex_client.get_citations if relation == "citations" else openalex_client.get_references
            results = fn(oa_id, limit=limit)
            time.sleep(delay)
        except Exception:
            pass
    if len(results) < limit // 2 and config.get_s2_api_key():
        ext = paper.get("external_ids") or {}
        s2_id = ext.get("DOI", "") or ext.get("ArXiv", "")
        pid = paper.get("paper_id", "")
        if pid and not pid.startswith("W"):
            s2_id = s2_id or pid
        if s2_id:
            try:
                fn = s2_client.get_citations if relation == "citations" else s2_client.get_references
                s2_results = fn(s2_id, limit=limit)
                time.sleep(delay)
                existing_titles = {relevance._normalize_title(r.get("title", "")) for r in results}
                for r in s2_results:
                    nt = relevance._normalize_title(r.get("title", ""))
                    if nt and nt not in existing_titles:
                        results.append(r)
                        existing_titles.add(nt)
            except Exception:
                pass
    return results


def build_graph(
    seed_papers: list[dict],
    max_hops: int = 2,
    max_papers: int = 50,
    direction: str = "both",
    min_citations: int = 0,
    citations_per_paper: int = 10,
    references_per_paper: int = 10,
    delay: float = 0.3,
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

    Returns:
        dict with nodes, edges, and stats
    """
    nodes = {}
    edges = []
    seen_titles = set()
    queue = deque()

    for p in seed_papers:
        nt = relevance._normalize_title(p.get("title", ""))
        if not nt or nt in seen_titles:
            continue
        seen_titles.add(nt)
        node = _paper_to_node(p, depth=0)
        node_id = node["id"] or nt
        nodes[node_id] = node
        queue.append((p, node_id, 0))

    while queue and len(nodes) < max_papers:
        paper, parent_id, depth = queue.popleft()

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
                    queue.append((c, cid, depth + 1))
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
                    queue.append((r, rid, depth + 1))
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

    summary = _summarize(node_list, unique_edges, stats)
    mermaid = _to_mermaid(node_list, unique_edges)

    return {
        "summary": summary,
        "mermaid": mermaid,
        "nodes": node_list,
        "edges": unique_edges,
        "stats": stats,
    }


def _summarize(nodes: list[dict], edges: list[dict], stats: dict) -> str:
    """Compact summary with stats and structural insights. No paper lists (those are in mermaid)."""
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

    return "\n".join(lines)


def _short_label(title: str, max_len: int = 30) -> str:
    """Shorten title for graph labels."""
    title = title.replace('"', "'")
    if len(title) <= max_len:
        return title
    return title[:max_len - 3] + "..."


def _to_mermaid(nodes: list[dict], edges: list[dict]) -> str:
    """Generate Mermaid flowchart from graph data."""
    lines = ["graph TD"]

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
        else:
            lines.append(f'    {safe_id}("{label}<br/>{tag}")')

    for e in edges:
        src = id_map.get(e["source"])
        tgt = id_map.get(e["target"])
        if src and tgt:
            lines.append(f"    {src} --> {tgt}")

    for i, n in enumerate(nodes):
        if n["depth"] == 0:
            lines.append(f"    style n{i} fill:#e1f5fe,stroke:#01579b")

    return "\n".join(lines)
