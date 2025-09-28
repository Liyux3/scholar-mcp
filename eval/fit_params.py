"""Stage 2: Fit ranking params (γ, α, β, δ) on cached results.

Usage:
    python eval/fit_params.py --cache eval/cache/litsearch.jsonl
    python eval/fit_params.py --cache eval/cache/litsearch.jsonl --cv
"""

import json
import math
import random
import argparse
from copy import deepcopy
from itertools import product
from pathlib import Path

from matching import normalize_title, titles_match, find_gt_rank, weighted_objective


def load_cache(cache_file: str) -> list[dict]:
    entries = []
    for line in Path(cache_file).read_text().strip().split("\n"):
        if line.strip():
            entries.append(json.loads(line))
    return entries


def train_val_split(entries, train_ratio=0.8, seed=42):
    rng = random.Random(seed)
    by_set = {}
    for e in entries:
        key = e.get("metadata", {}).get("query_set", "default")
        by_set.setdefault(key, []).append(e)

    train, val = [], []
    for key, group in by_set.items():
        rng.shuffle(group)
        split = int(len(group) * train_ratio)
        train.extend(group[:split])
        val.extend(group[split:])
    return train, val


def apply_formula(papers, gamma, alpha, beta, delta, n_sources=1):
    import datetime
    current_year = datetime.datetime.now().year

    for p in papers:
        r = max(p.get("rerank_score", 0.0), 1e-6)
        cites = p.get("citation_count", 0) or 0
        src_count = p.get("source_count", 1)
        year = p.get("year") or current_year
        recency = max(0, 1.0 - (current_year - year) / 10.0)

        p["_score"] = (r ** gamma
                       * (1 + alpha * math.log(cites + 1))
                       * (1 + beta * src_count / max(n_sources, 1))
                       * (1 + delta * recency))

    papers.sort(key=lambda p: -p["_score"])
    return papers


def source_dropout(papers, drop_rate=0.3, rng=None):
    if rng is None:
        rng = random.Random()
    out = []
    for p in papers:
        p2 = dict(p)
        ranks = dict(p2.get("source_ranks", {}))
        to_drop = [s for s in ranks if rng.random() < drop_rate]
        for s in to_drop:
            del ranks[s]
        p2["source_ranks"] = ranks
        p2["source_count"] = max(len(ranks), 1)
        out.append(p2)
    return out


def evaluate(params, entries, n_trials=5, drop_rate=0.3):
    gamma, alpha, beta, delta = params
    rng = random.Random(42)
    total_metrics = {"R@5": 0, "R@10": 0, "R@20": 0}

    for trial in range(n_trials):
        results = []
        for entry in entries:
            papers = source_dropout(deepcopy(entry["papers"]), drop_rate, rng)
            n_src = max(len(set(s for p in papers for s in p.get("source_ranks", {}))), 1)
            papers = apply_formula(papers, gamma, alpha, beta, delta, n_src)
            rank = find_gt_rank(papers, entry["gt_titles"])
            results.append({"gt_rank": rank})

        for k in [5, 10, 20]:
            hits = sum(1 for r in results if r["gt_rank"] is not None and r["gt_rank"] <= k)
            total_metrics[f"R@{k}"] += hits / len(entries)

    avg = {k: v / n_trials for k, v in total_metrics.items()}
    return weighted_objective(avg), avg


def grid_search(train_data):
    gamma_range = [0.5, 0.8, 1.0, 1.2, 1.5, 2.0]
    alpha_range = [0.0, 0.02, 0.05, 0.10, 0.15, 0.20]
    beta_range = [0.0, 0.01, 0.02, 0.05]
    delta_range = [0.0, 0.05, 0.10, 0.15, 0.20]

    grid = list(product(gamma_range, alpha_range, beta_range, delta_range))
    print(f"Grid search: {len(grid)} combos")

    best_obj = -1
    best_params = None
    best_metrics = None

    for i, params in enumerate(grid):
        obj, metrics = evaluate(params, train_data)
        if obj > best_obj:
            best_obj = obj
            best_params = params
            best_metrics = metrics
            if i % 100 == 0:
                print(f"  [{i}/{len(grid)}] best obj={best_obj:.4f} params={best_params}")

    return best_params, best_obj, best_metrics


def loo_cv(train_data, best_params_fn, step=10):
    print(f"\nLOO CV (step={step})...")
    all_params = []
    for i in range(0, len(train_data), step):
        loo = train_data[:i] + train_data[i+step:]
        params, _, _ = best_params_fn(loo)
        all_params.append(params)

    import numpy as np
    arr = np.array(all_params)
    means = arr.mean(axis=0)
    stds = arr.std(axis=0)
    names = ["gamma", "alpha", "beta", "delta"]
    print("  LOO parameter stability:")
    for name, mean, std in zip(names, means, stds):
        cv = std / max(abs(mean), 1e-6)
        stable = "stable" if cv < 0.3 else "UNSTABLE"
        print(f"    {name}: mean={mean:.4f}, std={std:.4f}, cv={cv:.2f} [{stable}]")

    return list(means), list(stds)


def main():
    parser = argparse.ArgumentParser(description="Fit ranking params")
    parser.add_argument("--cache", type=str, required=True)
    parser.add_argument("--cv", action="store_true", help="Run leave-one-out CV")
    parser.add_argument("--output", type=str, default="~/.scholar-mcp/rank_params.json")
    args = parser.parse_args()

    entries = load_cache(args.cache)
    print(f"Loaded {len(entries)} cached queries")

    train, val = train_val_split(entries)
    print(f"Train: {len(train)}, Val: {len(val)}")

    best_params, train_obj, train_metrics = grid_search(train)
    gamma, alpha, beta, delta = best_params

    print(f"\nBest params: gamma={gamma}, alpha={alpha}, beta={beta}, delta={delta}")
    print(f"Train: obj={train_obj:.4f}, R@5={train_metrics['R@5']:.3f}, R@10={train_metrics['R@10']:.3f}, R@20={train_metrics['R@20']:.3f}")

    val_obj, val_metrics = evaluate(best_params, val)
    print(f"Val:   obj={val_obj:.4f}, R@5={val_metrics['R@5']:.3f}, R@10={val_metrics['R@10']:.3f}, R@20={val_metrics['R@20']:.3f}")

    gap = abs(train_obj - val_obj)
    if gap > 0.05:
        print(f"WARNING: train-val gap {gap:.3f} > 0.05, possible overfit")

    if args.cv:
        def fit_fn(data):
            return grid_search(data)
        loo_cv(train, fit_fn, step=max(len(train) // 20, 5))

    out_path = Path(args.output).expanduser()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    params_dict = {"gamma": gamma, "alpha": alpha, "beta": beta, "delta": delta}
    out_path.write_text(json.dumps(params_dict, indent=2))
    print(f"\nSaved to {out_path}")


if __name__ == "__main__":
    main()
