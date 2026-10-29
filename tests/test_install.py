import json
import os

import pytest

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib

from scholar_mcp import cli, config, install  # noqa: E402


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData/Roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData/Local"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".config"))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.delenv("DSH_HOME", raising=False)
    for variable in ("QWEN_HOME", "KIMI_CODE_HOME", "CODEBUDDY_CONFIG_DIR", "QODER_CONFIG_DIR", "MINIMAX_DATA_DIR",
                     "MAVIS_DATA_DIR", "TRAE_HOME", "MIMOCODE_HOME", "MIMOCODE_CONFIG", "MIMOCODE_CONFIG_DIR"):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path / ".scholar-mcp"))
    return tmp_path


SPEC = install.Spec("/opt/bin/scholar-mcp", [], {"SCHOLAR_MCP_EXTENSIONS": "research"})


def test_json_client_install_is_idempotent_and_reversible(home):
    path = home / ".cursor/mcp.json"
    path.parent.mkdir()
    original = {"mcpServers": {"other": {"command": "x"}}, "workspace": "研究资料"}
    path.write_text(json.dumps(original, ensure_ascii=False), encoding="utf-8")

    assert install.install("cursor", SPEC)["status"] == "installed"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["workspace"] == original["workspace"]
    assert data["mcpServers"]["other"] == {"command": "x"}
    assert data["mcpServers"]["scholar"]["env"] == {"SCHOLAR_MCP_EXTENSIONS": "research"}
    assert install.install("cursor", SPEC)["status"] == "unchanged"
    assert next(row for row in install.status() if row["client"] == "cursor")["registered"]

    removed = install.uninstall("cursor")
    assert removed["status"] == "removed"
    assert json.loads(path.read_text(encoding="utf-8")) == original
    assert list((home / ".scholar-mcp/backups/cursor").iterdir())


@pytest.mark.parametrize("initial", [{}, {"ui": {"locale": "zh-CN"}, "mcp": {
    "servers": {"other": {"type": "stdio", "command": "other-tool"}}}}])
def test_zcode_nested_mcp_configuration_round_trips(home, initial):
    path = install.CLIENTS["zcode"].path()
    path.parent.mkdir(parents=True)
    original = json.dumps(initial)
    path.write_text(original)
    assert install.install("zcode", SPEC, dry_run=True)["status"] == "planned"
    assert path.read_text() == original
    assert install.install("zcode", SPEC)["status"] == "installed"
    data = json.loads(path.read_text())
    assert data["mcp"]["servers"]["scholar"] == {
        "type": "stdio", "enabled": True, "timeoutMs": 600_000,
        "command": SPEC.command, "args": [], "env": SPEC.env}
    assert "features" not in data
    assert install.install("zcode", SPEC)["status"] == "unchanged"
    row = next(row for row in install.status() if row["client"] == "zcode")
    assert row["registered"] and row["enabled"] and row["managed"]
    assert install.uninstall("zcode")["status"] == "removed"
    assert json.loads(path.read_text()) == initial


def test_zcode_explicit_mcp_opt_out_is_preserved(home):
    path = install.CLIENTS["zcode"].path()
    path.parent.mkdir(parents=True)
    original = '{"features": {"mcp": false}, "ui": {"locale": "zh-CN"}}'
    path.write_text(original)
    report = install.install("zcode", SPEC)
    assert report["status"] == "skipped" and 'features.mcp' in report["reason"]
    assert report["manual"]["features"]["mcp"] is True
    assert report["manual"]["mcp"]["servers"]["scholar"]["command"] == SPEC.command
    assert path.read_text() == original
    row = next(row for row in install.status() if row["client"] == "zcode")
    assert not row["registered"] and not row["enabled"]


