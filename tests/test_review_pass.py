from __future__ import annotations

from pathlib import Path

import yaml

from pmfi.commands import review_pass


ROOT = Path(__file__).resolve().parents[1]


def _minimal_graph(*, omit_constraint: str | None = None) -> dict:
    constraints = list(review_pass.REQUIRED_CONSTRAINTS)
    if omit_constraint is not None:
        constraints.remove(omit_constraint)
    return {
        "current_posture": {
            "summary": "current local posture",
            "constraints_intact": constraints,
            "verified_proof": ["focused proof"],
            "next_recommended_focus": {"id": "next", "summary": "next focus"},
            "residual_proof_gaps": ["residual gap"],
        },
        "high_priority_commands": [
            {"command": command, "why": label}
            for command, label in review_pass.REQUIRED_COMMANDS.items()
        ],
        "milestones": [{"id": f"M{i}", "name": f"M{i}"} for i in range(11)],
    }


def _write_minimal_root(root: Path, *, graph: dict | str | None = None) -> None:
    common_doc = (
        "local-only Postgres-first raw-before-derived no trading order placement "
        "default live API calls python scripts\\task.py review-pass "
        "python -m pmfi.cli review-pass"
    )
    for rel in review_pass.REQUIRED_FILES:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if rel == "docs/implementation/02_task_graph.yaml":
            continue
        if rel == "scripts/task.py":
            path.write_text(
                '"review-pass"\n'
                'module("pmfi.cli", "review-pass")\n'
                '"health"\n'
                'module("pmfi.cli", "health", *health_args)\n'
                '"report"\n'
                'module("pmfi.cli", "report", *report_args)\n'
                '"dead-letters"\n'
                'module("pmfi.cli", "dead-letters", *dead_letters_args)\n'
                '"review-packet"\n'
                'module("pmfi.cli", "alerts", "review-packet", *review_packet_args)\n'
                '"refresh-watchlist"\n'
                'module("pmfi.cli", "markets", "refresh-watchlist", *refresh_watchlist_args)\n'
                '"db-replay"\n'
                'module("pmfi.cli", "replay", *db_replay_args)\n',
                encoding="utf-8",
            )
        elif rel == "WORKLOG.md":
            path.write_text(
                "## 2026-06-18 10:55 local - Slice\n\n"
                "### Verification\n\n- focused tests passed\n\n"
                "### Residual risk / next steps\n\n- no residual test risk\n",
                encoding="utf-8",
            )
        else:
            path.write_text(common_doc, encoding="utf-8")

    for rel in [
        "docs/governance/00_operating_model.md",
        "docs/governance/05_stop_gates.md",
        "docs/TESTING.md",
    ]:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(common_doc, encoding="utf-8")

    graph_path = root / "docs/implementation/02_task_graph.yaml"
    graph_path.parent.mkdir(parents=True, exist_ok=True)
    if graph is None:
        graph = _minimal_graph()
    if isinstance(graph, str):
        graph_path.write_text(graph, encoding="utf-8")
    else:
        graph_path.write_text(yaml.safe_dump(graph), encoding="utf-8")


def test_review_pass_passes_on_current_repo():
    report = review_pass.collect_report(ROOT)
    assert report.ok is True, report.failures


def test_review_pass_text_contract_is_windows_safe():
    report = review_pass.collect_report(ROOT)
    text = review_pass.format_text(report)
    assert "PMFI review pass" in text
    assert "Result: PASS" in text
    assert "python scripts\\verify.py" in text
    assert "\x0b" not in text


def test_review_pass_fails_closed_on_malformed_task_graph(tmp_path):
    _write_minimal_root(tmp_path, graph="current_posture: [")

    report = review_pass.collect_report(tmp_path)

    assert report.ok is False
    parse_check = next(check for check in report.checks if check.name == "task graph parses")
    assert parse_check.ok is False
    assert "malformed" in parse_check.detail


def test_review_pass_fails_closed_on_missing_required_constraint(tmp_path):
    graph = _minimal_graph(omit_constraint="local_only_scope")
    _write_minimal_root(tmp_path, graph=graph)

    report = review_pass.collect_report(tmp_path)

    assert report.ok is False
    constraint_check = next(check for check in report.checks if check.name == "required constraints")
    assert constraint_check.ok is False
    assert "local-only scope" in constraint_check.detail


def test_review_pass_fails_closed_on_default_live_verify_marker(tmp_path):
    _write_minimal_root(tmp_path)
    (tmp_path / "scripts" / "verify.py").write_text(
        "PMFI_ENABLE_LIVE=1\nlive-smoke\n",
        encoding="utf-8",
    )

    report = review_pass.collect_report(tmp_path)

    assert report.ok is False
    verify_check = next(
        check for check in report.checks
        if check.name == "default verification stays offline"
    )
    assert verify_check.ok is False
    assert "live-enable environment flag" in verify_check.detail
    assert "live smoke command" in verify_check.detail
