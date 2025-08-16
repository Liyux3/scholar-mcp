"""
LitSearch benchmark evaluation for scholar-mcp.
Evaluates multi-source search quality on 597 NLP/ML queries.

Usage:
    S2_API_KEY=... python eval/litsearch_eval.py [--n N] [--sources s2,oa,arxiv]

Metrics: Recall@5, Recall@10, Recall@20, Hit@20, MRR
"""

import json
import os
import sys
import time
import argparse
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent.parent))

import logging
logging.getLogger("httpx").setLevel(logging.WARNING)

from datasets import load_dataset
from scholar_mcp import config, relevance
from scholar_mcp import s2_client, arxiv_client, openalex_client


GT_CACHE_FILE = Path(__file__).parent / "gt_papers_cache.json"
RESULTS_DIR = Path(__file__).parent / "results"

SOURCE_FUNCS = {
    "s2": lambda q, limit, **kw: ("semantic_scholar", s2_client.search_papers(q, limit=limit)),
    "oa": lambda q, limit, **kw: ("openalex", openalex_client.search_papers(q, limit=limit)),
    "arxiv": lambda q, limit, **kw: ("arxiv", arxiv_client.search_papers(q, max_results=limit)),
}


def load_queries(n=None):
    ds = load_dataset("princeton-nlp/LitSearch", "query", split="full")
    queries = list(ds)
    if n:
        queries = queries[:n]
    return queries


def resolve_gt_papers(queries):
    """Resolve S2 corpusids to titles and DOIs for cross-source matching."""
    if GT_CACHE_FILE.exists():
        cache = json.loads(GT_CACHE_FILE.read_text())
    else:
        cache = {}

    all_corpusids = set()
    for q in queries:
        for cid in q["corpusids"]:
            if str(cid) not in cache:
                all_corpusids.add(cid)

    if all_corpusids:
        print(f"Resolving {len(all_corpusids)} new corpusids via S2 batch API...")
        from scholar_mcp.s2_client import _get
        batch = list(all_corpusids)
        for i in range(0, len(batch), 100):
            chunk = batch[i:i+100]
            for cid in chunk:
                try:
                    data = _get(
                        f"https://api.semanticscholar.org/graph/v1/paper/CorpusID:{cid}",
                        params={"fields": "title,externalIds,year,venue,citationCount"}
                    )
                    if data and "title" in data:
                        cache[str(cid)] = {
                            "title": data["title"],
                            "doi": data.get("externalIds", {}).get("DOI"),
                            "arxiv_id": data.get("externalIds", {}).get("ArXiv"),
                            "year": data.get("year"),
                            "citation_count": data.get("citationCount", 0),
                        }
                    time.sleep(0.5)
                except Exception as e:
                    print(f"  Failed to resolve CorpusID:{cid}: {e}")
                    cache[str(cid)] = None

        GT_CACHE_FILE.write_text(json.dumps(cache, indent=2))
        print(f"Cached {len(cache)} GT papers.")

    return cache


def normalize_title(title):
    """Normalize title for matching (lowercase, strip punctuation)."""
    if not title:
        return ""
    import re
    t = title.lower().strip()
    t = re.sub(r'[^a-z0-9\s]', '', t)
    t = re.sub(r'\s+', ' ', t)
    return t


def match_paper(result_paper, gt_papers_info, corpusids):
    """Check if a search result matches any ground truth paper."""
    result_title = normalize_title(result_paper.get("title", ""))
    result_doi = result_paper.get("doi", "")

    for cid in corpusids:
        gt = gt_papers_info.get(str(cid))
        if not gt:
            continue

        gt_title = normalize_title(gt.get("title", ""))

        # DOI match (strongest)
        if result_doi and gt.get("doi") and result_doi.lower() == gt["doi"].lower():
            return True

        # Title match (fuzzy: 90% char overlap)
        if gt_title and result_title:
            if gt_title == result_title:
                return True
            # Check containment for partial matches
            shorter = min(gt_title, result_title, key=len)
            longer = max(gt_title, result_title, key=len)
            if len(shorter) > 20 and shorter in longer:
                return True

    return False


