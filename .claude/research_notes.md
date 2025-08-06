# Academic Search System Research Notes

## Landscape Map (as of 2026-05)

### The Problem Space: How researchers find papers

**Discovery patterns:**
1. Known-item search: "find this specific paper" (title, author, DOI)
2. Topic exploration: "what's out there on X?" (broad, needs recall)
3. Frontier tracking: "what's new in X this month?" (recency matters)
4. Citation chasing: "what cites/is cited by this paper?" (graph traversal)
5. Cross-domain discovery: "X from field A applied to field B" (semantic)
6. Literature review: systematic coverage of a subfield (completeness)

**Current tools researchers actually use:**
- Google Scholar: most popular but no API, CAPTCHA, poor ranking
- Semantic Scholar: 214M papers, SPECTER2 embeddings, Research Feeds, free API
- OpenAlex: 250M papers, best field taxonomy, replaced MAG, free
- PubMed/MEDLINE: gold standard for biomedicine
- arXiv: preprints, daily digest, CS/Physics/Math
- Crossref: DOI registry, 150M works, metadata-focused
- Elicit/Consensus: AI-powered synthesis tools (not just search)
- Connected Papers / Research Rabbit: visual exploration

### Retrieval Methods Taxonomy

**Stage 1: Initial Retrieval (recall-focused)**

| Method | How it works | Strengths | Weaknesses |
|--------|-------------|-----------|------------|
| BM25/TF-IDF | Term frequency, sparse vectors | Fast, no training, good for exact match | No semantic understanding |
| Dense retrieval | Learned embeddings (bi-encoder) | Semantic matching, handles paraphrasing | Needs training data, index |
| Learned sparse (SPLADE) | Neural sparse vectors | Best of both: semantic + exact | Needs training, slower than BM25 |
| Hybrid (sparse+dense) | Combine BM25 + dense scores | Consistently best recall | Complexity, tuning needed |

**Key dense retrieval models (2024-2026):**
- GritLM-7B: SOTA on LitSearch (74.8% recall@5), generative + representational
- E5-large/Mistral: strong instruction-following embeddings
- SPECTER2: S2's own model, trained on citation graph
- Instructor: instruction-tuned embeddings
- Nomic-embed: open source, competitive

**Stage 2: Reranking (precision-focused)**

| Method | Size | Speed | Quality | Notes |
|--------|------|-------|---------|-------|
| Cross-encoder (MiniLM) | 34MB | Fast (CPU) | Good | FlashRank default |
| Cross-encoder (TinyBERT) | 4MB | Fastest | OK | FlashRank nano |
| MonoT5 | 110MB | Medium | Better | Pointwise T5-based |
| ColBERT v2 | ~400MB | Medium | Very good | Late interaction |
| Jina Reranker v3 | 0.6B | Slow | SOTA | Last-but-not-late interaction |
| RankGPT / LLM reranker | API | Slowest | Best | Listwise, expensive |
| MICE (2026) | varies | Fast | Near cross-encoder | Minimal interaction |

**Stage 3: Post-processing**
- Deduplication (DOI, title matching)
- Field-of-study filtering
- Citation count / venue quality weighting
- Result merging from multiple sources

### Benchmarks

| Benchmark | Domain | Queries | Corpus | Key Metric | SOTA Score |
|-----------|--------|---------|--------|------------|------------|
| LitSearch | ML/NLP | 597 | 64K (ACL+ICLR) | recall@5 | 74.8% (GritLM) |
| STaRK-MAG | Academic | varies | MAG subset | Hit@5 | varies by model |
| BEIR/SciFact | Scientific | 300 | 5K | nDCG@10 | ~75% |
| LongEval 2025 | Scientific | 393 | 2M (CORE) | nDCG@10 | TBD |
| ClimateCheck 2026 | Climate | varies | varies | Recall@K | TBD |
| SCIDOCS | CS | 1K | 26K | MAP | ~20% |

**Key LitSearch findings:**
- BM25: 50% recall@5
- GritLM-7B: 74.8% recall@5 (BEST)
- E5: ~60% recall@5
- + GPT-4o reranking: +4.4% over best retriever
- Google Search: 42.8% recall@5 (much worse than dense retrievers!)
- Google Scholar: not formally tested but anecdotally similar to Google Search

### Gap Analysis: Where we could contribute

**Observation 1: Nobody has tested multi-API aggregation systematically**
LitSearch tests retrieval models against a local corpus. Nobody has tested:
"What if you query S2, OpenAlex, AND arXiv simultaneously and merge results?"
This is a different problem from traditional IR (fixed corpus, single index).
Our hypothesis: multi-source aggregation achieves higher recall than any single source.

**Observation 2: Lightweight reranking is understudied for academic search**
Most academic search papers use expensive models (GritLM-7B, GPT-4o).
FlashRank (4MB ONNX) has not been tested on academic search benchmarks.
If FlashRank + multi-source comes close to GritLM, that's a significant practical finding.

**Observation 3: API-based retrieval vs. local corpus retrieval is a different game**
Traditional IR assumes you have the full corpus indexed locally.
API-based retrieval (like ours) depends on what the API returns.
The retrieval quality is bounded by the API's own search engine, not ours.
This makes query formulation more important than embedding quality.

**Observation 4: Query understanding for academic search is underexplored**
- PromptReps (2024): use LLM to generate query/doc representations
- But nobody has systematically studied: how should queries be reformulated
  for different academic search APIs? (S2 vs arXiv vs OpenAlex have different
  query languages and different ranking algorithms)

**Possible paper contributions:**
1. First systematic evaluation of multi-source API aggregation for academic search
2. LitSearch evaluation of API-based systems (no local corpus needed)
3. FlashRank vs. expensive rerankers for academic search
4. Query optimization strategies for heterogeneous academic APIs
5. The "coverage gap" study: what papers does S2 miss that OpenAlex has, and vice versa?

## Key Papers to Deep-Read

**Must read (directly relevant):**
- [ ] LitSearch (EMNLP 2024, ArXiv:2407.18940) - our primary benchmark
- [ ] Sparse Meets Dense (2024, ArXiv:2401.04055) - hybrid retrieval for scientific docs
- [ ] PromptReps (2024, ArXiv:2404.18424) - LLM-based zero-shot retrieval
- [ ] GritLM (2024) - SOTA retriever on LitSearch
- [ ] Blended RAG (2024, ArXiv:2404.07220) - hybrid retrieval with semantic + BM25

**Should read (reranking):**
- [ ] FlashRank (PrithivirajDamodaran/FlashRank) - our current reranker
- [ ] MICE (2026, ArXiv:2602.16299) - efficient cross-encoder
- [ ] Jina Reranker v3 (2025, ArXiv:2509.25085) - SOTA reranker
- [ ] rerankers library (AnswerDotAI) - unified reranker API

**Should read (systems):**
- [ ] scimesh (LCDS2019/scimesh) - multi-provider search CLI
- [ ] research-superpower (kthorn) - Claude Code plugin for lit review
- [ ] Relaylit - 6-database search with AI ranking
- [ ] Alexandria (from Medium review) - citation graph + gap analysis

**Background:**
- [ ] BEIR (Thakur et al., 2021) - retrieval benchmark suite
- [ ] MTEB (Muennighoff et al., 2022) - massive text embedding benchmark
- [ ] STaRK (NeurIPS 2024) - semi-structured retrieval

## Next Steps
1. Run LitSearch eval on our system (need S2 API key to avoid 429)
2. Read LitSearch paper in detail, understand their eval methodology
3. Read PromptReps, could inform our query optimization
4. Design ablation experiments: single source vs multi-source
5. Compare FlashRank vs. larger rerankers on our data
6. Study the coverage gap between S2, OpenAlex, arXiv

## 2025-2026 Landscape: Related Systems (discovered 2026-05-11)

### Autonomous Literature Review Writing
- **LiRA** (AAAI 2026): multi-agent lit review writing, outline/write/edit/review agents. Retrieval is weak.
- **ResearchPilot** (Mar 2026): 4-stage pipeline (search->extract->synthesize->draft), S2+arXiv, DSPy orchestration
- **SWARM-SLR AIssistant** (2026): modular SLR automation framework, not scalable yet
- **WriteAssist** (2025): personalized lit review authoring with recommendation engine

### Autonomous Research Navigation (most relevant to us)
- **PaperScout** (Jan 2026, arXiv:2601.10029): MOST RELEVANT. Models paper search as sequential decision-making. Uses RL (PSPO) to train agent. Dynamically decides whether/when/how to invoke search and citation expansion tools.
- **Caesar** (Apr 2026, arXiv:2604.20855): graph-based discovery agent. Builds knowledge graph during traversal. Perceive-Think-Act loop. Backtracking via navigational stack. Key idea: "the path taken to find information provides useful context"
- **Discovery Engine** (May 2026, arXiv:2505.17500): Knowledge artifacts -> tensor manifold. Agents navigate the tensor space. Very ambitious, unclear if practical.
- **ResearchTwin** (Mar 2026, arXiv:2603.00080): Federated platform, researcher digital twins, inter-agentic discovery API with Schema.org types + HATEOAS. S-index metric beyond H-index.
- **Oignon** (2025): Citation graph viz tool, uses OpenAlex, dual-path ranking with recency weighting. Clean implementation.

### Knowledge Graph + RAG for Science
- **Hybrid RAG** (2025): Neo4j KG + FAISS vector store, LLaMA agent dynamically selects GraphRAG vs VectorRAG
- **AI-native Academic Retrieval** (Apr 2026, arXiv:2604.16416): tensor manifold theory for graph-vector fusion

