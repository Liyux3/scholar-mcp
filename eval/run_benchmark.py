"""Run v5 pipeline eval on multiple benchmark subsets.

Uses _pipeline with expansion + DashScope + per-source query + EMA.
Each subset saved to its own cache file for independent analysis.

Usage:
    python eval/run_benchmark.py --suite litsearch_subsets --n 50
    python eval/run_benchmark.py --suite all --n 50
    python eval/run_benchmark.py --suite v5_quick  # 50q inline_acl only
"""
import json, time, sys, argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))
sys.stdout.reconfigure(line_buffering=True)

from scholar_mcp import server, relevance
from matching import find_gt_rank, compute_metrics, titles_match

DATA_DIR = Path(__file__).parent / "data"
CACHE_DIR = Path(__file__).parent / "cache"


def load_litsearch_subset(subset: str, n: int = None):
    queries = json.loads((DATA_DIR / "litsearch_queries.json").read_text())
    title_map = json.loads((DATA_DIR / "litsearch_title_map.json").read_text())
    entries = []
    for q in queries:
        if subset != "all" and q.get("query_set", "") != subset:
            continue
        gt = [title_map[str(c)] for c in q["corpusids"] if str(c) in title_map]
        if gt:
            entries.append({"query": q["query"], "gt_titles": gt,
                            "metadata": {"query_set": q.get("query_set", ""), "specificity": q.get("specificity", 0)}})
    return entries[:n] if n else entries


def load_sage_oe(n=None):
    data = json.loads((DATA_DIR / "sage_oe_cs.json").read_text())["questions"]
    entries = []
    for q in data:
        gt = q.get("ground_truth", {})
        gt_titles = [p["title"] for p in gt.get("most_relevant", []) + gt.get("relevant", []) if p.get("title")]
        if gt_titles:
            entries.append({"query": q["question"], "gt_titles": gt_titles})
    return entries[:n] if n else entries


def load_pasa_real(n=None):
    data = json.loads((DATA_DIR / "pasa_real.json").read_text())
    entries = []
    for q in data:
        gt_titles = q.get("answer", [])
        if gt_titles:
            entries.append({"query": q["question"], "gt_titles": gt_titles})
    return entries[:n] if n else entries


SUITES = {
    "v5_quick": [("litsearch_inline_acl", lambda n: load_litsearch_subset("inline_acl", n))],
    "litsearch_subsets": [
        ("litsearch_inline_acl", lambda n: load_litsearch_subset("inline_acl", n)),
        ("litsearch_inline_nonacl", lambda n: load_litsearch_subset("inline_nonacl", n)),
        ("litsearch_manual_acl", lambda n: load_litsearch_subset("manual_acl", n)),
        ("litsearch_manual_iclr", lambda n: load_litsearch_subset("manual_iclr", n)),
    ],
    "cross_benchmark": [
        ("sage_oe", lambda n: load_sage_oe(n)),
        ("pasa_real", lambda n: load_pasa_real(n)),
    ],
    "all": [
        ("litsearch_inline_acl", lambda n: load_litsearch_subset("inline_acl", n)),
        ("litsearch_inline_nonacl", lambda n: load_litsearch_subset("inline_nonacl", n)),
        ("litsearch_manual_acl", lambda n: load_litsearch_subset("manual_acl", n)),
        ("litsearch_manual_iclr", lambda n: load_litsearch_subset("manual_iclr", n)),
        ("sage_oe", lambda n: load_sage_oe(n)),
        ("pasa_real", lambda n: load_pasa_real(n)),
    ],
}


def run_one(name: str, entries: list, limit: int = 100):
    cache_file = CACHE_DIR / f"bench_{name}.jsonl"
    cache_file.parent.mkdir(exist_ok=True)

    cached = set()
    if cache_file.exists():
        for line in cache_file.read_text().strip().split("\n"):
            if line.strip():
                cached.add(json.loads(line)["query_idx"])
    if cached:
        print(f"  Resuming: {len(cached)} cached")

    for i, e in enumerate(entries):
        if i in cached:
            continue
        t0 = time.time()
        search_query = relevance.optimize_query(e["query"])
        results, reports = server._pipeline(
            "search", search_query, limit=limit,
            rerank_query=e["query"], raw_query=e["query"],
            expand_citations=True, expand_top_n=3, expand_limit=20,
        )
        elapsed = time.time() - t0

        papers_out = []
        for p in results:
            papers_out.append({
                "title": p.get("title", ""),
                "citation_count": p.get("citation_count", 0) or 0,
                "year": p.get("year"),
                "venue": p.get("venue", ""),
                "source_count": p.get("_source_count", 1),
                "source_ranks": p.get("_source_ranks", {}),
                "rerank_score": p.get("_rerank_score", 0.0),
            })

        rank = find_gt_rank(papers_out, e["gt_titles"])
        ceiling = any(any(titles_match(p.get("title", ""), gt) for gt in e["gt_titles"]) for p in papers_out)
        status = f"HIT@{rank}" if rank else "MISS"

        record = {
            "query_idx": i, "query_raw": e["query"], "gt_titles": e["gt_titles"],
            "metadata": e.get("metadata", {}), "source_reports": reports,
            "n_papers": len(papers_out), "papers": papers_out, "elapsed_s": round(elapsed, 1),
        }
        with open(cache_file, "a") as f:
            f.write(json.dumps(record) + "\n")

        print(f"  [{i+1}/{len(entries)}] {elapsed:.0f}s {status:8s} ceil={'Y' if ceiling else 'N'} | {e['query'][:55]}")
        time.sleep(0.5)

    return cache_file


def eval_cache(cache_file: Path):
    results = []
    ceil = 0
    for line in cache_file.read_text().strip().split("\n"):
        if not line.strip():
            continue
        d = json.loads(line)
        r = find_gt_rank(d["papers"], d["gt_titles"])
        results.append({"gt_rank": r})
        if any(any(titles_match(p.get("title", ""), gt) for gt in d["gt_titles"]) for p in d["papers"]):
            ceil += 1
    m = compute_metrics(results)
    return m, ceil, len(results)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", type=str, default="all", choices=list(SUITES.keys()))
    parser.add_argument("--n", type=int, default=50)
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()

    suite = SUITES[args.suite]
    print(f"Benchmark suite: {args.suite}, n={args.n}, limit={args.limit}")
    print(f"Subsets: {[name for name, _ in suite]}")
    print()

    all_metrics = {}
    for name, loader in suite:
        entries = loader(args.n)
        print(f"=== {name} ({len(entries)}q) ===")
        cache_file = run_one(name, entries, limit=args.limit)
        m, ceil, n = eval_cache(cache_file)
        all_metrics[name] = (m, ceil, n)
        print(f"  R@5={m['R@5']:.3f} R@10={m['R@10']:.3f} R@20={m['R@20']:.3f} MRR={m['MRR']:.3f} ceiling={ceil}/{n}")
        print()

    print(f"\n{'='*80}")
    print(f"SUMMARY")
    print(f"{'='*80}")
    print(f"{'Subset':<28} {'n':>4} {'R@5':>7} {'R@10':>7} {'R@20':>7} {'MRR':>7} {'Ceiling':>10}")
    print("-" * 75)
    for name, (m, ceil, n) in all_metrics.items():
        print(f"{name:<28} {n:>4} {m['R@5']:>7.3f} {m['R@10']:>7.3f} {m['R@20']:>7.3f} {m['MRR']:>7.3f} {ceil:>4}/{n} ({100*ceil/n:.0f}%)")


if __name__ == "__main__":
    main()
