from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from pmfi.db.migrations import PARTITIONED_TABLES, _months_ahead


REQUIRED_RELATIONS = {
    "alert_deliveries",
    "alert_reviews",
    "alert_rules",
    "alerts",
    "data_quality_incidents",
    "dead_letters",
    "event_dedupe_keys",
    "feed_cursors",
    "ingestion_connections",
    "job_queue",
    "market_aliases",
    "market_baselines",
    "market_outcomes",
    "market_snapshots",
    "markets",
    "metric_windows",
    "normalized_trades",
    "orderbook_levels",
    "orderbook_snapshots",
    "raw_events",
    "system_heartbeats",
    "venues",
}

REQUIRED_VIEWS = {
    "v_alert_summary_24h",
    "v_open_data_quality_incidents",
    "v_recent_large_trades",
    "v_recent_raw_event_counts",
}

REQUIRED_COLUMNS = {
    "alerts": {
        "alert_id",
        "dedupe_key",
        "rule_key",
        "venue_code",
        "severity",
        "evidence",
        "fired_at",
    },
    "market_outcomes": {"market_id", "venue_code", "venue_outcome_id", "outcome_key"},
    "markets": {"market_id", "venue_code", "venue_market_id", "watched", "raw_metadata"},
    "metric_windows": {
        "metric_window_id",
        "market_id",
        "outcome_key",
        "window_start",
        "window_seconds",
    },
    "normalized_trades": {
        "trade_id",
        "raw_event_id",
        "venue_code",
        "venue_trade_id",
        "market_id",
        "outcome_key",
        "received_at",
        "source_payload",
    },
    "orderbook_snapshots": {"orderbook_snapshot_id", "venue_code", "market_id", "captured_at", "payload"},
    "raw_events": {"raw_event_id", "venue_code", "received_at", "payload", "payload_hash"},
    "venues": {"venue_code", "display_name", "enabled"},
}

REQUIRED_INDEXES = {
    "idx_alerts_fired",
    "idx_alerts_market",
    "idx_alerts_severity",
    "idx_data_quality_open",
    "idx_dead_letters_unresolved",
    "idx_job_queue_ready",
    "idx_markets_watched",
    "idx_normalized_trades_market_received",
    "idx_normalized_trades_size",
    "idx_normalized_trades_venue_received",
    "idx_normalized_trades_venue_trade_id",
    "idx_raw_events_market_received",
    "idx_raw_events_payload_gin",
    "idx_raw_events_venue_received",
}

REQUIRED_CONSTRAINTS = {
    "alert_deliveries_alert_id_fkey",
    "alert_deliveries_pkey",
    "alert_reviews_alert_id_fkey",
    "alert_reviews_pkey",
    "alert_rules_pkey",
    "alert_rules_rule_key_rule_version_key",
    "alerts_confidence_check",
    "alerts_dedupe_key_key",
    "alerts_market_id_fkey",
    "alerts_pkey",
    "alerts_severity_check",
    "alerts_venue_code_fkey",
    "data_quality_incidents_market_id_fkey",
    "data_quality_incidents_pkey",
    "data_quality_incidents_severity_check",
    "data_quality_incidents_venue_code_fkey",
    "dead_letters_pkey",
    "dead_letters_venue_code_fkey",
    "event_dedupe_keys_pkey",
    "event_dedupe_keys_venue_code_fkey",
    "feed_cursors_market_id_fkey",
    "feed_cursors_pkey",
    "feed_cursors_venue_code_feed_name_market_id_key",
    "feed_cursors_venue_code_fkey",
    "ingestion_connections_pkey",
    "ingestion_connections_venue_code_fkey",
    "job_queue_pkey",
    "market_aliases_check",
    "market_aliases_confidence_check",
    "market_aliases_pkey",
    "market_aliases_source_market_id_fkey",
    "market_aliases_target_market_id_fkey",
    "market_baselines_market_id_fkey",
    "market_baselines_market_scope_unique",
    "market_baselines_pkey",
    "market_baselines_scope_check",
    "market_baselines_venue_code_fkey",
    "market_outcomes_market_id_fkey",
    "market_outcomes_market_id_outcome_key_key",
    "market_outcomes_pkey",
    "market_outcomes_venue_code_fkey",
    "market_outcomes_venue_code_venue_outcome_id_key",
    "market_snapshots_market_id_fkey",
    "market_snapshots_pkey",
    "market_snapshots_venue_code_fkey",
    "markets_pkey",
    "markets_venue_code_fkey",
    "markets_venue_code_venue_market_id_key",
    "metric_windows_market_id_fkey",
    "metric_windows_pkey",
    "metric_windows_venue_code_fkey",
    "metric_windows_window_unique",
    "normalized_trades_aggressor_side_check",
    "normalized_trades_contracts_check",
    "normalized_trades_directional_side_check",
    "normalized_trades_market_id_fkey",
    "normalized_trades_outcome_id_fkey",
    "normalized_trades_pkey",
    "normalized_trades_price_check",
    "normalized_trades_side_confidence_check",
    "normalized_trades_venue_code_fkey",
    "orderbook_levels_contracts_check",
    "orderbook_levels_market_id_fkey",
    "orderbook_levels_pkey",
    "orderbook_levels_price_check",
    "orderbook_levels_side_check",
    "orderbook_snapshots_market_id_fkey",
    "orderbook_snapshots_pkey",
    "orderbook_snapshots_venue_code_fkey",
    "raw_events_market_id_fkey",
    "raw_events_pkey",
    "raw_events_venue_code_fkey",
    "system_heartbeats_pkey",
    "system_heartbeats_worker_name_key",
    "venues_pkey",
}

