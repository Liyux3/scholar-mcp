# scholar-mcp Architecture

## Overview

MCP server for academic paper search, built on Semantic Scholar's Academic Graph API (214M+ papers). Exposes 8 tools via FastMCP stdio transport, with arXiv, CORE, and Google Scholar as fallbacks.

## Module Map

```
scholar_mcp/
  config.py        — env var config (S2_API_KEY, CORE_API_KEY, DOWNLOAD_DIR, S2_TIMEOUT)
  s2_client.py     — core Semantic Scholar API client (direct httpx, not the PyPI wrapper)
  arxiv_client.py  — arXiv search fallback via Atom feed + feedparser
  core_client.py   — CORE API v3 client (250M+ open access, search + PDF discovery)
  scholar_client.py — Google Scholar HTML scraping fallback (last resort)
  pdf_utils.py     — PDF download chain + text extraction (pypdf)
  server.py        — FastMCP server, 8 tool definitions, fallback orchestration
```

## Data Flow

```
User query → server.py (tool dispatch)
                ↓
        s2_client.py (Semantic Scholar API)
                ↓ fails?
        arxiv_client.py (arXiv Atom feed)
                ↓ fails?
        core_client.py (CORE API v3, institutional repos)
                ↓ fails?
        scholar_client.py (Google Scholar scrape)
                ↓ fails?
        error response
```

PDF download chain (pdf_utils.py):
```
S2 open_access_url → arXiv direct → CORE (by DOI/title) → bioRxiv/medRxiv (DOI 10.1101/*) → fail with links
```

## Tools (8 total)

| Tool | Source | Fallback |
|------|--------|----------|
| search_papers | S2 → arXiv → CORE → Google Scholar | full chain |
| get_paper | S2 only | — |
| get_citations | S2 only | — |
| get_references | S2 only | — |
| recommend_papers | S2 Recommendations API | — |
| search_authors | S2 only | — |
| download_paper | S2 open access → arXiv → CORE → bioRxiv | multi-source |
| read_paper | download_paper + pypdf extract | — |

## Key Design Decisions

1. **Direct httpx over `semanticscholar` PyPI package**: The wrapper hangs on `search_paper()` due to internal async/nest_asyncio issues. Direct API calls with httpx work reliably.

2. **Unified output format**: All sources (S2, arXiv, Google Scholar) return the same dict schema via `format_paper()`. Keys: paper_id, title, authors, abstract, year, venue, citation_count, etc.

3. **Retry with backoff for S2 rate limits**: `_get()` retries up to 4 times with exponential backoff (2s, 4s, 8s, max 30s) on HTTP 429. Free tier: 100 req/5min.

4. **`show_banner=False` on FastMCP**: Required for stdio transport. The rich-formatted startup banner corrupts JSON-RPC protocol on stdout.

5. **Google Scholar as last resort only**: HTML scraping, no official API, risk of CAPTCHA/IP blocking. Random delays (1.5-3s) between requests.

6. **CORE for institutional PDF access** (v0.2.0): 250M+ open access papers from 10,000+ university repositories. Main value is PDF discovery for papers where S2 has no open access link but a preprint/postprint exists in an institutional repo. Quality sorting: results with downloadUrl and higher citations ranked first.

## CORE API Endpoints Used

- `GET /v3/search/works/?q=...&limit=...` — search works, supports DOI and title queries
- Auth: Optional Bearer token via `CORE_API_KEY` env var (free at core.ac.uk)
- Key fields: `downloadUrl`, `sourceFulltextUrls`, `citationCount`, `doi`

## S2 API Endpoints Used

- `GET /graph/v1/paper/search` — search with filters (year, venue, fields, citations, open access)
- `GET /graph/v1/paper/{id}` — full paper details with BibTeX, TLDR, venue metadata
- `GET /graph/v1/paper/{id}/citations` — papers that cite this one
- `GET /graph/v1/paper/{id}/references` — papers this one references
- `GET /recommendations/v1/papers/forpaper/{id}` — similar papers
- `GET /graph/v1/author/search` — author lookup with h-index, affiliations

## Dependencies

- fastmcp (>=2.0.0): MCP server framework
- httpx (>=0.27.0): HTTP client (replaces both requests and semanticscholar package)
- feedparser (>=6.0.0): arXiv Atom feed parsing
- pypdf (>=4.0.0): PDF text extraction
- beautifulsoup4 + lxml: Google Scholar HTML parsing

## Claude Code Integration

Entry point: `scholar_mcp.server:main` → installed as `scholar-mcp` console script.

Registration requires both:
1. `~/.claude/.mcp.json` entry with command path
2. Marketplace entry at `~/.claude/plugins/marketplaces/custom-mcps/scholar/`
   - `.mcp.json` (command config)
   - `.claude-plugin/plugin.json` (name, description, author)
   - Listed in parent `marketplace.json` and `plugin.json`
