# scholar-mcp

[![PyPI version](https://badge.fury.io/py/scholar-mcp.svg)](https://pypi.org/project/scholar-mcp)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![MCP](https://img.shields.io/badge/MCP-compatible-green.svg)](https://modelcontextprotocol.io)

A fused MCP server for academic paper search, cite, download, and read paper contents(gists, full text) online.

Roughly covers ~97% of existing literature/papers via: 

1. [Semantic Scholar](https://www.semanticscholar.org/)
2. arXiv
3. CORE
4. PubMed
5. bioRxiv/medRxiv
6. Google Scholar

## Quick Start

**One-liner for Claude Code:**

```bash
claude mcp add scholar -- uvx scholar-mcp
```

**Or with an API key for higher rate limits:**

```bash
claude mcp add scholar -e S2_API_KEY=your_key -- uvx scholar-mcp
```

**For Claude Desktop**, add to your config:

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

> [!NOTE]
> Requires Python 3.10+ and [uv](https://docs.astral.sh/uv/) installed. No API key needed for basic use (100 requests / 5 minutes free).

## Features

- **Search** across 214M+ papers with filters (year, venue, field of study, citation count, open access)
- **Paper details** with TLDR summaries, BibTeX, venue metadata
- **Citation graph** traversal (who cites this paper, what does it reference)
- **Recommendations** for similar/related papers
- **Author search** with h-index, affiliations, paper counts
- **PDF download** with smart fallback chain (Semantic Scholar -> arXiv -> CORE -> bioRxiv/medRxiv)
- **Full-text extraction** from downloaded PDFs
- **Fallback search** via arXiv, [CORE](https://core.ac.uk/) (250M+ open access papers), [PubMed](https://pubmed.ncbi.nlm.nih.gov/) (36M+ biomedical), and Google Scholar

## Tools

| Tool | Description |
|------|-------------|
| `search_papers` | Search 214M+ papers with year, venue, field, citation filters. Falls back to arXiv, CORE, PubMed, then Google Scholar |
| `get_paper` | Paper details by Semantic Scholar ID, DOI, ArXiv ID (`ArXiv:xxxx`), or PMID (`PMID:xxxx`) |
| `get_citations` | Papers that cite a given paper (up to 1000) |
| `get_references` | Papers referenced by a given paper (up to 1000) |
| `recommend_papers` | Similar/related papers via S2 recommendation engine (up to 500) |
| `search_authors` | Find researchers with h-index, affiliations, paper/citation counts |
| `download_paper` | Download PDF: tries S2 open access, arXiv, CORE, bioRxiv/medRxiv |
| `read_paper` | Download + extract full text from PDF (with optional page limit) |

## Configuration

All configuration is via environment variables (all optional):

| Variable | Default | Description |
|----------|---------|-------------|
| `S2_API_KEY` | — | [Semantic Scholar API key](https://www.semanticscholar.org/product/api#api-key-form) for higher rate limits |
| `CORE_API_KEY` | — | [CORE API key](https://core.ac.uk/services/api) for institutional repository search (free) |
| `SCHOLAR_DOWNLOAD_DIR` | `./downloads` | Directory for downloaded PDFs |
| `S2_TIMEOUT` | `30` | API request timeout in seconds |
| `SCIHUB_ENABLED` | `false` | Enable Sci-Hub as last-resort PDF source (opt-in) |

**Rate limits:** Free tier allows 100 requests per 5 minutes. With an API key: ~100 requests per second.

## Examples

Search with filters:
```python
search_papers("transformer architecture", year="2020-2024", venue="NeurIPS", min_citations=100)
```

Look up specific papers:
```python
get_paper("ArXiv:1706.03762")      # by arXiv ID
get_paper("10.1038/nature12373")   # by DOI
get_paper("PMID:19872477")         # by PubMed ID
```

Explore the citation graph:
```python
get_citations("ArXiv:1706.03762", limit=20)   # who cites this?
get_references("ArXiv:1706.03762", limit=20)  # what does it cite?
recommend_papers("ArXiv:1706.03762")           # find similar work
```

Download and read:
```python
download_paper("ArXiv:1706.03762")
read_paper("ArXiv:1706.03762", max_pages=5)
```

## Development

```bash
git clone https://github.com/Liyux3/scholar-mcp.git
cd scholar-mcp
uv venv && uv pip install -e ".[dev]"
uv run pytest tests/
```

## How It Works

```
Search:     S2 -> arXiv (preprints) -> CORE (institutional) -> PubMed (biomedical) -> Google Scholar (scraping)
Download:   S2 open access -> arXiv -> CORE (by DOI/title) -> bioRxiv/medRxiv -> [Sci-Hub] -> fail
```

`[Sci-Hub]` only active when `SCIHUB_ENABLED=1`.

## License

MIT
