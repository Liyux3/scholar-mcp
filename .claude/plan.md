# Scholar-MCP v0.3 Upgrade Plan

## Goal
Make scholar-mcp a black-box tool that works like a smart research assistant: give it any query in any style, get back high-quality ranked results regardless of domain or query format.

## Current State (v0.2)
- Sources: S2 + arXiv (primary), CORE + PubMed + Google Scholar (fallback)
- Relevance: keyword-based scoring with stopwords, title boost, field filtering
- Problems: S2 rate limit (no API key configured), CORE returns garbage, no OpenAlex, no reranker

## How Researchers Actually Search
1. Start broad: "what's known about X?" -> need good recall across sources
2. Find a seed paper -> follow citations/references to map the field
3. Search by specific title/author -> need exact match to work
4. Track a niche frontier -> need recent papers, preprints
5. Cross-domain exploration -> need sources that cover all fields, not just CS
Our tool needs to handle all of these patterns seamlessly.

## Changes (priority order)

### P0: Add OpenAlex as third primary source (~120 lines)
- new file: openalex_client.py
- reference scimesh's OpenAlex provider for API structure, abstract reconstruction (inverted index)
- query S2 + arXiv + OpenAlex in parallel in _collect_primary
- OpenAlex has 250M+ papers, best field-of-study taxonomy, free API key, no aggressive rate limit
- covers the "S2 is down" case much better than CORE fallback
- OpenAlex abstract comes as inverted index, need reconstruction logic

### P1: Add Crossref as better fallback (replace CORE position) (~80 lines)
- new file: crossref_client.py
- 150M works, no API key needed, no rate limit with polite pool (mailto header)
- much more reliable than CORE for metadata quality
- keep CORE as last resort only

### P2: FlashRank reranker (optional, ~40 lines in relevance.py)
- FlashRank: ONNX-based, CPU-only, no torch needed, 4MB smallest model
- pip install flashrank (single dep, no torch/transformers)
- ms-marco-TinyBERT-L-2-v2: 4MB, blazing fast, competitive accuracy
- Use as optional post-step: if flashrank is installed, rerank top-N after keyword scoring
- Graceful degradation: if not installed, falls back to current keyword scoring
- This is the highest-ROI "advanced" method: real semantic reranking for free, no GPU

### P3: Smart merge from scimesh (~20 lines in relevance.py)
- Adopt scimesh's merge strategy: when same paper from multiple sources,
  take longest abstract, most authors, highest citation count, union of topics
- Currently we just dedupe by keeping the first one seen

### P4: S2 API key pool config (~10 lines in config.py)
- Support S2_API_KEYS (comma-separated) for round-robin
- Simple: pick a random key per request
- Solves rate limit without needing multiple accounts (user provides keys)

### P5: Update fallback order
- Primary tier (parallel): S2 + arXiv + OpenAlex
- Fallback tier (sequential, only if primary empty): Crossref -> CORE -> PubMed -> Google Scholar
- All results go through: dedup -> merge -> field filter -> keyword score -> (optional rerank) -> return

## Non-goals for this iteration
- Async (our MCP server is sync, converting is a bigger refactor)
- Query expansion via LLM (adds API dependency, complexity)
- Local embedding search (needs model download, GPU preferred)
- Scopus-style query parser (scimesh has this, but overkill for MCP tool)

## Testing Strategy
- Unit tests for each new client (mock HTTP)
- E2E tests with real APIs (the 12-scenario suite from earlier)
- Verify: previously-failing queries now work
- Verify: reranker improves result ordering when installed

## Estimated time: ~2 hours
- P0 (OpenAlex): 45 min
- P1 (Crossref): 30 min
- P2 (FlashRank): 20 min
- P3 (Smart merge): 10 min
- P4 (API key pool): 5 min
- P5 (Integration + tests): 30 min
