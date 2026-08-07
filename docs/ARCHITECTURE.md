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
  expansion.py    — expansion channels, one function each, registered in a table
  traversal.py    — co-citation and bibliographic coupling
  graph.py        — citation graph construction, PageRank, pivot detection
  discovery.py    — field-landscape assembly for discover_field
  knowledge_base.py — JSONL persistence at ~/.scholar-mcp/kb/
  vault.py        — Obsidian export of saved papers
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
  expansion                — channels run over the top 3 results,
                             then a second rerank pass
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

## Expansion

Keyword search can only return papers whose text matches the query, but a
fifth of the ground truth in the cached benchmark run (9 of 42 papers) is
reachable only one hop away from a good result. `expansion.py` takes the top
three results as seeds and pulls in their neighbours.

Each channel is a function of `(seed, context)` registered in a table, not a
closure inside the pipeline. That is a testability decision with a specific
history: an evaluation harness that reconstructed the pipeline around the
closures omitted the reranking step, leaving seeds ordered by citation count,
and reported that four channels contributed nothing. A harness has to call the
same code that runs in production.

| Channel | Reaches | Cost per seed |
|---------|---------|---------------|
| references | what the seed cites, towards foundations | 2 requests |
| citations | what cites the seed, towards descendants | 2 requests |
| title_search | a full re-search from the seed's title | one fan-out |
| recommendations | SPECTER2 embedding neighbours | 1 request |
| frequent_terms | terms common to all seeds, run once globally | one fan-out |

`peers` and `kin` (co-citation and bibliographic coupling) are registered in
`OPTIONAL_CHANNELS` and off by default. They are the only relations that cross
field boundaries, since shared references imply shared method even when the
vocabulary differs, but each costs seconds and a batch of metered OpenAlex
requests. They are available on demand through `recommend_papers`.

Papers scored in both reranking passes have the two blended, weighted by
`PASS_BLEND`. Papers that arrive during expansion are scored once and keep
that score. Blending a missing first-pass score as zero, which is what an
earlier version did, applied a flat 20% penalty to every expanded paper for no
reason other than arriving late.

## Citations

Two sources answer citation queries, and they differ in a way that matters:

- Semantic Scholar orders citations by recency. For a heavily cited paper the
  first page is whatever cited it most recently, typically low-citation
  preprints.
- OpenAlex sorts by `cited_by_count`, so it returns the impactful citing
  work, but it cannot resolve arXiv identifiers: it does not index 10.48550
  DOIs and offers no arXiv-id filter.

The consequence, before this was fixed, was that any arXiv paper got
citations from S2 alone and its graph consisted entirely of very recent,
barely-cited papers. `parallel_citations` therefore takes an optional title,
which OpenAlex uses to resolve a work id via `title.search`. The match is
verified by normalised title equality, because `title.search` is fuzzy enough
to return a different paper (querying the BERT paper returns "FAD-BERT:
Improved prediction of FAD binding").

Callers that hold the paper dict should pass its title. Those that only have
an id will still work, but lose OpenAlex for arXiv papers.

Resolution tries the id, then its DOI, then the published DOI behind an arXiv
identity, then the title. The third route exists because OpenAlex indexes
neither arXiv DOIs nor arXiv ids, and its title index does not contain every
paper: searching it for BERT returns only a Japanese paper-review article that
quotes the title inside its own, which the normalised-title check rejects. So
a conference paper addressed by arXiv id had no route in at all, and every
relation in `recommend_papers` returned nothing for it. Semantic Scholar knows
both identities and bridges them, but it 404s on the arXiv DOI form and needs
`ArXiv:<id>`, which is why the identifier is rewritten rather than passed
through.

## OpenAlex record hazards

Two failure modes in OpenAlex's data affect anything that walks the graph, and
both are silent.

Some work ids that still appear inside other works' `referenced_works` arrays
are no longer served: they 404 on direct fetch, return no redirect, and are
simply omitted from batched `openalex_id:` filters. They are not obscure
papers. The two strongest co-citation edges for BERT are "Attention Is All You
Need" and ELMo, and both are dead ids, so the relation was discarding exactly
what it exists to surface. Ids minted during the MAG import keep the MAG
identifier as their numeric part, so `W2xxxxxxxxx` can be recovered through
Semantic Scholar as `MAG:xxxxxxxxx`. Measured over four seeds, 8 of 21 dead
edges come back. Ids allocated later by OpenAlex (W6 and above) have no MAG
counterpart and are unrecoverable.

Separately, OpenAlex holds several work records per paper, so a paper's
co-occurrence count is split across them: VGG appeared twice among ResNet's
peers at 32 and 20 votes, and neither figure was the real edge weight.
`traversal._materialise` merges by normalised title and sums the strength,
which both removes the duplicate and restores the count to 52.

A third hazard is deliberately not handled, and the reason is worth stating.
Some Works are overmerged: OpenAlex joins unrelated source records into one
Work and then selects each field from a different part of the cluster, so the
title and DOI describe one paper while the authors, year and citation count
belong to another. W2965373594 carries a LIPIcs title and DOI over RoBERTa's
author list and citation count.

There is no detector for this with an acceptable false-positive rate.
Comparing the title against its registration record catches the shape above,
but a 500-Work sample of Crossref-registered DOIs found zero mismatches while
a census of DataCite-only Works found 20 in 614 (3.3%), so it only addresses
one stratum. More importantly it cannot catch the worse case: W3038568908 has
a coherent title, DOI, authors and locations while reporting 801,217 citations
against Crossref's 9. Contaminated citation edges are invisible to any check
on descriptive fields.

The defence is therefore cross-source agreement rather than filtering. A paper
that several sources return is not distorted by one bad record, and
`rank_final` weights citations weakly (α=0.05, so 100k citations buys 23%)
precisely because the count cannot be trusted at face value.

Two related corrections to earlier readings of this data. "Attention Is All
You Need" showing 2025 and 6,598 citations is not a stale duplicate record: it
is the real Work, contaminated by eight unrelated posted-content DOIs attached
as locations. And GPT-3's low count is not preprint/proceedings splitting, as
its arXiv and NeurIPS versions are already locations on a single Work. See
`docs/OPENALEX_DATA_QUALITY.md` for the measurements.

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

## Latency

`parallel_search` enforces a wall-clock budget (`SOURCE_BUDGET_S`, default 8s,
env `SCHOLAR_SOURCE_BUDGET_S`). Sources still running when it expires are
reported as timed out and dropped. Without it the slowest source sets the
latency for all thirteen.

The dominant cost is the reranker, and it depends heavily on which one runs.
DashScope handles 300 documents in about 1.8s; the FlashRank fallback takes
12-17s for 100-300 documents, and the pipeline reranks twice per search
(before and after expansion). A search is roughly 15s on DashScope and 45-50s
on FlashRank, so an unavailable DashScope key is a latency problem as much as
a quality one.

Expansion issues most of the HTTP traffic: about 67 of the ~80 requests per
search, of which `title_search` alone is 39 (a full fan-out for each of the
top three papers). At that volume several APIs start returning 429. Reducing
it is open work and needs recall measurements first, since the channels
overlap.

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
