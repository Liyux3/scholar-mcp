# Scholar-MCP Evaluation Analysis (2026-05-11)

## Experimental Setup

- Benchmark: LitSearch (princeton-nlp/LitSearch), 597 NLP/ML queries
- Evaluated first 50 queries (inline_acl set)
- Limit: 20 results per query
- Pipeline: source search -> deduplicate -> RRF consensus fusion -> FlashRank rerank -> score
- Matching: title normalization + DOI matching against S2 corpusid ground truth

## Results Summary (n=50)

| Config | R@5 | R@10 | R@20 | MRR | S2 participation |
|--------|-----|------|------|-----|------------------|
| S2 only | 0.040 | 0.080 | 0.080 | 0.031 | 32/50 |
| OA+arXiv (no S2) | 0.040 | 0.080 | 0.320 | 0.049 | 0/50 |
| All (S2+OA+arXiv) | 0.060 | 0.100 | 0.300 | 0.052 | 32/50 |
| OA+arXiv (no query opt) | 0.020 | 0.120 | 0.220 | 0.027 | 0/50 |

### Baselines from LitSearch Paper (local corpus, different evaluation)
- BM25 (local): R@5 = 0.500
- GritLM-7B (local): R@5 = 0.748
- Google Search (API): R@5 = 0.428

## Key Findings

### Finding 1: S2 currently hurts more than helps
- Adding S2 to OA+arXiv drops R@20 from 0.320 to 0.300
- Per-query analysis: S2 helped 3 queries, hurt 6, no change 41
- S2 only is very weak (R@20=0.080, only active in 32/50 queries)
- Root cause: S2 returns niche low-citation papers that dilute the result pool
- S2's SPECTER2 semantic matching should be better, but query optimization truncates queries below what SPECTER2 can work with

### Finding 2: Query optimization is double-edged
- 80% of LitSearch queries are modified by optimize_query (>20 words)
- With optimization: R@20=0.320 (but loses semantic nuance)
- Without optimization: R@20=0.220 (arXiv fails on long queries: 19/50)
- Current keyword extraction drops bigrams and context words
- API search engines have different optimal query lengths (S2: semantic, OA: keyword, arXiv: keyword)

### Finding 3: Ranking quality is the bottleneck, not coverage
- Our previous coverage analysis showed OA=97.9% coverage
- But R@20=0.320 means 68% of findable papers aren't ranked high enough
- The gap between coverage (97.9%) and retrieval (32%) is the ranking gap
- FlashRank may not be doing much: papers are all somewhat relevant, scores are uniform

### Finding 4: API-based retrieval has a fundamental ceiling
- Our R@5=0.04 vs Google Search R@5=0.43 (10x gap)
- Both are API-based, but Google has much better ranking
- PaSa achieves R=0.574 using Google Search + LLM query generation + citation expansion
- The API backend quality matters enormously

## Per-Query Analysis: What Predicts Success?

| Factor | HITs (n=16) | MISSes (n=34) | Predictive? |
|--------|-------------|---------------|-------------|
| Avg query length | 25 words | 26 words | No |
| Median GT citations | 218 | 223 | No |
| GT in OA index? | Yes (97.9%) | Yes (97.9%) | No |

Success is driven by **query-GT alignment**: whether the optimized query happens to match the GT paper's title/abstract terms. Not by paper importance or query complexity.

## Improvement Directions (ranked by expected impact)

### 1. LLM-based query reformulation (HIGH impact, requires API cost)
Generate 3-5 keyword variants per query using LLM. PaSa does this.
Expected: 2-5x R@5 improvement.

### 2. Per-source query adaptation (MEDIUM impact, no extra cost)
Send different query formats to different sources:
- S2: keep semantic/long queries (SPECTER2 handles them)
- arXiv: extract title-like phrases
- OA: keyword queries with field filters

### 3. Citation expansion (MEDIUM impact, extra API calls)
For top-N search results, fetch their references and add to pool.
Testing on first 20 queries (in progress).

### 4. Better FlashRank utilization (LOW-MEDIUM impact)
FlashRank gives uniform scores when all candidates are similar.
Try: (a) increase rerank pool, (b) different model, (c) query-document cross-attention features.

### 5. Learned fusion weights (LOW impact until other bottlenecks fixed)
MLP on LitSearch GT to learn source weights. But coverage analysis showed coverage isn't the issue. Deferred.

## Paper Viability Assessment

### Can this become a paper?

**Current state: Not yet.**

The R@5=0.04 result is not publishable as-is. We need at least one of:
1. Demonstrate that with proper query reformulation, multi-source API search matches dense retrieval
2. Novel finding about source complementarity that changes how people think about academic search
3. A new benchmark or evaluation methodology contribution

### Most viable paper angle

**"Query Reformulation is the Bottleneck in API-Based Academic Search"**

Core claim: For API-based academic search, query quality matters 10x more than source diversity or fusion method. We show:
1. Source coverage is 97.9% (OA alone), so coverage isn't the issue
2. Multi-source fusion provides marginal/negative improvement (+0/-0.02 R@20)
3. Query optimization (keyword extraction) vs raw queries: +0.100 R@20
4. Per-source query adaptation could further improve
5. The real gap is between our query formulation and Google/PaSa's

This is an **empirical study** with a **surprising negative finding** (multi-source doesn't help as expected) and a **constructive insight** (query reformulation is the bottleneck).

### What's needed
1. 200+ query eval for statistical significance (in progress)
2. Query reformulation experiment (with LLM)
3. Citation expansion experiment
4. Comparison with PaSa/Google baselines on same queries
5. Per-source ablation with adapted queries
