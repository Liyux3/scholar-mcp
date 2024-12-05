# scholar-mcp Progress Log

## Session 1 (2026-03-15): Design + Full Implementation

### Motivation
- Existing `paper-search-mcp` (v0.1.3 by OpenAGS) had 13 fragmented tools, bugs, and falsely claimed Semantic Scholar support
- Coverage gap: paper-search only covers ~38M papers (arXiv ~2.5M, PubMed ~36M, bioRxiv/medRxiv ~400K, Google Scholar scraping)
- Semantic Scholar covers 214M+ papers, 2.49B citations, 79M authors, especially strong for CS/EECS conference papers
- Decision: build a unified replacement rather than patching the existing tool

### What Was Built
- Complete MCP server with 8 tools (down from 13 fragmented ones)
- S2 as primary search with arXiv + Google Scholar fallbacks
- Smart PDF download chain: S2 open access → arXiv → bioRxiv/medRxiv
- All tools end-to-end tested with real API calls

### Key Technical Decisions & Bugs
1. **semanticscholar PyPI package hangs**: `search_paper()` deadlocks due to internal async/nest_asyncio. Rewrote s2_client.py to use direct httpx calls. The wrapper package was removed from dependencies entirely.
2. **429 rate limiting**: Hit during rapid testing. Added exponential backoff retry (2/4/8/30s) in `_get()`.
3. **requests vs httpx**: scholar_client.py (Google Scholar scraper) originally used `requests` from the forked code, but our deps only include httpx. Replaced throughout.

### Tests Performed
- search_papers: "attention is all you need" → correct results from S2 with 169K+ citations
- get_paper: by S2 ID, DOI, ArXiv:ID all working
- get_citations / get_references: verified graph traversal
- recommend_papers: returns related papers
- search_authors: finds researchers with h-index, affiliations
- download_paper: successfully downloaded "Attention Is All You Need" from arXiv
- read_paper: PDF text extraction verified
- Fallback chain: forced S2 failure → Google Scholar results returned seamlessly

---

## Session 2 (2026-03-15 ~ 03-16): MCP Registration Debugging

### Problem
Server works perfectly in manual testing (JSON-RPC handshake, all tools), but Claude Code silently ignores it. No connection attempt, no logs, no errors.

### Debug Timeline

1. **FastMCP banner corrupting stdout**: FastMCP v3.1.1 prints a rich-formatted box to stdout on startup, breaking JSON-RPC protocol. Fixed with `mcp.run(transport="stdio", show_banner=False)`.

2. **Tried multiple .mcp.json command formats**:
   - Direct python: `"/path/.venv/bin/python" ["-m", "scholar_mcp.server"]`
   - `uv run --directory`: `"uv" ["run", "--directory", "/path", "scholar-mcp"]`
   - Full path uv: `"/Users/liyux/.local/bin/uv" [...]`
   - `uvx --from`: `"uvx" ["--from", "/path", "scholar-mcp"]`
   - All handshake correctly in manual testing. None worked in Claude Code.

3. **Cache investigation**: Discovered `~/.claude/plugins/cache/custom-mcps/` has entries for working MCPs (zotero, exa, google, paper-search) but not scholar. Created manual cache entry. Still didn't work.

4. **Log investigation**: Found MCP logs at `~/Library/Caches/claude-cli-nodejs/{project}/mcp-logs-*`. Scholar had NO log directory at all, meaning Claude Code never even attempted connection. Paper-search had logs showing `ModuleNotFoundError` (environment mismatch).

5. **claude-code-guide agent consulted**: Suggested using entry point directly (`/path/.venv/bin/scholar-mcp`) instead of `-m` module path. Updated config. Still didn't work.

6. **Root cause found**: User pointed out the MARKETPLACE directory (`~/.claude/plugins/marketplaces/custom-mcps/`), not the cache. This is where Claude Code discovers custom MCPs. Each MCP needs:
   - `{name}/.claude-plugin/plugin.json` (metadata)
   - `{name}/.mcp.json` (command config)
   - Listed in parent `marketplace.json` plugins array
   - Listed in parent `plugin.json` mcpServers array

### Resolution
Created proper marketplace entry for scholar with all required files. Restarted Claude Code. All 8 tools appeared and work correctly.

### Key Lesson
`.mcp.json` tells Claude Code HOW to connect, but the marketplace directory tells it WHAT EXISTS. Without marketplace registration, entries in `.mcp.json` are silently ignored. No documentation or skill covered this, it was found through comparing directory structures of working vs non-working MCPs.

---

## Status

### Working
- All 8 tools functional via Claude Code
- S2 API (free tier, 100 req/5min)
- Fallback chain (S2 → arXiv → Google Scholar)
- PDF download + text extraction

### TODO
- [ ] Clean README for GitHub open source
- [ ] Request S2 API key for higher rate limits (1 req/sec → 100 req/sec)
- [ ] Add unit tests (currently empty tests/ directory)
- [ ] Consider publishing to PyPI for easier installation
- [ ] Remove paper-search-mcp from .mcp.json (superseded)
