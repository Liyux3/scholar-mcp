"""Register the Scholar MCP server in agent clients with one command.

Every write is planned first, backed up, atomic and idempotent. Entries written
here are recorded in a ledger, so an existing hand-made ``scholar`` entry is
never replaced without ``--force`` and uninstall only removes what it added.
"""

from __future__ import annotations

import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from functools import wraps
from importlib import metadata
from pathlib import Path
from typing import Callable

from filelock import FileLock

from . import __version__, config

try:
    import tomllib
except ImportError:  # pragma: no cover - Python 3.10
    import tomli as tomllib

NAME = "scholar"
WHEEL_INDEX = f"https://github.com/Liyux3/scholar-mcp/releases/download/v{__version__}/wheel-index.html"
MARK_BEGIN = "# >>> scholar-mcp (managed by `scholar-mcp install`) >>>"
MARK_END = "# <<< scholar-mcp <<<"
# Environment keys the installer owns. Everything else a user adds to an entry
# (API keys, timeouts, flags) survives reinstalls and profile changes.
MANAGED_ENV = {"SCHOLAR_MCP_EXTENSIONS"}


class Skipped(ValueError):
    """An expected reason to leave a client alone, reported without failing the run."""


def _home() -> Path:
    return Path.home()


def _client_home(default: Path, *variables: str, strip: bool = False) -> Path:
    """Use the client's first nonempty override, then its native default."""
    for name in variables:
        value = os.environ.get(name, '')
        value = value.strip() if strip else value
        if value:
            return Path(value).expanduser()
    return default


def _appdata(*parts: str) -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA") or _home() / "AppData/Roaming", *parts)
    if sys.platform == "darwin":
        return _home().joinpath("Library/Application Support", *parts)
    return _xdg(*parts)


def _xdg(*parts: str) -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or _home() / ".config", *parts)


def _mimo_path() -> Path:
    if os.environ.get("MIMOCODE_CONFIG"):
        return Path(os.environ["MIMOCODE_CONFIG"]).expanduser()
    root = (Path(os.environ["MIMOCODE_CONFIG_DIR"]) if os.environ.get("MIMOCODE_CONFIG_DIR")
            else Path(os.environ["MIMOCODE_HOME"]) / "config" if os.environ.get("MIMOCODE_HOME")
            else _xdg("mimocode"))
    commented = root / "mimocode.jsonc"
    return commented if commented.exists() else root / "mimocode.json"


# ---------- launch spec ----------

@dataclass
class Spec:
    command: str
    args: list[str]
    env: dict[str, str]


def install_method() -> str:
    """How this copy was installed: uv-tool, pipx, uvx, dev or pip."""
    prefix = Path(sys.prefix)
    if (prefix / "uv-receipt.toml").exists():
        return "uv-tool"
    if (prefix / "pipx_metadata.json").exists():
        return "pipx"
    posix = prefix.as_posix()
    if "/uv/" in posix and ("/archive-v" in posix or "/builds-v" in posix):
        return "uvx"
    try:
        direct = json.loads(metadata.distribution("scholar-mcp").read_text("direct_url.json") or "{}")
    except metadata.PackageNotFoundError:
        return "dev"
    return "dev" if direct.get("dir_info", {}).get("editable") else "pip"


def launch_spec(mode: str = "auto", profile: str = "core") -> Spec:
    """The command clients run.

    A persistent install registers its own executable, so upgrading the tool
    upgrades every client; an ephemeral uvx run registers a pinned uvx command.
    """
    if profile not in {"core", "research"}:
        raise ValueError("profile must be core or research")
    env = {"SCHOLAR_MCP_EXTENSIONS": profile} if profile != "core" else {}
    if mode == "auto":
        mode = "uvx" if install_method() == "uvx" else "tool"
    if mode == "tool":
        found = shutil.which("scholar-mcp") or str(Path(sys.executable).parent / "scholar-mcp")
        return Spec(str(Path(found).absolute()), [], env)
    return Spec("uvx", ["--python", "3.12", "--find-links", WHEEL_INDEX,
                        "--from", f"scholar-mcp[rerank]=={__version__}", "scholar-mcp"], env)


