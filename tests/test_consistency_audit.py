from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_consistency_audit():
    import importlib.util

    spec = importlib.util.spec_from_file_location("consistency_audit", ROOT / "scripts" / "consistency_audit.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_consistency_audit_passes():
    module = _load_consistency_audit()
    assert module.main() == 0


def test_consistency_audit_skips_generated_reports():
    module = _load_consistency_audit()

    assert module._skip(ROOT / "reports" / "handoff" / "snapshot.md") is True


def test_consistency_audit_text_files_are_tracked_and_skip_coordination_paths(tmp_path, monkeypatch):
    module = _load_consistency_audit()
    tracked = tmp_path / "src" / "actual.py"
    untracked_state = tmp_path / "state" / "agent-inbox" / "for-claude.md"
    untracked_worktree = tmp_path / "worktrees" / "lane" / "note.md"
    tracked.parent.mkdir(parents=True)
    untracked_state.parent.mkdir(parents=True)
    untracked_worktree.parent.mkdir(parents=True)
    tracked.write_text("print('tracked')\n", encoding="utf-8")
    untracked_state.write_text("coordination " + "ho" + "ok", encoding="utf-8")
    untracked_worktree.write_text("scratch " + "ho" + "ok", encoding="utf-8")

    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "_tracked_files", lambda: [tracked], raising=False)

    assert module.text_files() == [tracked]
