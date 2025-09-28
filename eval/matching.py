"""Shared title matching and metric computation for eval pipeline."""

import math
import re


def normalize_title(t: str) -> str:
    if not t:
        return ""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s]", "", t.lower().strip()))


def titles_match(a: str, b: str) -> bool:
    na, nb = normalize_title(a), normalize_title(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    shorter, longer = sorted([na, nb], key=len)
    return len(shorter) > 20 and shorter in longer


def find_gt_rank(papers: list[dict], gt_titles: list[str]) -> int | None:
    for rank, p in enumerate(papers):
        title = p.get("title", "")
        if any(titles_match(title, gt) for gt in gt_titles):
            return rank + 1
    return None


def compute_metrics(entries: list[dict], k_values=(5, 10, 20)) -> dict:
    n = len(entries)
    if n == 0:
        return {}
    metrics = {}
    for k in k_values:
        hits = sum(1 for e in entries if e.get("gt_rank") is not None and e["gt_rank"] <= k)
        metrics[f"R@{k}"] = hits / n

    rrs = [1.0 / e["gt_rank"] if e.get("gt_rank") else 0.0 for e in entries]
    metrics["MRR"] = sum(rrs) / n
    metrics["Hit@20"] = metrics.get("R@20", 0.0)
    metrics["n"] = n
    return metrics


def weighted_objective(metrics: dict) -> float:
    return 0.5 * metrics.get("R@5", 0) + 0.3 * metrics.get("R@10", 0) + 0.2 * metrics.get("R@20", 0)
