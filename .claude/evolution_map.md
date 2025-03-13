# Academic Paper Search & Research Agent Evolution Map (2024-2026)

## Lineage Tree

```
2024
├── LitSearch (EMNLP, Princeton)          [BENCHMARK: 597 queries for lit search eval]
│   └── showed: dense retrieval >> BM25 for academic search
├── STaRK (NeurIPS D&B, Stanford)         [BENCHMARK: semi-structured retrieval]
├── BEIR/MTEB                             [BENCHMARK: general retrieval]
├── GritLM (2024)                         [MODEL: SOTA dense retriever, 74.8% recall@5 on LitSearch]
├── RRF (Cormack 2009, still standard)    [METHOD: rank fusion]
├── Knowledge Gap Detection (UCD)         [ANALYSIS: citation graph gaps via community detection]
│
2025 H1
├── PaSa (ACL 2025, ByteDance)            [AGENT: first RL-trained paper search agent]
│   ├── created AutoScholarQuery (35k synthetic) + RealScholarQuery (50 real) benchmarks
│   ├── Crawler + Selector dual-agent architecture
│   └── PaSa-7B beats Google+GPT-4o by 37.78% recall@20
│
├── MoR - Mixture of Retrievers (EMNLP 2025) [METHOD: heterogeneous retriever fusion]
│   ├── zero-shot weighted combination of diverse retrievers
│   ├── 0.8B mixture beats 7B GritLM by +3.9%
│   └── pre/post-retrieval signal for weight allocation
│
├── GRO-RAG (OpenReview, Sep 2025)        [METHOD: source selection for multi-source RAG]
│   ├── relevance-redundancy tradeoff formalization
│   └── gradient-aware document reranking
│
├── AIssistant (Sep 2025)                 [SYSTEM: human-AI collaborative scientific workflow]
├── SWARM-SLR (2025)                      [SYSTEM: systematic literature review automation]
│
2025 H2
├── SciRAG (Nov 2025)                     [SYSTEM: citation-aware adaptive RAG for science]
│   ├── sequential/parallel retrieval switching
│   └── outline-guided synthesis
│
2026 Q1
├── PaperScout (Jan 2026, USTC)           [AGENT: improves PaSa with PSPO]
│   ├── POMDP formulation for paper search
│   ├── Search + Expand tools (same as PaSa)
│   ├── PSPO: sequence-level RL (fixes PPO granularity mismatch)
│   └── beats PaSa baselines on RealScholarQuery
│
├── SAGE (Feb 2026, NYU/Yale)             [BENCHMARK: 1200 queries, 4 domains]
│   ├── **KEY FINDING: BM25 >> dense retrieval by ~30% for agents**
│   ├── agents generate keyword-oriented sub-queries
│   └── corpus-level test-time scaling proposal
│
├── DualGraph (Feb 2026, Microsoft)       [SYSTEM: SOTA deep research]
│   ├── Outline Graph + Knowledge Graph co-evolution
│   ├── SBM-based gap discovery
│   ├── beats OpenAI Deep Research (RACE 53.08 with GPT-5)
│   └── self-termination via 6-dimensional scoring
│
├── ResearchPilot (Mar 2026)              [SYSTEM: 4-stage lit synthesis]
├── ResearchTwin (Mar 2026)               [SYSTEM: federated researcher digital twins]
├── AgentIR (Mar 2026)                    [METHOD: reasoning-aware retrieval]
│   └── embed agent reasoning trace with query
│
2026 Q2
├── Paper Circle (Apr 2026)               [SYSTEM: multi-agent discovery + KG analysis]
├── Caesar (May 2026, Cognizant)          [SYSTEM: graph-based creative synthesis]
│   ├── Perceive-Think-Act loop
│   ├── navigational stack + backtracking
│   └── adversarial refinement
│
├── Diversity CCBQP (Apr 2026)            [METHOD: diversity retrieval with Frank-Wolfe]
├── LiRA (AAAI 2026)                      [SYSTEM: multi-agent lit review writing]
```

## Key Inheritance Relationships

```
PaSa (ACL 2025) ──created benchmarks──> PaperScout (Jan 2026) ──improved RL──> ...
                                           └── uses same AutoScholarQuery/RealScholarQuery

GritLM (2024) ──SOTA retriever──> LitSearch eval ──challenged by──> SAGE (BM25 > dense for agents!)

DualGraph ──builds on──> STORM/WebWeaver (outline-centric)
           ──adds──> Knowledge Graph gap detection (from bibliometrics)
           ──uses──> SBM (from network science)

MoR (EMNLP 2025) ──extends──> ensemble retrieval theory
                   ──alternative to──> GRO-RAG (gradient-based source selection)

Caesar ──builds on──> ReAct (2023) + GraphRAG
        ──adds──> adversarial refinement + navigational stack

SAGE ──extends──> BrowseComp, DeepResearchBench
      ──challenges──> LLM retriever dominance (key 2026 finding)
```

## Research Directions Taxonomy

```
Direction A: Better Retrieval
├── Dense models: GritLM, E5, SPECTER2
├── Hybrid: BM25 + dense fusion
├── Heterogeneous fusion: MoR, GRO-RAG
├── Lightweight reranking: FlashRank, MICE, Jina v3
└── **Our angle: multi-API keyword fusion + consensus scoring**

Direction B: Better Exploration Policy
├── Fixed workflow: SPAR, search-then-expand
├── RL-trained: PaSa, PaperScout (PSPO)
├── LLM-driven: DualGraph, Caesar
└── **Potential: could build exploration layer on our retrieval backbone**

Direction C: Better Knowledge Synthesis
├── Linear accumulation: search-then-generate
├── Outline-centric: STORM, WebWeaver
├── Dual-graph: DualGraph (OG+KG co-evolution)
├── Adversarial: Caesar (seek contradictions)
└── **Further work: beyond current scope**

Direction D: Better Evaluation
├── LitSearch: standard for academic search
├── PaSa benchmarks: for agent-based search
├── SAGE: for agent retrieval behavior analysis
├── DeepResearchBench/Gym: for deep research output quality
└── **Potential: new benchmark for multi-source coverage**
```

## What Nobody Has Done (as of May 2026)

1. **Systematic multi-API coverage analysis**: how much does each academic API uniquely cover?
2. **Consensus-based quality estimation from API agreement** (Dawid-Skene for APIs)
3. **Proving that keyword multi-source > single dense retriever for agent workflows** (SAGE showed keyword > dense, but not multi-source > single-source)
4. **MCP-native academic search tool** evaluated on standard benchmarks
5. **PaperScout-style exploration with multi-source backend** (they only use S2)
