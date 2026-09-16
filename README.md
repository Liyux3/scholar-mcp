<!-- mcp-name: io.github.Liyux3/scholar-mcp -->

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/scholar-mcp-logo-dark.svg">
    <source media="(prefers-color-scheme: light)" srcset="docs/assets/scholar-mcp-logo.svg">
    <img alt="Scholar MCP" src="docs/assets/scholar-mcp-logo.svg" width="820">
  </picture>
</p>

<p align="center">
  <a href="https://vscode.dev/redirect/mcp/install?name=scholar-mcp&amp;config=%7B%22type%22%3A%22stdio%22%2C%22command%22%3A%22uvx%22%2C%22args%22%3A%5B%22scholar-mcp%22%5D%7D"><img src="https://img.shields.io/badge/Install_in-VS_Code-53665B.svg?style=flat-square" alt="Install in VS Code"></a>
  <a href="cursor://anysphere.cursor-deeplink/mcp/install?name=scholar&amp;config=eyJzY2hvbGFyIjp7ImNvbW1hbmQiOiJ1dngiLCJhcmdzIjpbInNjaG9sYXItbWNwIl19fQ=="><img src="https://img.shields.io/badge/Add_to-Cursor-6A3A3D.svg?style=flat-square" alt="Add to Cursor"></a>
  <a href="https://kiro.dev/launch/mcp/add?name=scholar-mcp&amp;config=%7B%22command%22%3A%22uvx%22%2C%22args%22%3A%5B%22scholar-mcp%22%5D%2C%22disabled%22%3Afalse%2C%22autoApprove%22%3A%5B%5D%7D"><img src="https://img.shields.io/badge/Add_to-Kiro-8C714C.svg?style=flat-square" alt="Add to Kiro"></a>
  <a href="https://registry.modelcontextprotocol.io/?search=io.github.Liyux3%2Fscholar-mcp"><img src="https://img.shields.io/badge/MCP_Registry-Scholar-3D5946.svg?style=flat-square" alt="MCP Registry"></a>
</p>

<p align="center">
  Find the paper. Follow the evidence. Build the field.
</p>

<p align="center">
  <a href="https://pypi.org/project/scholar-mcp"><img src="https://img.shields.io/pypi/v/scholar-mcp.svg?style=flat-square" alt="PyPI"></a>
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/python-3.10+-53665B.svg?style=flat-square" alt="Python 3.10+"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache_2.0-3D5946.svg?style=flat-square" alt="Apache 2.0"></a>
  <a href="https://modelcontextprotocol.io"><img src="https://img.shields.io/badge/MCP-compatible-8C714C.svg?style=flat-square" alt="MCP compatible"></a>
</p>

Scholar MCP finds papers from natural-language questions, follows citations and related work, and opens the primary text. Save selected papers and notes in a local library that carries your research across sessions.

`Natural-language discovery` · `Related-work discovery` · `Primary evidence` · `Field maps` · `Zotero · Obsidian · Notion connectors`

## Quick demo

![Scholar MCP quick demo](docs/assets/quick-demo.gif)

One continuous agent flow: `search_papers` → `build_paper_graph` → `paper_info` + `read_paper` → `paper_library` → library connectors.

## How it works

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/retrieval-pipeline-dark.svg">
  <source media="(prefers-color-scheme: light)" srcset="docs/assets/retrieval-pipeline.svg">
  <img alt="Scholar MCP retrieval pipeline" src="docs/assets/retrieval-pipeline.svg">
</picture>

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/runtime-architecture-dark.svg">
  <source media="(prefers-color-scheme: light)" srcset="docs/assets/runtime-architecture.svg">
  <img alt="Scholar MCP runtime architecture" src="docs/assets/runtime-architecture.svg">
</picture>

Agents call typed MCP tools over stdio or Streamable HTTP. Scholar returns concise text and structured data, while a persistent SQLite library drives FTS5 search, PDF attachments, JSONL snapshots, and Obsidian, Zotero, and Notion connectors.

## Quick start

Claude Code:

```bash
claude mcp add scholar -- uvx scholar-mcp
```

Claude Desktop or any stdio MCP client:

```json
{
  "mcpServers": {
    "scholar": {
      "command": "uvx",
      "args": ["scholar-mcp"]
    }
  }
}
```