### Key Observation
All existing systems either:
1. Focus on WRITING (LiRA, ResearchPilot) with weak retrieval
2. Focus on NAVIGATION (PaperScout, Caesar) with heavy compute requirements
3. Are too ambitious to be practical (Discovery Engine, ResearchTwin)

Nobody has a PRACTICAL, LIGHTWEIGHT, API-BASED tool that does navigation well.
Our scholar-mcp is the retrieval backbone. If we add smart citation traversal + RRF fusion + lightweight reranking, and make it work as an MCP tool that any LLM agent can call, we fill a real gap.

### Papers to Download and Deep-Read
- [ ] PaperScout (arXiv:2601.10029) - closest to our direction
- [ ] Caesar (arXiv:2604.20855) - graph-based exploration
- [ ] Discovery Engine (arXiv:2505.17500) - tensor approach
- [ ] LiRA (AAAI 2026) - lit review agents
- [ ] ResearchPilot (arXiv:2603.14629) - multi-agent system

## PaperScout Deep Analysis (from reading full paper)

**Benchmarks:**
- RealScholarQuery: 50 real-world scholarly queries with reference papers
- AutoScholarQuery: 112 synthetic queries (filtered subset with 5+ ground truth papers)
- Code: github.com/pty12345/PaperScout

**Key results (RealScholarQuery):**
- Google Search: Recall 0.304, Recall@25 0.221
- Google Scholar: Recall 0.247, Recall@25 0.158
- SPAR (fixed workflow): Recall 0.496, Recall@25 0.504
- PaperScout (Qwen3-4B PSPO): Recall 0.574, Recall@25 0.577

**Architecture:**
- POMDP: state = paper pool, action = Search(query) or Expand(paper)
- Agent sees summarized observation of top-k papers (dual-list: expanded vs unexpanded)
- Reward = relevance gain - repetition penalty
- PSPO: sequence-level RL, advantage estimation at interaction level not token level
- Training: local Milvus + ar5iv cache to avoid API rate limits
- Eval: Google Search (serper.dev) as retrieval backend

**Tool call analysis (Figure 6):**
- Trained agent balances Search and Expand evenly
- Untrained Qwen3-4B barely explores (few tool calls)
- Untrained Qwen3-Max is Expand-heavy, few Search calls
- => Policy matters more than model size for exploration efficiency

**Direct relevance to us:**
1. Our multi-source search could replace their Google Search backend
2. Their benchmarks (RealScholarQuery, AutoScholarQuery) can test our system
3. If our single-query recall beats Google Search's 0.304, that's already useful
4. Combined: our retrieval + their exploration policy = potential best system
5. Their PSPO training requires local corpus; our API-based approach is complementary

**Potential collaboration/contribution angles:**
- "Better retrieval backbone for agentic paper search"
- Test our system on their benchmarks
- Show that multi-source API fusion > Google Search for paper retrieval
- Then show that PaperScout + our backend > PaperScout + Google Search

## Technically Ambitious Directions (brainstorm, 2026-05-11)

### Direction A: Multi-Source Retrieval as Contextual Bandit
- Each API source (S2, OpenAlex, arXiv, Crossref) = an arm
- Query features = context (length, domain keywords, author mentions, etc.)
- Reward = recall@k on ground truth
- Learn a policy via UCB/Thompson Sampling to allocate API calls efficiently
- Insight: not all sources are equally useful for all queries. Learning to route saves latency and improves quality.
- Mathematical formulation: contextual bandit with structured action space
- Can also model as Markov chain across sources (query S2 first, if low confidence, try OpenAlex)

### Direction B: Weighted RRF with Learned Source Reliability
- Standard RRF treats all sources equally: score = sum(1/(k+rank_i))
- Propose: Weighted RRF where w_i depends on (query_domain, source_i)
- w_i = P(source_i is reliable | query_features)
- Learn w_i from ground truth data (LitSearch queries)
- Theoretical contribution: prove optimality conditions for weighted RRF
- Practical contribution: 2-3% recall improvement over uniform RRF

### Direction C: Query-Adaptive Source Routing
- Use a lightweight classifier on query text to predict:
  1. Which sources are likely to return relevant results
  2. Whether to use exact (keyword) or semantic (embedding) search
  3. Whether citation expansion would help
- Can be rule-based first (fast), then learned (better)
- Links to PaperScout's POMDP formulation but at the source-selection level

### Direction D: Coverage Gap Analysis
- Empirical study: for a set of ground-truth papers, which sources cover them?
- Question: how much unique coverage does each source add?
- If S2 covers 80%, OpenAlex adds 10%, arXiv adds 5%, Crossref adds 2% =>
  the marginal value of each source can be quantified
- This is an empirical contribution (novel data, useful for the community)
- Could lead to a "source recommendation" system

### Direction E: Federated Academic Retrieval for LLM Agents (system paper)
- Frame as: LLM agents need real-time academic search, can't maintain local indices
- Our system solves this via API aggregation + lightweight reranking
- Contribution: first MCP-based academic search tool evaluated on standard benchmarks
- Less theoretical but very practical, good for demo track or system paper

### Venue Mapping
- PaperScout: arXiv only so far, probably targeting SIGIR or ACL 2026
- LiRA: AAAI 2026 (published)
- Caesar: arXiv, probably targeting EMNLP or NeurIPS
- LitSearch: EMNLP 2024 (published)
- Our work could target: SIGIR demo, EMNLP, CIKM, or JCDL

## Latest Wave: Citation-Aware Exploration Systems (Nov 2025 - May 2026)

### SciRAG (Nov 2025, arXiv:2511.14362)
- Adaptive retrieval alternating sequential/parallel evidence gathering
- Citation-aware symbolic reasoning for filtering
- Outline-guided synthesis with plan-critique-refine loop
- Benchmarks: QASA, ScholarQA
- Key: explicitly uses citation graph structure for organization

### DualGraph (Feb 2026, arXiv:2602.13830)
- TWO co-evolving graphs: Outline Graph (OG) + Knowledge Graph (KG)
- KG stores entities, concepts, relations
- Uses KG topology + OG structure to generate targeted queries
- GPT-5 scores 53.08 RACE on DeepResearch Bench
- Key insight: separate "what you know" from "how you write"

### RLM-on-KG (Apr 2026, arXiv:2604.17056)
- LLM as navigator over RDF knowledge graph
- Core finding: LLM control advantage depends on evidence scatter
  - High scatter (6-10 chunks): +3.21 pp F1
  - Low scatter: +1.85 pp F1
- Separation of candidate discovery (LLM breadth) from ranking (vector reranking)
- Uses GraphRAG-Bench Novel (519 questions)

### Paper Circle (Apr 2026, arXiv:2604.06170)
- Multi-agent system: Intent -> Search -> Sort -> Analysis -> Export
- Builds typed Knowledge Graph from papers (concepts, methods, experiments, figures)
- Graph-aware Q&A with 1-hop neighbor expansion
- Multi-source retrieval with diversity-aware ranking
- Open-source, fully reproducible outputs

## Emerging Pattern (2026 Research Landscape)

The field is converging on a common architecture:
1. Multi-source retrieval (S2 + arXiv + OpenAlex + web)
2. Citation/knowledge graph construction during exploration
3. Dual memory (graph topology + vector semantics)
4. Adaptive exploration policy (LLM-driven or RL-trained)
5. Structured synthesis with adversarial refinement

Nobody has yet combined ALL of these into a single, practical system.
Most systems are either:
- Theoretically rich but impractical (Caesar, Discovery Engine)
- Practical but theoretically thin (ResearchPilot, research-superpower)
- Strong on one component but weak on others (PaperScout strong on exploration, weak on retrieval)

## Refined Research Direction

Our unique position: we already have the retrieval backbone (scholar-mcp) with
multi-source fusion + reranking. What we need to add is:
1. Citation graph traversal with adaptive exploration
2. Knowledge graph construction from discovered papers
3. A clean, practical system that works as MCP tool for any LLM agent

The "story" could be:
"We show that combining heterogeneous academic API fusion with
adaptive citation graph exploration achieves state-of-the-art recall
on standard benchmarks, while requiring no local index, no GPU,
and no training. Our system works as a drop-in MCP tool for
any LLM agent, democratizing access to systematic literature
exploration."

This frames it as both a system contribution AND an empirical finding
(multi-source API fusion + citation expansion > single-source dense retrieval).

## DualGraph Deep Analysis (from reading paper)

**Key technical contributions:**
1. Dual-graph memory: OG (hierarchical outline) + KG (semantic entity-relation graph)
2. Co-evolution: search results update both OG and KG simultaneously
3. KG gap discovery via:
   - SBM (Stochastic Block Model) for cross-community link probability estimation
   - Structural hole detection (Burt 2004) for bridging opportunities
   - Semantic similarity between core entities and concept nodes
4. Two types of search chains:
   - Enrich: strengthen weakly-supported existing relations
   - Explore: discover potentially important missing relations
5. Self-termination via 6-dimensional OG scoring
6. Leiden community detection on KG for higher-level structure

**Benchmarks:**
- DeepResearch Bench (100 PhD-level tasks, 22 domains)
- DeepResearchGym (100 info-seeking queries)
- DeepConsult (business/consulting)
- DualGraph (GPT-5) beats OpenAI Deep Research, Claude Research, Gemini 2.5-Pro DR

**From Microsoft Research. Very high quality. Feb 2026 preprint.**

**Implementation:**
- Bing Search API + Crawl4AI for web search and parsing
- GPT-4.1 and GPT-5 as backend LLMs
- MAX_ITER=5 rounds, 10 search queries per round from OG+KG
- Top-5 URLs per query, content extracted to evidence bank

## New Ambitious Idea: Citation Graph Gap Analysis

Instead of just finding papers, help users find RESEARCH GAPS:

