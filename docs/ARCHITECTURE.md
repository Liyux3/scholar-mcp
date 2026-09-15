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

`sort` selects the final ordering: relevance, citation count, or date.
`intent` guides semantic reranking and expansion toward a research purpose such
as foundational work, recent work, methods, surveys, or datasets.
The default is relevance ordering with balanced intent.

## Source and model boundaries

The source registry declares search, paper lookup, citation, reference and PDF
resolution capabilities. An adapter supplies the capabilities its provider
actually supports. Adding a source does not require another MCP tool.

Sources run through shared bounded executors. A failed source is recorded
separately from a successful search with no matches. Each search round has a
configurable 30-second budget; pending work can be cancelled when it expires.

DashScope `qwen3-rerank` is the configured cloud default. A compatible hosted or
local reranker can be selected through `SCHOLAR_RERANK_URL`; FlashRank provides
the portable local fallback. Custom endpoints use a separate credential and do
not silently fall back to the cloud. See the [README](../README.md#retrieval) for
the request contract.

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
| `traversal.py`, `graph.py` | Citation relationships and graph construction |
| `pdf_utils.py`, `paper_reader.py` | PDF access, structured reading and visuals |
| `knowledge_base.py`, `library_store.py` | Persistent research records and search |
| `vault.py`, `library_connectors.py` | External library projections and synchronization |
| `config.py`, `cache.py` | Configuration and response caching |

See [Contributing](../CONTRIBUTING.md) for adapter and validation expectations.
