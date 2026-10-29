# Install Scholar MCP

These steps are written for an AI agent installing Scholar MCP into the client
it runs in. Scholar MCP is a local stdio server published on PyPI. Nothing is
hosted: the client starts one command, `uvx` downloads the package from PyPI on
first launch, and the client talks to that local process. Source keys are
optional.

## 1. Use the installer when the client is supported

With `uv` available:

```bash
uv tool install --python 3.12 --find-links https://github.com/Liyux3/scholar-mcp/releases/latest/download/wheel-index.html "scholar-mcp[rerank]"
scholar-mcp install --all --dry-run   # preview
scholar-mcp install --all             # or name one client, for example: scholar-mcp install qwen-code
```

`scholar-mcp install` without arguments lists the clients it supports and
detects. `--profile research` adds `build_paper_graph` and `paper_library` to
the six core tools. An existing `scholar` entry it did not create is left
alone unless `--force` is given. If your client is listed, stop here.

## 2. Otherwise, write the entry yourself

Every client needs the same server, written in its own format:

- name: `scholar`
- command: `uvx`
- args: `--python 3.12 --find-links https://github.com/Liyux3/scholar-mcp/releases/latest/download/wheel-index.html --from scholar-mcp[rerank] scholar-mcp`
- env: `SCHOLAR_MCP_EXTENSIONS=research` (optional)
- startup timeout: at least 120 seconds, because the first launch prepares Python 3.12
- tool-call timeout: at least 600 seconds, because one search can wait on many sources

Find where your client keeps MCP servers (its documentation, or an existing
entry in its settings) and translate. Common layouts:

```json
{ "mcpServers": { "scholar": { "command": "uvx", "args": ["--python", "3.12", "--find-links", "https://github.com/Liyux3/scholar-mcp/releases/latest/download/wheel-index.html", "--from", "scholar-mcp[rerank]", "scholar-mcp"], "env": { "SCHOLAR_MCP_EXTENSIONS": "research" } } } }
```

- VS Code: the same entry under `servers` with `"type": "stdio"`.
- Antigravity: `~/.gemini/config/mcp_config.json` or `.agents/mcp_config.json`, under `mcpServers`.
- Devin Desktop: `~/.config/devin/mcp_config.json` (or `$XDG_CONFIG_HOME/devin/mcp_config.json`), under `mcpServers`; Windows uses `%APPDATA%\devin\mcp_config.json`. Older Windsurf keeps its separate configuration.
- OpenCode, Kilo Code: under `mcp`, `"type": "local"`, `command` is one array of the command and all args, env goes in `environment`; set `"timeout": 600000` for research calls (milliseconds).
- DeepSeek Harness: `scholar-mcp install deepseek` adds the native MCP plugin to `$DSH_HOME/cordis.patch.yml` (default `~/.dsh/cordis.patch.yml`). This is a Cordis YAML insert patch, shared across profiles, with `serverName: scholar`, `transport: stdio` and `toolCallTimeoutMs: 600000`. Keep existing YAML and `!!js` expressions intact. For a profile-specific manual setup, put the insert in that profile's `cordis.patch.yml`; an invocation-specific file requires `dsh <profile> --patch <file>`.
- TRAE CLI 2.0: `scholar-mcp install trae-cli` writes `[mcp_servers.scholar]` in `~/.trae/traecli.toml`, respecting `TRAE_HOME`. Version 1's `trae_cli.yaml` is a separate format.
- MiMo Code: `scholar-mcp install mimocode` uses the OpenCode-style command array under `mcp`, in `~/.config/mimocode/mimocode.json`. It respects `MIMOCODE_HOME`, `MIMOCODE_CONFIG_DIR` and `MIMOCODE_CONFIG`; an existing higher-precedence `mimocode.jsonc` is selected and comments are left intact. Project configuration uses `mimocode.json`.
- ZCode: run `scholar-mcp install zcode`. It writes `mcp.servers.scholar` in `~/.zcode/cli/config.json` and preserves existing objects. MCP is enabled by default; an explicit `features.mcp=false` is preserved and reported. Provider credentials use ZCode's own configuration.
- Codex (`~/.codex/config.toml`): a `[mcp_servers.scholar]` table with `command`, `args`, `startup_timeout_sec`, `tool_timeout_sec` and an `[mcp_servers.scholar.env]` table.
- Goose (`~/.config/goose/config.yaml`): under `extensions`, `type: stdio`, `cmd: uvx`, `args`, `envs`, `timeout`.
- Trae Agent (`trae_config.yaml`): under `mcp_servers`, `command` and `args`.
- Clients that manage MCP servers in a settings screen (Trae IDE, Tongyi Lingma, Baidu Comate, Cherry Studio, ChatWise, Cline, Roo Code) accept the JSON above pasted into that screen.

Keep any other servers in the file untouched, and back the file up before editing.

## 3. Verify

Restart the client and check that it lists `search_papers`, `paper_info`,
`recommend_papers`, `search_authors`, `read_paper` and `download_paper`, plus
the two research tools when the extension is enabled. Then run one search. Add
source keys only when the user wants higher limits or optional paid sources.