# ---------- clients ----------

def _mcp_servers(spec: Spec) -> dict:
    entry = {"command": spec.command, "args": spec.args}
    if spec.env:
        entry["env"] = spec.env
    return entry


def _with(**extra) -> Callable[[Spec], dict]:
    """The common mcpServers entry plus client-specific fields.

    A full search can run for a minute, past several clients' default call
    timeout, so clients that document a per-server timeout get a long one.
    """
    return lambda spec: {**_mcp_servers(spec), **extra}


def _local_array(spec: Spec) -> dict:
    """OpenCode and Kilo: type local, command as one array, environment."""
    return {"type": "local", "command": [spec.command, *spec.args], "enabled": True, "timeout": 600_000,
            **({"environment": spec.env} if spec.env else {})}


def _cordis_entry(spec: Spec) -> dict:
    return {"id": "mcp-scholar", "name": "@deepseek-ai/dsh-mcp-client", "config": {
        "serverName": NAME, "transport": "stdio", **_mcp_servers(spec), "toolCallTimeoutMs": 600_000}}


@dataclass
class Client:
    name: str
    label: str
    path: Callable[[], Path]
    key: str | tuple[str, ...] = "mcpServers"
    render: Callable[[Spec], dict] = _mcp_servers
    project_path: str | None = None
    binary: str | None = None
    app: str | None = None
    kind: str = "json"
    gate: tuple[str, ...] | None = None

    def config_path(self, project: bool = False) -> Path:
        if not project:
            return self.path()
        if not self.project_path:
            raise ValueError(f"{self.label} has no project-scoped configuration")
        return Path.cwd() / self.project_path

    def detected(self) -> bool:
        if self.binary and shutil.which(self.binary):
            return True
        if self.app and Path(self.app).exists():
            return True
        return self.path().parent.exists()


