"""Stage 1: Cache search + rerank results for all datasets.

Usage:
    python eval/cache_results.py --dataset litsearch --n 50
    python eval/cache_results.py --dataset sage_oe
    python eval/cache_results.py --dataset pasa_real
    python eval/cache_results.py --dataset all
"""

import json
import sys
import time
import argparse
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
logging.getLogger("httpx").setLevel(logging.WARNING)

from scholar_mcp import sources, relevance

DATA_DIR = Path(__file__).parent / "data"
CACHE_DIR = Path(__file__).parent / "cache"


def load_litsearch(n=None):
    queries = json.loads((DATA_DIR / "litsearch_queries.json").read_text())
    title_map = json.loads((DATA_DIR / "litsearch_title_map.json").read_text())

    entries = []
    for q in queries:
        gt_titles = []
        for cid in q["corpusids"]:
            title = title_map.get(str(cid))
            if title:
                gt_titles.append(title)
        if not gt_titles:
            continue
        entries.append({
            "query": q["query"],
            "gt_titles": gt_titles,
            "metadata": {
                "query_set": q.get("query_set", ""),
                "specificity": q.get("specificity", 0),
                "quality": q.get("quality", 0),
            },
        })

    if n:
        entries = entries[:n]
    return entries


def load_sage_oe(n=None):
    data = json.loads((DATA_DIR / "sage_oe_cs.json").read_text())["questions"]
    entries = []
    for q in data:
        gt = q.get("ground_truth", {})
        gt_titles = []
        for p in gt.get("most_relevant", []) + gt.get("relevant", []):
            if p.get("title"):
                gt_titles.append(p["title"])
        if not gt_titles:
            continue
        entries.append({"query": q["question"], "gt_titles": gt_titles})

    if n:
        entries = entries[:n]
    return entries


def load_sage_sf(n=None):
    raw = json.loads((DATA_DIR / "sage_sf_cs.json").read_text())
    data = raw if isinstance(raw, list) else raw.get("questions", [])
    entries = []
    for q in data:
        gt = q.get("ground_truth", {})
        title = gt.get("title", "") if isinstance(gt, dict) else ""
        if not title:
            continue
        query = q.get("complete_query", q.get("question", ""))
        entries.append({"query": query, "gt_titles": [title]})

    if n:
        entries = entries[:n]
    return entries


def load_pasa_real(n=None):
    data = json.loads((DATA_DIR / "pasa_real.json").read_text())
    entries = []
    for q in data:
        gt_titles = q.get("answer", [])
        if not gt_titles:
            continue
        entries.append({
            "query": q["question"],
            "gt_titles": gt_titles,
            "metadata": {"arxiv_ids": q.get("answer_arxiv_id", [])},
        })

    if n:
        entries = entries[:n]
    return entries


def load_pasa_auto(n=None):
    data = json.loads((DATA_DIR / "pasa_auto_test.json").read_text())
    entries = []
    for q in data:
        gt_titles = q.get("answer", [])
        if not gt_titles:
            continue
        entries.append({"query": q["question"], "gt_titles": gt_titles})

    if n:
        entries = entries[:n]
    return entries


LOADERS = {
    "litsearch": load_litsearch,
    "sage_oe": load_sage_oe,
    "sage_sf": load_sage_sf,
    "pasa_real": load_pasa_real,
    "pasa_auto": load_pasa_auto,
}


def load_existing_cache(cache_file: Path) -> set[int]:
    if not cache_file.exists():
        return set()
    cached = set()
    for line in cache_file.read_text().strip().split("\n"):
        if line.strip():
            entry = json.loads(line)
            cached.add(entry.get("query_idx", -1))
    return cached


def cache_one_query(query_text: str, delay: float = 2.0):
    search_query = relevance.optimize_query(query_text)
    source_results = sources.parallel_search(search_query, limit=100)

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

    deduped = relevance.deduplicate(all_papers)

    reranked = relevance.rerank(query_text, deduped, top_n=min(200, len(deduped)))

    papers_out = []
    for p in reranked:
        papers_out.append({
            "title": p.get("title", ""),
            "citation_count": p.get("citation_count", 0) or 0,
            "year": p.get("year"),
            "venue": p.get("venue", ""),
            "publication_date": p.get("publication_date"),
            "source_count": p.get("_source_count", 1),
            "source_ranks": p.get("_source_ranks", {}),
            "rerank_score": p.get("_rerank_score", 0.0),
        })

    time.sleep(delay)
    return papers_out, source_reports, search_query


def run(dataset_name: str, n: int = None, delay: float = 2.0):
    loader = LOADERS[dataset_name]
    entries = loader(n)
    print(f"Dataset: {dataset_name}, {len(entries)} queries")

    CACHE_DIR.mkdir(exist_ok=True)
    cache_file = CACHE_DIR / f"{dataset_name}.jsonl"
    cached_idxs = load_existing_cache(cache_file)
    print(f"Already cached: {len(cached_idxs)} queries")

    with open(cache_file, "a") as f:
        for i, entry in enumerate(entries):
            if i in cached_idxs:
                continue

            t0 = time.time()
            papers, reports, opt_query = cache_one_query(entry["query"], delay=delay)
            elapsed = time.time() - t0

            sources_ok = [r["source"] for r in reports if r["status"] == "ok"]

            cache_entry = {
                "dataset": dataset_name,
                "query_idx": i,
                "query_raw": entry["query"],
                "query_optimized": opt_query,
                "gt_titles": entry["gt_titles"],
                "metadata": entry.get("metadata", {}),
                "source_reports": reports,
                "n_papers": len(papers),
                "papers": papers,
            }
            f.write(json.dumps(cache_entry) + "\n")
            f.flush()

            from matching import find_gt_rank
            rank = find_gt_rank(papers, entry["gt_titles"])
            status = f"HIT@{rank}" if rank else "MISS"

            print(f"  [{i+1}/{len(entries)}] {elapsed:.1f}s {status:8s} src={','.join(sources_ok):30s} | {entry['query'][:60]}")

    print(f"\nCached to {cache_file}")


def main():
    parser = argparse.ArgumentParser(description="Cache search + rerank results")
    parser.add_argument("--dataset", type=str, default="litsearch",
                        choices=list(LOADERS.keys()) + ["all"])
    parser.add_argument("--n", type=int, default=None, help="Limit queries")
    parser.add_argument("--delay", type=float, default=2.0)
    args = parser.parse_args()

    if args.dataset == "all":
        for name in LOADERS:
            run(name, n=args.n, delay=args.delay)
    else:
        run(args.dataset, n=args.n, delay=args.delay)


if __name__ == "__main__":
    main()
