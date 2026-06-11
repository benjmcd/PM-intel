from __future__ import annotations

def test_init_records_migrations_only_after_ledger_exists(tmp_path, monkeypatch):
    import scripts.db_local as db_local

    sql_dir = tmp_path / "sql"
    sql_dir.mkdir()
    (sql_dir / "001_init.sql").write_text("CREATE SCHEMA pmfi;", encoding="utf-8")
    (sql_dir / "012_schema_migrations.sql").write_text(
        "CREATE TABLE pmfi.schema_migrations ();", encoding="utf-8"
    )
    (sql_dir / "013_extra.sql").write_text("SELECT 13;", encoding="utf-8")

    events: list[tuple[str, str]] = []

    def fake_psql_stdin(sql_text: str) -> None:
        events.append(("apply", sql_text))

    def fake_psql_command(sql_text: str) -> None:
        events.append(("record", sql_text))

    monkeypatch.setattr(db_local, "ROOT", tmp_path)
    monkeypatch.setattr(
        db_local,
        "SQL_FILES",
        [
            "sql/001_init.sql",
            "sql/012_schema_migrations.sql",
            "sql/013_extra.sql",
        ],
    )
    monkeypatch.setattr(db_local, "wait", lambda: None)
    monkeypatch.setattr(db_local, "psql_stdin", fake_psql_stdin)
    monkeypatch.setattr(db_local, "psql_command", fake_psql_command)

    db_local.init()

    assert [event for event, _ in events] == [
        "apply",
        "apply",
        "record",
        "record",
        "apply",
        "record",
    ]

    record_sql = [sql for event, sql in events if event == "record"]
    assert any("001_init.sql" in sql for sql in record_sql)
    assert any("012_schema_migrations.sql" in sql for sql in record_sql)
    assert any("013_extra.sql" in sql for sql in record_sql)