STARTUP_MAINTENANCE_ARTIFACTS = {
    "idx_markets_watched",
    "idx_normalized_trades_venue_trade_id",
    "market_baselines_market_scope_unique",
    "metric_windows_window_unique",
}

REQUIRED_SEED_VENUES = {"polymarket", "kalshi"}


@dataclass(frozen=True)
class IntegrityCheck:
    name: str
    status: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "name": self.name,
            "status": self.status,
            "message": self.message,
        }
        if self.details:
            data["details"] = self.details
        return data


@dataclass(frozen=True)
class IntegrityResult:
    ok: bool
    status: str
    checks: list[IntegrityCheck]

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "status": self.status,
            "checks": [check.as_dict() for check in self.checks],
        }


@dataclass(frozen=True)
class PartitionNames:
    parents: tuple[str, ...]
    defaults: tuple[str, ...]
    current: tuple[str, ...]


def partition_names_for_month(now: datetime | None = None, *, months_ahead: int = 3) -> PartitionNames:
    parents = tuple(PARTITIONED_TABLES)
    defaults = tuple(f"{table}_default" for table in parents)
    if now is None:
        return PartitionNames(parents=parents, defaults=defaults, current=())
    months = _months_ahead(now.year, now.month, months_ahead)
    current = tuple(f"{table}_{year}_{month:02d}" for table in parents for year, month in months)
    return PartitionNames(parents=parents, defaults=defaults, current=current)


async def verify_database_integrity(pool: Any, *, now: datetime | None = None) -> IntegrityResult:
    """Validate the existing local DB schema without writes or maintenance side effects."""
    check_time = now or datetime.now(timezone.utc)
    try:
        async with pool.acquire() as conn:
            relation_check = await _check_relations(conn)
            checks = [
                relation_check,
                await _check_columns(conn),
                await _check_indexes(conn),
                await _check_constraints(conn),
                await _check_seed_venues(conn)
                if "venues" not in relation_check.details.get("missing", [])
                else IntegrityCheck(
                    "seed_venues",
                    "fail",
                    "cannot check seed venues because venues table is missing",
                    {"missing": sorted(REQUIRED_SEED_VENUES)},
                ),
                await _check_partitions(conn, check_time),
            ]
            checks.append(_check_startup_maintenance(checks))
    except Exception as exc:
        checks = [
            IntegrityCheck(
                "db",
                "fail",
                f"DB integrity verification failed: {exc}",
                {"error": str(exc)},
            )
        ]

    ok = all(check.status == "pass" for check in checks)
    return IntegrityResult(ok=ok, status="ready" if ok else "blocked", checks=checks)


async def _check_relations(conn: Any) -> IntegrityCheck:
    required = sorted(REQUIRED_RELATIONS | REQUIRED_VIEWS)
    missing = await _missing_regclasses(conn, required)
    return _pass_or_fail(
        "relations",
        missing,
        "required tables/views present",
        "missing required tables/views",
    )


async def _check_columns(conn: Any) -> IntegrityCheck:
    rows = await conn.fetch(
        """
        SELECT table_name, column_name
        FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = ANY($1::text[])
        """,
        sorted(REQUIRED_COLUMNS),
    )
    present = {(row["table_name"], row["column_name"]) for row in rows}
    missing = sorted(
        f"{table}.{column}"
        for table, columns in REQUIRED_COLUMNS.items()
        for column in columns
        if (table, column) not in present
    )
    return _pass_or_fail(
        "columns",
        missing,
        "critical columns present",
        "missing critical columns",
    )


async def _check_indexes(conn: Any) -> IntegrityCheck:
    rows = await conn.fetch(
        """
        SELECT indexname
        FROM pg_indexes
        WHERE schemaname = current_schema()
        """,
    )
    present = {row["indexname"] for row in rows}
    missing = sorted(REQUIRED_INDEXES - present)
    return _pass_or_fail(
        "indexes",
        missing,
        "required indexes present",
        "missing required indexes",
    )