CLIENTS: dict[str, Client] = {c.name: c for c in [
    # Pi reads MCP servers through pi-mcp-adapter's shared mcp.json.
    Client("pi", "Pi", lambda: _xdg("mcp", "mcp.json"), project_path=".pi/mcp.json", binary="pi"),
    Client("claude-code", "Claude Code", lambda: _home() / ".claude.json", project_path=".mcp.json",
           binary="claude", kind="claude"),
    Client("claude-desktop", "Claude Desktop", lambda: _appdata("Claude", "claude_desktop_config.json"),
           app="/Applications/Claude.app"),
    Client("codex", "Codex", lambda: Path(os.environ.get("CODEX_HOME", _home() / ".codex")) / "config.toml",
           project_path=".codex/config.toml", binary="codex", kind="toml"),
    Client("cursor", "Cursor", lambda: _home() / ".cursor/mcp.json", project_path=".cursor/mcp.json",
           app="/Applications/Cursor.app"),
    Client("vscode", "VS Code", lambda: _appdata("Code", "User", "mcp.json"), key="servers",
           render=lambda s: {"type": "stdio", **_mcp_servers(s)}, project_path=".vscode/mcp.json",
           binary="code"),
    Client("gemini", "Gemini CLI", lambda: _home() / ".gemini/settings.json",
           project_path=".gemini/settings.json", binary="gemini"),
    Client("opencode", "OpenCode", lambda: _xdg("opencode", "opencode.json"), key="mcp",
           render=_local_array, project_path="opencode.json", binary="opencode"),
    Client("antigravity", "Antigravity", lambda: _home() / ".gemini/config/mcp_config.json",
           project_path=".agents/mcp_config.json",
           app="/Applications/Antigravity.app"),
    Client("windsurf", "Windsurf", lambda: _home() / ".codeium/windsurf/mcp_config.json",
           app="/Applications/Windsurf.app"),
    Client("devin", "Devin Desktop", lambda: (_appdata("devin", "mcp_config.json") if sys.platform == "win32"
                                               else _xdg("devin", "mcp_config.json")),
           app="/Applications/Devin.app"),
    Client("kiro", "Kiro", lambda: _home() / ".kiro/settings/mcp.json", project_path=".kiro/settings/mcp.json",
           app="/Applications/Kiro.app"),
    Client("zed", "Zed", lambda: (_appdata("Zed", "settings.json") if sys.platform == "win32"
                                  else _xdg("zed", "settings.json")), key="context_servers",
           render=lambda s: {"source": "custom", **_mcp_servers(s)}, project_path=".zed/settings.json",
           app="/Applications/Zed.app"),
    # Chinese coding agents with documented file configuration (checked 2026-10-17).
    Client("qwen-code", "Qwen Code", lambda: _client_home(_home() / ".qwen", "QWEN_HOME") / "settings.json",
           project_path=".qwen/settings.json", binary="qwen"),
    Client("kimi-code", "Kimi Code", lambda: _client_home(_home() / ".kimi-code", "KIMI_CODE_HOME") / "mcp.json",
           render=_with(startupTimeoutMs=120_000, toolTimeoutMs=600_000),
           project_path=".kimi-code/mcp.json", binary="kimi"),
    Client("qoder-cli", "Qoder CLI", lambda: _client_home(_home() / ".qoder", "QODER_CONFIG_DIR") / "settings.json",
           render=_with(type="stdio"), project_path=".mcp.json"),
    Client("codebuddy", "CodeBuddy Code", lambda: _client_home(_home() / ".codebuddy", "CODEBUDDY_CONFIG_DIR") / ".mcp.json",
           render=_with(type="stdio"), project_path=".mcp.json", binary="codebuddy"),
    Client("iflow", "iFlow CLI", lambda: _home() / ".iflow/settings.json",
           render=_with(timeout=600_000), project_path=".iflow/settings.json", binary="iflow"),
    Client("minimax", "MiniMax Code", lambda: _client_home(_home() / ".minimax", "MINIMAX_DATA_DIR", "MAVIS_DATA_DIR", strip=True) / "mcp.json",
           render=_with(type="stdio", enabled=True, timeout=600_000), binary="mcode"),
    Client("deepseek", "DeepSeek Harness", lambda: _client_home(_home() / ".dsh", "DSH_HOME") / "cordis.patch.yml",
           render=_cordis_entry, binary="dsh", kind="cordis"),
    Client("trae-cli", "TRAE CLI 2.0", lambda: _client_home(_home() / ".trae", "TRAE_HOME") / "traecli.toml",
           binary="traecli", kind="toml"),
    Client("mimocode", "MiMo Code", _mimo_path, key="mcp", render=_local_array,
           project_path="mimocode.json", binary="mimo"),
    Client("zcode", "ZCode", lambda: _home() / ".zcode/cli/config.json", key=("mcp", "servers"),
           render=_with(type="stdio", enabled=True, timeoutMs=600_000), binary="zcode",
           app="/Applications/ZCode.app", gate=("features", "mcp")),
    # Other agents with documented file configuration.
    Client("cline", "Cline CLI", lambda: Path(os.environ.get("CLINE_MCP_SETTINGS_PATH")
                                              or _home() / ".cline/data/settings/cline_mcp_settings.json"),
           render=_with(timeout=600), binary="cline"),
    Client("kilo", "Kilo Code", lambda: _xdg("kilo", "kilo.json"), key="mcp",
           render=_local_array, project_path="kilo.json", binary="kilo"),
    Client("amp", "Amp", lambda: _home() / ".config/amp/settings.json", key="amp.mcpServers",
           project_path=".amp/settings.json", binary="amp"),
    Client("copilot-cli", "GitHub Copilot CLI",
           lambda: Path(os.environ.get("COPILOT_HOME", _home() / ".copilot")) / "mcp-config.json",
           render=_with(type="local", timeout=600_000, deferTools="never"), binary="copilot"),
    Client("factory", "Factory Droid", lambda: _home() / ".factory/mcp.json",
           render=_with(type="stdio"), project_path=".factory/mcp.json", binary="droid"),
    Client("junie", "Junie CLI", lambda: _home() / ".junie/mcp/mcp.json",
           project_path=".junie/mcp/mcp.json", binary="junie"),
    Client("augment", "Augment CLI", lambda: _home() / ".augment/settings.json", binary="auggie"),
    Client("warp", "Warp", lambda: _home() / ".warp/.mcp.json", project_path=".warp/.mcp.json",
           app="/Applications/Warp.app"),
    Client("lmstudio", "LM Studio", lambda: _home() / ".lmstudio/mcp.json", app="/Applications/LM Studio.app"),
]}


