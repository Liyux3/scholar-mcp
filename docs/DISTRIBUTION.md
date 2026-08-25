# Scholar MCP Distribution

The v0.8 release treats distribution artifacts as one tested product. A GitHub Release
drives the package, registry, container, and MCPB workflows from the same tag.

## Automatic release channels

| Channel | Artifact / entry | Automation | Status before v0.8 release |
|---|---|---|---|
| GitHub Release | Source, notes, attached MCPB bundles | Release is the trigger | Prepared |
| PyPI / uvx | `scholar-mcp==0.8.0` | `publish.yml` | Prepared; public PyPI still 0.2.1 |
| Official MCP Registry | `io.github.liyux3/scholar-mcp` | OIDC publisher after PyPI | Manifest validates; not published |
| GHCR | `ghcr.io/liyux3/scholar-mcp` | Multi-arch Docker build | Prepared |
| MCPB | Darwin arm64 and x86_64 bundles | macOS matrix build + MCP smoke | Prepared and locally smoke-tested |
| Codex marketplace | `.agents/plugins/marketplace.json` | Repository marketplace | Manifest validates |
| Claude marketplace | `.claude-plugin/marketplace.json` | Repository marketplace | Manifest validates |
| Agent Plugins | `plugins/scholar-mcp/plugin.json` | Repository distribution | Manifest present |

The release workflow verifies version parity, runs tests and Ruff, builds the package,
validates distributions, installs the wheel in an isolated environment, performs a real
MCP handshake, publishes PyPI, then publishes the official Registry entry.

## Client install paths

| Client / harness | Install path |
|---|---|
| Codex | Repository plugin marketplace or `codex mcp add scholar -- uvx scholar-mcp` |
| Claude Code | Repository plugin marketplace or `claude mcp add scholar -- uvx scholar-mcp` |
| Claude Desktop | MCPB bundle or stdio JSON configuration |
| VS Code / Copilot | `vscode.dev/redirect/mcp/install` button in README |
| Kiro | `kiro.dev/launch/mcp/add` button in README |
| Cursor / compatible MCP clients | stdio config with `uvx scholar-mcp` |
| OpenCode | Local MCP command `uvx scholar-mcp` |
| Pi | Agent Plugin plus `pi-mcp-adapter` when MCP bridging is required |
| Docker / server hosts | `ghcr.io/liyux3/scholar-mcp:<version>` |
| Python environments | `pip install scholar-mcp` or `uvx scholar-mcp` |

The published container uses the lightweight `rerank` extra. Build with
`--build-arg SCHOLAR_EXTRAS=compression,rerank` only when a large image with the local
KeyBERT/model stack is explicitly desired; DashScope reranking remains available through
`DASHSCOPE_API_KEY` in the lightweight image.

## Directory submissions requiring external accounts

These cannot be completed by repository automation alone. Submit after the public v0.8
artifacts exist and resolve correctly.

| Directory | Submission form / artifact | Required external state |
|---|---|---|
| Smithery | Publish the signed/local MCPB or a public Streamable HTTP URL | Smithery namespace and login |
| Glama | Import the GitHub MCP repository | Glama account / approval |
| PulseMCP | Submit the official Registry or GitHub entry | Directory review |
| MCP.so / mcpservers.org | Submit GitHub and install metadata | Directory review |
| Awesome MCP Servers | Focused pull request with tested install command | Maintainer review |
| Cline and other client marketplaces | Marketplace-specific listing | Maintainer/vendor review |

Do not submit a directory listing that points at PyPI 0.2.1 while advertising v0.8.
Publication order is package first, official Registry second, directory mirrors last.

## Release procedure

1. Confirm version parity across `pyproject.toml`, package `__version__`, `server.json`,
   `manifest.json`, all plugin manifests, and marketplace entries.
2. Run the unit suite, Ruff, official Registry validation, plugin validation, and MCPB
   manifest validation.
3. Build a candidate wheel and run `scripts/smoke_release.py` in isolation.
4. Build the platform MCPB and run `scripts/smoke_mcpb.py` against the packed archive.
5. Tag the reviewed commit and create GitHub Release `v<version>`.
6. Wait for PyPI, official Registry, GHCR, and MCPB workflows to complete.
7. Verify clean installs from PyPI, Registry, container, and MCPB on fresh clients.
8. Submit Smithery and external directories, then publish the launch benchmark/demo.

## P1: access coverage and connector collaboration

P1 follows the release foundation. It expands full-text success without weakening
retrieval quality or enlarging the default tool surface.

1. Add OpenAIRE and Europe PMC as repository PDF fallback providers.
2. Add source-native download providers for HAL, Zenodo, DOAJ, and Europe PMC when a
   record exposes a stable open file.
3. Define one connector contract: normalized identity, query style, capabilities,
   timeout, health result, and deterministic fixtures.
4. Generate the public source capability matrix from connector contract tests.
5. Keep source access behind the existing registry, fan-out budget, canonical merge,
   and reranker.

P1 does not add one MCP tool per provider. It increases the capability of the existing
`search_papers`, `download_paper`, and `read_paper` primitives.