async def _check_constraints(conn: Any) -> IntegrityCheck:
    rows = await conn.fetch(
        """
        SELECT con.conname
        FROM pg_constraint con
        JOIN pg_class rel ON rel.oid = con.conrelid
        JOIN pg_namespace ns ON ns.oid = rel.relnamespace
        WHERE ns.nspname = current_schema()
        """,
    )
    present = {row["conname"] for row in rows}
    missing = sorted(REQUIRED_CONSTRAINTS - present)
    return _pass_or_fail(
        "constraints",
        missing,
        "required constraints present",
        "missing required constraints",
    )


async def _check_seed_venues(conn: Any) -> IntegrityCheck:
    rows = await conn.fetch(
        """
        SELECT venue_code
        FROM venues
        WHERE venue_code = ANY($1::text[])
        """,
        sorted(REQUIRED_SEED_VENUES),
    )
    present = {row["venue_code"] for row in rows}
    missing = sorted(REQUIRED_SEED_VENUES - present)
    return _pass_or_fail(
        "seed_venues",
        missing,
        "seed venues present",
        "missing seed venues",
    )


async def _check_partitions(conn: Any, now: datetime) -> IntegrityCheck:
    names = partition_names_for_month(now)
    required = sorted(set(names.parents) | set(names.defaults) | set(names.current))
    missing_relations = await _missing_regclasses(conn, required)

    parent_rows = await conn.fetch(
        """
        SELECT rel.relname
        FROM pg_partitioned_table pt
        JOIN pg_class rel ON rel.oid = pt.partrelid
        JOIN pg_namespace ns ON ns.oid = rel.relnamespace
        WHERE ns.nspname = current_schema()
          AND rel.relname = ANY($1::text[])
        """,
        sorted(names.parents),
    )
    partitioned = {row["relname"] for row in parent_rows}
    not_partitioned = sorted(set(names.parents) - partitioned)

    child_rows = await conn.fetch(
        """
        SELECT parent.relname AS parent, child.relname AS child
        FROM pg_inherits
        JOIN pg_class child ON child.oid = inhrelid
        JOIN pg_namespace child_ns ON child_ns.oid = child.relnamespace
        JOIN pg_class parent ON parent.oid = inhparent
        JOIN pg_namespace parent_ns ON parent_ns.oid = parent.relnamespace
        WHERE parent_ns.nspname = current_schema()
          AND child_ns.nspname = current_schema()
          AND parent.relname = ANY($1::text[])
        """,
        sorted(names.parents),
    )
    attached_by_parent: dict[str, set[str]] = {parent: set() for parent in names.parents}
    for row in child_rows:
        attached_by_parent.setdefault(row["parent"], set()).add(row["child"])

    missing_attachments: list[str] = []
    for parent in names.parents:
        expected_children = {f"{parent}_default"}
        expected_children.update(child for child in names.current if child.startswith(f"{parent}_"))
        attached = attached_by_parent.get(parent, set())
        for child in sorted(expected_children - attached):
            missing_attachments.append(f"{parent}->{child}")

    if missing_relations or not_partitioned or missing_attachments:
        details: dict[str, list[str]] = {}
        if missing_relations:
            details["missing"] = missing_relations
        if not_partitioned:
            details["not_partitioned"] = not_partitioned
        if missing_attachments:
            details["missing_attachments"] = missing_attachments
        summary = []
        if missing_relations:
            summary.append(f"missing relations: {', '.join(missing_relations)}")
        if not_partitioned:
            summary.append(f"not partitioned: {', '.join(not_partitioned)}")
        if missing_attachments:
            summary.append(f"missing attachments: {', '.join(missing_attachments)}")
        return IntegrityCheck("partitions", "fail", "; ".join(summary), details)

    return IntegrityCheck(
        "partitions",
        "pass",
        "partition parents, defaults, and startup-maintenance horizon are attached",
    )

def _check_startup_maintenance(checks: list[IntegrityCheck]) -> IntegrityCheck:
    missing: list[str] = []
    for check in checks:
        missing.extend(str(item) for item in check.details.get("missing", []))
    drift = sorted(item for item in missing if item in STARTUP_MAINTENANCE_ARTIFACTS)
    return _pass_or_fail(
        "startup_maintenance",
        drift,
        "startup-maintenance artifacts present",
        "startup-maintenance drift detected",
    )


async def _missing_regclasses(conn: Any, names: list[str]) -> list[str]:
    missing: list[str] = []
    for name in names:
        exists = await conn.fetchval("SELECT to_regclass($1)", f"pmfi.{name}")
        if exists is None:
            missing.append(name)
    return missing


def _pass_or_fail(name: str, missing: list[str], pass_message: str, fail_message: str) -> IntegrityCheck:
    if missing:
        return IntegrityCheck(
            name,
            "fail",
            f"{fail_message}: {', '.join(missing)}",
            {"missing": missing},
        )
    return IntegrityCheck(name, "pass", pass_message)
