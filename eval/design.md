# Eval & Train Pipeline Design

## Dataset Survey

### LitSearch (EMNLP 2024)
- 597 queries, NLP/ML domain
- GT: S2 corpusid, matched via title normalization
- ~1.1 GT papers/query (sparse)
- LitSearch paper Table 7 (API-based systems, 80q subset): Google Search 23.1%, Google Scholar 20.5%
- Our v0.6 baseline: R@5=7.5%, R@20=27.0% (200q, OA+arXiv only)
- Data: HuggingFace princeton-nlp/LitSearch
- GT title map: `/Users/liyux/Desktop/Research/scholar-search-research/benchmarks/results/litsearch_title_map.json` (64K entries)

### SAGE (Feb 2026)
- CS domain: 150 open-ended + 150 short-form = 300 queries
- GT: S2 paperIds, multiple per query (2 most_relevant + ~4 relevant for OE, 1 for SF)
- Relevance weights: most_relevant=2, relevant=1
- Data at: `/Users/liyux/Desktop/Research/scholar-search-research/repos/Sage/`
- Prior results (v0.3, 10q each): SAGE OE R@20=3.1%, SAGE SF R@20=0.0%

### PaSa/RealScholarQuery (ACL 2025)
- 50 real AI researcher queries, multiple GT per query
- Title matching via keep_letters() normalization
- Data: HuggingFace CarlanLark/pasa-dataset (not cached locally, need download)

### Existing Artifacts
- GT cache: `eval/gt_papers_cache.json` (214 S2 corpusid resolutions)
- LLM reformulation cache: 20 SAGE queries reformulated by Claude Haiku
- Prior eval results: `/Users/liyux/Desktop/Research/scholar-search-research/benchmarks/results/` (v0.3-v0.5 era, 10q each)

## Train/Test Split

```
Train:   LitSearch 478q (80%, stratified by query_set)
Val:     LitSearch 119q (20%)
OOD Test: SAGE OE CS 150q + PaSa 50q
```

Why not train on SAGE: SAGE queries are very long (100+ words), different distribution from typical agent queries. Better as OOD test.

## Pipeline: 3 Stages

### Stage 1: Cache (run once, ~2-3h)

For each dataset (LitSearch 597 + SAGE 300 + PaSa 50):

```python
for query in dataset:
    # 1. Search all sources
    source_results = sources.parallel_search(optimize_query(query), limit=100)
    
    # 2. Dedup
    deduped = relevance.deduplicate(merge_all(source_results))
    
    # 3. Rerank (DashScope)
    reranked = relevance.rerank(query, deduped)
    
    # 4. Save everything
    cache_entry = {
        "query": query,
        "gt": gt_papers,
        "source_results": [{source, status, count, papers}],
        "deduped_count": len(deduped),
        "papers": [{  # each paper with all features
            "title", "citation_count", "year", "venue",
            "source_count", "source_ranks",
            "_rerank_score",  # full precision from DashScope
        }],
    }
```

Cost estimate:
- LitSearch 597 × ($0.001 OA × 3 emails + $0.003 DashScope) ≈ $2.5
- SAGE 300 × same ≈ $1.2
- PaSa 50 × same ≈ $0.2
- Total: ~$4

### Stage 2: Fit (offline, <30s)

```python
# Grid search over (γ, α, β, δ)
# With random source dropout for robustness

gamma_range = [0.5, 0.8, 1.0, 1.2, 1.5, 2.0]         # 6
alpha_range = [0.0, 0.02, 0.05, 0.10, 0.15, 0.20]     # 6
beta_range  = [0.0, 0.01, 0.02, 0.05]                  # 4
delta_range = [0.0, 0.05, 0.10, 0.15, 0.20]            # 5

# Total combos: 6 × 6 × 4 × 5 = 720
# × 5 random source dropout trials = 3600 evaluations
# × 478 queries × sort = ~1.7M sorts, <10s

def evaluate(params, data, n_dropout_trials=5):
    gamma, alpha, beta, delta = params
    total_r20 = 0
    for trial in range(n_dropout_trials):
        hits = 0
        for entry in data:
            papers = copy(entry["papers"])
            # Random source dropout (30%)
            for p in papers:
                drop_mask = random_mask(p["source_ranks"], drop_rate=0.3)
                p["source_count"] = recount(p, drop_mask)
            # Apply formula
            for p in papers:
                p["score"] = (p["rerank_score"]**gamma 
                             * (1 + alpha * log(p["cites"]+1))
                             * (1 + beta * p["src_count"]/N) 
                             * (1 + delta * p["recency"]))
            papers.sort(by=-score)
            if any_gt_in_top20(papers, entry["gt"]):
                hits += 1
        total_r20 += hits / len(data)
    return total_r20 / n_dropout_trials

# Find best
best = max(grid, key=lambda p: evaluate(p, train_data))

# Validate
train_r20 = evaluate(best, train_data)
val_r20 = evaluate(best, val_data)
print(f"Train R@20: {train_r20:.3f}, Val R@20: {val_r20:.3f}")
# If gap > 0.05, params may overfit

# Leave-one-out CV on training set
loo_params = []
for i in range(0, len(train_data), 10):  # every 10th for efficiency
    loo_train = train_data[:i] + train_data[i+10:]
    loo_best = max(grid, key=lambda p: evaluate(p, loo_train))
    loo_params.append(loo_best)
param_std = std(loo_params, axis=0)
# If std is small relative to param value, result is stable

# Save
save("~/.scholar-mcp/rank_params.json", best)
```

### Stage 3: Eval (offline, <1min)

Report on all datasets with fitted params:

```
Metrics:
  R@5, R@10, R@20, MRR, Hit@20

Ablations (re-sort cached results, no API calls):
  1. Reranker: DashScope vs FlashRank vs no rerank (use cached scores)
  2. Sources: all vs S2-only vs OA-only (use source dropout to simulate)
  3. Params: fitted vs default vs alpha=beta=delta=0 (rerank-only)
  4. Intent: foundational vs recent vs default (need separate rerank cache per intent)
  5. By query type: LitSearch inline_acl / inline_nonacl / manual_acl / manual_iclr
  6. By specificity: broad vs specific

Comparison table:
  | Config | LitSearch Val R@20 | SAGE OE R@20 | PaSa Recall |
  |--------|--------------------|--------------| ------------|
  | v0.6 baseline | 0.27 | 0.03 | TBD |
  | v0.7 default | ? | ? | ? |
  | v0.7 fitted | ? | ? | ? |
  | v0.7 rerank-only | ? | ? | ? |
```

## Implementation Plan

1. `eval/cache_results.py`: Cache search + rerank for all datasets
2. `eval/fit_params.py`: Grid search + dropout + LOO CV + save
3. `eval/eval_benchmark.py`: Metrics + ablations + comparison table
4. `eval/cache/`: Cached results (gitignored)

## Open Questions

1. Should we cache multiple intent variants? (4x DashScope cost but enables intent ablation)
2. DashScope rerank cost for 950 queries × 200 docs: need to estimate token budget
3. SAGE OE queries are 100+ words, optimize_query will truncate heavily. Test with/without optimization.
4. PaSa uses keep_letters() for matching, our eval uses normalize_title(). Need to align.
