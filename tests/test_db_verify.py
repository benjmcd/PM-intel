from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace


class _Acquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeVerifyPool:
    def __init__(
        self,
        *,
        missing_relation: str | None = None,
        missing_column: tuple[str, str] | None = None,
        missing_index: str | None = None,
        missing_constraint: str | None = None,
        missing_venue: str | None = None,
        missing_partition: str | None = None,
        parent_not_partitioned: str | None = None,
        unattached_partition: str | None = None,
    ):
        from pmfi.db.verify import (
            REQUIRED_COLUMNS,
            REQUIRED_CONSTRAINTS,
            REQUIRED_INDEXES,
            REQUIRED_RELATIONS,
            REQUIRED_SEED_VENUES,
            REQUIRED_VIEWS,
            partition_names_for_month,
        )

        names = partition_names_for_month(datetime(2026, 6, 17, tzinfo=timezone.utc))
        self.sql: list[str] = []
        self.closed = False
        self.relations = {f"pmfi.{name}" for name in REQUIRED_RELATIONS | REQUIRED_VIEWS}
        self.relations.update({f"pmfi.{name}" for name in names.defaults})
        self.relations.update({f"pmfi.{name}" for name in names.current})
        if missing_relation:
            self.relations.discard(f"pmfi.{missing_relation}")
        if missing_partition:
            self.relations.discard(f"pmfi.{missing_partition}")

        self.partitioned_parents = set(names.parents)
        if parent_not_partitioned:
            self.partitioned_parents.discard(parent_not_partitioned)
        self.attached_children = {
            parent: {f"{parent}_default"} | {child for child in names.current if child.startswith(f"{parent}_")}
            for parent in names.parents
        }
        if missing_partition:
            for children in self.attached_children.values():
                children.discard(missing_partition)
        if unattached_partition:
            for children in self.attached_children.values():
                children.discard(unattached_partition)

        self.columns = {
            (table, column)
            for table, columns in REQUIRED_COLUMNS.items()
            for column in columns
        }
        if missing_column:
            self.columns.discard(missing_column)

        self.indexes = set(REQUIRED_INDEXES)
        if missing_index:
            self.indexes.discard(missing_index)

        self.constraints = set(REQUIRED_CONSTRAINTS)
        if missing_constraint:
            self.constraints.discard(missing_constraint)

        self.venues = set(REQUIRED_SEED_VENUES)
        if missing_venue:
            self.venues.discard(missing_venue)

    def acquire(self):
        return _Acquire(self)

    async def fetchval(self, sql, *args):
        self.sql.append(sql)
        normalized = " ".join(sql.split()).lower()
        if normalized.startswith("select to_regclass"):
            return args[0] if args[0] in self.relations else None
        raise AssertionError(f"unexpected fetchval SQL: {sql}")

    async def fetch(self, sql, *args):
        self.sql.append(sql)
        normalized = " ".join(sql.split()).lower()
        if "from information_schema.columns" in normalized:
            return [
                {"table_name": table, "column_name": column}
                for table, column in sorted(self.columns)
            ]
        if "from pg_indexes" in normalized:
            return [{"indexname": name} for name in sorted(self.indexes)]
        if "from pg_constraint" in normalized:
            return [{"conname": name} for name in sorted(self.constraints)]
        if "from pg_partitioned_table" in normalized:
            return [{"relname": name} for name in sorted(self.partitioned_parents)]
        if "from pg_inherits" in normalized:
            return [
                {"parent": parent, "child": child}
                for parent, children in sorted(self.attached_children.items())
                for child in sorted(children)
            ]
        if "from venues" in normalized:
            return [{"venue_code": name} for name in sorted(self.venues)]
        raise AssertionError(f"unexpected fetch SQL: {sql}")

    async def close(self):
        self.closed = True


def test_verify_database_integrity_passes_with_required_schema():
    from pmfi.db.verify import verify_database_integrity

    pool = _FakeVerifyPool()
    result = asyncio.run(
        verify_database_integrity(
            pool,
            now=datetime(2026, 6, 17, tzinfo=timezone.utc),
        )
    )

    assert result.ok is True
    assert result.status == "ready"
    assert all(check.status == "pass" for check in result.checks)
    assert all(sql.lstrip().lower().startswith("select") for sql in pool.sql)


def test_verify_database_integrity_fails_closed_on_missing_core_table():
    from pmfi.db.verify import verify_database_integrity

    pool = _FakeVerifyPool(missing_relation="raw_events")
    result = asyncio.run(
        verify_database_integrity(
            pool,
            now=datetime(2026, 6, 17, tzinfo=timezone.utc),
        )
    )

    assert result.ok is False
    assert result.status == "blocked"
    rel_check = next(check for check in result.checks if check.name == "relations")
    assert rel_check.status == "fail"
    assert "raw_events" in rel_check.message


def test_verify_database_integrity_keeps_details_when_venues_table_is_missing():
    from pmfi.db.verify import verify_database_integrity

    pool = _FakeVerifyPool(missing_relation="venues")
    result = asyncio.run(
        verify_database_integrity(
            pool,
            now=datetime(2026, 6, 17, tzinfo=timezone.utc),
        )
    )

    assert result.ok is False
    assert result.status == "blocked"
    assert not any(check.name == "db" for check in result.checks)
    rel_check = next(check for check in result.checks if check.name == "relations")
    seed_check = next(check for check in result.checks if check.name == "seed_venues")
    assert rel_check.status == "fail"
    assert seed_check.status == "fail"
    assert "venues table is missing" in seed_check.message


