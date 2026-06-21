from pathlib import Path
import json
import subprocess

ROOT = Path(__file__).resolve().parents[1]
_SKIP = {".git", ".pytest_cache", "__pycache__", ".venv", ".omc", "reports", "state", "worktrees"}


def _skip_path(p: Path) -> bool:
    try:
        parts = p.relative_to(ROOT).parts
    except ValueError:
        parts = p.parts
    return any(part in _SKIP or part.endswith(".egg-info") for part in parts)


def _tracked_files() -> list[Path]:
    result = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "-z"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    rels = result.stdout.decode("utf-8", errors="replace").split("\0")
    return [ROOT / rel for rel in rels if rel and (ROOT / rel).is_file()]


def test_windows_command_wrappers_exist():
    assert (ROOT / "pmfi.cmd").exists()
    assert (ROOT / "pmfi.ps1").exists()
    assert (ROOT / "scripts" / "task.py").exists()
    assert (ROOT / "scripts" / "db_local.py").exists()


def test_generated_reports_are_not_scanned_for_source_contracts():
    assert _skip_path(ROOT / "reports" / "handoff" / "snapshot.md") is True


def test_no_non_windows_wrapper_files():
    forbidden_suffix = "." + "sh"
    forbidden_name = "Make" + "file"
    offenders = [p.relative_to(ROOT).as_posix() for p in _tracked_files() if p.is_file() and not _skip_path(p) and (p.name.endswith(forbidden_suffix) or p.name == forbidden_name)]
    assert offenders == []


def test_claude_settings_have_no_automatic_command_triggers():
    settings = json.loads((ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert settings == {}


def _legacy_term_offenders(banned: list[str] | None = None) -> list[str]:
    banned = banned or [
        "```" + "bas" + "h",
        "make " + "verify",
        "make " + "db-",
        "./" + "scripts/",
        "ubuntu-" + "latest",
        "ho" + "ok",
    ]
    exts = {".md", ".py", ".toml", ".yaml", ".yml", ".json", ".rules", ".ps1", ".cmd"}
    offenders: list[str] = []
    for path in _tracked_files():
        if not path.is_file() or _skip_path(path) or path.suffix not in exts:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for needle in banned:
            if needle in text:
                offenders.append(f"{path.relative_to(ROOT).as_posix()}::{needle}")
    return offenders


def test_no_legacy_terms_in_text_files():
    offenders = _legacy_term_offenders()
    assert offenders == []


def test_legacy_term_scan_uses_tracked_source_paths_only(tmp_path, monkeypatch):
    tracked = tmp_path / "src" / "actual.py"
    untracked_state = tmp_path / "state" / "agent-inbox" / "for-claude.md"
    untracked_worktree = tmp_path / "worktrees" / "lane" / "note.md"
    tracked.parent.mkdir(parents=True)
    untracked_state.parent.mkdir(parents=True)
    untracked_worktree.parent.mkdir(parents=True)
    tracked.write_text("# source " + "ho" + "ok", encoding="utf-8")
    untracked_state.write_text("# coordination " + "ho" + "ok", encoding="utf-8")
    untracked_worktree.write_text("# scratch " + "ho" + "ok", encoding="utf-8")

    monkeypatch.setattr(__import__(__name__), "ROOT", tmp_path)
    monkeypatch.setattr(__import__(__name__), "_tracked_files", lambda: [tracked], raising=False)

    assert _legacy_term_offenders(["ho" + "ok"]) == ["src/actual.py::" + "ho" + "ok"]


def test_no_remote_workflow_directory():
    assert not (ROOT / ".github").exists()


def test_no_hosted_ci_workflow_required_for_local_only_phase():
    assert not (ROOT / ".github" / "workflows" / "ci.yml").exists()


def test_claude_subagents_do_not_request_non_windows_shell_tool():
    offenders = []
    for path in (ROOT / ".claude" / "agents").glob("*.md"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "tools: Read, Grep, Glob, " + "Ba" + "sh" in text or "tools: Read, Grep, " + "Ba" + "sh" in text:
            offenders.append(path.relative_to(ROOT).as_posix())
    assert offenders == []


def test_governance_document_numbers_are_unique():
    seen: dict[str, list[str]] = {}
    for path in (ROOT / "docs" / "governance").glob("[0-9][0-9]_*.md"):
        seen.setdefault(path.name[:2], []).append(path.name)
    duplicates = {prefix: names for prefix, names in seen.items() if len(names) > 1}
    assert duplicates == {}


def test_repo_does_not_reintroduce_reserved_db_port():
    exts = {".md", ".py", ".toml", ".yaml", ".yml", ".json", ".sql", ".cmd", ".ps1", ".rules", ".txt", ".example"}
    reserved_port = "54" + "32"
    offenders = []
    for path in _tracked_files():
        if not path.is_file() or path.suffix not in exts:
            continue
        # tests/fixtures/live/ is a gitignored transient-capture directory (like .venv)
        if any(part in {"__pycache__", ".pytest_cache", ".git", ".venv"} for part in path.parts):
            continue
        if "fixtures" in path.parts and "live" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if reserved_port in text:
            offenders.append(path.relative_to(ROOT).as_posix())
    assert offenders == []
