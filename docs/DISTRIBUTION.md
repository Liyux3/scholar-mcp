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

## MCP clients

Scholar exposes local stdio and Streamable HTTP. The demo shows clients with
documented MCP support, not a claim of marketplace approval or an end-to-end
test on every client. Use the quick-start server command with your client's
configuration format. Remote clients need a reachable, appropriately protected
HTTP deployment; a cloud agent cannot use your laptop's localhost address.

| Client | MCP integration |
|---|---|
| Codex, Claude Code, Claude Desktop, Cursor | Plugin/configuration and desktop bundle paths above |
| [Antigravity](https://antigravity.google/docs/mcp) | Custom MCP configuration; stdio or HTTP |
| [Grok Bot](https://docs.x.ai/grok-bot/teams-and-enterprises) | Bot-computer/plugin access subject to Cursor MCP and team policy |
| [Grok Build](https://docs.x.ai/build/features/mcp-servers) | Local command or HTTP through `grok mcp` |
| [DeepSeek Harness](https://deepseek-harness.github.io/deepseek-harness/en/guide/mcp-memory) | Generic MCP client plugin/overlay; developer preview |
| [Kimi Code](https://moonshotai.github.io/kimi-cli/en/reference/kimi-mcp.html) | MCP configuration through `kimi mcp` |
| [Gemini CLI](https://geminicli.com/docs/tools/mcp-server/) | MCP server configuration or `gemini mcp` |
| [VS Code](https://code.visualstudio.com/docs/agent-customization/mcp-servers) | Agent-mode MCP servers |
| [Cline](https://docs.cline.bot/mcp/mcp-overview) | Local or hosted MCP server configuration |
| [Windsurf / Devin Desktop](https://docs.devin.ai/desktop/cascade/mcp) | Cascade MCP integration |
| [OpenCode](https://opencode.ai/docs/mcp-servers/) | Local or remote MCP configuration |
| [Qwen Code](https://qwenlm.github.io/qwen-code-docs/en/users/features/mcp/) | MCP server configuration or `qwen mcp` |
| [Trae](https://docs.trae.ai/ide/model-context-protocol?_lang=en) | Local stdio or remote HTTP |
| [Pi](https://github.com/nicobailon/pi-mcp-adapter) | Via an MCP adapter |
| [Kilo Code](https://kilo.ai/docs/automate/mcp/server-transports) | MCP transport configuration |
| [CodeBuddy](https://www.codebuddy.ai/docs/cli/mcp) | MCP server configuration or `codebuddy mcp` |
| [Qoder](https://docs.qoder.com/cli/mcp-servers) | MCP configuration or `qoder mcp` |
| [Roo Code](https://docs.roocode.com/features/mcp/using-mcp-in-roo) | Project or global MCP server configuration |

Names and marks in the demo identify clients, not model providers or commercial
partnerships. See [asset credits](ASSET_CREDITS.md).
Claude Code and Claude Desktop share one Claude tile in the demo; their
installation routes remain distinct.

## Package and runtime

The repository includes `scripts/launch.sh` for macOS/Linux and
`scripts/launch.cmd` (or `scripts/launch.ps1`) for Windows. They reuse uv when available, otherwise
bootstrap it into an application-specific directory using the official
installer, without editing shell profiles. uv then prepares isolated managed
Python 3.12 and the `rerank` dependencies before starting the server. Normal
server arguments pass through unchanged. The launchers pin the release's package
version rather than running a local checkout. Before that version is published,
`SCHOLAR_PACKAGE` selects a candidate wheel and `SCHOLAR_WHEEL_INDEX` can select
its accompanying compatibility-wheel directory.

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

The managed install path uses Python 3.12. The source test matrix covers Python
3.10-3.13; plain pip installs additionally depend on native wheel availability
for that interpreter and platform. The server supports stdio and Streamable HTTP.
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
on macOS, a declined challenge can retry with no startup window and a hidden
browser page.
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
