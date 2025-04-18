"""Citation graph exploration and construction.

Builds paper relationship graphs via multi-hop citation/reference traversal.
Primarily uses OpenAlex for citation data (impact-ranked, generous rate limits).
"""

import time
from collections import deque
from . import openalex_client
from . import s2_client
from . import relevance


def _get_openalex_id(paper: dict) -> str | None:
    """Extract OpenAlex-compatible ID from a paper dict."""
    ext = paper.get("external_ids") or {}
    doi = ext.get("DOI", "")
    if doi:
        return doi
    oa_id = ext.get("OpenAlex", "")
    if oa_id:
        return oa_id
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

        if direction in ("citations", "both") and oa_id:
            try:
                cites = openalex_client.get_citations(oa_id, limit=citations_per_paper)
                time.sleep(delay)
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

        if direction in ("references", "both") and oa_id:
            try:
                refs = openalex_client.get_references(oa_id, limit=references_per_paper)
                time.sleep(delay)
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

    return {
        "nodes": list(nodes.values()),
        "edges": unique_edges,
        "stats": {
            "total_nodes": len(nodes),
            "total_edges": len(unique_edges),
            "max_depth": max(n["depth"] for n in nodes.values()) if nodes else 0,
            "seed_count": sum(1 for n in nodes.values() if n["depth"] == 0),
        },
    }