# ---------- ledger, backups, atomic writes ----------

def _state_dir() -> Path:
    return Path(config.DATA_DIR)


def _ledger() -> dict:
    try:
        return json.loads((_state_dir() / "installs.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _record(key: str, value: dict | None) -> None:
    ledger = _ledger()
    if value is None:
        ledger.pop(key, None)
    else:
        ledger[key] = value
    _atomic_write(_state_dir() / "installs.json", json.dumps(ledger, indent=2) + "\n")


def _atomic_write(path: Path, text: str) -> None:
    target = path.resolve() if path.is_symlink() else path
    target.parent.mkdir(parents=True, exist_ok=True)
    mode = target.stat().st_mode & 0o777 if target.exists() else 0o600
    fd, temp = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.chmod(temp, mode)
        os.replace(temp, target)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise


def _backup(client: str, path: Path) -> str | None:
    if not path.exists():
        return None
    folder = _state_dir() / "backups" / client
    folder.mkdir(parents=True, exist_ok=True)
    copy = folder / f"{time.strftime('%Y%m%dT%H%M%S')}{time.time_ns() % 1_000_000_000:09d}-{path.name}"
    shutil.copy2(path, copy)
    for old in sorted(folder.iterdir())[:-5]:
        old.unlink()
    return str(copy)


# ---------- per-format edits: (text before, text after, existing entry) ----------

def _merged(existing, desired: dict) -> dict:
    """The managed fields over an existing entry, keeping the user's own keys and environment."""
    if not isinstance(existing, dict):
        return desired
    merged = {**existing, **{k: v for k, v in desired.items() if k not in ("env", "environment")}}
    for key in ("env", "environment"):
        if key in desired or key in existing:
            env = {k: v for k, v in (existing.get(key) or {}).items() if k not in MANAGED_ENV}
            env.update(desired.get(key) or {})
            if env:
                merged[key] = env
            else:
                merged.pop(key, None)
    return merged


def _json_parent(data: dict, key: str | tuple[str, ...], *, create: bool = False) -> tuple[dict, str, list]:
    """Resolve object keys; a string containing dots stays one literal key."""
    parts = (key,) if isinstance(key, str) else key
    parents = []
    for part in parts[:-1]:
        child = data.get(part)
        if child is None:
            child = {}
            if create:
                data[part] = child
        if not isinstance(child, dict):
            raise Skipped(f"configuration section {part} must be a JSON object; it was left untouched")
        parents.append((data, part))
        data = child
    return data, parts[-1], parents


def _json_edit(client: Client, path: Path, entry: dict | None) -> tuple[str, str, object]:
    before = path.read_text(encoding="utf-8") if path.exists() else ""
    try:
        data = json.loads(before) if before.strip() else {}
    except ValueError as error:
        raise Skipped(f"{path} is not plain JSON (comments or syntax), so it was left untouched: {error}")
    if not isinstance(data, dict):
        raise Skipped(f"{path} does not hold a JSON object")
    if entry is not None and client.gate:
        settings, flag, _ = _json_parent(data, client.gate)
        if settings.get(flag) is False:
            raise Skipped(f"{client.label} explicitly disables {'.'.join(client.gate)}; the setting was preserved")
    parent, key, parents = _json_parent(data, client.key, create=entry is not None)
    servers = parent.get(key) or {}
    if not isinstance(servers, dict):
        raise Skipped(f"{path}: {key} must be a JSON object; it was left untouched")
    existing = servers.get(NAME)
    if entry is not None:
        entry = _merged(existing, entry)
    if existing == entry:
        return before, before, existing
    if entry is None:
        servers.pop(NAME, None)
    else:
        servers[NAME] = entry
    if servers:
        parent[key] = servers
    else:
        parent.pop(key, None)
        for ancestor, part in reversed(parents):
            if ancestor.get(part):
                break
            ancestor.pop(part, None)
    return before, json.dumps(data, indent=2, ensure_ascii=False) + "\n", existing


def _manual_configuration(client: Client, spec: Spec):
    if client.kind == "cordis":
        return [{"insert": [client.render(spec)]}]
    if client.kind == "toml":
        return _toml_block(spec, codex=client.name == "codex")
    data = {}
    parent, key, _ = _json_parent(data, client.key, create=True)
    parent[key] = {NAME: client.render(spec)}
    if client.gate:
        parent, key, _ = _json_parent(data, client.gate, create=True)
        parent[key] = True
    return data


def _toml_value(value) -> str | None:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float, str)) or (isinstance(value, list) and all(isinstance(v, str) for v in value)):
        return json.dumps(value)
    return None


