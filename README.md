# scholar-mcp

MCP server for academic paper search, powered by [Semantic Scholar](https://www.semanticscholar.org/) (214M+ papers, 2.49B citations). Built for use with [Claude Code](https://docs.anthropic.com/en/docs/claude-code) and other MCP-compatible clients.

## Features

- **Search** across 214M+ papers with filters (year, venue, field of study, citation count, open access)
- **Paper details** with TLDR summaries, BibTeX, venue metadata
- **Citation graph** traversal (who cites this paper, what does it reference)
- **Recommendations** for similar/related papers
- **Author search** with h-index, affiliations, paper counts
- **PDF download** with smart fallback chain (Semantic Scholar → arXiv → bioRxiv/medRxiv)
- **Full-text extraction** from downloaded PDFs
- **Fallback search** via arXiv and Google Scholar when S2 is unavailable

## Tools

| Tool | Description |
|------|-------------|
| `search_papers` | Search papers with filters. Falls back to arXiv → Google Scholar |
| `get_paper` | Get paper details by S2 ID, DOI, ArXiv ID, or PMID |
| `get_citations` | Get papers that cite a given paper |
| `get_references` | Get papers referenced by a given paper |
| `recommend_papers` | Find similar papers via S2 recommendation engine |
| `search_authors` | Search for researchers by name |
| `download_paper` | Download PDF with multi-source fallback |
| `read_paper` | Download PDF and extract text content |

## Installation

Requires Python 3.10+.

```bash
# Clone and install
git clone https://github.com/Liyux3/scholar-mcp.git
cd scholar-mcp
uv venv && uv pip install -e .

# Or with pip
pip install -e .
```

## Usage with Claude Code

Add to `~/.claude/.mcp.json`:

```json
{
  "scholar": {
    "command": "/path/to/scholar-mcp/.venv/bin/scholar-mcp"
  }
}
```

Or run directly:

```bash
scholar-mcp
```

## Configuration

Environment variables (all optional):

| Variable | Default | Description |
|----------|---------|-------------|
| `S2_API_KEY` | None | Semantic Scholar API key for higher rate limits |
| `SCHOLAR_DOWNLOAD_DIR` | `./downloads` | Directory for downloaded PDFs |
| `S2_TIMEOUT` | `30` | API request timeout in seconds |

### Rate Limits

Without an API key: 100 requests per 5 minutes.
With an API key: ~100 requests per second. Request one at [Semantic Scholar API](https://www.semanticscholar.org/product/api#api-key-form).

## Examples

Search for papers:
```
search_papers("transformer architecture", year="2020-2024", venue="NeurIPS", min_citations=100)
```

Get paper details:
```
get_paper("ArXiv:1706.03762")  # Attention Is All You Need
get_paper("10.1038/nature12373")  # by DOI
```

Download and read:
```
download_paper("ArXiv:1706.03762")
read_paper("ArXiv:1706.03762", max_pages=5)
```

## License

MIT
