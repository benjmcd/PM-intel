from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]
_SKIP = {".git", ".pytest_cache", "__pycache__", ".venv", "reports"}


def _skip_path(p: Path) -> bool:
    return any(part in _SKIP or part.endswith(".egg-info") for part in p.parts)


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
    offenders = [p.relative_to(ROOT).as_posix() for p in ROOT.rglob("*") if p.is_file() and not _skip_path(p) and (p.name.endswith(forbidden_suffix) or p.name == forbidden_name)]
    assert offenders == []


def test_claude_settings_have_no_automatic_command_triggers():
    settings = json.loads((ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert settings == {}


def test_no_legacy_terms_in_text_files():
    banned = [
        "```" + "bas" + "h",
        "make " + "verify",
        "make " + "db-",
        "./" + "scripts/",
        "ubuntu-" + "latest",
        "ho" + "ok",
    ]
    exts = {".md", ".py", ".toml", ".yaml", ".yml", ".json", ".rules", ".ps1", ".cmd"}
    offenders: list[str] = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or _skip_path(path) or path.suffix not in exts:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for needle in banned:
            if needle in text:
                offenders.append(f"{path.relative_to(ROOT).as_posix()}::{needle}")
    assert offenders == []


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
    for path in ROOT.rglob("*"):
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