def _toml_key(key: str) -> str:
    return key if re.fullmatch(r"[A-Za-z0-9_-]+", key) else json.dumps(key)


def _toml_tables(name: str, table: dict) -> list[list[str]]:
    """A table's own keys, then each nested table, so the user's per-tool settings survive a reinstall."""
    own = [f"{_toml_key(k)} = {rendered}" for k, v in table.items()
           if not isinstance(v, dict) and (rendered := _toml_value(v)) is not None]
    nested = {k: v for k, v in table.items() if isinstance(v, dict)}
    blocks = [[f"[{name}]", *own]] if own or not nested else []
    for k, v in nested.items():
        blocks += _toml_tables(f"{name}.{_toml_key(k)}", v)
    return blocks


def _toml_block(spec: Spec, existing=None, *, codex: bool = True) -> str:
    entry = _merged(existing, {"command": spec.command, "args": spec.args, "env": spec.env})
    if codex:
        entry.setdefault("startup_timeout_sec", 60)
        entry.setdefault("tool_timeout_sec", 600)
    entry["env"] = {k: str(v) for k, v in (entry.get("env") or {}).items()}
    if not entry["env"]:
        del entry["env"]
    if codex:  # Codex otherwise cuts the middle out of long tool results.
        tools = {name: dict(settings) for name, settings in (entry.get("tools") or {}).items()}
        tools.setdefault("read_paper", {}).setdefault("output_token_limit", 30000)
        entry["tools"] = tools
    body = "\n\n".join("\n".join(block) for block in _toml_tables(f"mcp_servers.{NAME}", entry))
    return f"{MARK_BEGIN}\n{body}\n{MARK_END}\n"


def _strip_toml(text: str) -> str:
    """Drop the Scholar tables (managed block or hand-written) and the block markers.

    Removal follows table headers, not the markers alone: Codex rewrites this
    file and may place its own tables before the end marker.
    """
    out, skipping = [], False
    for line in text.splitlines(True):
        stripped = line.strip()
        if stripped in (MARK_BEGIN, MARK_END):
            skipping = stripped == MARK_BEGIN
            continue
        if stripped.startswith("["):
            table = stripped.strip("[]").strip()
            skipping = not stripped.startswith("[[") and (
                table == f"mcp_servers.{NAME}" or table.startswith(f"mcp_servers.{NAME}."))
        if not skipping:
            out.append(line)
    return "".join(out)


def _toml_edit(path: Path, spec: Spec | None, *, codex: bool = True) -> tuple[str, str, object]:
    before = path.read_text(encoding="utf-8") if path.exists() else ""
    existing = tomllib.loads(before).get("mcp_servers", {}).get(NAME) if before.strip() else None
    block = None if spec is None else _toml_block(spec, existing, codex=codex)
    if block is not None and block in before:
        return before, before, existing
    base = _strip_toml(before)
    after = base if block is None else base.rstrip("\n") + ("\n\n" if base.strip() else "") + block
    remaining = tomllib.loads(after).get("mcp_servers", {}).get(NAME) if after.strip() else None
    if spec is None and remaining is not None:
        raise Skipped(f"{path} defines '{NAME}' inline under [mcp_servers]; remove it by hand")
    return before, after, existing