def test_verify_database_integrity_detects_startup_maintenance_drift():
    from pmfi.db.verify import verify_database_integrity

    pool = _FakeVerifyPool(missing_index="idx_normalized_trades_venue_trade_id")
    result = asyncio.run(
        verify_database_integrity(
            pool,
            now=datetime(2026, 6, 17, tzinfo=timezone.utc),
        )
    )

    assert result.ok is False
    drift = next(check for check in result.checks if check.name == "startup_maintenance")
    assert drift.status == "fail"
    assert "idx_normalized_trades_venue_trade_id" in drift.message


def test_verify_database_integrity_detects_missing_required_constraint():
    from pmfi.db.verify import verify_database_integrity

    pool = _FakeVerifyPool(missing_constraint="alerts_dedupe_key_key")
    result = asyncio.run(
        verify_database_integrity(
            pool,
            now=datetime(2026, 6, 17, tzinfo=timezone.utc),
        )
    )

    assert result.ok is False
    constraints = next(check for check in result.checks if check.name == "constraints")
    assert constraints.status == "fail"
    assert "alerts_dedupe_key_key" in constraints.message


def test_verify_database_integrity_detects_missing_baseline_upsert_constraint():
    from pmfi.db.verify import verify_database_integrity

    pool = _FakeVerifyPool(missing_constraint="market_baselines_market_scope_unique")
    result = asyncio.run(
        verify_database_integrity(
            pool,
            now=datetime(2026, 6, 17, tzinfo=timezone.utc),
        )
    )

    assert result.ok is False
    constraints = next(check for check in result.checks if check.name == "constraints")
    startup = next(check for check in result.checks if check.name == "startup_maintenance")
    assert constraints.status == "fail"
    assert startup.status == "fail"
    assert "market_baselines_market_scope_unique" in constraints.message


def test_verify_database_integrity_detects_missing_current_partition():
    from pmfi.db.verify import verify_database_integrity

    pool = _FakeVerifyPool(missing_partition="normalized_trades_2026_06")
    result = asyncio.run(
        verify_database_integrity(
            pool,
            now=datetime(2026, 6, 17, tzinfo=timezone.utc),
        )
    )

    assert result.ok is False
    partitions = next(check for check in result.checks if check.name == "partitions")
    assert partitions.status == "fail"
    assert "normalized_trades_2026_06" in partitions.message


def test_verify_database_integrity_detects_unattached_partition():
    from pmfi.db.verify import verify_database_integrity

    pool = _FakeVerifyPool(unattached_partition="normalized_trades_2026_06")
    result = asyncio.run(
        verify_database_integrity(
            pool,
            now=datetime(2026, 6, 17, tzinfo=timezone.utc),
        )
    )

    assert result.ok is False
    partitions = next(check for check in result.checks if check.name == "partitions")
    assert partitions.status == "fail"
    assert "normalized_trades->normalized_trades_2026_06" in partitions.message


def test_verify_database_integrity_detects_parent_not_partitioned():
    from pmfi.db.verify import verify_database_integrity

    pool = _FakeVerifyPool(parent_not_partitioned="raw_events")
    result = asyncio.run(
        verify_database_integrity(
            pool,
            now=datetime(2026, 6, 17, tzinfo=timezone.utc),
        )
    )

    assert result.ok is False
    partitions = next(check for check in result.checks if check.name == "partitions")
    assert partitions.status == "fail"
    assert "raw_events" in partitions.details["not_partitioned"]


def test_partition_names_cover_startup_maintenance_horizon():
    from pmfi.db.verify import partition_names_for_month

    names = partition_names_for_month(datetime(2026, 6, 17, tzinfo=timezone.utc))
    assert "raw_events_2026_06" in names.current
    assert "raw_events_2026_09" in names.current
    assert "normalized_trades_2026_09" in names.current


def test_db_verify_cli_json_uses_integrity_result(monkeypatch, capsys):
    from pmfi.cli import main
    from pmfi.db.verify import IntegrityCheck, IntegrityResult

    pool = _FakeVerifyPool()
    calls = {"create": 0, "close": 0, "verify": 0}
    monkeypatch.setattr(
        "pmfi.config.load_config",
        lambda: SimpleNamespace(database=SimpleNamespace(url="postgresql://local/pmfi")),
    )

    async def create_pool(*args, **kwargs):
        calls["create"] += 1
        return pool

    async def close_pool(pool_arg):
        calls["close"] += 1
        await pool_arg.close()

    async def verify(pool_arg):
        calls["verify"] += 1
        assert pool_arg is pool
        return IntegrityResult(
            ok=True,
            status="ready",
            checks=[IntegrityCheck("relations", "pass", "all relations present")],
        )

    monkeypatch.setattr("pmfi.db.create_pool", create_pool)
    monkeypatch.setattr("pmfi.db.close_pool", close_pool)
    monkeypatch.setattr("pmfi.db.verify.verify_database_integrity", verify)

    rc = main(["db-verify", "--format", "json"])
    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert payload["ok"] is True
    assert payload["checks"][0]["name"] == "relations"
    assert calls == {"create": 1, "close": 1, "verify": 1}
    assert pool.closed is True