1. Build citation graph from seed papers (using S2/OpenAlex citations API)
2. Apply community detection (Leiden/Louvain) to find subfields
3. Use SBM to estimate expected cross-community citation density
4. Flag pairs of communities where actual citations << expected
   => These are cross-disciplinary research opportunities
5. Identify emerging frontiers: communities with high recent growth rate
6. Find unexploited methods: highly-cited papers whose approaches
   haven't been applied to certain domains (structural holes)

This is technically interesting (graph theory + probabilistic models),
practically useful (researchers always ask "what's the gap?"),
and differentiable from all existing work (nobody does this on-the-fly
from API data without a local index).

Mathematical formulation:
- Citation graph G = (V, E) where V = papers, E = citations
- Community partition C = {C_1, ..., C_k} via Leiden
- For each pair (C_i, C_j), estimate expected edge count via SBM:
  E[|E_{ij}|] = |C_i| * |C_j| * p_{ij}
- Gap score = (E[|E_{ij}|] - |E_{ij}|) / sqrt(Var[|E_{ij}|])
- High gap score = under-connected communities = research opportunity

This could be a paper on its own, or a key component of a larger system.

## Prior Work on Citation Gap Detection (CRITICAL FINDING)

### "Knowledge Transfer, Knowledge Gaps, and Knowledge Silos in Citation Networks"
- PLOS One 2024, arXiv:2406.03921
- Dynamic community detection on citation networks
- Model expected knowledge transfer from content similarity + structural proximity
- Residual analysis: actual citation << expected => knowledge gap
- Case study on XAI research
- VERY close to our gap detection idea, but offline/batch processing

### SBM on Journal Citation Networks
- 2017 paper using SBM on Thomson Reuters citation data (630M citations)
- Finds blocks: clusters, bridges, sources, sinks
- Demonstrates hierarchical grouping via SBM

### Community Detection Comparison for Citation Networks
- PLOS One 2016: Infomap/map equation methods perform best
- 2025 Scientometrics: comparison of citation clustering vs topic modeling

### Implication for Our Work
The gap detection idea is NOT novel in isolation. But applying it:
1. On-the-fly from API data (not pre-built full network)
2. As part of an interactive LLM agent tool
3. Combined with multi-source retrieval + reranking
...IS still novel.

Our framing should be:
"We bring citation-aware gap detection from offline bibliometric analysis
into real-time agent-driven research exploration."

### Updated Paper Story Options

**Option A: System Paper (practical, broader audience)**
"FederatedScholar: A Multi-Source Academic Search Agent with 
Citation-Aware Exploration"
- Multi-source API fusion with RRF + lightweight reranking
- Citation graph exploration with gap detection
- MCP tool interface for LLM agents
- Evaluated on LitSearch + PaperScout benchmarks
- Venue: SIGIR demo, JCDL, CIKM

**Option B: Methodology Paper (more theoretical)**
"Online Citation Graph Analysis for Knowledge Gap Detection 
in Heterogeneous Academic APIs"
- Formal model: federated retrieval as contextual bandit
- Online community detection + SBM for gap estimation
- Theoretical analysis of coverage/recall bounds
- Venue: SIGIR, WWW, WSDM

**Option C: Benchmark + System Paper**
"How Complete Is Your Literature Search? Measuring Multi-Source 
Retrieval Coverage in Academic Search"
- Systematic study of S2 vs OpenAlex vs arXiv coverage
- Coverage gap quantification across domains
- New benchmark for federated academic retrieval
- Venue: EMNLP, NeurIPS D&B

## Knowledge Gap Detection Paper - Detailed Method (Cunningham & Greene, 2024)

**Method:**
1. Cumulative citation network G_t = (V_t, E_t), time steps by year
2. OSLM algorithm for overlapping/hierarchical community detection
3. TF-ICF labeling for communities
4. SciBERT embeddings for topic coherence measurement
5. Community interaction network I_t = (C_t, J_t)
6. p_ij = |cross-citations| / (|C_i| * |C_j|) as interaction probability
7. Regression model: expected p_ij from content similarity + structural proximity
8. Residuals = gaps (actual << expected)

**Their limitation (our opportunity):**
- Offline batch processing on pre-built complete citation network
- Our approach: online/incremental from API calls, partial graph
- Research question: how much of the graph do you need to sample for reliable gap detection?

## Layered Architecture Understanding

```
Layer 4: Report Generation (LiRA, ResearchPilot)
Layer 3: Knowledge Synthesis (DualGraph, Caesar)
Layer 2: Exploration Policy (PaperScout, SciRAG)
Layer 1: Retrieval Backend (scholar-mcp, scimesh)
Layer 0: Data Sources (S2, OpenAlex, arXiv, Crossref)
```

Most existing work only covers 1-2 layers.
Cross-layer integration is the real gap.

## Core Research Claim to Validate

"For API-based academic search, multi-source fusion with citation expansion
achieves recall comparable to dense retrieval on local corpora, while requiring
zero training, zero index, and zero GPU."

If true on LitSearch + PaperScout benchmarks, this challenges the assumption
that academic search requires local infrastructure.

## Immediate TODO (next session)
1. Register S2 API key (human task)
2. Implement RRF in relevance.py
3. Run full LitSearch 597 queries with S2 key
4. Compare: S2-only vs S2+arXiv vs S2+arXiv+OpenAlex
5. Compare: no reranker vs FlashRank vs larger reranker
6. Read PaperScout code to understand their benchmark data format
7. Design citation expansion experiment:
   - For each LitSearch query, search -> get top-5 papers -> expand references
   - Measure: does expansion improve recall?
8. Write up coverage gap analysis: for LitSearch ground truth papers,
   which source covers them? (S2 vs OpenAlex vs arXiv)

## Papers Downloaded (in scholar-search-research/papers/)
- 2407.18940.pdf - LitSearch (EMNLP 2024)
- 2511.14362.pdf - SciRAG (Nov 2025)
- 2601.10029.pdf - PaperScout (Jan 2026)
- 2602.13830.pdf - DualGraph (Feb 2026, Microsoft)
- 2604.06170.pdf - Paper Circle (Apr 2026)
- 2604.20855.pdf - Caesar (May 2026, Cognizant)
- 2406.03921.pdf - Knowledge Gap Detection (Jun 2024, UCD)

## Repos Cloned (in scholar-search-research/repos/)
- PaperScout (USTC)
- LitSearch (Princeton NLP)
- STaRK (Stanford)
- oignon (citation graph viz)
- scimesh (multi-provider search, in /tmp/)
- research-superpower (Claude plugin, in /tmp/)

## Mathematical Framework: Submodular Multi-Source Retrieval

### Core Formulation

Multi-source academic retrieval as Submodular Function Maximization:

**Definition.** Let U be the universe of academic papers. For each source s_i in S = {S2, OpenAlex, arXiv, Crossref, ...}, define coverage function:
  f_i(q) = {p in U : p is returned by querying s_i with query q and p is relevant}

**Property.** f_union(A) = |union of f_i(q_i) for each source i| is submodular in the number of sources queried, because each additional source has diminishing returns (most relevant papers are already found by previous sources).

