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
