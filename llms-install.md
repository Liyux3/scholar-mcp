# Install Scholar MCP

Scholar MCP is a local stdio server distributed through PyPI. It needs Python 3.10+
and `uv`; every data-source key is optional.

Use this MCP configuration:

```json
{
  "mcpServers": {
    "scholar": {
      "command": "uvx",
      "args": ["scholar-mcp"],
      "env": {
        "SCHOLAR_MCP_EXTENSIONS": "research"
      }
    }
  }
}
```

The default profile exposes six compact research primitives. The `research`
extension adds `build_paper_graph` and `paper_library`. Start without credentials;
add source keys only when the user wants higher limits or optional paid channels.

After installation, verify that the server starts and exposes `search_papers`,
`paper_info`, `recommend_papers`, `search_authors`, `read_paper`, and
`download_paper`.
