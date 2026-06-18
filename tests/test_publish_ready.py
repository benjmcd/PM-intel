from __future__ import annotations

import subprocess
from pathlib import Path

from scripts import publish_ready, task


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        text=True,
        capture_output=True,
        check=True,
    )


def _commit(root: Path, message: str) -> str:
    _git(root, "add", ".")
    _git(root, "commit", "-m", message)
    return _git(root, "rev-parse", "HEAD").stdout.strip()


def _repo_with_origin(tmp_path: Path) -> tuple[Path, Path]:
    remote = tmp_path / "origin.git"
    work = tmp_path / "work"
    _git(tmp_path, "init", "--bare", str(remote))
    _git(tmp_path, "clone", str(remote), str(work))
    _git(work, "config", "user.email", "pmfi@example.test")
    _git(work, "config", "user.name", "PMFI Test")
    (work / "README.md").write_text("base\n", encoding="utf-8")
    _commit(work, "Initial commit")
    _git(work, "branch", "-M", "main")
    _git(work, "push", "-u", "origin", "main")
    return work, remote


def test_collect_report_accepts_clean_branch_ahead_of_upstream(tmp_path):
    work, _remote = _repo_with_origin(tmp_path)
    (work / "slice.txt").write_text("local slice\n", encoding="utf-8")
    head = _commit(work, "Add local slice")

    report = publish_ready.collect_report(work)

    assert report.ok is True
    assert report.branch == "main"
    assert report.head == head
    assert report.upstream == "origin/main"
    assert report.ahead == 1
    assert report.behind == 0
    assert report.dirty_entries == []
    assert report.changed_files == ["A\tslice.txt"]
    assert report.failures == []
    assert report.attribution_hits == []


def test_collect_report_fails_closed_without_upstream(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    _git(work, "init", "-b", "main")
    _git(work, "config", "user.email", "pmfi@example.test")
    _git(work, "config", "user.name", "PMFI Test")
    (work / "README.md").write_text("base\n", encoding="utf-8")
    _commit(work, "Initial commit")

    report = publish_ready.collect_report(work)

    assert report.ok is False
    assert any("No upstream branch is configured" in failure for failure in report.failures)


def test_collect_report_fails_closed_on_dirty_worktree(tmp_path):
    work, _remote = _repo_with_origin(tmp_path)
    (work / "README.md").write_text("dirty\n", encoding="utf-8")

    report = publish_ready.collect_report(work)

    assert report.ok is False
    assert report.dirty_entries == [" M README.md"]
    assert any("Worktree is not clean" in failure for failure in report.failures)


def test_collect_report_fails_when_upstream_advanced_past_head(tmp_path):
    work, remote = _repo_with_origin(tmp_path)
    other = tmp_path / "other"
    _git(tmp_path, "clone", "--branch", "main", str(remote), str(other))
    _git(other, "config", "user.email", "pmfi@example.test")
    _git(other, "config", "user.name", "PMFI Test")
    (other / "remote.txt").write_text("remote\n", encoding="utf-8")
    _commit(other, "Remote update")
    _git(other, "push", "origin", "main")
    _git(work, "fetch", "origin", "main")

    report = publish_ready.collect_report(work)

    assert report.ok is False
    assert report.behind == 1
    assert any("behind upstream" in failure for failure in report.failures)
    assert any("not an ancestor" in failure for failure in report.failures)


def test_collect_report_fetch_option_detects_stale_remote_tracking_ref(tmp_path):
    work, remote = _repo_with_origin(tmp_path)
    (work / "slice.txt").write_text("local slice\n", encoding="utf-8")
    _commit(work, "Add local slice")
    other = tmp_path / "other"
    _git(tmp_path, "clone", "--branch", "main", str(remote), str(other))
    _git(other, "config", "user.email", "pmfi@example.test")
    _git(other, "config", "user.name", "PMFI Test")
    (other / "remote.txt").write_text("remote\n", encoding="utf-8")
    _commit(other, "Remote update")
    _git(other, "push", "origin", "main")

    stale_report = publish_ready.collect_report(work)
    fresh_report = publish_ready.collect_report(work, fetch=True)

    assert stale_report.ok is True
    assert stale_report.remote_freshness.startswith("not checked")
    assert fresh_report.ok is False
    assert fresh_report.remote_freshness == "checked with git fetch --prune origin"
    assert fresh_report.behind == 1
    assert any("behind upstream" in failure for failure in fresh_report.failures)


def test_collect_report_scans_commit_messages_and_diff_for_generated_footers(tmp_path):
    work, _remote = _repo_with_origin(tmp_path)
    (work / "slice.txt").write_text("local slice\n", encoding="utf-8")
    _commit(work, "Add local slice\n\n" + "Co-" + "authored-by: Tool <tool@example.test>")

    report = publish_ready.collect_report(work)

    assert report.ok is False
    assert any(hit.source == "commit" for hit in report.attribution_hits)
    assert any("Attribution or generated footer string found" in failure for failure in report.failures)


def test_task_routes_publish_ready(monkeypatch):
    routed: list[tuple[str, tuple[str, ...]]] = []

    def fake_python_script(script: str, *args: str) -> None:
        routed.append((script, args))

    monkeypatch.setattr(task, "python_script", fake_python_script)

    rc = task.main(["publish-ready"])

    assert rc == 0
    assert routed == [("scripts/publish_ready.py", ())]


def test_task_routes_publish_ready_fetch(monkeypatch):
    routed: list[tuple[str, tuple[str, ...]]] = []

    def fake_python_script(script: str, *args: str) -> None:
        routed.append((script, args))

    monkeypatch.setattr(task, "python_script", fake_python_script)

    rc = task.main(["publish-ready", "--fetch"])

    assert rc == 0
    assert routed == [("scripts/publish_ready.py", ("--fetch",))]
