# Scholar MCP Distribution

The v0.8 release treats distribution artifacts as one tested product. A GitHub Release
drives the package, registry, container, and MCPB workflows from the same tag.

## Automatic release channels

| Channel | Artifact / entry | Automation |
|---|---|---|
| GitHub Release | Source, notes, attached MCPB bundles | Release trigger |
| PyPI / uvx | `scholar-mcp` | `publish.yml` |
| Official MCP Registry | `io.github.liyux3/scholar-mcp` | OIDC publisher after PyPI |
| GHCR | `ghcr.io/liyux3/scholar-mcp` | Multi-architecture Docker build |
| MCPB | Darwin arm64 and x86_64 bundles | macOS matrix build and MCP smoke |
| Codex marketplace | `.agents/plugins/marketplace.json` | Repository marketplace |
| Claude marketplace | `.claude-plugin/marketplace.json` | Repository marketplace |
| Cursor marketplace | `.cursor-plugin/marketplace.json` | Repository import and refresh |
| Agent Plugins | `plugins/scholar-mcp/plugin.json` | Repository distribution |
| Smithery | `smithery.yaml` | Repository build configuration |
| Cline | `llms-install.md` | Agent-readable installation |

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
| Cursor | Agent Plugin / Cursor marketplace, or stdio config with `uvx scholar-mcp` |
| OpenCode | Local MCP command `uvx scholar-mcp` |
| Pi | Agent Plugin plus `pi-mcp-adapter` when MCP bridging is required |
| Docker / server hosts | `ghcr.io/liyux3/scholar-mcp:<version>` |
| Python environments | `pip install scholar-mcp` or `uvx scholar-mcp` |

The published container uses the lightweight `rerank` extra. Build with
`--build-arg SCHOLAR_EXTRAS=compression,rerank` only when a large image with the local
KeyBERT/model stack is explicitly desired; DashScope reranking remains available through
`DASHSCOPE_API_KEY` in the lightweight image.

## Directory submissions requiring external accounts

These channels require account or maintainer review after release artifacts resolve.

| Directory | Submission form / artifact | Required external state |
|---|---|---|
| Cursor Marketplace | Submit the public repository | Cursor publisher approval |
| Claude official marketplace | Submit the public plugin | Anthropic plugin review |
| Cline Marketplace | Submit repository, square logo, and install proof | GitHub issue review |
| Smithery | Import `smithery.yaml` or a public Streamable HTTP URL | Smithery namespace and login |
| Glama | Import the GitHub MCP repository | Glama account / approval |
| PulseMCP | Submit the official Registry or GitHub entry | Directory review |
| MCP.so / mcpservers.org | Submit GitHub and install metadata | Directory review |
| Docker MCP Catalog | Contribute the validated image/server entry | Docker catalog review |
| Windsurf Marketplace | Submit the stdio/HTTP server entry | Vendor review |
| Awesome MCP Servers | Focused pull request with tested install command | Maintainer review |

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

## Access coverage and connector collaboration

The source registry expands full-text success while preserving the compact tool surface.

1. OpenAIRE, Europe PMC, HAL, Zenodo, DOAJ, and CORE expose one registry-level
   `resolve_pdf` capability.
2. Repository resolution runs in parallel under the same wall-clock budget and yields
   stable source-priority candidates to one validated downloader.
3. HAL and OpenAIRE join default discovery; Zenodo remains PDF-only after live probes
   showed higher discovery noise.
4. `scholar-mcp sources` generates the capability matrix directly from the registry.
5. Lexical anchors preserve dataset, benchmark, shared-task, acronym, and proper-name
   signals before keyword APIs receive a compressed query.

Provider capabilities remain behind the existing `search_papers`, `download_paper`,
and `read_paper` primitives.