def _cordis_edit(path: Path, spec: Spec | None) -> tuple[str, str, object]:
    """Append an owned Cordis insert patch, preserving the user's YAML and !!js tags."""
    import yaml

    before = path.read_text(encoding="utf-8") if path.exists() else ""
    pattern = rf"(?m)^{re.escape(MARK_BEGIN)}\r?\n(.*?)^{re.escape(MARK_END)}(?:\r?\n|$)"
    match = re.search(pattern, before, re.S)
    if (MARK_BEGIN in before or MARK_END in before) and match is None:
        raise Skipped(f"{path} has an incomplete managed block; it was left untouched")
    base = before[:match.start()] + before[match.end():] if match else before
    try:
        # compose inspects syntax without constructing or evaluating custom tags.
        root = yaml.compose(base)
        existing = yaml.safe_load(match[1])[0]["insert"][0] if match else None
        if existing is not None and not isinstance(existing, dict):
            raise ValueError("the managed plugin entry is not an object")
    except (yaml.YAMLError, KeyError, IndexError, TypeError, ValueError) as error:
        # YAML diagnostics can quote configuration lines containing credentials.
        raise Skipped(f"{path} is not a supported Cordis patch ({type(error).__name__}); it was left untouched") from None
    if root is not None:
        if not isinstance(root, yaml.SequenceNode) or (root.flow_style and root.value):
            raise Skipped(f"{path} needs a block-style Cordis patch list; it was left untouched")
        pending, seen = [root], set()
        while pending:
            node = pending.pop()
            if id(node) in seen:
                continue
            seen.add(id(node))
            if isinstance(node, yaml.MappingNode):
                fields = {key.value: value for key, value in node.value if isinstance(key, yaml.ScalarNode)}
                if (getattr(fields.get("serverName"), "value", None) == NAME
                        or getattr(fields.get("id"), "value", None) == "mcp-scholar"):
                    if spec is None and match is None:
                        return before, before, {"config": {"command": getattr(fields.get("command"), "value", None)}}
                    raise Skipped(f"{path} already configures Scholar outside its managed block; it was left untouched")
                pending.extend(value for _, value in node.value)
            elif isinstance(node, yaml.SequenceNode):
                pending.extend(node.value)
        if root.flow_style:  # an empty [] is replaced by the managed list
            base = base[:root.start_mark.index] + base[root.end_mark.index:]
    if spec is None:
        return before, base if root and root.value else base.rstrip() + ("\n" if base.strip() else "") + "[]\n", existing
    row = {**(existing or {}), **_cordis_entry(spec)}
    row["config"] = _merged((existing or {}).get("config"), row["config"])
    block = f"{MARK_BEGIN}\n- {json.dumps({'insert': [row]}, ensure_ascii=False)}\n{MARK_END}\n"
    after = base + ("\n" if base and not base.endswith("\n") else "") + block
    return before, after, existing


# ---------- operations ----------

def _ledger_key(client: Client, path: Path) -> str:
    return f"{client.name}:{path}"


def _plan(client: Client, spec: Spec | None, project: bool) -> dict:
    try:
        path = client.config_path(project)
    except ValueError as error:
        raise Skipped(str(error)) from None
    if client.kind in {"toml", "cordis"}:
        before, after, existing = (_toml_edit(path, spec, codex=client.name == "codex")
                                   if client.kind == "toml" else _cordis_edit(path, spec))
        owned = MARK_BEGIN in before
    else:
        before, after, existing = _json_edit(client, path, None if spec is None else client.render(spec))
        owned = _ledger_key(client, path) in _ledger()
    return {"path": path, "before": before, "after": after, "existing": existing, "owned": owned}


