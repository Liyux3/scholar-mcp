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

The repository includes `scripts/launch.sh` for macOS/Linux and
`scripts/launch.cmd` (or `scripts/launch.ps1`) for Windows. They reuse uv when available, otherwise
bootstrap it into an application-specific directory using the official
installer, without editing shell profiles. uv then prepares isolated managed
Python 3.12 and the `rerank` dependencies before starting the server. Normal
server arguments pass through unchanged. `SCHOLAR_PACKAGE` can select a
version or candidate wheel; the default launches the published PyPI package,
not unpublished development code.

These launchers need network access on first use. macOS MCPB bundles include a
relocatable Python runtime and native dependencies, so they do not require an
existing Python or uv installation. Optional Google browser/codec components
remain separate. The portable-runtime CI matrix exercises macOS, Linux and
Windows on x64 and ARM64.

The release wheel index provides hash-pinned native `cryptography` wheels
for Intel macOS and Windows ARM, where current upstream releases do not ship
binaries. CI builds the unmodified upstream source with static OpenSSL and
uploads those wheels before publishing PyPI. The recommended configurations
use this index automatically; users do not need Rust or an OpenSSL
development installation. Other platforms use the upstream PyPI wheels.

Python 3.10 or newer is required. The server supports stdio and Streamable HTTP.
The core profile has six tools; the research extension adds graph and library
operations.

Containers and MCPB bundles include the portable `rerank` extra. Its local
model downloads on first use. A custom container build can also
include query-compression dependencies with
`--build-arg SCHOLAR_EXTRAS=compression,rerank`.
Hosted reranking uses environment configuration and does not require local
model weights.

The optional `google` extra adds browser-based session recovery. It needs
Chrome, Edge, or Chromium and ffmpeg. Recovery is headless by default; a graphical
display is needed only for desktop verification. Auto mode first tries headless;
on macOS, a declined challenge can retry in a hidden, non-activating browser.
Set `SCHOLAR_GOOGLE_RECOVERY=headless` to forbid this retry, or `headed` to
explicitly permit a visible window.
`SCHOLAR_GOOGLE_BROWSER` selects a nonstandard browser executable. Browser
dependencies are not bundled into the default server, container or MCPB.
DrissionPage and SpeechRecognition retain their own licensing terms.

## Release artifacts

GitHub Release workflows build and validate Python packages, publish the
official Registry entry, build Linux AMD64/ARM64 containers and package macOS
Intel/Apple Silicon MCPB bundles. Versioned manifests identify their server
package explicitly.

Runtime credentials belong in environment variables or the client's secure
configuration. Downloaded papers, library databases and research notes stay
outside the package. Container build inputs are limited to the server source
and its package metadata.
