# Architecture

Scholar MCP connects an agent's research question to papers, citation relationships,
full text, and a persistent research library.

## Retrieval

```text
Research question
    ↓
Source-specific query routing
    ↓
Parallel retrieval → shared paper identities and merged metadata
    ↓
First relevance ranking → select expansion seeds
    ↓
Follow references, citations and related work
    ↓
Rerank the combined candidates → apply relevance and metadata ordering
    ↓
Papers with identifiers for reading, graph exploration and saving
```

Keyword-oriented sources receive a compact query; semantic sources receive the
original question. Source adapters return one paper format, so identifiers and
metadata from different providers can be combined before ranking.

The first ranking selects promising seeds. Expansion adds connected papers, and
the final ranking scores the combined candidates together. It does not average
scores from different model passes.

Provider batch limits do not cap the candidate pool. Large pools are scored in
batches, then ranked globally using one provider per pass. Sparse reference
records are resolved through DOI/PMID batches, native identifiers and citation
matching before ranking. Metadata lookups are not counted as extra search votes.
The output limit is applied after complete-pool scoring, composite ranking and
requested filters. arXiv DOI aliases and author-backed redeposit matches keep
one work from occupying multiple result slots; conflicting titles are checked
against native identifier records.

```text
Source records and citation references
    ↓
Normalize DOI, arXiv versions and identifier aliases
    ↓
Match shared identifiers, then compatible exact titles
    ↓
Merge metadata and retain independent source provenance
    ↓
Resolve missing titles and conflicting records only
    ├─ DOI → OpenAlex batches → exact Crossref fallback
    ├─ PMID → Europe PMC batches
    ├─ arXiv → native record
    └─ citation text → validated bibliographic match
    ↓
Rank and follow connections
    ↓
Enrich incomplete CorpusId records when S2 is healthy
    ↓
Merge newly linked identities → filters → final results
```

Identity matching and merging are local work. Each completed source starts its
metadata work while slower sources are still retrieving. DOI and PMID batches
also run concurrently with native arXiv and citation lookups, using shared bounded
workers and coalesced caches. Only unresolved records and fields needed by an
explicit filter trigger this work. S2 snippet enrichment remains low priority.
Year, venue, field, citation, publication-type and open-access filters are applied
to the combined pool before the output limit, including expansion candidates.
Unknown type or access status cannot satisfy an explicit strict filter.

`sort` selects the final ordering: relevance, citation count, or date.
`intent` guides semantic reranking and expansion toward a research purpose such
as foundational work, recent work, methods, surveys, or datasets.
The default is relevance ordering with balanced intent.

## Source and model boundaries

The source registry declares search, paper lookup, citation, reference and PDF
resolution capabilities. An adapter supplies the capabilities its provider
actually supports. Adding a source does not require another MCP tool.

Crossref supplies deposited references; Europe PMC supplies citation relations
and can recover references from its own open-access JATS full text. INSPIRE-HEP
provides native citation traversal and batched reference records. All feed
the same graph traversal as Semantic Scholar and OpenAlex. Cached requests are
namespaced by provider implementation and shared across concurrent callers.

Sources run through shared bounded executors. A failed source is recorded
separately from a successful search with no matches. Each search round has a
configurable 30-second budget; pending work can be cancelled when it expires.

arXiv can fall back from Atom to its own HTTPS search. DBLP completes bounded
same-origin verification redirects. The optional Google recovery worker runs
in a separate process, saves a private route-bound session and exits; normal
searches reuse that session through HTTP. Recovery is headless by default;
only `SCHOLAR_GOOGLE_RECOVERY=headed` permits a visible browser. Missing optional
browser dependencies do not prevent the MCP server from starting. A cold default fan-out allows 150
seconds for this setup. Warm Google searches receive a page-count-aware budget
up to 120 seconds. Explicit caller budgets remain authoritative.

DashScope `qwen3-rerank` is the configured cloud default. A compatible hosted or
local reranker can be selected through `SCHOLAR_RERANK_URL`; FlashRank provides
the portable local fallback when the `rerank` extra is installed. Custom
endpoints use a separate credential and do not silently fall back to the cloud.
`SCHOLAR_RERANK_BATCH_SIZE` adapts their per-request capacity without reducing
the candidate pool. The built-in Qwen adapter also budgets estimated request
tokens; payload-size rejections split the batch on the same model. Scores must
be comparable across batches from that model;
listwise or batch-normalized endpoints require a different adapter contract.
See the [README](../README.md#retrieval) for the request contract.

Reranker responses are validated before candidate scores are updated. Final
relevance ordering also considers citations, source agreement and recency.
These are ranking signals, not guarantees about a paper's quality.

## Reading and keeping papers

`read_paper` resolves a PDF, reads it in temporary storage and cleans up the
temporary file. The default returns pages 1-10 as page-aware Markdown.
An explicit page range reaches later sections, while figure, table and page
selectors support focused visual inspection. Recoverable tables are returned
as structured text.

`download_paper` retains the PDF in the configured papers directory and indexes
it in a library collection unless indexing is disabled. Candidate locations
are resolved and probed with bounded parallel work. The downloader validates a
PDF before atomically moving its staging file into place.

The library uses SQLite for persistent records and full-text search. JSONL
supports migration and snapshots. PDF attachments and Markdown vaults remain
separate files. Obsidian, Zotero and Notion integrations project or synchronize
library records through explicit connector operations.

## MCP surface

The core profile exposes six tools:

| Tool | Research task |
|---|---|
| `search_papers` | Discover papers from a question, topic or remembered idea |
| `paper_info` | Inspect a paper and follow citations or references |
| `recommend_papers` | Explore semantic neighbours, co-cited peers or shared references |
| `search_authors` | Find researchers and their profiles |
| `read_paper` | Read text and inspect figures or tables |
| `download_paper` | Keep a PDF and attach it to a collection |

The `research` extension adds `build_paper_graph` and `paper_library`.
The deep-research skill composes these tools into literature-review workflows.
`scholar://status` exposes diagnostics as a resource.

Normal search responses contain papers and a short warning when coverage or
ranking is degraded. `debug=true` adds per-source status and ranking provenance.
Bibliographic fields remain present independently of debug output.

## Code map

| Module | Responsibility |
|---|---|
| `server.py`, `cli.py` | MCP tools, orchestration and command-line entry points |
| `sources.py`, `*_client.py` | Source contracts, query routing and provider access |
| `relevance.py`, `expansion.py` | Query preparation, identity merge and ranking |
| `metadata.py` | Automatic hydration of sparse relation records |
| `traversal.py`, `graph.py` | Citation relationships and graph construction |
| `pdf_utils.py`, `paper_reader.py` | PDF access, structured reading and visuals |
| `knowledge_base.py`, `library_store.py` | Persistent research records and search |
| `vault.py`, `library_connectors.py` | External library projections and synchronization |
| `config.py`, `cache.py` | Configuration and response caching |

See [Contributing](../CONTRIBUTING.md) for adapter and validation expectations.
