"""Quick test: per-source query adaptation vs uniform query."""
import json
import sys
import time
import re
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
logging.getLogger("httpx").setLevel(logging.WARNING)

from datasets import load_dataset
from scholar_mcp import s2_client, openalex_client, arxiv_client, relevance

GT_CACHE_FILE = Path(__file__).parent / "gt_papers_cache.json"


def normalize_title(t):
    if not t: return ""
    t = t.lower().strip()
    t = re.sub(r'[^a-z0-9\s]', '', t)
    return re.sub(r'\s+', ' ', t)


def match(title, corpusids, gt_cache):
    rt = normalize_title(title)
    for cid in corpusids:
        gt = gt_cache.get(str(cid))
        if not gt: continue
        gt_t = normalize_title(gt.get("title", ""))
        if gt_t and rt and (gt_t == rt or (len(min(gt_t, rt, key=len)) > 20 and min(gt_t, rt, key=len) in max(gt_t, rt, key=len))):
            return True
    return False


def adaptive_queries(query):
    """Generate per-source optimized queries."""
    # S2: keep more context (SPECTER2 handles semantic queries)
    # Truncate to ~25 words max, keep full phrases
    words = query.split()
    s2_q = " ".join(words[:30]) if len(words) > 30 else query

    # arXiv: extract key technical terms only (max 10 words)
    kw = relevance.extract_keywords(query, max_keywords=10)
    arxiv_q = " ".join(kw)

    # OA: moderate, the default optimization
    oa_q = relevance.optimize_query(query)

    return {"s2": s2_q, "oa": oa_q, "arxiv": arxiv_q}


def main():
    ds = load_dataset("princeton-nlp/LitSearch", "query", split="full")
    gt_cache = json.loads(GT_CACHE_FILE.read_text())

    n = 30
    queries = list(ds)[:n]

    # Baseline: uniform query (current behavior)
    uniform_hits = 0
    adaptive_hits = 0

    for i, q in enumerate(queries):
        query = q["query"]
        cids = q["corpusids"]
        uniform_q = relevance.optimize_query(query)
        adapted = adaptive_queries(query)

        # Uniform: search all sources with same query
        uniform_results = []
        try:
            s2_r = s2_client.search_papers(uniform_q, limit=10)
            uniform_results.extend(s2_r)
        except: pass
        try:
            oa_r = openalex_client.search_papers(uniform_q, limit=10)
            uniform_results.extend(oa_r)
        except: pass
        try:
            ax_r = arxiv_client.search_papers(uniform_q, max_results=10)
            uniform_results.extend(ax_r)
        except: pass

        uniform_found = any(match(p.get("title", ""), cids, gt_cache) for p in uniform_results)

        # Adaptive: different query per source
        adaptive_results = []
        try:
            s2_r = s2_client.search_papers(adapted["s2"], limit=10)
            adaptive_results.extend(s2_r)
        except: pass
        try:
            oa_r = openalex_client.search_papers(adapted["oa"], limit=10)
            adaptive_results.extend(oa_r)
        except: pass
        try:
            ax_r = arxiv_client.search_papers(adapted["arxiv"], max_results=10)
            adaptive_results.extend(ax_r)
        except: pass

        adaptive_found = any(match(p.get("title", ""), cids, gt_cache) for p in adaptive_results)

        if uniform_found: uniform_hits += 1
        if adaptive_found: adaptive_hits += 1

        delta = "+" if adaptive_found and not uniform_found else ("-" if uniform_found and not adaptive_found else "=")
        print(f"  [{i+1}/{n}] uniform={'HIT' if uniform_found else 'MISS':4s} adaptive={'HIT' if adaptive_found else 'MISS':4s} {delta}")

        time.sleep(1.5)

    print(f"\nUniform hits: {uniform_hits}/{n} ({uniform_hits/n:.1%})")
    print(f"Adaptive hits: {adaptive_hits}/{n} ({adaptive_hits/n:.1%})")
    print(f"Delta: {adaptive_hits - uniform_hits:+d}")


if __name__ == "__main__":
    main()
