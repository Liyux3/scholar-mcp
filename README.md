# scholar-mcp

[![PyPI version](https://badge.fury.io/py/scholar-mcp.svg)](https://pypi.org/project/scholar-mcp)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![MCP](https://img.shields.io/badge/MCP-compatible-green.svg)](https://modelcontextprotocol.io)

Multi-source academic paper search, citation graph exploration, and PDF download as an MCP server. Designed for LLM agents doing research.

Queries 13 academic sources in parallel, reranks the merged pool with a cross-encoder, then expands it by walking the citation graph of the top results. PDF access spans 10+ preprint servers.

Each source is sent the query format it actually wants: semantic endpoints get the full question, keyword endpoints get a compressed keyphrase query, and Semantic Scholar gets a short one to stay under its length limit. That routing is worth more recall than any ranking change measured so far, see [docs/QUERY_COMPRESSION.md](docs/QUERY_COMPRESSION.md).

## Quick Start

**Claude Code:**

```bash
claude mcp add scholar -- uvx scholar-mcp
```

**With API key (recommended, higher rate limits):**

```bash
claude mcp add scholar -e S2_API_KEY=your_key -- uvx scholar-mcp
```

**Claude Desktop** (add to config):

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

> Requires Python 3.10+ and [uv](https://docs.astral.sh/uv/). No API key needed for basic use.

## Tools

| Tool | Description |
|------|-------------|
| `search_papers` | Multi-source search with citation expansion. Filters: year, venue, field, citations, open access |
| `paper_info` | Paper details, citations, and references. `include` selects which |
| `recommend_papers` | Similar papers via SPECTER2 embeddings |
| `search_authors` | Researchers with h-index, affiliations, paper counts |
| `build_paper_graph` | Citation graph with PageRank analytics and Mermaid visualization |
| `search_openreview` | Conference papers (ICLR, NeurIPS, ICML) |
| `discover_field` | Auto-map a research field: find surveys, foundations, recent trends, build citation graph |
| `knowledge_base` | Save, list, and search persistent paper collections |
| `read_paper` | Download a PDF across 10+ sources and extract its text |
| `scholar_status` | Version, active sources, saved collections |

## Search Sources

| Source | Coverage | Strength |
|--------|----------|----------|
| OpenAlex | 250M works | Best coverage, impact-ranked citations |
| OpenAlex semantic | 250M works | Embedding search, carries most ground-truth hits |
| Semantic Scholar | 214M papers | Citations, references, SPECTER2 recommendations |
| Scopus | 100M+ items | Curated metadata, citation-sorted (needs key) |
| arXiv | CS/Math/Physics | Preprints |
| arxiv.gg | 644K arXiv papers | Embedding search over preprints |
| Crossref | 150M DOIs | Metadata, strong on exact titles |
| PubMed | 36M biomedical | Medicine, biology |
| Europe PMC | Biomedical + EU | PubMed superset |
| DBLP | CS bibliography | Conferences, proceedings |
| DOAJ | 9M articles | Open-access journals |
| INSPIRE-HEP | High-energy physics | Particle physics |
| CORE | 250M open access | Institutional repositories (needs key) |
| Exa | Web-wide | Neural search (needs key, off by default) |

A failing source degrades recall rather than failing the request. Every search returns per-source status, latency, and error, so a blocked or throttled source is visible instead of looking like a query with no matches.

## PDF Download Chain

1. Semantic Scholar open access
2. arXiv direct
3. CORE (institutional repositories)
4. Preprint servers: bioRxiv, medRxiv, SSRN, ChemRxiv, PsyArXiv, EarthArXiv, SocArXiv, engrXiv, AgriXiv, SportRxiv, Preprints.org
5. Unpaywall (legal OA discovery)
6. PubMed Central
7. Sci-Hub (opt-in via `SCIHUB_ENABLED=1`)

## Citation Graph

`build_paper_graph` builds an interactive citation network:

- BFS expansion with velocity-weighted priority (new + influential papers first)
- PageRank and betweenness centrality via networkx
- Pivot/bridge paper detection
- Topic filtering to keep graph focused
- Mermaid output with color-coded nodes (seed, high-cite, bridge)

```
build_paper_graph("Attention Is All You Need", max_hops=2, max_papers=20, topic_filter="attention transformer")
```

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `S2_API_KEY` | - | [Semantic Scholar API key](https://www.semanticscholar.org/product/api#api-key-form) (1 req/s) |
| `OPENALEX_API_KEYS` | - | Comma-separated OpenAlex keys, rotated per request |
| `SCOPUS_API_KEY` | - | [Elsevier developer key](https://dev.elsevier.com/) |
| `DASHSCOPE_API_KEY` | - | Primary reranker (qwen3-rerank); falls back to FlashRank if unset |
| `EXA_API_KEY` | - | Enables the Exa source, off without it |
| `CORE_API_KEY` | - | [CORE API key](https://core.ac.uk/services/api) |
| `OPENALEX_EMAIL` | - | Email for Unpaywall + OpenAlex polite pool |
| `SCHOLAR_SOURCE_BUDGET_S` | `8` | Wall-clock cap on the source fan-out |
| `SCHOLAR_DOWNLOAD_DIR` | `./downloads` | PDF save directory |
| `SCIHUB_ENABLED` | `false` | Enable Sci-Hub as last-resort source |

## Development

```bash
git clone https://github.com/Liyux3/scholar-mcp.git
cd scholar-mcp
uv venv && uv pip install -e ".[dev]"

uv run pytest                    # 115 unit tests, ~8s, no network
uv run pytest -m integration     # 37 tests against live APIs
uv run python scripts/smoke_test.py   # manual end-to-end check
```

Unit tests are the default because integration tests are slow, rate-limited, and fail for reasons unrelated to the code. Anything calling a live API must be marked `@pytest.mark.integration`.

Evaluation harnesses live in `eval/`. `compare_compression.py` A/B tests query strategies against LitSearch ground truth, and `citation_boost_sweep.py` re-ranks cached results offline so ranking changes can be checked in seconds without spending API calls.

## License

MIT