**Optimization Problem:**
  max f_union(S') subject to |S'| <= k and cost(S') <= B
  where S' subset of S, k = budget of sources, B = API call budget

**Theorem (informal).** The greedy algorithm (add the source with highest marginal gain) achieves a (1-1/e)-approximation for this problem.

**Practical implications:**
1. Explains WHY multi-source fusion works (submodularity => diversity helps)
2. Gives a BOUND on how much better k sources can be vs 1 source
3. Suggests OPTIMAL source ordering (query the most complementary source next)
4. Can be verified empirically on LitSearch benchmark

### Related Mathematical Tools

- **Submodular Mutual Information (SMI)**: IF(A;Q) = F(A) + F(Q) - F(A∪Q)
  Measures information overlap between retrieved set A and query set Q.
  Recent paper (2024) gives tight bounds on query relevance and coverage.

- **Multi-Submodular Cover (Chekuri)**: Covering multiple submodular constraints.
  Our problem: cover all relevant papers by querying multiple sources.
  Bicriteria approximation: (1-1/e-ε) coverage with O(1/ε) cost.

- **Weighted RRF as a monotone submodular aggregation:**
  RRF_score(d) = sum(w_i / (k + rank_i(d)))
  Can show this is a concave aggregation of ranks, related to submodularity.

### Potential Theoretical Contributions

1. **Coverage bound**: Prove that k heterogeneous sources achieve at least
   (1 - prod(1-p_i)) coverage where p_i is per-source recall.
   This is the "coupon collector" insight applied to retrieval.

2. **Complementarity measure**: Define a measure of source complementarity
   based on the joint distribution of relevant papers across sources.
   Show that max complementarity maximizes the coverage bound.

3. **Optimal query strategy**: For a given budget B (total API calls),
   what's the optimal allocation of calls across sources?
   Formalize as a variant of the secretary problem or adaptive submodularity.

4. **Reranking amplification**: Show that reranking after fusion amplifies
   the coverage advantage of multi-source (because more candidates =>
   reranker has more "good" options to promote).

### This Gives Us a Paper Structure:

1. Problem formulation (submodular multi-source retrieval)
2. Theoretical analysis (coverage bounds, complementarity measure)
3. Algorithm (greedy source selection + RRF fusion + lightweight reranking)
4. Experiments (LitSearch, PaperScout benchmarks, ablation studies)
5. Practical system (MCP tool, open-source)

This is a SIGIR/EMNLP-level paper if executed well.

## Adaptive Submodularity Theory (Background for Our Work)

### Foundational Results
- **Golovin & Krause 2011**: Defined adaptive submodularity. Greedy achieves (1-1/e).
  Key paper: "Adaptive Submodularity: Theory and Applications in Active Learning and Stochastic Optimization"
- **Balkanski & Singer, STOC 2018**: Adaptive complexity of submodular max = Theta(log n).
  Tight: O(log n) rounds sufficient, Omega(log n / log log n) necessary.
- **Esfandiari et al., COLT 2021**: Semi-adaptive policy, O(log n * log k) rounds for (1-1/e-eps).
  Also proved the Golovin-Krause conjecture on stochastic minimum cost coverage.
- **Fahrbach et al., ICML 2019**: Non-monotone case, O(log n) rounds, O(n log k) queries.

### Connection to Our Problem

Our multi-source retrieval = adaptive submodular maximization:
- Ground set V = all (source, query) pairs
- State s = set of papers found so far
- Action a = query source i with query q (or expand citations of paper p)
- Utility f(s) = |relevant papers in s| (monotone submodular)
- Observation = set of papers returned by the action
- Budget = k total API calls

Greedy policy: always pick the (source, query) pair with highest expected marginal gain.
This achieves (1-1/e) approximation to optimal sequential policy.

Semi-adaptive: query all sources in parallel (batch mode), then rerank.
This is what our system does. Esfandiari's result says O(log k) rounds suffice.

### Why This Framing is Valuable

1. It EXPLAINS the empirical success of multi-source fusion
   (submodularity => diversity helps, formally)
2. It gives BOUNDS on how much better we can get
   (at most 1-1/e of optimal, and greedy achieves this)
3. It suggests ALGORITHMIC improvements
   (e.g., if one source has high correlation with another, skip it)
4. It's a well-studied theory with clean results
   (reviewers at SIGIR/EMNLP will recognize and appreciate this)

### What We Still Need to Prove

1. That retrieval coverage IS submodular in our setting
   (need to formalize and prove, probably via coverage function argument)
2. That the sources are "heterogeneous" in a quantifiable way
   (complementarity measure, empirically verifiable)
3. That greedy source ordering matches or beats uniform fusion
   (needs experiments on LitSearch)
4. That reranking amplifies the benefit of source diversity
   (informal argument: more candidates => better top-k after reranking)

### Paper Structure (Refined)

Title: "Submodular Multi-Source Retrieval: Theory and Application to Federated Academic Search"
or: "Provably Optimal Source Fusion for Heterogeneous Academic Search APIs"

1. Introduction: multi-source academic search as motivating example
2. Problem Formulation: adaptive submodular maximization
3. Theoretical Analysis:
   - Prove coverage submodularity
   - Coverage bound: (1 - prod(1-p_i)) where p_i = per-source recall
   - Greedy optimality: (1-1/e) approximation
   - Complementarity measure and its effect on the bound
4. Algorithm: greedy source selection + RRF + FlashRank reranking
5. Experiments:
   - LitSearch (597 queries): recall@5/10/20
   - Ablation: single source vs 2 vs 3 vs all
   - Source complementarity: coverage overlap analysis
   - Reranking: FlashRank vs no reranker vs larger models
   - Comparison with PaperScout, Google Scholar baselines
6. System Description: MCP tool, open-source
7. Discussion & Conclusion

Target venue: SIGIR 2027 (deadline ~Jan 2027), or EMNLP 2026 (deadline ~Jun 2026)

## Submodularity in IR: Existing Applications vs Our Novelty

### What's been done:
- Document diversification: submodular function = quality, distance = diversity (Borodin et al.)
- RAG reranking: submodular functions for coverage + diversity in retrieved set (UW thesis 2024)
- Result summarization: submodular selection of representative documents
- Active learning: adaptive submodular optimization for label acquisition

### What HASN'T been done:
- **Source selection** for federated/multi-API search using submodularity
- Treating API sources themselves as the "items" to select, not documents
- Proving coverage bounds for heterogeneous API ensemble

This is our novelty: lifting submodularity from document level to SOURCE level.

### Additional Mathematical Tools to Consider

- **DPP (Determinantal Point Process)**: probabilistic model that naturally produces diverse subsets. Connection to submodularity: DPP mode = submodular maximization.
- **Information-theoretic**: mutual information between source responses and ground truth. I(Y_1, ..., Y_k; R) where Y_i = response of source i, R = relevant papers.
- **Coupon collector**: multi-source coverage ~ coupon collector problem. Expected number of sources needed to cover all relevant papers.
- **Online learning**: if we query sources sequentially and observe results, this is a multi-armed bandit with submodular rewards. UCB/Thompson sampling for source selection.

### Clean Story Summary

"Existing work uses submodularity to select WHICH DOCUMENTS to show users.
We use submodularity to select WHICH SOURCES TO QUERY.
This is a higher level of abstraction: we optimize over retrieval APIs, not documents.
We prove that greedy source selection achieves near-optimal coverage,
and demonstrate this empirically on academic search benchmarks."

## Federated Search Source Selection: Prior Art Summary

### Foundational (2003-2010):
- Si & Callan (SIGIR 2003): relevant document distribution estimation for source selection
- Si & Callan (CIKM 2004): unified utility maximization framework
- Si & Callan (SIGIR 2005): modeling search engine effectiveness for federated search
- Kulkarni & Callan (selective search): topical sharding + resource ranking
- VLDB 2010: cost-aware source selection via dynamic programming

### Key Assumption in All Prior Work:
Sources are HOMOGENEOUS (same type of search engine, different document sets).
Nobody has formally studied HETEROGENEOUS source selection where sources
have fundamentally different indexing, ranking, and coverage characteristics.

### Our Novelty (Refined):

"Heterogeneous Federated Retrieval with Adaptive Source Selection"

The problem: given k heterogeneous search APIs (each with different coverage,
ranking algorithm, and query interface), how to optimally combine their results
for maximum recall under a query budget constraint?

Key technical innovations:
1. Formalize as adaptive submodular maximization over heterogeneous oracles
2. Prove coverage bounds that depend on source complementarity (new quantity)
3. Show that greedy source ordering respects complementarity (diminishing returns)
4. Design lightweight fusion algorithm (RRF + FlashRank) that's near-optimal
5. Empirical validation on LitSearch and PaperScout benchmarks

### Why This is Clean and Novel:
- Prior federated search: homogeneous sources, same index type
- Our work: heterogeneous APIs, different everything
- Prior submodular IR: document-level diversity/coverage
- Our work: source-level selection and fusion
- Both established fields, but the intersection is unexplored

### Conference Target:
Best fit: SIGIR (Information Retrieval, covers federated search and submodularity)
Alternative: EMNLP (if framed around academic/scientific search specifically)

## Deeper Insight: Inter-Source Agreement as Information Signal

### The Compositional Trap
Simply applying submodularity to source selection is compositional novelty.
Need to go deeper: what is the INHERENT STRUCTURE between heterogeneous sources?

### Key Observation
Different sources that index the same universe of papers but with different
algorithms create a natural "committee" of rankers. Their agreement pattern
on a given query carries information:

- High agreement (sources return similar results) => "easy" query, consensus
- Low agreement (sources return different results) => "hard" query or coverage gap
- Asymmetric disagreement (one source has results, another doesn't) => coverage gap

### Source Agreement Entropy

For a query q and sources S_1, ..., S_k returning result sets R_1, ..., R_k:

Define agreement matrix A where A_ij = |R_i ∩ R_j| / |R_i ∪ R_j| (Jaccard)
Source Agreement Entropy H(q) = -sum_ij (A_ij * log A_ij) / Z

Properties:
- H(q) is low when sources agree (easy query)
- H(q) is high when sources disagree (hard query)
- Can predict retrieval quality from H(q)
- Can adapt fusion strategy based on H(q)

### Why This Is Deeper Than Submodularity

1. It doesn't just say "diversity helps" (submodularity)
2. It characterizes the INFORMATION CONTENT of source disagreement
3. It suggests that source disagreement is itself a useful FEATURE
   for downstream tasks (query difficulty prediction, quality estimation)
4. It connects to ensemble learning theory (bias-variance decomposition)
   and voting theory (Condorcet jury theorem)

### Condorcet Jury Theorem Connection

If each source has probability p > 0.5 of returning the correct answer,
then the majority vote of k independent sources converges to 1 as k grows.
But sources are NOT independent. Their correlation structure determines
how much each additional source helps.

This gives us:
- A principled way to measure source INDEPENDENCE (from disagreement data)
- A formula for optimal number of sources (diminishing returns based on correlation)
- A prediction for when adding a new source won't help (saturated information)

### Information-Theoretic Formulation

Let R = set of relevant papers, Y_i = results from source i.

Define:
- Individual relevance: I(Y_i; R) = mutual information between source i and truth
- Joint coverage: I(Y_1, ..., Y_k; R) = joint mutual information
- Source synergy: S(Y_1, ..., Y_k) = I(Y_1,...,Y_k; R) - sum I(Y_i; R)
  (positive = sources complement each other, negative = redundant)

If we can ESTIMATE these from empirical data (LitSearch ground truth),
we get a PRINCIPLED measure of source complementarity.

This is a DISCOVERY, not a composition:
"We discover that inter-source agreement patterns in federated academic
search are informative signals for query difficulty and retrieval quality,
and that source synergy can be estimated from standard benchmarks."

### Even Deeper: What Makes Academic Search Sources Heterogeneous?

S2 uses SPECTER2 embeddings (citation-trained)
OpenAlex uses topic taxonomy (field-of-study hierarchy)
arXiv is keyword-based (lexical matching on preprints)
Crossref is DOI metadata (bibliographic fields)

These represent DIFFERENT VIEWS of the same paper universe:
- Semantic view (S2)
- Taxonomic view (OpenAlex)
- Lexical view (arXiv)
- Bibliographic view (Crossref)

The OPTIMAL combination should leverage the strengths of each view.
This is related to multi-view learning and co-training in ML.

Could formalize as: each source provides a PROJECTION of the full
information space, and optimal fusion is finding the best reconstruction
from multiple projections. This has connections to compressed sensing
and low-rank matrix completion.

## Cross-Domain Research Angles

### Signal Processing / Sensor Fusion
- Each API source = sensor with different noise characteristics
- Bayesian sensor fusion: P(R | Y_1,...,Y_k) posterior estimation
- Kalman Filter analogy for sequential source querying
- Information gain per source = reduction in uncertainty about R
- Venue: IEEE TSP, IEEE TKDE

### Control Theory
- Source selection as optimal control: state = known papers, control = which source
- Bellman equation for optimal query policy
- Model Predictive Control (MPC) for online source selection
- Venue: IEEE TAC, CDC

### Statistical Learning Theory
- Each source ranking = weak learner, fusion = ensemble
- PAC-Bayesian bounds for multi-source fusion error
- Source correlation structure determines ensemble improvement rate
- Connection to boosting theory: each source reduces error multiplicatively
  IF independent, less so if correlated
- Venue: JMLR, NeurIPS, COLT

### Random Graph Theory / Network Science
- Citation graph coverage as random coverage problem
- Each source covers a random subset of the paper universe
- Union coverage formula: P(covered by at least one source) = 1 - prod(1-p_i)
- Can model source coverage as inhomogeneous random graph
- Venue: Network Science, Physical Review E

### Applied Math / Optimization
- Multi-source retrieval as Set Cover or Maximum Coverage problem
- Approximation algorithms with provable guarantees
- Online optimization with partial feedback (bandit setting)
- Venue: Mathematical Programming, Operations Research

### The Ideal Paper Would:
1. Identify a genuine PHENOMENON (e.g., source disagreement predicts quality)
2. Model it with clean MATH (e.g., information-theoretic or probabilistic)
3. Prove non-trivial BOUNDS (e.g., convergence rate, approximation ratio)
4. Design a PRACTICAL ALGORITHM informed by the theory
5. VALIDATE empirically on standard benchmarks
6. Release an OPEN-SOURCE TOOL that anyone can use

## Information-Theoretic Framework: Deep Connections

### BatchBALD Connection (NeurIPS 2019, Kirsch et al.)
- Batch active learning as joint mutual information maximization
- I(y_1,...,y_b; omega | x_1,...,x_b) is submodular
- Greedy selection is (1-1/e)-approximate
- DIRECT analogy: replace "data points" with "sources", "model params" with "relevant papers"

### Sequential Information Maximization (COLT 2015, Chen et al.)
- Greedy MI maximization under noisy observations
- Separability condition determines when greedy is near-optimal
- If separability is too small, greedy FAILS
- Question for us: what is the "separability" of academic search sources?

### Active Multi-Source (UAI 2019)
- Cost-sensitive multi-source acquisition
- Acquisition rate = utility / cost
- MI-rate and variance-reduction-rate as acquisition functions

### Our Formulation (Refined)

Given sources S = {s_1, ..., s_k}, query q, true relevant set R:

**Source i's conditional information value:**
  I_i(q) = I(Y_i; R | Y_{S\i}, q)
  where Y_i = result of querying source i with q
  Y_{S\i} = results from all other sources

**Greedy source selection:**
  At each step, select s* = argmax_i I(Y_i; R | Y_{selected}) / cost(i)

**Theorem (to prove):** Under assumption that coverage function is submodular,
greedy achieves (1-1/e) of optimal source selection strategy.

**Key quantity to estimate empirically:**
  Source Synergy: S(s_i, s_j) = I(Y_i, Y_j; R) - I(Y_i; R) - I(Y_j; R)
  If S > 0: sources are synergistic (together better than sum of parts)
  If S < 0: sources are redundant (overlap)
  If S ≈ 0: sources are independent

This can be ESTIMATED from LitSearch ground truth data.

### Non-Compositional Insight

The insight is NOT "apply submodularity to source selection."
The insight IS: "Academic search APIs have a specific information-theoretic
structure (moderate synergy, domain-dependent correlation) that makes
greedy multi-source fusion near-optimal AND predictable."

The PREDICTION is: source synergy can be estimated from a small calibration
set, and then used to predict multi-source fusion performance on unseen queries.
If this prediction is accurate, that's a genuine scientific finding.

### Ideal Paper Story (Final Version)

"We study the information-theoretic structure of heterogeneous academic
search APIs. We show that (1) the coverage function over sources is submodular,
(2) source synergy is query-dependent but estimable, (3) greedy source
selection is provably near-optimal, and (4) inter-source agreement predicts
retrieval quality. We validate on LitSearch (597 queries), demonstrating
that a lightweight multi-source system with FlashRank reranking achieves
recall competitive with GritLM-7B dense retrieval, at zero training cost."

## CRITICAL NEW FINDINGS (2025-2026)

### PaSa (ACL 2025, ByteDance) - KEY PAPER
- Full paper at ACL 2025 (top NLP venue)
- Created AutoScholarQuery (35k synthetic) and RealScholarQuery (50 real) benchmarks
- PaSa-7B beats Google+GPT-4o by 37.78% recall@20
- Code: github.com/bytedance/pasa, Demo: pasa-agent.ai
- PaperScout is a direct follow-up to PaSa (same benchmarks)

### SAGE (Feb 2026) - CHANGES EVERYTHING
- 1200 queries, 4 scientific domains, 200K paper corpus
- **SHOCKING FINDING: BM25 BEATS LLM-based retrievers by ~30%**
- Why: agents generate keyword-oriented sub-queries
- Solution proposed: corpus-level test-time scaling (augment docs with metadata)
- Implication: keyword search (like our API approach) may be BETTER than dense retrieval for agent workflows!

### AgentIR (Mar 2026)
- Reasoning-Aware Retrieval: embed agent's reasoning trace + query together
- AgentIR-4B: 68% on BrowseComp-Plus vs 50% for conventional embeddings
- New paradigm: retrieval models should understand agent reasoning, not just queries

### How This Changes Our Story

OLD story: "We use API keyword search as a lightweight alternative to dense retrieval."
NEW story: "We show that heterogeneous API keyword search is the RIGHT paradigm
for agent-driven academic retrieval, supported by SAGE's finding that BM25 > dense
in agent workflows, and amplified by multi-source fusion."

This is a much STRONGER claim because:
1. SAGE provides independent evidence that keyword search > dense for agents
2. Our multi-source fusion AMPLIFIES this advantage (more diverse keyword results)
3. FlashRank reranking adds semantic understanding ON TOP of keyword recall
4. The combination (keyword recall + semantic reranking) may be optimal for agents

### Updated Paper Pitch

"SAGE (2026) showed that BM25 outperforms dense retrievers in agent workflows
because agents naturally generate keyword queries. We extend this finding by showing
that heterogeneous multi-source keyword retrieval (querying S2, OpenAlex, and arXiv
simultaneously) further amplifies this advantage through source diversity. We prove
that the coverage function over heterogeneous sources is submodular, and demonstrate
on LitSearch and PaSa benchmarks that our lightweight system matches or exceeds
dense retrieval baselines, validating the 'keywords + diversity + reranking' paradigm
for agent-driven academic search."

### Papers to Download
- PaSa: bytedance/pasa on GitHub, ACL 2025
- SAGE: arXiv:2602.05975
- AgentIR: arXiv:2603.04384

## SAGE Deep Analysis (from reading paper pages 1-4)

### Benchmark Design
- 4 domains: CS, Natural Science, Healthcare, Humanities
- 300 short-form + 300 open-ended per domain = 1200 total queries
- ~50K paper corpus per domain (200K total), open-access PDFs only
- Short-form: single GT, Exact Match metric
- Open-ended: multiple GT with relevance scores {0,1,2}, Weighted Recall metric

### Short-form Question Construction
- From paper metadata, figures/tables, inter-paper relationships
- Papers must share >= 4 common references to be "related"
- Questions require reasoning over metadata + cross-paper relationships
- Generated by GPT-5-mini

### Open-ended Question Construction
- Background context + detailed information + query instructions
- Multiple GT papers with hierarchical relevance
- Seed papers (most relevant, r=2) + shared references (relevant, r=1)

### Key Experimental Finding
- BM25 >> LLM retrievers by ~30% in agent workflows
- Reason: agents generate keyword-oriented sub-queries
- LLM retrievers try to do semantic matching on keyword queries => mismatch
- Corpus-level test-time scaling (adding metadata) helps +8%/+2%

### Evaluated Agents
- GPT-5 (OpenAI)
- Gemini-2.5-Pro (Google)
- DR Tulu (open-source, RL-trained for deep research)
- All struggle with reasoning-intensive retrieval

### Code: github.com/HughieHu/Sage (cloned to repos/Sage)

### For Our Work
- SAGE is the ideal benchmark (4 domains, reasoning-intensive, 200K corpus)
- But we'd need to adapt: their eval assumes local corpus search,
  we'd need to map GT papers to API-retrievable papers
- Their finding directly supports our API keyword search approach
- We should cite SAGE prominently and frame our work as extending their finding

## Evaluation Plan (3 Benchmarks)

### 1. LitSearch (EMNLP 2024)
- 597 queries, NLP/ML domain
- Metric: recall@5/10/20
- Baselines: BM25 (50%), GritLM (74.8%), E5 (60%), +GPT-4o rerank (79.2%)
- Data: HuggingFace princeton-nlp/LitSearch
- Our target: competitive with GritLM WITHOUT local index

### 2. PaSa/RealScholarQuery (ACL 2025)
- 50 real-world AI queries
- Metric: recall@k, precision
- Baselines: Google (0.304), PaSa-7B (0.574)
- Data: HuggingFace CarlanLark/pasa-dataset
- Our target: beat Google, competitive with PaSa-7B

### 3. SAGE (Feb 2026)
- 1200 queries, 4 domains, 200K corpus
- Metric: Exact Match (short-form), Weighted Recall (open-ended)
- Baselines: BM25 (best), LLM retrievers (~30% worse), agents
- Data: github.com/HughieHu/Sage
- Our target: confirm SAGE finding that keyword > dense for agents,
  show multi-source amplifies this

### Ablation Studies
1. Single source vs 2 vs 3 vs all sources
2. With vs without FlashRank reranking
3. With vs without query optimization
4. With vs without RRF fusion
5. Source coverage overlap analysis (Venn diagram)
6. Source synergy computation

### Repos and Data Ready
- LitSearch: repos/LitSearch + HuggingFace
- PaSa: repos/pasa + HuggingFace
- SAGE: repos/Sage (queries in JSON)
- PaperScout: repos/PaperScout
- All 8 repos cloned, 15 papers downloaded

## CRITICAL: Competing Work Discovered (GRO-RAG, MoR)

### GRO-RAG (OpenReview Sep 2025)
- Multi-source RAG with source selection as relevance-redundancy tradeoff
- Gradient-aware reranking: uses LLM gradients to score documents
- Proves gradient-based top-k approximates loss-minimizing subset selection
- Leave-one-out loss upper bound
- Training-free but requires LLM gradient access
- DIRECTLY relevant: they formalize source selection mathematically

### MoR - Mixture of Retrievers (EMNLP 2025)
- Zero-shot weighted combination of heterogeneous retrievers
- Multi-granularity deep fusion (query variants + passage variants)
- Pre-retrieval signals (retriever trustworthiness) + post-retrieval signals
- 0.8B mixture BEATS 7B GritLM by +3.9% NDCG@20
- EMNLP Main 2025 = top venue
- DIRECTLY relevant: heterogeneous retriever fusion

### Diversity CCBQP (Apr 2026)
- Diversity retrieval as binary quadratic programming
- Frank-Wolfe algorithm with convergence guarantees
- Dominates MMR and DPP on Pareto frontier
- 2.4x to 22.9x speedup over DPP

### Impact on Our Positioning

We CANNOT claim:
- "First to do heterogeneous retriever fusion" (MoR did it)
- "First to formalize source selection" (GRO-RAG did it)

We CAN claim:
- "First to study API-based heterogeneous fusion for academic search"
  (MoR uses local retrievers, not APIs; GRO-RAG needs gradient access)
- "First to show that API keyword search + multi-source diversity matches
  dense retrieval for agent workflows" (supported by SAGE finding)
- "First systematic coverage analysis of academic search APIs"
  (nobody has measured S2 vs OpenAlex vs arXiv overlap)

### Refined Unique Contributions

1. EMPIRICAL: systematic coverage analysis of academic APIs
   (what does each source uniquely cover?)
2. THEORETICAL: adapt MoR/GRO-RAG framework to API setting
   (where you can't train, can't access gradients, sources have different query interfaces)
3. PRACTICAL: first MCP-based academic search tool evaluated on standard benchmarks
4. FINDING: confirm SAGE's BM25>dense finding AND show multi-source amplifies it

### Need to Read
- MoR paper in detail (EMNLP 2025)
- GRO-RAG paper in detail
- Check if they cite each other or if there are more related works

## Truly Unique Insight: Consensus-Based Quality Estimation in API Settings

### The Gap That Only API Settings Have

MoR: knows retriever quality (trained weights)
GRO-RAG: knows document contribution (gradient access)
Our setting: knows NOTHING about source quality a priori

We can only observe: which papers each API returns for a given query.

### Key Insight: Output Agreement as Quality Signal

If paper X is returned by both S2 AND OpenAlex for the same query:
  => X is more likely relevant (two independent confirmations)

If paper Y is only returned by arXiv:
  => Y's relevance is less certain

This is exactly the Condorcet Jury Theorem applied to retrieval:
- Each source is a "voter" with some accuracy p_i > 0.5
- Papers that receive "majority votes" (appear in multiple sources) are more likely correct
- The aggregate accuracy improves with more independent sources

### Mathematical Formulation

Let R_i(q) = papers returned by source i for query q
Let p_i(q) = precision of source i for query q (unknown)

For a paper d, define:
  vote(d, q) = |{i : d in R_i(q)}|  (number of sources returning d)

Claim: P(d is relevant | vote(d,q) = v) increases monotonically with v
  (under mild independence assumptions)

Proof sketch:
  P(d relevant | vote = v) = P(vote = v | relevant) * P(relevant) / P(vote = v)
  P(vote = v | relevant) = C(k,v) * prod(p_i) for participating sources
  P(vote = v | not relevant) = C(k,v) * prod(false_positive_i) << P(vote=v|relevant)
  Since p_i > false_positive_i, the ratio increases with v. QED.

### This Gives a NOVEL Ranking Strategy

Instead of RRF (which uses rank positions), use:
  CONSENSUS_score(d) = vote(d) * RRF_score(d)

Papers appearing in multiple sources get boosted.
Papers appearing in only one source get discounted.

This is DIFFERENT from MoR (which learns weights from training data)
and from GRO-RAG (which uses gradients).
It's a ZERO-SHOT, TRAINING-FREE, GRADIENT-FREE method that
exploits the STRUCTURAL PROPERTY of multi-source agreement.

### Connection to Crowdsourcing Theory

This is essentially the DAWID-SKENE model (1979) applied to retrieval:
- Each source is an "annotator" with unknown accuracy
- Each paper is an "item" to be classified (relevant/not)
- Use EM to jointly estimate source accuracies and paper relevances
- Well-studied in crowdsourcing literature, clean theory

### Why This Could Be a Paper

Title: "Consensus-Based Ranking for Multi-API Academic Retrieval"
or: "When Sources Agree: Quality Estimation in Heterogeneous Retrieval APIs"

1. Formalize the multi-source consensus problem (Section 2)
2. Prove that consensus score has monotone quality guarantee (Section 3)
3. Connect to Dawid-Skene and derive EM-based source quality estimation (Section 4)
4. Algorithm: multi-source search -> consensus scoring -> reranking (Section 5)
5. Experiments: LitSearch + SAGE + PaSa benchmarks (Section 6)
6. Analysis: when does consensus help vs hurt? (Section 7)

This story is CLEAN, NOVEL, and has MATHEMATICAL DEPTH.
It's not compositional (applying X to Y).
It identifies a STRUCTURAL PROPERTY unique to API settings.

## Prior Work on Consensus/Agreement in Retrieval

### ULARA (2007)
- Unsupervised rank aggregation via agreement maximization
- Linear combination of rankers, weight ~ agreement with pool
- TREC-3 data fusion experiments
- Core insight matches ours: accurate rankers agree more
- But: uses linear combination, not Bayesian; all systems are homogeneous

### Dawid-Skene in IR (Hosseini et al. 2012, TREC 2011)
- Used for aggregating crowdsourced relevance judgments
- EM to estimate worker accuracy + document relevance jointly
- Applied to human annotators, NOT search APIs
- Our contribution: apply this framework to search API "voters"

### Factored Bradley-Terry (2019)
- Ranking from pairwise comparisons with irrelevant factor bias
- Handles worker biases towards certain features
- Related but different problem (pairwise vs set-based)

### Gap We Fill
Prior work applies consensus to: human annotators, TREC system runs (homogeneous)
Nobody has applied Dawid-Skene style estimation to:
- Heterogeneous academic search APIs
- Where each API has different coverage, ranking algorithm, query interface
- In an agent-driven workflow
- With the specific goal of estimating source reliability WITHOUT training data

This is genuinely novel: treating APIs as "annotators" with unknown quality,
and using their agreement pattern to estimate both source reliability and
document relevance simultaneously.

## Existing Gap Detection Tools (discovered)

- LitGapFinder (Mar 2026): concept co-occurrence graph, GapScore = sim/(1+cooccurrence)
- HySemRAG (Aug 2025): hybrid RAG + KG for gap analysis, Neo4j + Qdrant
- Cicadus: commercial product, citation mapping + gap identification
- Connected Papers, Research Rabbit: visual exploration (not gap detection per se)

Gap detection is a CROWDED space. We should NOT position as "gap detection tool."

## Recalibrating: What Direction Has the Most Impact?

After reviewing everything, the most impactful and least crowded directions are:

### Option 1: PaperScout-style Agent with Better Backend (PRACTICAL)
- Use our multi-source retrieval as backend for a PaperScout-like agent
- No RL training needed (zero-shot LLM decision making)
- Key advantage: PaperScout only uses S2, we use 3+ sources
- Could test: does multi-source backend improve PaperScout's recall?
- VERY practical, could be a popular open-source tool

### Option 2: SAGE Extension (EMPIRICAL/BENCHMARK)
- Extend SAGE's finding: not just "BM25 > dense for agents"
  but "multi-source BM25 >> single-source BM25 >> dense for agents"
- Systematic study of how source diversity affects agent retrieval
- New benchmark contribution or significant empirical study
- CLEAN story, extends a very recent important finding

### Option 3: Consensus Ranking Theory (THEORETICAL)
- Dawid-Skene for API retrieval
- Novel but may be too narrow for top venue
- Better as a component of a larger system paper

### BEST APPROACH: Combine 1 + 2
Build a practical agent tool (like PaperScout but zero-shot + multi-source),
AND do a systematic empirical study extending SAGE's finding.
Theory (consensus scoring) can be one component, not the main contribution.

Title: "Beyond Single-Source Search: How Source Diversity Amplifies
Agent-Driven Academic Retrieval"

1. Motivation: SAGE showed BM25 > dense for agents. We ask: does querying
   MULTIPLE keyword sources further improve agent retrieval?
2. System: multi-source search MCP tool + zero-shot exploration agent
3. Theory: submodular coverage bounds for multi-source, consensus scoring
4. Experiments: LitSearch + SAGE + PaSa benchmarks
5. Ablation: number of sources, source combinations, with/without reranking
6. Analysis: source complementarity, coverage overlap, agreement patterns
7. Tool: open-source MCP server + exploration agent

## MoR (EMNLP 2025) Deep Analysis

### Core Method
- Weight function f(q, R_i, D) for each retriever per query
- Adjusted score: s̃(q, d) = sum(f * s_i) over retrievers
- Pre-retrieval signal: retriever-query familiarity (embedding geometry)
- Post-retrieval signal: query performance prediction
- Multi-granularity: query variants (original, sub-question) x passage variants (paragraph, sentence)

### Key Results
- Route Oracle (best single retriever per query) beats GritLM by 13.5%
  => massive potential from retriever diversity
- MoR 0.8B beats GritLM 7B by +3.9% average NDCG@20
- Works with "human retrievers" (noisy but useful), +58.9% over humans alone

### Critical Difference from Our Setting
MoR has SCORES (each retriever gives score for each doc).
We have only RANKS (APIs return ranked lists without scores).
=> MoR's weighted score sum doesn't apply.
=> We need RANK-based fusion (RRF) + agreement-based quality estimation.

This is not just an engineering difference. It's a FUNDAMENTAL difference
in the information available for fusion:
- Score-based: can do weighted average (MoR)
- Rank-based: must use rank fusion (RRF) or agreement (our consensus idea)

### Paper by CMU + Darmstadt, EMNLP 2025 Main
Code: github.com/Josh1108/MixtureRetrievers

## PaSa Evaluation Pipeline (from code reading)

### Title Matching Method
keep_letters(s) = only keep alphabetic chars, lowercase
e.g., "Attention Is All You Need!" -> "attentionisallyouneed"
This is how they match predictions to ground truth.

### Metrics
- Crawler Recall: all crawled papers / ground truth
- Selected Precision: selected papers (score > 0.5) that are correct
- Selected Recall: ground truth found in selected papers
- Recall@20/50/100: top-k crawled papers (by score) / ground truth

### Ground Truth Format
paper_root["extra"]["answer"] = list of paper titles
Matching is done via keep_letters(title) normalization

### How We Can Use This
1. Download RealScholarQuery from HuggingFace (CarlanLark/pasa-dataset)
2. For each query, run our multi-source search
3. Normalize returned titles with keep_letters()
4. Compare against ground truth titles
5. Report recall@20/50/100 and precision
6. Direct comparison with:
   - Google Search: recall 0.304
   - Google Scholar: recall 0.247
   - PaSa-7B: recall 0.574
   - PaperScout: recall 0.574

### Code
- github.com/bytedance/pasa (Python, simple eval script)
- We can reuse their keep_letters() and cal_micro() directly

## Session: 2026-05-11 (Autonomous, RRF + Coverage Analysis)

### Work Done
1. Implemented RRF fusion in relevance.py (rrf_score, consensus_rrf_score, rrf_fuse)
2. Added per-source rank tracking through dedup pipeline
3. Improved keyword extraction for long research queries
4. Built benchmark evaluation framework (eval_framework.py, coverage_analysis.py)
5. Ran comprehensive coverage analysis across all 4 SAGE domains

### Key Implementation: RRF Fusion
- tag_source_ranks(papers, source_name): annotates each paper with rank position
- rrf_score(paper, k=60): sum(1/(k+rank_i)) across sources (Cormack 2009)
- consensus_rrf_score: vote_count * rrf_score (Condorcet-inspired)
- rrf_fuse(papers, method="rrf"|"consensus"): score and sort
- _source_ranks merged during dedup alongside _source_count
- Pipeline: collect -> tag_ranks -> dedup (merge ranks) -> rrf_fuse -> filter -> score -> rerank

### Critical Finding: Source Coverage Analysis

Tested on SAGE benchmark, 20 queries per domain, title-based search:

| Domain | Papers | OpenAlex | arXiv | Crossref | Union |
|--------|--------|----------|-------|----------|-------|
| CS | 96 | 97.9% | 71.9% | 47.9% | 97.9% |
| Healthcare | 87 | 97.7% | 0.0% | 83.9% | 97.7% |
| Humanities | 62 | 100% | 0.0% | 95.2% | 100% |
| Nat. Science | 104 | 95.2% | 0.0% | 86.5% | 95.2% |

Analysis:
- OpenAlex alone = union coverage (no source adds marginal papers)
- arXiv is a strict subset of OpenAlex for CS, 0% for non-CS
- Crossref adds nothing beyond OpenAlex, but useful as 2nd choice for non-CS
- All 3 sources combined still miss 2-5% of papers (very new or obscure)

### Implication for Paper Story
The original "source diversity amplifies recall" hypothesis is WRONG for coverage.
Multi-source fusion value (if any) must come from ranking quality, not coverage.

Possible pivots:
1. Coverage analysis itself as empirical contribution (novel measurement)
2. Ranking quality: multi-source RRF improves precision@K even with same coverage
3. Query reformulation as the real bottleneck (coverage=97.9% but retrieval recall << 10%)
4. S2 as the differentiator (SPECTER2 embeddings, but needs API key)
5. Citation expansion as the coverage extender (single-hop coverage is high, multi-hop adds more)

### Retrieval Results (preliminary)
- SAGE open-ended (5 CS queries, arxiv+openalex): recall@20=0.029, hit_rate=0.200
- SAGE short-form: 0% (queries reference visual/tabular content, not keyword-searchable)
- Root cause: query reformulation, not source coverage
- SAGE queries are 200-800 word research questions, keyword extraction loses key concepts

### Next Steps
1. Decide pivot direction
2. Run OpenAlex-only vs multi-source ranking comparison
3. Test LitSearch (more natural queries)
4. LLM-based query reformulation experiment
5. Citation expansion experiment

## Session: 2026-05-11 (Continued, S2 Key + Citation Quality)

### S2 API Key Obtained
Configured in ~/.zshenv (env var, not committed). Added S2 to eval framework.

### CRITICAL BUG FIX: Wrong Paper ID
Previously used S2 ID `649def34...` thinking it was "Attention Is All You Need",
but it was actually "Construction of the Literature Graph in Semantic Scholar" (Allen AI 2018).
Correct AIAYN ID: `204e3073870fae3d05bcbc2f6a8e263d9b72e776` (175K citations).
This invalidated earlier S2 reference quality claims.

### Citation API Quality Comparison (corrected)

**S2 citations**: Returns most recent citations only, no sort by impact.
For AIAYN (175K cites), top 300 recent citations are all cites=0-1.
Our fix: fetch 3x and sort locally by citation_count. Helps for <1K citation papers,
but fundamentally limited for high-citation papers.

**OpenAlex citations**: Sorted by cited_by_count:desc by default. Returns
AlphaFold (43K), ViT (21K), etc. Far superior for finding influential follow-ups.

**S2 references**: Quality is fine (confirmed with correct paper ID).
Returns real references like Conv Seq2Seq, MoE, Xception.

**OpenAlex references**: Also fine, different subset.
Returns ResNet, Bahdanau attention, Penn Treebank.

**Source strengths:**
- S2: best for SEARCH (SPECTER2 semantic embeddings)
- OpenAlex: best for COVERAGE (97.9%) and CITATION GRAPH (impact-ranked)
- arXiv: CS preprints, adds diversity

### LitSearch Results with S2

| Config | N | R@5 | R@20 | Hit% | MRR |
|--------|---|-----|------|------|-----|
| OA+arXiv | 10 | 0.000 | 0.100 | 10% | 0.006 |
| S2-only | 20 | 0.150 | 0.200 | 10% | 0.028 |
| S2+OA+arXiv | 20 | 0.125 | 0.250 | 20% | 0.114 |

S2 adds significant value for search (R@5 from 0 to 15%).
Multi-source further improves R@20 and MRR.
50-query eval running for more robust numbers.

### Tool Improvements Made
1. S2 citations sorted by citation_count (fetch 3x, sort locally)
2. OpenAlex citation/reference fallback for S2-unavailable scenarios
3. ID resolution for OpenAlex (handle full URLs, W-ids, DOIs)
4. Query optimization threshold raised (20 words, preserve agent queries)
5. Internal fields stripped from tool output
6. Tool descriptions updated with usage tips

## Citation Graph Visualization Tools Survey (2026-05-11)

### Most Relevant Tools

**citracer** (2026, Python, MIT, marcpinet/citracer)
- Recursive citation tracing with keyword filtering
- Sources: S2, arXiv, OpenReview, OpenAlex, Sci-Hub
- pyvis interactive HTML rendering, networkx analytics (PageRank, centrality)
- Forward + reverse tracing, fuzzy title matching (rapidfuzz)
- Cross-graph bibliographic links post-processing
- 5-depth trace: 50-150 papers in minutes

**Paper Master** (2025, TypeScript, snooow1029/paper_master)
- D3.js force-directed graph, LLM-powered relationship analysis
- GROBID PDF parsing, arXiv search integration
- Obsidian integration (export to knowledge graph)
- Section-level citation filtering

**oowekyala/citegraph** (Python, MIT)
- S2 API, DOI-based exploration priority
- Output: GEXF (Gephi), DOT (Graphviz), PDF, PNG, SVG
- Smart exploration: degree-of-interest (DOI) calculation

**Citegraph.io** (Java, 21 stars)
- 5M papers, 294M edges, JanusGraph database
- Deployed web service, too heavy for MCP tool

### Key Design Insights
1. pyvis > mermaid for interactive citation graphs (scalable, clickable, physics-based layout)
2. Keyword-filtered expansion (citracer) is smarter than blind BFS
3. Cross-graph link detection (fuzzy title matching after main traversal) catches indirect connections
4. networkx analytics (PageRank, betweenness centrality) identify key papers better than raw citation count
5. Obsidian/Gephi export broadens tool utility for researchers

### Our Differentiation
- MCP interface: agent-callable, not CLI-only
- Multi-source backend: S2+OA+arXiv (citracer also does this)
- Zero-LLM exploration: no LLM scorer needed (Paper Master needs LLM)
- Compact agent-friendly output (summary + mermaid, not just raw data)
- Could add pyvis HTML export as alternative to mermaid for larger graphs

## Key Algorithms from Citation Graph Tools (2026-05-11)

### oowekyala/citegraph: Degree of Interest (DOI) Algorithm
DOI(n) = api_weight * API(n) - distance_penalty * distance(n)
API(n) = min(degree(n), degree_cut) * (1 + clustering * clustering_coefficient(n))

Key design choices:
- degree_cut=3: caps influence of super-popular papers, favors niche connectors
- clustering_coefficient: prefers papers tightly connected to existing graph
- distance_penalty: papers closer to seed get higher priority
- author_similarity for edge cost: same-group papers connected cheaper
- biblio bonus: 3x for papers in user's bibliography
- Dynamic DOI: recalculated each iteration as graph grows

Our adaptation: citation velocity priority (simpler, no graph-structure awareness)
Potential: add connectivity bonus (# existing nodes in references/citations)

### citracer: Keyword-Filtered Recursive Tracing
- Parse PDF, find sentences containing keyword
- Only follow references cited in those keyword-matching sentences
- Cross-graph bibliographic link detection (fuzzy title matching)
- Reverse tracing: walk UP citations filtered by S2 citation contexts

Our adaptation: topic_filter parameter for title/abstract keyword matching.
Not as precise as section-level filtering (no PDF parsing) but lightweight.

### citracer: Analytics (networkx)
- Betweenness centrality > 2x mean + keyword match = pivot paper
- PageRank on citation graph
- Timeline: per-year keyword density
- Global: density, avg_degree, connected_components

Could integrate: add networkx-based analytics to our graph output.
Would need networkx as dependency.

## Field Discovery & Exploration Systems Survey (2026-05-11)

### Most Relevant to Our Work

**Perspicacite-AI** (CNRS, Apache-2.0, 12 stars, holobiomicslab/Perspicacite-AI)
- 5 RAG modes: Basic -> Advanced -> Profound -> Agentic -> Literature Survey
- Literature Survey mode: broad search -> theme clustering -> AI recommendations
- Multi-source: S2, OpenAlex, PubMed, arXiv, HAL, DBLP
- MCP server: 8 tools (search, get_content, get_references, KB management)
- ChromaDB for local KB
- Most architecturally similar to our scholar-mcp + agent workflow

**LitGapFinder** (clawRxiv, 2026)
- Concept graph from 100 papers -> gap detection -> hypothesis generation
- Gap = under-connected concept pairs (GapScore = sim / (1 + cooccurrence))
- Multi-domain presets: drug_discovery, physics, economics, climate, neuro
- Uses sentence-transformers for concept embeddings

**Open Coscientist** (Jataware, 109 forks, LangGraph)
- Google AI Co-Scientist open reproduction
- 8-10 agents: Supervisor, Literature, Generator, Reviewer, Ranker, Tournament, Meta, Evolve
- Elo-based tournament ranking for hypotheses
- MCP server for literature access

**Literature Search skill** (clawRxiv, OpenClaw)
- Valyu semantic search API across PubMed+arXiv+bioRxiv+medRxiv
- Natural language -> semantic query (no Boolean construction)
- Full-text retrieval with figures
- $0.0025/query

### Key Observations
- All systems use multi-agent orchestration (LangGraph, DSPy, or custom)
- Literature search is just the foundation; synthesis + gap detection is the value
- Nobody has our specific combination: multi-source RRF fusion + citation graph + MCP
- Perspicacite-AI is closest competitor but uses different ranking (WRRF vs our consensus RRF)
- LitGapFinder's concept graph approach could complement our citation graph

## Learned Fusion Weights Idea (2026-05-11)

Replace hardcoded scoring weights with a tiny learned network:
- Input: [keyword_score, citation_score, venue_score, recency_score, source_count, query_features]
- Output: relevance_score
- Training: LitSearch 597 queries with ground truth
- Model: 3-layer MLP, hidden=16, <10KB
- Query features: length, domain keywords detected, has_title_words

Why novel:
- MoR (EMNLP 2025): score-level fusion with learned weights, needs scores from each retriever
- GRO-RAG: gradient-based source selection, needs LLM gradients
- Ours: rank-level fusion with learned weights, zero training infrastructure needed
- Plus: query-adaptive weights (different queries need different weight balance)

Could compare: static weights vs learned MLP vs per-domain presets

## Reference Implementation Deep Dive (2026-05-11)

### Perspicacite-AI (most relevant competitor)
Architecture: FastMCP + ChromaDB + SciLExAdapter
Key features we lack:
- Knowledge Base management (create/add/search KB) - persistent paper collections
- Full-text retrieval pipeline (PMC JATS XML, arXiv HTML)
- Multiple RAG modes (Basic -> Advanced -> Profound -> Agentic -> Literature Survey)
- generate_report tool (synthesize from KB)
- async design throughout

What we have that they don't:
- RRF multi-source fusion (they use SciLEx which is different)
- Citation graph with PageRank analytics
- discover_field tool
- Priority BFS with velocity weighting

### RE-literature-discovery
Architecture: Agent skills (markdown files + Python scripts)
Key ideas:
- authority-ranking: multi-component scoring with explainable components
- CCF ranking, journal metrics, venue authority resolution
- evidence-grading: calibrate evidence strength independently of venue prestige
- field-ranking-profile: switch weights by field (CS vs bio vs econ)
- Pipeline: search -> venue-resolve -> quality-filter -> rank -> review -> write

Actionable for us:
- field-ranking-profile could inform our learned fusion weights idea
- venue authority resolution is much better than our TOP_VENUES set

### Open Coscientist
Architecture: LangGraph multi-agent + FastMCP
MCP server: PubMed-focused, INDRA for biomedical KG
Not directly useful for our general-purpose tool.

### Awesome-AI-Research landscape
Our position: Infrastructure/Module layer for Literature Discovery
Above us: Agent systems (AI Scientist, AutoResearchClaw, etc.)
Key competitors at our level: PaperQA2, OpenScholar, Perspicacite-AI
Our unique combo: multi-source RRF + citation graph + MCP + zero-LLM-required

## Final Session Status (2026-05-11, 98 commits)

### What Works (verified via actual testing)
- MCP connection: stable, tested search + citations + graph
- Search pipeline: parallel (6.5s), 3 sources concurrent
- FlashRank reranking: swapped to rerank-first pipeline
- KB: add, list, search, auto-save on download and discover_field
- arXiv DOI auto-conversion: 10.48550/arXiv.XXXX -> ArXiv:XXXX
- Source registry: 9 search sources, priority-based dispatch
- PDF download: 8-layer chain, 10+ preprint servers

### What Needs Attention
- MCP server runs OLD code (needs restart to pick up changes)
- S2 citation quality: recency-only for high-cite papers, our sort helps but limited
- Generic query ranking: API-level issue, canonical papers sometimes missing from results
- FlashRank gives all 1.000 scores when candidates are all similar

### Scholar-MCP v0.6 Architecture Summary

```
Agent
  |
  v
[MCP Tools] (13 tools)
  |
  ├── search_papers ──> [Parallel: S2 + OpenAlex + arXiv] ──> RRF fusion ──> FlashRank rerank ──> score_results
  ├── get_paper ──────> [Source registry fallback chain]
  ├── get_citations ──> [Source registry: S2 -> OpenAlex]
  ├── get_references ─> [Source registry: S2 -> OpenAlex]
  ├── recommend_papers > [S2 SPECTER2]
  ├── search_authors ─> [S2]
  ├── build_paper_graph > [Priority BFS + networkx analytics + mermaid]
  ├── discover_field ──> [Survey search + expand + graph + auto-save KB]
  ├── save_papers ────> [JSONL KB, ~/.scholar-mcp/kb/]
  ├── list_saved_papers > [KB search/list]
  ├── download_paper ─> [8-layer chain + auto-save KB]
  ├── read_paper ─────> [download + pypdf extract]
  └── search_openreview > [OpenReview API]
```

### Files Changed This Session
- scholar_mcp/relevance.py: RRF, consensus scoring, velocity scoring, weight rebalance
- scholar_mcp/server.py: parallel search, compact output, source registry, KB integration
- scholar_mcp/graph.py: priority BFS, topic filter, networkx analytics, mermaid
- scholar_mcp/discovery.py: field discovery with auto-KB save
- scholar_mcp/knowledge_base.py: persistent JSONL KB
- scholar_mcp/sources.py: source registry pattern
- scholar_mcp/openalex_client.py: citations, references, ID resolution
- scholar_mcp/s2_client.py: citation sorting, retry hardening
- scholar_mcp/arxiv_client.py: query handling, timeout, retry
- scholar_mcp/pdf_utils.py: preprint servers, Unpaywall, PMC
- scholar_mcp/europepmc_client.py: NEW
- scholar_mcp/dblp_client.py: NEW
- scholar_mcp/inspirehep_client.py: NEW
- tests/test_graph.py: NEW (9 tests)
- README.md: updated for v0.6
