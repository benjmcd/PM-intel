from pathlib import Path
import subprocess
import sys

import yaml

ROOT = Path(__file__).resolve().parents[1]


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_governance_number_prefixes_are_unique():
    prefixes: dict[str, list[str]] = {}
    for path in (ROOT / "docs" / "governance").glob("*.md"):
        prefix = path.name.split("_", 1)[0]
        if prefix[:2].isdigit():
            prefixes.setdefault(prefix, []).append(path.name)
    assert {k: v for k, v in prefixes.items() if len(v) > 1} == {}


def test_claude_prompt_uses_shared_start_file_not_codex_start_file():
    text = read("claude_prompts/00_initial_claude_prompt.md")
    assert "AGENT_START_HERE.md" in text
    assert "CODEX_START_HERE.md" not in text


def test_no_obsolete_legacy_command_runner_wording():
    offenders = []
    for path in ROOT.rglob("*.md"):
        if any(part in {"__pycache__", ".pytest_cache"} for part in path.parts):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "Mak" + "e target" in text or "Mak" + "efile" in text:
            offenders.append(path.relative_to(ROOT).as_posix())
    assert offenders == []


def test_task_graph_uses_windows_task_wrapper_for_fixture_replay():
    graph = yaml.safe_load(read("docs/implementation/02_task_graph.yaml"))
    gates = [m.get("gate", "") for m in graph["milestones"]]
    assert "python scripts\\task.py fixture-replay" in gates
    assert all("python -m pmfi.cli " + "replay-fixtures" not in gate for gate in gates)


def test_task_graph_names_executable_operator_smoke_gate():
    graph = yaml.safe_load(read("docs/implementation/02_task_graph.yaml"))
    gates = [m.get("gate", "") for m in graph["milestones"]]
    assert any("python scripts\\task.py operator-smoke" in gate for gate in gates)


def test_status_and_task_graph_name_executable_db_smoke_gate():
    command = r"python scripts\task.py db-smoke"
    graph = yaml.safe_load(read("docs/implementation/02_task_graph.yaml"))
    gates = [m.get("gate", "") for m in graph["milestones"]]
    status = subprocess.run(
        [sys.executable, "scripts/repo_status.py"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    task_router = read("scripts/task.py")

    assert any(command in gate for gate in gates)
    assert command in status
    assert '"db-smoke"' in task_router


def test_status_and_task_router_name_executable_setup_smoke_gate():
    command = r"python scripts\task.py setup-smoke"
    status = subprocess.run(
        [sys.executable, "scripts/repo_status.py"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    task_router = read("scripts/task.py")

    assert command in status
    assert '"setup-smoke"' in task_router
    assert "scripts/setup_smoke.py" in task_router


def test_status_and_task_router_name_executable_lifecycle_smoke_gate():
    command = r"python scripts\task.py lifecycle-smoke"
    status = subprocess.run(
        [sys.executable, "scripts/repo_status.py"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    task_router = read("scripts/task.py")

    assert command in status
    assert '"lifecycle-smoke"' in task_router
    assert "scripts/lifecycle_smoke.py" in task_router


def test_status_and_task_router_name_executable_baseline_smoke_gate():
    command = r"python scripts\task.py baseline-smoke"
    status = subprocess.run(
        [sys.executable, "scripts/repo_status.py"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    task_router = read("scripts/task.py")

    assert command in status
    assert '"baseline-smoke"' in task_router
    assert "scripts/baseline_smoke.py" in task_router


def test_status_and_task_router_name_executable_scope_smoke_gate():
    command = r"python scripts\task.py scope-smoke"
    status = subprocess.run(
        [sys.executable, "scripts/repo_status.py"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    task_router = read("scripts/task.py")

    assert command in status
    assert '"scope-smoke"' in task_router
    assert "scripts/scope_smoke.py" in task_router


def test_alignment_audit_doc_names_canonical_contracts():
    text = read("docs/governance/10_alignment_audit.md")
    for phrase in [
        "Windows-native",
        "local-only",
        "Postgres",
        "adaptive milestone map",
        "Default verification",
    ]:
        assert phrase in text



def test_task_graph_contains_orthogonal_decision_policy():
    graph = yaml.safe_load(read("docs/implementation/02_task_graph.yaml"))
    policy = graph["advancement_policy"]
    assert policy["orthogonal_reasoning_allowed"] is True
    assert policy["compact_decision_debate_required_for_unclear_architecture"] is True
    assert policy["material_progress_over_ceremony"] is True
    assert "decision_loop" in policy
    assert graph["decision_policy"]["canonical_doc"] == "docs/governance/12_decision_methods.md"
