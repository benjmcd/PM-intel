from pathlib import Path
import json
import tomllib


ROOT = Path(__file__).resolve().parents[1]


def test_claude_imports_agents_without_duplication():
    claude = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    assert "@AGENTS.md" in claude
    assert len(claude.splitlines()) <= 80
    assert "Mandatory first actions" not in claude


def test_agent_context_files_stay_thin():
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert len(agents.splitlines()) <= 200


def test_tool_configs_parse():
    tomllib.loads((ROOT / ".codex/config.toml").read_text(encoding="utf-8"))
    json.loads((ROOT / ".claude/settings.json").read_text(encoding="utf-8"))


def test_agent_context_check_passes(monkeypatch):
    import importlib.util

    spec = importlib.util.spec_from_file_location("agent_context_check", ROOT / "scripts" / "agent_context_check.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.main(["--quiet"]) == 0