@pytest.mark.parametrize("client, original", [
    ("cursor", '[]'), ("cursor", '{"mcpServers": "private-value"}'),
    ("zcode", '{"mcp": "private-value"}'), ("zcode", '{"mcp": {"servers": [1]}}'),
])
def test_json_configuration_shape_errors_are_preserved(home, client, original):
    path = install.CLIENTS[client].path()
    path.parent.mkdir(parents=True)
    path.write_text(original)
    report = install.install(client, SPEC)
    assert report["status"] == "skipped" and 'private-value' not in report["reason"]
    assert path.read_text() == original
    row = next(row for row in install.status() if row["client"] == client)
    assert 'error' in row and 'private-value' not in row['error']


def test_dry_run_writes_nothing(home):
    report = install.install("gemini", SPEC, dry_run=True)
    assert report["status"] == "planned"
    assert '"scholar"' in report["diff"]
    assert not (home / ".gemini/settings.json").exists()


def test_qwen_home_override_selects_the_actual_client_directory(home, monkeypatch):
    target = home / "qwen-profile"
    monkeypatch.setenv("QWEN_HOME", str(target))
    report = install.install("qwen-code", SPEC)
    assert report["status"] == "installed" and report["path"] == str(target / "settings.json")
    assert json.loads((target / "settings.json").read_text())["mcpServers"]["scholar"]["command"] == SPEC.command
    assert not (home / ".qwen/settings.json").exists()


@pytest.mark.parametrize("name, variable, filename", [
    ("codebuddy", "CODEBUDDY_CONFIG_DIR", ".mcp.json"),
    ("qoder-cli", "QODER_CONFIG_DIR", "settings.json"),
    ("minimax", "MAVIS_DATA_DIR", "mcp.json"),
])
def test_native_client_directory_overrides(home, monkeypatch, name, variable, filename):
    target = home / 'native-profile'
    monkeypatch.setenv(variable, str(target))
    if name == 'minimax':
        monkeypatch.setenv('MINIMAX_DATA_DIR', '  \t')
        monkeypatch.setenv('MAVIS_DATA_DIR', f' {target} ')
    result = install.install(name, SPEC)
    assert result['status'] == 'installed' and result['path'] == str(target / filename)
    assert json.loads((target / filename).read_text())['mcpServers']['scholar']['command'] == SPEC.command


def test_empty_client_directory_overrides_use_native_defaults(home, monkeypatch):
    monkeypatch.setenv('QWEN_HOME', '')
    monkeypatch.setenv('MINIMAX_DATA_DIR', '')
    monkeypatch.setenv('MAVIS_DATA_DIR', '')
    assert install.CLIENTS['qwen-code'].path() == home / '.qwen/settings.json'
    assert install.CLIENTS['minimax'].path() == home / '.minimax/mcp.json'


def test_existing_foreign_entry_needs_force(home):
    path = home / ".kiro/settings/mcp.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"mcpServers": {"scholar": {"command": "my-own"}}}))

    assert install.install("kiro", SPEC)["status"] == "skipped"
    assert install.uninstall("kiro")["status"] == "skipped"
    assert install.install("kiro", SPEC, force=True)["status"] == "updated"
    assert json.loads(path.read_text())["mcpServers"]["scholar"]["command"] == SPEC.command


def test_client_specific_shapes(home):
    install.install("vscode", SPEC)
    install.install("opencode", SPEC)
    vscode = json.loads(install.CLIENTS["vscode"].path().read_text())
    opencode = json.loads((home / ".config/opencode/opencode.json").read_text())
    assert vscode["servers"]["scholar"]["type"] == "stdio"
    assert opencode["mcp"]["scholar"] == {
        "type": "local", "command": [SPEC.command], "enabled": True, "timeout": 600_000,
        "environment": SPEC.env}


def test_commented_json_is_left_untouched(home):
    path = home / ".config/zed/settings.json"
    path.parent.mkdir(parents=True)
    path.write_text('{\n  // my settings\n  "theme": "One Dark"\n}\n')
    report = install.install("zed", SPEC)
    assert report["status"] == "skipped" and "not plain JSON" in report["reason"]
    assert report["manual"]["context_servers"]["scholar"]["command"] == SPEC.command
    assert "// my settings" in path.read_text()


