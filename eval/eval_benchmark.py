"""Stage 3: Evaluate + ablation on cached results.

Usage:
    python eval/eval_benchmark.py --cache eval/cache/litsearch.jsonl
    python eval/eval_benchmark.py --cache eval/cache/ --ablation
"""

import json
import math
import argparse
from copy import deepcopy
from pathlib import Path
from collections import defaultdict

from matching import find_gt_rank, compute_metrics, weighted_objective


def load_cache(path: str) -> dict[str, list[dict]]:
    p = Path(path)
    by_dataset = defaultdict(list)
    files = list(p.glob("*.jsonl")) if p.is_dir() else [p]
    for f in files:
        for line in f.read_text().strip().split("\n"):
            if line.strip():
                entry = json.loads(line)
                by_dataset[entry["dataset"]].append(entry)
    return dict(by_dataset)


def rank_with_params(papers, gamma, alpha, beta, delta):
    import datetime
    current_year = datetime.datetime.now().year
    n_src = max(len(set(s for p in papers for s in p.get("source_ranks", {}))), 1)

    for p in papers:
        r = max(p.get("rerank_score", 0.0), 1e-6)
        cites = p.get("citation_count", 0) or 0
        src_count = p.get("source_count", 1)
        year = p.get("year") or current_year
        recency = max(0, 1.0 - (current_year - year) / 10.0)

        p["_score"] = (r ** gamma
                       * (1 + alpha * math.log(cites + 1))
                       * (1 + beta * src_count / max(n_src, 1))
                       * (1 + delta * recency))

    papers.sort(key=lambda p: -p["_score"])
    return papers


def eval_entries(entries, gamma=1.0, alpha=0.05, beta=0.02, delta=0.10):
    results = []
    for entry in entries:
        papers = deepcopy(entry["papers"])
        papers = rank_with_params(papers, gamma, alpha, beta, delta)
        rank = find_gt_rank(papers, entry["gt_titles"])
        results.append({"gt_rank": rank, **entry.get("metadata", {})})
    return compute_metrics(results)


def load_params(path="~/.scholar-mcp/rank_params.json"):
    try:
        d = json.loads(Path(path).expanduser().read_text())
        return d["gamma"], d["alpha"], d["beta"], d["delta"]
    except (FileNotFoundError, KeyError):
        return 1.0, 0.05, 0.02, 0.10


def run_eval(by_dataset, params):
    gamma, alpha, beta, delta = params
    print(f"Params: gamma={gamma}, alpha={alpha}, beta={beta}, delta={delta}")
    print()
    print(f"{'Dataset':20s} {'n':>5s} {'R@5':>7s} {'R@10':>7s} {'R@20':>7s} {'MRR':>7s} {'obj':>7s}")
    print("-" * 65)

    for name, entries in sorted(by_dataset.items()):
        m = eval_entries(entries, gamma, alpha, beta, delta)
        obj = weighted_objective(m)
        print(f"{name:20s} {m['n']:5d} {m['R@5']:7.3f} {m['R@10']:7.3f} {m['R@20']:7.3f} {m['MRR']:7.3f} {obj:7.3f}")


def run_ablation(by_dataset, params):
    gamma, alpha, beta, delta = params

    print("\n" + "=" * 70)
    print("ABLATION STUDIES")
    print("=" * 70)

    for name, entries in sorted(by_dataset.items()):
        print(f"\n--- {name} ({len(entries)} queries) ---")
        print(f"{'Config':35s} {'R@5':>7s} {'R@10':>7s} {'R@20':>7s} {'obj':>7s}")
        print("-" * 65)

        configs = {
            "Fitted params": (gamma, alpha, beta, delta),
            "Default params": (1.0, 0.05, 0.02, 0.10),
            "Rerank only (α=β=δ=0)": (1.0, 0.0, 0.0, 0.0),
            "High citation weight": (1.0, 0.20, 0.0, 0.0),
            "High recency weight": (1.0, 0.0, 0.0, 0.30),
        }

        for label, (g, a, b, d) in configs.items():
            m = eval_entries(entries, g, a, b, d)
            obj = weighted_objective(m)
            print(f"{label:35s} {m['R@5']:7.3f} {m['R@10']:7.3f} {m['R@20']:7.3f} {obj:7.3f}")

        # Reranker ablation: sort by rerank score vs citation vs random
        print()
        for sort_label, sort_key in [
            ("Sort: rerank score", lambda p: -p.get("rerank_score", 0)),
            ("Sort: citation count", lambda p: -(p.get("citation_count", 0) or 0)),
            ("Sort: year (newest)", lambda p: -(p.get("year", 0) or 0)),
        ]:
            results = []
            for entry in entries:
                papers = sorted(deepcopy(entry["papers"]), key=sort_key)
                rank = find_gt_rank(papers, entry["gt_titles"])
                results.append({"gt_rank": rank})
            m = compute_metrics(results)
            obj = weighted_objective(m)
            print(f"{sort_label:35s} {m['R@5']:7.3f} {m['R@10']:7.3f} {m['R@20']:7.3f} {obj:7.3f}")

        # By query type (LitSearch only)
        if name == "litsearch":
            print()
            by_type = defaultdict(list)
            for entry in entries:
                qset = entry.get("metadata", {}).get("query_set", "unknown")
                by_type[qset].append(entry)

            for qset, group in sorted(by_type.items()):
                m = eval_entries(group, gamma, alpha, beta, delta)
                obj = weighted_objective(m)
                print(f"  {qset:33s} {m['R@5']:7.3f} {m['R@10']:7.3f} {m['R@20']:7.3f} {obj:7.3f} (n={m['n']})")

            # By specificity
            for spec, label in [(0, "Broad"), (1, "Specific")]:
                group = [e for e in entries if e.get("metadata", {}).get("specificity") == spec]
                if group:
                    m = eval_entries(group, gamma, alpha, beta, delta)
                    obj = weighted_objective(m)
                    print(f"  {label:33s} {m['R@5']:7.3f} {m['R@10']:7.3f} {m['R@20']:7.3f} {obj:7.3f} (n={m['n']})")


def main():
    parser = argparse.ArgumentParser(description="Evaluate + ablation")
    parser.add_argument("--cache", type=str, required=True, help="Cache file or directory")
    parser.add_argument("--ablation", action="store_true")
    parser.add_argument("--params", type=str, default="~/.scholar-mcp/rank_params.json")
    args = parser.parse_args()

    by_dataset = load_cache(args.cache)
    print(f"Loaded: {', '.join(f'{k}({len(v)})' for k, v in by_dataset.items())}")

    params = load_params(args.params)
    run_eval(by_dataset, params)

    if args.ablation:
        run_ablation(by_dataset, params)


if __name__ == "__main__":
    main()
