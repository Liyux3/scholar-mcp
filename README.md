# scholar-mcp

[![PyPI version](https://badge.fury.io/py/scholar-mcp.svg)](https://pypi.org/project/scholar-mcp)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![MCP](https://img.shields.io/badge/MCP-compatible-green.svg)](https://modelcontextprotocol.io)

Multi-source academic paper search, citation graph exploration, and PDF download as an MCP server. Designed for LLM agents doing research.

Fuses results from 9 academic sources via Reciprocal Rank Fusion, with PDF access across 10+ preprint servers.

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
| `search_papers` | Multi-source search with RRF fusion. Filters: year, venue, field, citations, open access |
| `get_paper` | Paper details by S2 ID, DOI, ArXiv ID, PMID, or OpenAlex ID |
| `get_citations` | Papers citing a given paper (impact-sorted) |
| `get_references` | Papers referenced by a given paper |
| `recommend_papers` | Similar papers via SPECTER2 embeddings |
| `search_authors` | Researchers with h-index, affiliations, paper counts |
| `build_paper_graph` | Citation graph with PageRank analytics and Mermaid visualization |
| `search_openreview` | Conference papers (ICLR, NeurIPS, ICML) |
| `discover_field` | Auto-map a research field: find surveys, foundations, recent trends, build citation graph |
| `save_papers` | Save papers to a persistent collection for later reference |
| `list_saved_papers` | List or search saved paper collections (persists across sessions) |
| `download_paper` | Smart PDF download across 10+ sources |
| `read_paper` | Download + extract text from PDF |

## Search Sources

| Source | Coverage | Strength |
|--------|----------|----------|
| Semantic Scholar | 214M papers | SPECTER2 semantic search |
| OpenAlex | 250M works | Best coverage, impact-ranked citations |
| arXiv | CS/Math/Physics | Preprints |
| PubMed | 36M biomedical | Medicine, biology |
| Europe PMC | Biomedical + EU | PubMed superset |
| Crossref | 150M DOIs | Metadata |
| DBLP | CS bibliography | Conferences, proceedings |
| INSPIRE-HEP | High-energy physics | Particle physics |
| CORE | 250M open access | Institutional repositories |

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
| `CORE_API_KEY` | - | [CORE API key](https://core.ac.uk/services/api) |
| `OPENALEX_EMAIL` | - | Email for Unpaywall + OpenAlex polite pool |
| `SCHOLAR_DOWNLOAD_DIR` | `./downloads` | PDF save directory |
| `SCIHUB_ENABLED` | `false` | Enable Sci-Hub as last-resort source |

## Development

```bash
git clone https://github.com/Liyux3/scholar-mcp.git
cd scholar-mcp
uv venv && uv pip install -e ".[dev]"
uv run pytest tests/
```

35 tests (relevance scoring + graph analytics).

## License

MIT
