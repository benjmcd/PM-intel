from __future__ import annotations

from scripts import db_local


def test_scratch_sweep_sql_drops_only_inactive_testiso_databases() -> None:
    sql = db_local.scratch_sweep_sql()

    assert "LIKE 'pmfi_testiso_%'" in sql
    assert "pg_stat_activity" in sql
    assert "a.datname = d.datname" in sql
    assert "NOT EXISTS" in sql
    assert "\\gexec" in sql
    assert "FORCE" not in sql.upper()


def test_scratch_sweep_dry_run_sql_lists_candidates_and_skipped_without_drop() -> None:
    sql = db_local.scratch_sweep_dry_run_sql()

    assert "LIKE 'pmfi_testiso_%'" in sql
    assert "pg_stat_activity" in sql
    assert "SKIPPED_ACTIVE" in sql
    assert "CANDIDATE" in sql
    assert "DROP DATABASE" not in sql.upper()


def test_sweep_scratch_defaults_to_dry_run(monkeypatch) -> None:
    calls: list[tuple[str, str] | str] = []
    monkeypatch.setattr(db_local, "wait", lambda: calls.append("wait"))
    monkeypatch.setattr(db_local, "psql_command", lambda sql: calls.append(("command", sql)))
    monkeypatch.setattr(db_local, "psql_stdin", lambda sql: calls.append(("stdin", sql)))

    assert db_local.main(["sweep-scratch"]) == 0

    assert calls == ["wait", ("command", db_local.scratch_sweep_dry_run_sql())]


def test_sweep_scratch_apply_lists_then_drops(monkeypatch) -> None:
    calls: list[tuple[str, str] | str] = []
    monkeypatch.setattr(db_local, "wait", lambda: calls.append("wait"))
    monkeypatch.setattr(db_local, "psql_command", lambda sql: calls.append(("command", sql)))
    monkeypatch.setattr(db_local, "psql_stdin", lambda sql: calls.append(("stdin", sql)))

    assert db_local.main(["sweep-scratch", "--apply"]) == 0

    assert calls == [
        "wait",
        ("command", db_local.scratch_sweep_dry_run_sql()),
        ("stdin", db_local.scratch_sweep_sql()),
    ]
