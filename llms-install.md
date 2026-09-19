# Install Scholar MCP

Scholar MCP is a local stdio server distributed through PyPI. Install `uv`;
the configuration below prepares Python 3.12 and the CPU reranker. Source keys
are optional. Desktop MCPB bundles include their own Python runtime.

Use this MCP configuration:

```json
{
  "mcpServers": {
    "scholar": {
      "command": "uvx",
      "args": ["--python", "3.12", "--constraints", "https://github.com/Liyux3/scholar-mcp/releases/download/v0.8.5/runtime-constraints.txt", "--from", "scholar-mcp[rerank]==0.8.5", "scholar-mcp"],
      "env": {
        "SCHOLAR_MCP_EXTENSIONS": "research"
      }
    }
  }
}
```

This configuration exposes eight tools: the six core tools plus
`build_paper_graph` and `paper_library`. Remove `SCHOLAR_MCP_EXTENSIONS` for
the compact six-tool profile. Start without credentials;
add source keys only when the user wants higher limits or optional paid channels.

After installation, verify that the server starts and exposes `search_papers`,
`paper_info`, `recommend_papers`, `search_authors`, `read_paper`, and
`download_paper`, plus the two research tools when the extension is enabled.