The direct server exposes the compact core profile. Python 3.10+ and [uv](https://docs.astral.sh/uv/) are required. Optional source keys unlock deeper coverage and higher throughput.

The repository also ships a research plugin with citation graphs, a local paper library, and the Deep Research skill:

```bash
# Codex
codex plugin marketplace add Liyux3/scholar-mcp
codex plugin add scholar-mcp@scholar-mcp

# Claude Code
claude plugin marketplace add Liyux3/scholar-mcp
claude plugin install scholar-mcp@scholar-mcp
```

The same plugin directory follows the Agent Plugins standard for Cursor, Pi, and compatible harnesses. OpenCode can launch `uvx scholar-mcp` as a local MCP; Pi can use `pi-mcp-adapter`.

Release artifacts also include the PyPI package, multi-architecture GHCR image, and macOS MCPB bundles. See the complete [distribution matrix](docs/DISTRIBUTION.md).

## Tools

| Profile | Tool | Responsibility |
|---|---|---|
| Core | `search_papers` | Multi-source retrieval, filters, reranking, and citation discovery |
| Core | `paper_info` | Paper detail, citations, and references through one selective call |
| Core | `recommend_papers` | Related work through semantic and citation connections |
| Core | `search_authors` | Author profiles, affiliations, paper counts, and h-index |
| Core | `read_paper` | Read paper text, tables, and selected figures; pages 1-10 by default |
| Core | `download_paper` | Persist a PDF and index it in a collection |
| Research | `build_paper_graph` | Bounded citation graph with PageRank, bridges, nodes, edges, and Mermaid |
| Research | `paper_library` | Collections, FTS search, notes, tags, PDFs, and Markdown vault export |

`scholar://status` reports source availability and the actual reranker used without occupying the tool surface. Tool responses retain concise YAML text and also expose structured MCP data.

The bundled Deep Research skill turns search, paper inspection, graph traversal, and selected library writes into a living field map.

## Retrieval

| Channel | Sources | Query form and role |
|---|---|---|
| Semantic | OpenAlex semantic, arxiv.gg, optional Exa | Full natural-language question |
| Full text | Semantic Scholar snippet search | Matching passages from open-access papers |
| Broad metadata | OpenAlex, Semantic Scholar, Crossref, optional Scopus | Identity, coverage, citations, and filters |
| Preprints and conferences | arXiv, OpenReview | Recent work and conference records |
| Biomedical | PubMed, Europe PMC | Medicine, biology, and full-text repositories |
| Domain and repository | DBLP, INSPIRE-HEP, DOAJ, CORE, OpenAIRE, HAL | CS, physics, open journals, and repositories |
| Web fallback | Google Scholar | HTTP search with optional automatic session recovery |

Keyword APIs receive measured source-specific query budgets. Semantic endpoints keep the original question. Every source contributes independently to one canonical evidence pool.

Results are canonicalized across DOI, arXiv, Semantic Scholar, OpenAlex, PubMed, and OpenReview identities. Duplicate records contribute complementary metadata and independent source evidence instead of appearing several times.

DashScope `qwen3-rerank` is the primary reranker when configured. Install the `rerank` extra for the FlashRank local fallback: `uvx --from 'scholar-mcp[rerank]' scholar-mcp`. Containers and MCPB bundles include this extra; the local model downloads on first use. Search ranks the initial matches, follows connections from the strongest papers, then reranks the combined set.

<details>
<summary>Bring your own reranker</summary>

Set `SCHOLAR_RERANK_URL` to the full endpoint and `SCHOLAR_RERANK_MODEL` to its model name. Add `SCHOLAR_RERANK_API_KEY` if needed. Cloud and self-hosted models use the same Cohere-style contract:

```text
Request:  query, documents, top_n, model
Response: results: [{index, relevance_score}]
```

Scores must be finite and in `[0, 1]`. Raw logits need model-specific normalization in the serving backend. Changing models can change the balance with citation and recency ranking. A custom endpoint replaces DashScope and falls back only to the local model.

</details>

Normal responses focus on papers, with a short warning if availability affected the search. `debug=true` adds `_meta` with source coverage, the actual reranker, per-source yield, latency, provenance, and detailed errors. The base parallel-search budget is 30 seconds, configurable through `SCHOLAR_SOURCE_BUDGET_S`. Google Scholar pagination can extend it up to 120 seconds to retain completed multi-page results.

<details>
<summary>Google Scholar session recovery</summary>

With Chrome and ffmpeg installed, run `uvx --from 'scholar-mcp[google]' scholar-mcp` to enable automatic verification recovery. A short-lived browser establishes the session, then ordinary HTTP handles searches and pagination. It uses a fresh browser profile, never your personal Chrome profile, and online audio recognition rather than a local model.

Sessions are stored privately under `<data>/sessions/` and tied to the configured proxy. A cold search can wait up to 150 seconds for the bounded recovery worker and the subsequent search. Warm sessions use the normal budget. Set `SCHOLAR_GOOGLE_RECOVERY=off` to disable browser recovery. Headless servers need a graphical display for this optional path. Google can still refuse a connection; failed recovery is reported and briefly backed off.

</details>

## Measured retrieval quality

![LitSearch quality comparison](docs/assets/litsearch-quality.svg)

In the frozen matched LitSearch run, Scholar achieved a **10-percentage-point higher top-five query hit rate** and **6 points higher at top twenty** than Exa research-paper search.

| System | R@5 | R@10 | R@20 | MRR |
|---|---:|---:|---:|---:|
| **Scholar** | **0.62** | **0.68** | **0.70** | **0.442** |
| Exa `research paper` | 0.52 | 0.58 | 0.64 | 0.435 |
| BM25 `title + abstract` | 0.46 | 0.46 | 0.56 | 0.335 |

Scholar recovered nine R@5 hits that Exa missed; Exa recovered four that Scholar missed.

<details>
<summary>Benchmark protocol</summary>

R@k here measures the fraction of queries with at least one ground-truth paper in the top k results. MRR averages the reciprocal rank of the first match, with zero for a miss.

The comparison uses the same first 50 LitSearch inline-ACL queries, ground-truth titles, title matcher, and top-20 cutoff. Exa ran with category `research paper`. Scholar used its standard retrieval pipeline with Qwen reranking. BM25 follows the official LitSearch title+abstract implementation: lowercase tokenization, English stopword removal, Porter stemming, and `BM25Okapi` over the 64K-paper corpus. The Scholar/Exa run was collected on 12 May 2026; BM25 was reproduced on 25 August 2026. The frozen summary is in [`docs/benchmarks/litsearch-inline-acl-50.json`](docs/benchmarks/litsearch-inline-acl-50.json), with [raw BM25 results](docs/benchmarks/bm25_title_abstract_inline_acl_50.jsonl) and their [hash manifest](docs/benchmarks/bm25_title_abstract_inline_acl_50.summary.json).

</details>

## Citation graph and paper library

![Real paper-library graph](docs/assets/paper-library-graph.svg)

Rendered from a live local collection, the graph reveals foundations, bridges, and the papers that move a field forward. Stable identities and parallel citation traversal keep the map connected as it grows.

The paper library uses one persistent SQLite authority with WAL transactions and FTS5 search. Existing JSONL collections migrate automatically and remain available as compatibility snapshots. Stable identifiers, notes, tags, PDF paths, connector IDs, and sync revisions stay attached to the same canonical record.

Default data layout:

```text
~/.scholar-mcp/
├── papers/    persistent PDFs
├── kb/
│   ├── library.sqlite3    authority + FTS5 + sync state
│   └── *.jsonl            compatibility snapshots
└── vault/                 Markdown projections and wikilinks
```

### Library connectors

```bash
# No login: write directly into an Obsidian vault
scholar-mcp library export obsidian --collection rag --path /path/to/vault

# Dry-run by default; add --apply for external writes
scholar-mcp library sync zotero --collection rag
scholar-mcp library publish notion --collection rag
```

[Obsidian](https://help.obsidian.md/Files+and+folders/Manage+vaults) is a live Markdown projection. [Zotero](https://www.zotero.org/support/dev/web_api/v3/write_requests) manages bibliographic items, collections, tags, and notes. [Notion](https://developers.notion.com/reference/post-page) receives a one-way reading-list view. External connectors keep their IDs, versions, and content hashes in SQLite, so unchanged papers do not publish twice.

## Paper access

`read_paper` uses a temporary PDF and reads pages 1-10 by default, which usually reaches the conclusion of an AI conference paper. It returns page-aware Markdown, structured tables when their geometry is reliable, and caption selectors for figures and visual table fallbacks. Pass `visual="Figure 3"` or another returned selector to receive one focused page crop alongside its text. Pass `pages="11-20"` to continue into references or appendices. The PDF is removed after extraction. `download_paper` streams into a staging file, atomically publishes a validated PDF, reuses a valid local copy, and indexes its metadata in the selected collection.

The shared resolution chain covers:

1. Native open-access records and canonical archives such as arXiv and Europe PMC
2. Registered repository resolvers: CORE, OpenAIRE, HAL, Zenodo, and DOAJ
3. bioRxiv, medRxiv, SSRN, ChemRxiv, and other preprint servers
4. Unpaywall and an optional institutional proxy

`scholar-mcp sources` prints the registry-derived capability matrix. Add `--check` to test configured search providers, or `--check --source arxiv --query "retrieval augmented generation"` to inspect one. arXiv falls back to its own HTTPS search when its Atom API is unavailable. Zenodo participates in PDF resolution but stays out of default discovery because its broad publication records add more candidate noise than retrieval value.

## Configuration

All credentials are optional and remain in the MCP process environment.

| Variable | Purpose |
|---|---|
| `SCHOLAR_DATA_DIR` | Shared data root; default `~/.scholar-mcp` |
| `SCHOLAR_KB_DIR` | SQLite library and JSONL snapshot directory |
| `SCHOLAR_OBSIDIAN_VAULT` | Obsidian projection root; no authentication required |
| `S2_API_KEY` / `S2_API_KEYS` | Semantic Scholar search, snippets, graph, and rate limits |
| `OPENALEX_API_KEY` / `OPENALEX_API_KEYS` | OpenAlex search, semantic search, and graph calls |
| `OPENALEX_EMAIL` | OpenAlex polite pool and Unpaywall |
| `DASHSCOPE_API_KEY` | Qwen reranker |
| `SCHOLAR_RERANK_URL`, `SCHOLAR_RERANK_MODEL`, `SCHOLAR_RERANK_API_KEY` | Compatible hosted or local reranker; separate credential |
| `SCHOLAR_RERANK_TIMEOUT` | Custom reranker request timeout; default 120 seconds |
| `SCHOLAR_GOOGLE_PROXY` | Dedicated Google Scholar proxy; other sources keep their existing route |
| `SCOPUS_API_KEY` | Optional Scopus metadata source |
| `CORE_API_KEY` | Optional CORE repository source |
| `EXA_API_KEY` | Optional Exa research-paper source |
| `OPENREVIEW_USERNAME`, `OPENREVIEW_PASSWORD` | OpenReview API |
| `SCHOLAR_SOURCE_BUDGET_S` | Per-round source fan-out budget; default 30 seconds |
| `SCHOLAR_DOWNLOAD_DIR` | Persistent PDF directory; default `<data>/papers` |
| `SCHOLAR_MCP_EXTENSIONS` | Use `research` for graph and paper-library tools |
| `ZOTERO_API_KEY`, `ZOTERO_LIBRARY_ID` | Zotero Web API or authorized local API connector |
| `ZOTERO_LIBRARY_TYPE`, `ZOTERO_API_BASE` | Optional Zotero library type and endpoint override |
| `NOTION_API_KEY`, `NOTION_DATA_SOURCE_ID` | Notion one-way publisher |

Errors returned to the model redact request URLs and credentials.

## Development

```bash
git clone https://github.com/Liyux3/scholar-mcp.git
cd scholar-mcp
uv sync --extra dev
uv run pytest
```

Unit tests run by default. Run live API tests separately with `uv run pytest -m integration`.

Connector and feature contributions follow [CONTRIBUTING.md](CONTRIBUTING.md). Report security issues through the private process in [SECURITY.md](SECURITY.md); citation metadata is available in [CITATION.cff](CITATION.cff).

Local and Docker clients use stdio by default. Set `SCHOLAR_MCP_TRANSPORT=http` for Streamable HTTP; the default endpoint is `/mcp`.

## License

Apache License 2.0