def test_deepseek_patch_preserves_user_yaml_and_round_trips(home, monkeypatch):
    import yaml

    monkeypatch.setenv("DSH_HOME", str(home / "custom-dsh"))
    path = install.CLIENTS["deepseek"].path()
    path.parent.mkdir()
    original = '# my model\n- id: model\n  config:\n    key: !!js process.env.MY_KEY\n'
    path.write_text(original)
    assert install.install("deepseek", SPEC, dry_run=True)["status"] == "planned"
    assert path.read_text() == original
    assert install.install("deepseek", SPEC)["status"] == "installed"
    assert path.read_text().startswith(original)
    assert install.install("deepseek", SPEC)["status"] == "unchanged"
    status = next(row for row in install.status() if row["client"] == "deepseek")
    assert status["registered"] and status["managed"] and status["command"] == SPEC.command
    path.write_text(path.read_text().replace('"SCHOLAR_MCP_EXTENSIONS": "research"',
                                            '"S2_API_KEY": "local-test-value", "SCHOLAR_MCP_EXTENSIONS": "research"'))
    assert install.install("deepseek", install.Spec("/new/scholar-mcp", [], {}))["status"] == "updated"
    body = path.read_text().split(install.MARK_BEGIN)[1].split(install.MARK_END)[0]
    entry = yaml.safe_load(body)[0]["insert"][0]
    assert entry["name"] == "@deepseek-ai/dsh-mcp-client"
    assert entry["config"] == {"serverName": "scholar", "transport": "stdio", "command": "/new/scholar-mcp",
                               "args": [], "env": {"S2_API_KEY": "local-test-value"}, "toolCallTimeoutMs": 600_000}
    assert install.uninstall("deepseek")["status"] == "removed"
    assert path.read_text() == original


@pytest.mark.parametrize("original", ["", "[]\n", "# empty\n[]\n"])
def test_deepseek_empty_patch_is_valid_after_install_and_uninstall(home, original):
    import yaml

    path = install.CLIENTS["deepseek"].path()
    path.parent.mkdir()
    path.write_text(original)
    assert install.install("deepseek", SPEC)["status"] == "installed"
    assert yaml.safe_load(path.read_text())[0]["insert"][0]["config"]["serverName"] == "scholar"
    assert install.install("deepseek", SPEC)["status"] == "unchanged"
    assert install.uninstall("deepseek")["status"] == "removed"
    assert yaml.safe_load(path.read_text()) == []


@pytest.mark.parametrize("original", ["not: a-list\n", "- config: [private-value\n",
    '- insert:\n  - id: own-mcp\n    config:\n      serverName: scholar\n      command: custom\n'])
def test_deepseek_foreign_or_invalid_patch_is_left_untouched(home, original):
    path = install.CLIENTS["deepseek"].path()
    path.parent.mkdir()
    path.write_text(original)
    report = install.install("deepseek", SPEC)
    assert report["status"] == "skipped" and report["manual"][0]["insert"][0]["config"]["serverName"] == "scholar"
    assert 'private-value' not in report['reason']
    assert path.read_text() == original


def test_trae_cli_uses_the_shared_toml_editor_with_its_own_fields(home, monkeypatch):
    monkeypatch.setenv("TRAE_HOME", str(home / "trae-v2"))
    path = install.CLIENTS["trae-cli"].path()
    path.parent.mkdir()
    original = 'model = "mine"\n\n[mcp_servers.other]\ncommand = "other-tool"\n'
    path.write_text(original)
    assert install.install("trae-cli", SPEC)["status"] == "installed"
    assert tomllib.loads(path.read_text())["mcp_servers"]["scholar"] == {
        "command": SPEC.command, "args": SPEC.args, "env": SPEC.env}
    assert install.install("trae-cli", SPEC)["status"] == "unchanged"
    assert install.uninstall("trae-cli")["status"] == "removed"
    assert tomllib.loads(path.read_text()) == tomllib.loads(original)
    skipped = install.install("trae-cli", SPEC, project=True)
    assert skipped["status"] == "skipped"
    assert tomllib.loads(skipped["manual"])["mcp_servers"]["scholar"]["command"] == SPEC.command