def _claude_cli(args: list[str]) -> tuple[bool, str]:
    binary = shutil.which("claude") or str(_home() / ".claude/local/claude")
    try:
        result = subprocess.run([binary, *args], capture_output=True, text=True,
                                encoding='utf-8', errors='replace', timeout=60)
    except (OSError, subprocess.SubprocessError) as error:
        return False, f"{type(error).__name__}: {error}"[-400:]
    return result.returncode == 0, (result.stderr or result.stdout).strip()[-400:]


def _locked(function):
    """Read, plan and write under one lock, so two installers cannot both start from the old file."""
    @wraps(function)
    def run(*args, **kwargs):
        with FileLock(str(_state_dir() / "install.lock")):
            return function(*args, **kwargs)
    return run


@_locked
def install(name: str, spec: Spec, *, project: bool = False, dry_run: bool = False,
            force: bool = False) -> dict:
    client = CLIENTS[name]
    if client.kind == "claude" and not project:
        return _install_claude_code(spec, dry_run=dry_run, force=force)
    try:
        plan = _plan(client, spec, project)
    except Skipped as reason:
        return {"client": name, "status": "skipped", "reason": str(reason),
                "manual": _manual_configuration(client, spec)}
    path = plan["path"]
    report = {"client": name, "path": str(path)}
    if plan["before"] == plan["after"]:
        return {**report, "status": "unchanged"}
    if plan["existing"] is not None and not plan["owned"] and not force:
        return {**report, "status": "skipped",
                "reason": f"an existing '{NAME}' entry was not created by this installer; use --force to replace it"}
    diff = "".join(difflib.unified_diff(plan["before"].splitlines(True), plan["after"].splitlines(True),
                                        f"{path} (current)", f"{path} (planned)"))
    if dry_run:
        return {**report, "status": "planned", "diff": diff}
    report["backup"] = _backup(name, path)
    _atomic_write(path, plan["after"])
    _record(_ledger_key(client, path), {"version": __version__, "command": spec.command,
                                         "installed_at": time.strftime("%Y-%m-%dT%H:%M:%S")})
    return {**report, "status": "updated" if plan["existing"] is not None else "installed"}


@_locked
def uninstall(name: str, *, project: bool = False, dry_run: bool = False, force: bool = False) -> dict:
    client = CLIENTS[name]
    if client.kind == "claude" and not project:
        return _uninstall_claude_code(dry_run=dry_run, force=force)
    try:
        plan = _plan(client, None, project)
    except Skipped as reason:
        return {"client": name, "status": "skipped", "reason": str(reason)}
    path = plan["path"]
    report = {"client": name, "path": str(path)}
    if plan["existing"] is None:
        return {**report, "status": "absent"}
    if not plan["owned"] and not force:
        return {**report, "status": "skipped",
                "reason": f"the '{NAME}' entry was not created by this installer; use --force to remove it"}
    if dry_run:
        return {**report, "status": "planned"}
    report["backup"] = _backup(name, path)
    _atomic_write(path, plan["after"])
    _record(_ledger_key(client, path), None)
    return {**report, "status": "removed"}


