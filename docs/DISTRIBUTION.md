# Distribution

Scholar MCP uses the same Python server across MCP clients. Choose a package,
container or plugin install to fit your harness.

## Installation channels

| Channel | Entry |
|---|---|
| PyPI / uvx | `scholar-mcp` |
| Official MCP Registry | `io.github.Liyux3/scholar-mcp` |
| Container | `ghcr.io/liyux3/scholar-mcp:<version>` |
| Claude Desktop | MCPB bundles attached to GitHub Releases |
| Codex, Claude Code, Cursor | Repository plugin and marketplace manifests |
| Other MCP clients | Launch `uvx scholar-mcp` over stdio |

See the [README](../README.md#quick-start) for client configuration and
[llms-install.md](../llms-install.md) for agent-readable installation instructions.
A repository manifest provides an install path; it is not an assertion of
acceptance into a vendor-curated marketplace.

## Package and runtime

Python 3.10 or newer is required. The server supports stdio and Streamable HTTP.
The core profile has six tools; the research extension adds graph and library
operations.

The container includes the portable `rerank` extra. A custom build can also
include query-compression dependencies with
`--build-arg SCHOLAR_EXTRAS=compression,rerank`.
Hosted reranking uses environment configuration and does not require local
model weights.

## Release artifacts

GitHub Release workflows build and validate Python packages, publish the
official Registry entry, build Linux AMD64/ARM64 containers and package macOS
Intel/Apple Silicon MCPB bundles. Versioned manifests identify their server
package explicitly.

Runtime credentials belong in environment variables or the client's secure
configuration. Downloaded papers, library databases and research notes stay
outside the package. Container build inputs are limited to the server source
and its package metadata.
