from __future__ import annotations

from pathlib import Path


def test_compose_project_name_is_stable_across_worktrees():
    from scripts import db_local

    assert db_local.COMPOSE_PROJECT == "pm-intel"
    assert db_local.COMPOSE[:4] == ["docker", "compose", "-p", "pm-intel"]
    assert db_local.COMPOSE[4:] == ["-f", "docker-compose.local.yml"]


def test_db_local_sql_files_include_every_numbered_sql_file():
    from scripts import db_local

    sql_dir = db_local.ROOT / "sql"
    expected = sorted(
        f"sql/{path.name}"
        for path in sql_dir.glob("*.sql")
        if path.name[:3].isdigit()
    )

    assert db_local.SQL_FILES == expected


def test_schema_readiness_sql_fails_closed_on_missing_required_objects():
    from scripts import db_local

    sql = db_local.schema_readiness_sql()

    assert "RAISE EXCEPTION 'missing required schema objects: %'" in sql
    assert "idx_markets_volume" in sql
    assert "idx_markets_venue_volume" in sql
    assert "normalized_trade_dedupe_keys" in sql
    assert "idx_normalized_trade_dedupe_venue_id" in sql
    assert "idx_normalized_trade_dedupe_fingerprint" in sql
    assert "idx_dead_letters_raw_stage_class_dedupe" in sql
    assert "v_alert_summary_24h" in sql
    assert "alert_reviews" in sql
    assert "data_quality_incidents" in sql
    assert "expected.relkinds LIKE '%' || c.relkind::text || '%'" in sql


def test_verify_runs_schema_readiness_before_venue_seed_check(monkeypatch):
    from scripts import db_local

    calls: list[str] = []

    monkeypatch.setattr(db_local, "wait", lambda: calls.append("wait"))
    monkeypatch.setattr(db_local, "psql_command", lambda sql: calls.append(sql))

    db_local.verify()

    assert calls[0] == "wait"
    assert "missing required schema objects" in calls[1]
    assert "idx_markets_volume" in calls[1]
    assert "normalized_trade_dedupe_keys" in calls[1]
    assert "idx_dead_letters_raw_stage_class_dedupe" in calls[1]
    assert "select venue_code from pmfi.venues order by venue_code;" == calls[2]


def test_manifest_paths_exist():
    from scripts import db_local

    missing = [rel for rel in db_local.SQL_FILES if not (Path(db_local.ROOT) / rel).exists()]

    assert missing == []