def run_search(query_text, limit=20, sources=None):
    """Run search with specific sources using bottom-level clients."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    active_sources = sources or ["s2", "oa", "arxiv"]
    search_query = relevance.optimize_query(query_text)

    all_papers = []
    sources_used = []
    sources_failed = []

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {}
        for src in active_sources:
            if src in SOURCE_FUNCS:
                futures[pool.submit(SOURCE_FUNCS[src], search_query, limit)] = src

        for future in as_completed(futures):
            src = futures[future]
            try:
                name, results = future.result()
                if results:
                    relevance.tag_source_ranks(results, name)
                    all_papers.extend(results)
                    sources_used.append(name)
            except Exception as e:
                sources_failed.append(src)

    all_papers = relevance.deduplicate(all_papers)
    if len(sources_used) > 1:
        all_papers = relevance.rrf_fuse(all_papers, method="consensus")

    reranked = relevance.rerank(query_text, all_papers, top_n=limit * 2)
    results = relevance.score_results(query_text, reranked, min_score=0.0)
    results = results[:limit]

    compact = []
    for r in results:
        doi = (r.get("external_ids") or {}).get("DOI", "")
        p = {
            "title": r.get("title", ""),
            "year": r.get("year"),
            "citation_count": r.get("citation_count", 0),
        }
        if doi:
            p["doi"] = doi
        compact.append(p)

    return {
        "results": compact,
        "_meta": {"sources_used": sources_used, "sources_failed": sources_failed, "total": len(compact)},
    }


def evaluate(queries, gt_cache, sources=None, limit=20, delay=2.0):
    """Run evaluation on queries."""
    results = []
    k_values = [5, 10, 20]

    for i, q in enumerate(queries):
        query_text = q["query"]
        corpusids = q["corpusids"]

        result = run_search(query_text, limit=limit, sources=sources)
        papers = result.get("results", [])
        meta = result.get("_meta", {})

        # Find rank of first GT match
        first_match_rank = None
        matched_ranks = []
        for rank, p in enumerate(papers):
            if match_paper(p, gt_cache, corpusids):
                if first_match_rank is None:
                    first_match_rank = rank + 1
                matched_ranks.append(rank + 1)

        entry = {
            "query_idx": i,
            "query_set": q["query_set"],
            "specificity": q["specificity"],
            "quality": q["quality"],
            "n_gt": len(corpusids),
            "n_results": len(papers),
            "sources_used": meta.get("sources_used", []),
            "sources_failed": meta.get("sources_failed", []),
            "first_match_rank": first_match_rank,
            "matched_ranks": matched_ranks,
        }
        results.append(entry)

        status = f"HIT@{first_match_rank}" if first_match_rank else "MISS"
        src = ",".join(meta.get("sources_used", []))
        print(f"  [{i+1}/{len(queries)}] {status:8s} src={src:30s} | {query_text[:60]}")

        if delay > 0:
            time.sleep(delay)

    return results


def compute_metrics(results, k_values=[5, 10, 20]):
    """Compute recall@k, hit@k, and MRR."""
    metrics = {}
    n = len(results)

    for k in k_values:
        hits = sum(1 for r in results if r["first_match_rank"] is not None and r["first_match_rank"] <= k)
        metrics[f"recall@{k}"] = hits / n if n else 0
        metrics[f"hit@{k}"] = hits / n if n else 0

    # MRR
    rrs = []
    for r in results:
        if r["first_match_rank"] is not None:
            rrs.append(1.0 / r["first_match_rank"])
        else:
            rrs.append(0.0)
    metrics["mrr"] = sum(rrs) / len(rrs) if rrs else 0

    # By query set
    by_set = defaultdict(list)
    for r in results:
        by_set[r["query_set"]].append(r)

    metrics["by_query_set"] = {}
    for qset, qresults in by_set.items():
        n_set = len(qresults)
        hits_20 = sum(1 for r in qresults if r["first_match_rank"] is not None and r["first_match_rank"] <= 20)
        metrics["by_query_set"][qset] = {
            "n": n_set,
            "recall@20": hits_20 / n_set if n_set else 0,
        }

    # By specificity
    for spec in [0, 1]:
        spec_results = [r for r in results if r["specificity"] == spec]
        n_spec = len(spec_results)
        if n_spec:
            hits_20 = sum(1 for r in spec_results if r["first_match_rank"] is not None and r["first_match_rank"] <= 20)
            metrics[f"spec_{spec}_recall@20"] = hits_20 / n_spec

    # Source usage stats
    source_counts = defaultdict(int)
    for r in results:
        for s in r["sources_used"]:
            source_counts[s] += 1
    metrics["source_usage"] = dict(source_counts)

    # Source failure stats
    fail_counts = defaultdict(int)
    for r in results:
        for s in r["sources_failed"]:
            fail_counts[s] += 1
    metrics["source_failures"] = dict(fail_counts)

    return metrics


def main():
    parser = argparse.ArgumentParser(description="LitSearch evaluation for scholar-mcp")
    parser.add_argument("--n", type=int, default=50, help="Number of queries to eval")
    parser.add_argument("--sources", type=str, default=None, help="Comma-separated sources (e.g. s2,oa,arxiv)")
    parser.add_argument("--limit", type=int, default=20, help="Results per query")
    parser.add_argument("--delay", type=float, default=2.0, help="Delay between queries (s)")
    parser.add_argument("--tag", type=str, default=None, help="Tag for output file")
    args = parser.parse_args()

    sources = args.sources.split(",") if args.sources else None
    source_label = args.sources or "all"

    print(f"Scholar-MCP LitSearch Eval")
    print(f"  Queries: {args.n}")
    print(f"  Sources: {source_label}")
    print(f"  Limit: {args.limit}")
    print(f"  S2 key: {bool(config.get_s2_api_key())}")
    print()

    queries = load_queries(args.n)
    gt_cache = resolve_gt_papers(queries)

    print(f"\nRunning evaluation on {len(queries)} queries...\n")
    results = evaluate(queries, gt_cache, sources=sources, limit=args.limit, delay=args.delay)

    metrics = compute_metrics(results)

    print(f"\n{'='*60}")
    print(f"Results: {source_label} (n={len(queries)})")
    print(f"{'='*60}")
    print(f"  Recall@5:  {metrics['recall@5']:.3f}")
    print(f"  Recall@10: {metrics['recall@10']:.3f}")
    print(f"  Recall@20: {metrics['recall@20']:.3f}")
    print(f"  MRR:       {metrics['mrr']:.3f}")
    print(f"  Source usage: {metrics['source_usage']}")
    print(f"  Source failures: {metrics['source_failures']}")
    print()
    print("  By query set:")
    for qset, qm in metrics["by_query_set"].items():
        print(f"    {qset}: n={qm['n']}, R@20={qm['recall@20']:.3f}")

    RESULTS_DIR.mkdir(exist_ok=True)
    tag = args.tag or source_label.replace(",", "_")
    out_file = RESULTS_DIR / f"litsearch_{tag}_n{args.n}.json"
    output = {
        "config": {
            "sources": source_label,
            "n_queries": len(queries),
            "limit": args.limit,
            "s2_key": bool(config.get_s2_api_key()),
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        },
        "metrics": metrics,
        "per_query": results,
    }
    out_file.write_text(json.dumps(output, indent=2))
    print(f"\nResults saved to {out_file}")


if __name__ == "__main__":
    main()
