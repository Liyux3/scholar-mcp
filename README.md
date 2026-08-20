# OpenInquiry MCP

<!-- mcp-name: io.github.liyux3/scholar-mcp -->

[![PyPI](https://img.shields.io/pypi/v/scholar-mcp.svg)](https://pypi.org/project/scholar-mcp)
[![Python](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-yellow.svg)](LICENSE)

OpenInquiry MCP is a federated academic search and evidence workspace distributed as the
`scholar-mcp` package. It searches up to 17 retrieval channels across
15 source families, reranks the merged evidence, traverses citations, and reads open-access papers.
The default interface stays compact: six orthogonal tools plus a Deep Research skill.

## Quick start

```bash
# Claude Code
claude mcp add scholar -- uvx scholar-mcp

# Higher Semantic Scholar rate limits
claude mcp add scholar -e S2_API_KEY=your_key -- uvx scholar-mcp
```

Any stdio MCP client can launch `uvx scholar-mcp`. Python 3.10+ and
[uv](https://docs.astral.sh/uv/) are required; basic search works without API keys.

The repository also ships a plugin with the Deep Research skill:

```bash
# Codex
codex plugin marketplace add Liyux3/scholar-mcp
codex plugin add scholar-mcp@scholar-mcp

# Claude Code
claude plugin marketplace add Liyux3/scholar-mcp
claude plugin install scholar-mcp@scholar-mcp
```

The same plugin directory follows the Agent Plugins standard for Cursor, Pi, and compatible
harnesses. OpenCode can run the MCP directly; Pi uses its MCP adapter:

```bash
# OpenCode: add uvx scholar-mcp as a local MCP in opencode.json
# Pi
pi install npm:pi-mcp-adapter
```

All adapters launch the same six-tool server and discover the same `deep-research` skill. The
portable plugin is `plugins/scholar-mcp`; `server.json` is the MCP Registry distribution manifest.

## Tools

| Tool | Responsibility |
|---|---|
| `search_papers` | Multi-source search, filters, reranking, and citation expansion |
| `paper_info` | Paper metadata, citations, and references |
| `recommend_papers` | Semantic neighbors, co-citation peers, and bibliographic kin |
| `search_authors` | Author profiles, affiliations, paper counts, and h-index |
| `read_paper` | Temporarily fetch a paper, extract text, then clean the PDF |
| `download_paper` | Persist and index a PDF through the open-access fallback chain |

`scholar://status` reports active sources and runtime capabilities as an MCP resource. Set
`SCHOLAR_MCP_EXTENSIONS=knowledge_base` to expose the optional persistent collection tool.

## Retrieval

The source registry covers OpenAlex, Semantic Scholar, arXiv, arxiv.gg, Scopus, Crossref, PubMed,
Europe PMC, DBLP, DOAJ, INSPIRE-HEP, CORE, OpenReview, Google Scholar, and optional Exa retrieval.
Semantic endpoints receive the full question, keyword APIs receive a compressed query, and every
response reports source availability. One slow or throttled source therefore reduces recall while the
rest of the fleet continues.

`paper_info` owns citation and reference traversal. `recommend_papers` adds the three distinct graph
relations: semantic similarity, co-citation, and bibliographic coupling. The bundled skill composes
field maps and multi-hop citation lineages from these primitives, keeping each evidence step visible.

## Paper access

`read_paper` uses a temporary directory and leaves no retained PDF. `download_paper` persists the file
under `~/.scholar-mcp/papers` by default and indexes its metadata in the `downloads` collection. That
collection can later be searched or exported as a linked vault. Both tools share one fallback chain:

1. Semantic Scholar and arXiv open access
2. CORE and PubMed Central
3. bioRxiv, medRxiv, SSRN, ChemRxiv, and other preprint servers
4. Unpaywall and an optional institutional proxy
5. Sci-Hub as an explicit, disabled-by-default local fallback

## Configuration

All credentials are optional and stay in the MCP process environment.

| Variable | Purpose |
|---|---|
| `SCHOLAR_DATA_DIR` | Shared data root; default `~/.scholar-mcp` |
| `S2_API_KEY` | Semantic Scholar rate limits and snippets |
| `OPENALEX_API_KEYS` | Comma-separated OpenAlex keys |
| `SCOPUS_API_KEY` | Scopus search |
| `CORE_API_KEY` | CORE repository search |
| `EXA_API_KEY` | Optional Exa source |
| `OPENREVIEW_USERNAME`, `OPENREVIEW_PASSWORD` | OpenReview search |
| `DASHSCOPE_API_KEY` | Qwen reranker; FlashRank is the local fallback |
| `OPENALEX_EMAIL` | OpenAlex polite pool and Unpaywall |
| `SCHOLAR_SOURCE_BUDGET_S` | Search fan-out budget; default `8` seconds |
| `SCHOLAR_DOWNLOAD_DIR` | Persistent PDF directory; default `<data>/papers` |
| `SCHOLAR_MCP_EXTENSIONS` | Optional tools, currently `knowledge_base` |
| `SCIHUB_ENABLED` | Local opt-in fallback; default `false` |

Errors returned to the model redact request URLs, and release artifacts contain no credentials.

## Development and deployment

```bash
git clone https://github.com/Liyux3/scholar-mcp.git
cd scholar-mcp
uv sync --extra dev
uv run pytest
```

Local and Docker clients use stdio by default. Set `SCHOLAR_MCP_TRANSPORT=http` for Streamable HTTP;
the default endpoint is `/mcp`. Keep hosted credentials in the deployment secret store.

## License

MIT
