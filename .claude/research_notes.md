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
