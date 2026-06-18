from __future__ import annotations


def test_task_outcome_audit_passes_through_cli_flags(monkeypatch):
    from scripts import task

    calls: list[tuple] = []

    def fake_module(*args, env=None):
        calls.append(args)

    monkeypatch.setattr(task, "module", fake_module)

    rc = task.main([
        "outcome-audit",
        "--since",
        "2026-06-18T16:23:02+00:00",
        "--until",
        "2026-06-18T16:33:04+00:00",
        "--strict",
        "--format",
        "json",
        "--rule",
        "directional_cluster_v1",
        "--rule",
        "momentum_v1",
        "--limit",
        "20",
    ])

    assert rc == 0
    assert calls == [(
        "pmfi.cli",
        "alerts",
        "outcome-audit",
        "--since",
        "2026-06-18T16:23:02+00:00",
        "--until",
        "2026-06-18T16:33:04+00:00",
        "--strict",
        "--format",
        "json",
        "--rule",
        "directional_cluster_v1",
        "--rule",
        "momentum_v1",
        "--limit",
        "20",
    )]