def test_mimo_custom_config_and_jsonc_precedence(home, monkeypatch):
    monkeypatch.setenv("MIMOCODE_HOME", str(home / "mimo-profile"))
    path = home / "mimo-profile/config/mimocode.jsonc"
    assert install.CLIENTS["mimocode"].path() == path.with_suffix('.json')
    monkeypatch.setenv("MIMOCODE_CONFIG_DIR", str(path.parent))
    path.parent.mkdir(parents=True)
    path.write_text('// user config\n{"mcp": {}}\n')
    assert install.CLIENTS["mimocode"].path() == path
    assert install.install("mimocode", SPEC)["status"] == "skipped"
    assert not path.with_suffix('.json').exists()
    custom = home / "team.json"
    monkeypatch.setenv("MIMOCODE_CONFIG", str(custom))
    assert install.install("mimocode", SPEC)["status"] == "installed"
    assert json.loads(custom.read_text())["mcp"]["scholar"]["command"] == [SPEC.command]


def test_codex_managed_block_preserves_other_settings(home):
    path = home / ".codex/config.toml"
    path.parent.mkdir()
    path.write_text('model = "gpt"\n\n[mcp_servers.other]\ncommand = "x"\n')

    assert install.install("codex", SPEC)["status"] == "installed"
    parsed = tomllib.loads(path.read_text())
    assert parsed["model"] == "gpt"
    assert parsed["mcp_servers"]["scholar"]["tool_timeout_sec"] == 600
    assert parsed["mcp_servers"]["scholar"]["env"] == SPEC.env
    assert install.install("codex", SPEC)["status"] == "unchanged"

    assert install.uninstall("codex")["status"] == "removed"
    parsed = tomllib.loads(path.read_text())
    assert "scholar" not in parsed["mcp_servers"]
    assert parsed["mcp_servers"]["other"] == {"command": "x"}


def test_codex_reinstall_keeps_the_users_per_tool_settings(home):
    path = home / ".codex/config.toml"
    path.parent.mkdir()
    install.install("codex", SPEC)
    managed = "[mcp_servers.scholar.tools.read_paper]\noutput_token_limit = 30000\n"
    assert managed in path.read_text()
    path.write_text(path.read_text().replace(managed, (
        "[mcp_servers.scholar.tools.read_paper]\nenabled = false\noutput_token_limit = 20000\n\n"
        "[mcp_servers.scholar.tools.search_papers]\noutput_token_limit = 12000\n")))
    assert install.install("codex", install.Spec("/new/scholar-mcp", [], {}))["status"] == "updated"
    tools = tomllib.loads(path.read_text())["mcp_servers"]["scholar"]["tools"]
    assert tools == {"search_papers": {"output_token_limit": 12000},
                     "read_paper": {"enabled": False, "output_token_limit": 20000}}
    assert install.install("codex", install.Spec("/new/scholar-mcp", [], {}))["status"] == "unchanged"


def test_codex_unmanaged_table_is_replaced_only_with_force(home):
    path = home / ".codex/config.toml"
    path.parent.mkdir()
    path.write_text('[mcp_servers.scholar]\ncommand = "mine"\n\n[mcp_servers.scholar.env]\nA = "1"\n\n[other]\nx = 1\n')

    assert install.install("codex", SPEC)["status"] == "skipped"
    assert install.install("codex", SPEC, force=True)["status"] == "updated"
    parsed = tomllib.loads(path.read_text())
    assert parsed["mcp_servers"]["scholar"]["command"] == SPEC.command
    assert parsed["mcp_servers"]["scholar"]["env"] == {"A": "1", **SPEC.env}
    assert parsed["other"] == {"x": 1}


