# scholar-mcp Architecture

MCP server for academic literature search. Queries 13 sources in parallel,
fuses and reranks the union, and optionally expands the pool by walking the
citation graph of the top results.

## Module map

```
scholar_mcp/
  server.py       — FastMCP server, 10 tools, the shared _pipeline
  sources.py      — source registry, parallel dispatch, per-source query routing
  relevance.py    — query compression, dedup, rerank, final ranking
  graph.py        — citation graph construction, PageRank, pivot detection
  discovery.py    — field-landscape assembly for discover_field
  knowledge_base.py — JSONL persistence at ~/.scholar-mcp/kb/
  cache.py        — response cache, 5 min TTL
  pdf_utils.py    — download chain across preprint servers, pypdf extraction
  config.py       — env var configuration
  *_client.py     — one module per API, all returning the same paper dict
```

Adding a source means writing a `*_client.py` with a `search_papers` function
and calling `sources.register()`. Nothing else changes.

## Search pipeline

`server._pipeline` runs every search-shaped tool:

```
query
  ├─ optimize_query        -> ~6 keyword query   (keyword sources)
  ├─ optimize_query_short  -> ~8 keyword query   (Semantic Scholar)
  └─ raw query                                   (semantic sources)
        ↓
  sources.parallel_search  — 12 threads, one per available source
        ↓
  deduplicate              — DOI, then normalized title; merges metadata
        ↓
  rerank                   — DashScope qwen3-rerank, FlashRank fallback
        ↓
  rank_final               — rerank score adjusted by citations, source
                             agreement, recency
        ↓
  citation expansion       — refs + cites + recommendations of top 10,
                             then a second rerank pass with EMA smoothing
        ↓
  truncate to limit
```

Sources are queried in parallel rather than as a fallback chain. An
individual source failing degrades recall slightly; it does not fail the
request. `SourceResult` records status, latency, and error per source, and
those reports are returned alongside results.

### Query routing

The three query forms exist because keyword APIs and semantic APIs want
opposite things. See `docs/QUERY_COMPRESSION.md` for the measurements behind
the ~6 word target and the per-source optima.

| Route | Sources |
|-------|---------|
| raw | openalex_semantic, arxivgg_semantic, exa |
| short (~8 words) | semantic_scholar |
| compressed (~6 words) | openalex, arxiv, crossref, scopus, doaj, pubmed, europepmc, dblp, inspirehep, core, google_scholar |

### Ranking

`rank_final` multiplies the reranker score by three metadata factors:

```
score = rerank^γ × (1 + α·log(citations+1)) × (1 + β·source_count/N) × (1 + δ·recency)
```

Defaults γ=1.0, α=0.05, β=0.02, δ=0.10, overridable via
`~/.scholar-mcp/rank_params.json`. The citation term is deliberately weak:
at α=0.05 a 100-citation paper gains roughly 23%, which is enough to break
ties but was measured as enough to displace low-citation ground truth when
set higher.

## Sources

Priority determines ordering in `all_sources()`, not fallback sequence.

| Source | Priority | Route | Notes |
|--------|----------|-------|-------|
| semantic_scholar | 90 | short | 1 req/s with key; value is citations and recommendations more than search |
| exa | 85 | raw | needs EXA_API_KEY, disabled by default |
| openalex | 80 | compressed | primary coverage source |
| openalex_semantic | 75 | raw | carries a large share of ground-truth hits |
| scopus | 75 | compressed | needs SCOPUS_API_KEY; free tier caps pages at 25 |
| arxiv | 70 | compressed | 3s between calls, official limit |
| arxivgg_semantic | 65 | raw | 644K arXiv papers with embeddings, no auth |
| pubmed | 40 | compressed | biomedical |
| europepmc | 35 | compressed | biomedical, European repositories |
| crossref | 30 | compressed | metadata matching, strong on exact titles |
| dblp | 25 | compressed | CS bibliography, intermittent 500s |
| doaj | 20 | compressed | open-access journals only |
| inspirehep | 15 | compressed | physics |
| core | 10 | compressed | needs CORE_API_KEY |
| google_scholar | 5 | compressed | HTML scraping, last resort |

## Tools

| Tool | Purpose |
|------|---------|
| search_papers | Multi-source search with expansion, the main entry point |
| paper_info | Paper detail, citations, references; `include` selects which |
| recommend_papers | S2 SPECTER2 similarity |
| search_authors | Author lookup with h-index |
| build_paper_graph | Citation graph with PageRank and pivot detection |
| discover_field | Surveys, foundations, and recent work for a topic |
| knowledge_base | Save, list, and search the persistent JSONL store |
| read_paper | Download PDF and extract text |
| search_openreview | Conference submissions and reviews |
| scholar_status | Version, active sources, KB collections |

## Error handling

Clients raise on HTTP errors. `sources._timed_call` catches them and records
status and message on the `SourceResult`, so a broken source shows up in the
per-source report instead of silently returning nothing.

Catching an exception and returning `[]` inside a client is a bug. It makes
an outage indistinguishable from a genuine empty result, which is how a
completely broken Scopus client went unnoticed for two months, and how DBLP
throttling read as sparse CS coverage. Sources that fail intermittently are
not an exception to this: intermittent failure is precisely what needs to be
visible in the report.

## Rate limits

| API | Limit |
|-----|-------|
| arXiv | 3s between requests |
| Semantic Scholar | 1 req/s with key |
| OpenAlex | $1/day per key, rotates across `OPENALEX_API_KEYS` |
| Scopus | 6 req/s, 20K/week, 25 results per page |
| Exa | 10 QPS, 1000 free/month |
| DOAJ | 2 req/s |

## Integration

Entry point `scholar_mcp.server:main`, installed as the `scholar-mcp`
console script. Registration needs an entry in `~/.claude/.mcp.json` and a
marketplace entry under `~/.claude/plugins/marketplaces/custom-mcps/scholar/`.

Code changes require restarting the MCP server; a stale process will keep
serving the old module.
