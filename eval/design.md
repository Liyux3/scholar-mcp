# Eval & Train Pipeline Design

## Datasets

| Dataset | Queries | Domain | GT/query | Use |
|---------|---------|--------|----------|-----|
| LitSearch | 597 | NLP/ML | ~1.1 | Train (478) + Val (119) |
| PaSa RealScholarQuery | 50 | AI | multi | OOD Test |
| SAGE CS | ~300 | CS | multi | OOD Test (if mapped) |

## Pipeline: 3 stages

### Stage 1: Cache search results (run once, ~2h)

```
for each query in LitSearch 597:
    results = parallel_search(optimize_query(q), limit=100)
    # Store per-source raw results before dedup
    cache[q] = {
        "query": q,
        "corpusids": gt_corpusids,
        "source_results": [
            {"source": "s2", "papers": [...], "status": "ok"},
            {"source": "oa", "papers": [...], "status": "ok"},
            ...
        ],
        "deduped": [...],  # after dedup, with source_count, source_ranks
        "rerank_scores": {paper_id: score},  # from DashScope
    }
    save to eval/cache/litsearch_cached.jsonl
```

This is the expensive step (API calls). After this, everything is offline.
Estimated cost: 597 queries × $0.001 OA × 3 emails + DashScope rerank = ~$1-2

### Stage 2: Fit params (offline, <1 min)

```python
# eval/fit_params.py

import json
from scipy.optimize import differential_evolution

cached = load("eval/cache/litsearch_cached.jsonl")
train, val = split(cached, 0.8)  # deterministic split

def objective(params, data, drop_rate=0.3):
    gamma, alpha, beta, delta = params
    total_hits = 0
    n_trials = 5  # random source drops per evaluation
    
    for trial in range(n_trials):
        hits = 0
        for entry in data:
            papers = entry["deduped"]
            # Randomly drop sources
            for p in papers:
                if random() < drop_rate:
                    drop some source_ranks entries
                    recompute source_count
            
            # Apply formula
            for p in papers:
                r = p["rerank_score"]
                c = p["citations"]
                s = p["source_count"]
                rec = p["recency"]
                p["score"] = r**gamma * (1+alpha*log(c+1)) * (1+beta*s/N) * (1+delta*rec)
            
            papers.sort(by score)
            
            # Check if GT in top-20
            if gt_paper in papers[:20]:
                hits += 1
        
        total_hits += hits
    
    return -total_hits / (n_trials * len(data))  # minimize negative R@20

# Grid search (robust, no gradient needed)
best_score = -1
best_params = None
for gamma in [0.5, 0.8, 1.0, 1.2, 1.5, 2.0]:
    for alpha in [0.0, 0.02, 0.05, 0.10, 0.15, 0.20]:
        for beta in [0.0, 0.01, 0.02, 0.05, 0.10]:
            for delta in [0.0, 0.05, 0.10, 0.15, 0.20, 0.30]:
                score = -objective([gamma, alpha, beta, delta], train)
                if score > best_score:
                    best_score = score
                    best_params = (gamma, alpha, beta, delta)

# Validate on held-out set
val_score = -objective(best_params, val)
print(f"Train R@20: {best_score}, Val R@20: {val_score}")

# Leave-one-out CV for robustness check
loo_params = []
for i in range(len(train)):
    loo_train = train[:i] + train[i+1:]
    # fit on loo_train, record params
    loo_params.append(best_for_loo_train)
print(f"Param variance: {std(loo_params)}")  # low = robust

# Save
json.dump({"gamma": ..., "alpha": ..., "beta": ..., "delta": ...}, 
          open("~/.scholar-mcp/rank_params.json", "w"))
```

Grid: 6×6×5×6 = 1080 combos × 5 trials × 478 queries = ~2.5M sort ops, <10s.

### Stage 3: Eval (offline, <1 min)

```python
# eval/eval_benchmark.py

# Re-use cached results from Stage 1
# Apply fitted params
# Report metrics on:
#   - LitSearch val (119q): R@5, R@10, R@20, MRR
#   - LitSearch full (597q): R@5, R@10, R@20, MRR
#   - PaSa (50q): Recall, Precision (need separate cache run)

# Ablation studies (all offline, just re-sort cached results):
#   1. DashScope rerank vs FlashRank vs no rerank
#   2. All sources vs S2-only vs OA-only
#   3. Default params vs fitted params
#   4. With/without source dropout
#   5. By query type: inline_acl vs inline_nonacl vs manual_acl vs manual_iclr
#   6. By specificity: broad vs specific
```

## Key Design Decisions

1. **Cache once, fit many**: API calls only happen in Stage 1. All param fitting
   and evaluation is offline on cached results. Can iterate params in seconds.

2. **Source dropout in training**: Each eval randomly drops 30% of sources,
   simulates real-world API failures. Params trained this way are robust.

3. **Train/val/OOD split**: 
   - Train on LitSearch 80% (fit params)
   - Val on LitSearch 20% (check overfit)
   - OOD test on PaSa (check generalization)

4. **Leave-one-out CV**: On training set, check if params are stable.
   High variance = GT too noisy to fit, should use defaults.

5. **Grid search not gradient**: R@20 is non-smooth (paper jumps in/out of top-20).
   Grid search is simple, exhaustive, and 1080 combos is trivial.

## Files to Create

- eval/cache_results.py: Stage 1, cache search + rerank results
- eval/fit_params.py: Stage 2, grid search + CV + save params
- eval/eval_benchmark.py: Stage 3, eval + ablation + report
- eval/cache/: cached results directory (gitignored, ~50MB)