def _claude_entry() -> dict | None:
    try:
        data = json.loads((_home() / ".claude.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return (data.get("mcpServers") or {}).get(NAME)


def _install_claude_code(spec: Spec, *, dry_run: bool, force: bool) -> dict:
    """Use Claude Code's own CLI: ~/.claude.json is rewritten by live sessions."""
    report = {"client": "claude-code", "path": str(_home() / ".claude.json"), "scope": "user"}
    existing = _claude_entry()
    desired = _merged(existing, {"type": "stdio", **_mcp_servers(spec)})
    owned = _ledger_key(CLIENTS["claude-code"], _home() / ".claude.json") in _ledger()
    if existing is not None and {k: v for k, v in existing.items() if k in desired} == desired:
        return {**report, "status": "unchanged"}
    if existing is not None and not owned and not force:
        return {**report, "status": "skipped",
                "reason": f"an existing '{NAME}' entry was not created by this installer; use --force to replace it"}
    command = ["mcp", "add-json", NAME, json.dumps(desired), "-s", "user"]
    if dry_run:
        return {**report, "status": "planned", "command": "claude " + " ".join(command[:3]) + " '<json>' -s user",
                "entry": desired}
    if not (shutil.which("claude") or (_home() / ".claude/local/claude").exists()):
        return {**report, "status": "skipped", "reason": "the claude command is not on PATH",
                "manual": f"claude mcp add-json {NAME} '{json.dumps(desired)}' -s user"}
    if existing is not None:
        removed, message = _claude_cli(["mcp", "remove", NAME, "-s", "user"])
        if not removed:
            return {**report, "status": "failed", "error": message}
    added, message = _claude_cli(command)
    if not added:
        if existing is not None:  # put the previous entry back rather than leave none
            restored, _ = _claude_cli(["mcp", "add-json", NAME, json.dumps(existing), "-s", "user"])
            report["restored_previous"] = restored
        return {**report, "status": "failed", "error": message}
    _record(_ledger_key(CLIENTS["claude-code"], _home() / ".claude.json"),
            {"version": __version__, "command": spec.command, "installed_at": time.strftime("%Y-%m-%dT%H:%M:%S")})
    return {**report, "status": "updated" if existing is not None else "installed"}


def _uninstall_claude_code(*, dry_run: bool, force: bool) -> dict:
    report = {"client": "claude-code", "path": str(_home() / ".claude.json"), "scope": "user"}
    key = _ledger_key(CLIENTS["claude-code"], _home() / ".claude.json")
    if _claude_entry() is None:
        return {**report, "status": "absent"}
    if key not in _ledger() and not force:
        return {**report, "status": "skipped",
                "reason": f"the '{NAME}' entry was not created by this installer; use --force to remove it"}
    if dry_run:
        return {**report, "status": "planned"}
    removed, message = _claude_cli(["mcp", "remove", NAME, "-s", "user"])
    if not removed:
        return {**report, "status": "failed", "error": message}
    _record(key, None)
    return {**report, "status": "removed"}


def status() -> list[dict]:
    """Detected clients and whether each has a Scholar entry, without secrets."""
    rows = []
    ledger = _ledger()
    for client in CLIENTS.values():
        path = client.config_path()
        row = {"client": client.name, "detected": client.detected(), "path": str(path)}
        try:
            if client.kind == "toml":
                text = path.read_text(encoding="utf-8") if path.exists() else ""
                entry = tomllib.loads(text).get("mcp_servers", {}).get(NAME) if (tomllib and text) else None
                owned = MARK_BEGIN in text
            elif client.kind == "claude":
                entry = _claude_entry()
                owned = _ledger_key(client, path) in ledger
            elif client.kind == "cordis":
                text, _, plugin = _cordis_edit(path, None)
                entry = plugin.get("config") if plugin else None
                owned = MARK_BEGIN in text
            else:
                text = path.read_text(encoding="utf-8") if path.exists() else ""
                data = json.loads(text) if text.strip() else {}
                if not isinstance(data, dict):
                    raise Skipped("configuration must be a JSON object")
                parent, key, _ = _json_parent(data, client.key)
                servers = parent.get(key) or {}
                if not isinstance(servers, dict):
                    raise Skipped(f"configuration section {key} must be a JSON object")
                entry = servers.get(NAME)
                if entry is not None and not isinstance(entry, dict):
                    raise Skipped("Scholar configuration must be a JSON object")
                owned = _ledger_key(client, path) in ledger
                if client.gate:
                    parent, key, _ = _json_parent(data, client.gate)
                    row["enabled"] = parent.get(key, True)
        except (OSError, ValueError) as error:
            row["error"] = str(error)[:160]
            rows.append(row)
            continue
        row["registered"] = entry is not None
        if entry is not None:
            command = entry.get("command")
            row["command"] = " ".join(command) if isinstance(command, list) else command
            row["managed"] = owned
        rows.append(row)
    return rows