def test_uvx_spec_pins_this_version():
    spec = install.launch_spec("uvx", "core")
    assert spec.command == "uvx"
    assert f"scholar-mcp[rerank]=={install.__version__}" in spec.args
    assert spec.env == {}


def test_cli_install_reports_json(home, capsys):
    assert cli.main(["install", "cursor", "--mode", "uvx", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["results"][0]["status"] == "installed"
    assert report["server"]["command"] == "uvx"


def test_cli_rejects_unknown_client(home):
    with pytest.raises(SystemExit):
        cli.main(["install", "not-a-client"])


def test_research_commands_share_the_tool_functions(monkeypatch, capsys):
    from scholar_mcp import api

    monkeypatch.setattr(api, "search", lambda query, **kw: {"results": [{"title": query, "limit": kw["limit"]}]})
    assert cli.main(["search", "graph rag", "--limit", "3", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {"results": [{"title": "graph rag", "limit": 3}]}

    monkeypatch.setattr(api, "info", lambda paper_id, **kw: {"error": f"Could not find paper '{paper_id}'"})
    assert cli.main(["info", "missing"]) == 1
    assert "Could not find paper" in capsys.readouterr().out


def test_read_prints_metadata_then_text(monkeypatch, capsys, tmp_path):
    from scholar_mcp import api

    monkeypatch.setattr(api, "read", lambda paper_id, **kw: {
        "pages": "1-2", "content": "# Title\nBody", "image_png": b"png"})
    image = tmp_path / "fig.png"
    assert cli.main(["read", "arXiv:1", "--image", str(image)]) == 0
    out = capsys.readouterr().out
    assert out.index("pages:") < out.index("---\n# Title")
    assert image.read_bytes() == b"png"


def test_pi_and_antigravity_use_mcp_servers(home):
    (home / ".gemini/antigravity").mkdir(parents=True)
    legacy = home / ".gemini/antigravity/mcp_config.json"
    legacy.write_text('{"mcpServers": {"existing": {"command": "old"}}}')
    assert install.install("antigravity", SPEC)["status"] == "installed"
    current = home / ".gemini/config/mcp_config.json"
    assert json.loads(current.read_text())["mcpServers"]["scholar"]["command"] == SPEC.command
    assert json.loads(legacy.read_text())["mcpServers"]["existing"]["command"] == "old"
    assert install.CLIENTS["antigravity"].project_path == ".agents/mcp_config.json"
    assert install.install("pi", SPEC)["status"] == "installed"
    assert json.loads((home / ".config/mcp/mcp.json").read_text())["mcpServers"]["scholar"]["command"] == SPEC.command


def test_reinstall_keeps_keys_the_user_added(home):
    install.install("cursor", SPEC)
    path = home / ".cursor/mcp.json"
    data = json.loads(path.read_text())
    data["mcpServers"]["scholar"]["env"]["S2_API_KEY"] = "secret"
    data["mcpServers"]["scholar"]["disabled"] = False
    path.write_text(json.dumps(data))
    assert install.install("cursor", SPEC)["status"] == "unchanged"
    core = install.Spec("/new/scholar-mcp", [], {})
    assert install.install("cursor", core)["status"] == "updated"
    entry = json.loads(path.read_text())["mcpServers"]["scholar"]
    assert entry == {"command": "/new/scholar-mcp", "args": [], "env": {"S2_API_KEY": "secret"}, "disabled": False}


def test_codex_tables_moved_inside_the_block_survive(home):
    path = home / ".codex/config.toml"
    path.parent.mkdir()
    install.install("codex", SPEC)
    text = path.read_text().replace(install.MARK_END, '[projects."/repo"]\ntrust_level = "trusted"\n' + install.MARK_END)
    path.write_text(text + "[[skills]]\nname = 'a'\n")
    assert install.install("codex", install.Spec("/new/scholar-mcp", [], {}))["status"] == "updated"
    parsed = tomllib.loads(path.read_text())
    assert parsed["projects"]["/repo"]["trust_level"] == "trusted"
    assert parsed["skills"] == [{"name": "a"}]
    assert parsed["mcp_servers"]["scholar"]["command"] == "/new/scholar-mcp"
    assert install.uninstall("codex")["status"] == "removed"
    assert tomllib.loads(path.read_text())["projects"]["/repo"]["trust_level"] == "trusted"


def test_project_scope_skips_clients_without_project_files(home):
    report = install.install("claude-desktop", SPEC, project=True)
    assert report["status"] == "skipped"


def test_claude_code_restores_the_previous_entry_when_add_fails(home, monkeypatch):
    (home / ".claude.json").write_text(json.dumps({"mcpServers": {"scholar": {"type": "stdio", "command": "old",
                                                                              "args": []}}}))
    monkeypatch.setattr(install.shutil, "which", lambda name: "/bin/claude")
    calls = []

    def fake(args):
        calls.append(args[:2])
        return (args[1] != "add-json" or len(calls) > 2), "boom"

    monkeypatch.setattr(install, "_claude_cli", fake)
    report = install.install("claude-code", SPEC, force=True)
    assert report["status"] == "failed" and report["restored_previous"] is True
    assert calls == [["mcp", "remove"], ["mcp", "add-json"], ["mcp", "add-json"]]


NEW_CLIENTS = {
    "mimocode": (".config/mimocode/mimocode.json", "mcp", {"type": "local", "enabled": True, "timeout": 600_000}),
    # name: (path under HOME, key, fields the client needs beyond command/args/env)
    "qwen-code": (".qwen/settings.json", "mcpServers", {}),
    "devin": (".config/devin/mcp_config.json", "mcpServers", {}),
    "kimi-code": (".kimi-code/mcp.json", "mcpServers", {"toolTimeoutMs": 600_000}),
    "qoder-cli": (".qoder/settings.json", "mcpServers", {"type": "stdio"}),
    "codebuddy": (".codebuddy/.mcp.json", "mcpServers", {"type": "stdio"}),
    "iflow": (".iflow/settings.json", "mcpServers", {"timeout": 600_000}),
    "minimax": (".minimax/mcp.json", "mcpServers", {"type": "stdio"}),
    "cline": (".cline/data/settings/cline_mcp_settings.json", "mcpServers", {"timeout": 600}),
    "kilo": (".config/kilo/kilo.json", "mcp", {"type": "local"}),
    "amp": (".config/amp/settings.json", "amp.mcpServers", {}),
    "copilot-cli": (".copilot/mcp-config.json", "mcpServers", {"deferTools": "never"}),
    "factory": (".factory/mcp.json", "mcpServers", {"type": "stdio"}),
    "junie": (".junie/mcp/mcp.json", "mcpServers", {}),
    "augment": (".augment/settings.json", "mcpServers", {}),
    "warp": (".warp/.mcp.json", "mcpServers", {}),
    "lmstudio": (".lmstudio/mcp.json", "mcpServers", {}),
}


@pytest.mark.parametrize("name", sorted(NEW_CLIENTS))
def test_each_added_client_gets_its_own_layout_and_round_trips(home, monkeypatch, name):
    for variable in ("KIMI_CODE_HOME", "MINIMAX_DATA_DIR", "CLINE_MCP_SETTINGS_PATH", "COPILOT_HOME"):
        monkeypatch.delenv(variable, raising=False)
    relative, key, fields = NEW_CLIENTS[name]
    path = home / relative
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({key: {"other": {"command": "x"}}, "theme": "dark"}))

    assert install.install(name, SPEC)["status"] == "installed"
    data = json.loads(path.read_text())
    entry = data[key]["scholar"]
    assert data["theme"] == "dark" and data[key]["other"] == {"command": "x"}
    assert {k: entry[k] for k in fields} == fields
    command = entry["command"]
    assert (command[0] if isinstance(command, list) else command) == "/opt/bin/scholar-mcp"
    assert install.install(name, SPEC)["status"] == "unchanged"
    assert install.uninstall(name)["status"] == "removed"
    assert json.loads(path.read_text()) == {key: {"other": {"command": "x"}}, "theme": "dark"}


def test_global_key_file_reaches_every_client_without_printing_values(home, monkeypatch, capsys):
    import io
    monkeypatch.setenv("SCHOLAR_DATA_DIR", str(home / ".scholar-mcp"))
    monkeypatch.delenv("ADS_API_TOKEN", raising=False)
    monkeypatch.setattr("sys.stdin", io.StringIO("token-value\n"))
    assert cli.main(["keys", "set", "ADS_API_TOKEN"]) == 0
    path = config.key_file()
    if os.name == "posix":  # Windows uses ACLs rather than POSIX mode bits.
        assert path.stat().st_mode & 0o777 == 0o600
    assert cli.main(["keys"]) == 0
    out = capsys.readouterr().out
    assert "ADS_API_TOKEN" in out and "token-value" not in out
    config._load_key_file()
    assert os.environ["ADS_API_TOKEN"] == "token-value"
    monkeypatch.setenv("ADS_API_TOKEN", "from-env")
    config._load_key_file()
    assert os.environ["ADS_API_TOKEN"] == "from-env"
    assert cli.main(["keys", "unset", "ADS_API_TOKEN"]) == 0
    assert "ADS_API_TOKEN" not in path.read_text()


def test_keys_set_replaces_an_exported_entry_and_tightens_the_mode(home, monkeypatch):
    import io
    monkeypatch.setenv("SCHOLAR_DATA_DIR", str(home / ".scholar-mcp"))
    path = config.key_file()
    path.parent.mkdir(parents=True)
    path.write_text("# mine\nexport OPENALEX_API_KEY=old\nS2_API_KEY=keep\n")
    path.chmod(0o644)
    monkeypatch.setattr("sys.stdin", io.StringIO("new\n"))
    assert cli.main(["keys", "set", "OPENALEX_API_KEY"]) == 0
    assert path.read_text() == "# mine\nS2_API_KEY=keep\nOPENALEX_API_KEY=new\n"
    if os.name == "posix":
        assert path.stat().st_mode & 0o777 == 0o600
    assert [config.key_name(line) for line in ("export A=1", "B = 2", "# C=3", "", "D")] == ["A", "B", "", "", ""]


def test_module_entrypoint_uses_the_same_cli(capsys, monkeypatch):
    import runpy
    import sys
    from scholar_mcp import __version__
    monkeypatch.setattr(sys, "argv", ["python -m scholar_mcp", "version"])
    with pytest.raises(SystemExit) as stopped:
        runpy.run_module("scholar_mcp", run_name="__main__")
    assert stopped.value.code == 0
    assert capsys.readouterr().out.strip() == __version__


def test_source_check_exits_nonzero_when_the_quota_is_used(monkeypatch, capsys):
    from types import SimpleNamespace
    from scholar_mcp import sources
    from scholar_mcp.sources import pacing

    def limited(*args, **kwargs):
        raise pacing.Paced("quota used")

    source = SimpleNamespace(name="fixture", search=limited)
    monkeypatch.setattr(sources, "capabilities", lambda: [{"name": "fixture", "search": True, "available": True}])
    monkeypatch.setattr(sources, "get", lambda name: source)
    assert cli.main(["sources", "--check"]) == 1
    assert json.loads(capsys.readouterr().out)["sources"][0]["status"] == "paced"
