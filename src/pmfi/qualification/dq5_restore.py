from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import subprocess
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import asyncpg
import yaml

from pmfi.commands._shared import ROOT, is_loopback_db_url
from pmfi.commands.backup import create_backup
from pmfi.commands.restore import restore_backup
from pmfi.db import create_pool
from pmfi.domain import RawEvent
from pmfi.pipeline.engine import AlertEngine
from pmfi.pipeline.runner import process_event
from pmfi.qualification.evidence import (
    evidence_contains_secret,
    sanitize_git_remote,
    schema_fingerprint,
)
from pmfi.replay import replay_from_db

DEFAULT_MANIFEST = ROOT / "tests" / "qualification" / "dq5_restore_manifest.yaml"
_DB_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")


def load_dq5_manifest(manifest_path: Path = DEFAULT_MANIFEST) -> dict[str, Any]:
    data = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("DQ-5 manifest must be a mapping")
    return data


def _git_value(args: list[str]) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
        )
    except OSError:
        return None
    value = result.stdout.strip()
    return value or None


def _sha256_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _manifest_rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _admin_dsn(base_dsn: str) -> str:
    if not is_loopback_db_url(base_dsn):
        raise RuntimeError("DQ-5 restore trial requires a loopback PMFI_DB_URL")
    parsed = urlsplit(base_dsn)
    return urlunsplit((parsed.scheme, parsed.netloc, "/postgres", parsed.query, parsed.fragment))


def _database_dsn(base_dsn: str, database: str) -> str:
    parsed = urlsplit(base_dsn)
    return urlunsplit((parsed.scheme, parsed.netloc, f"/{database}", parsed.query, parsed.fragment))


def _quote_ident(identifier: str) -> str:
    if not _DB_NAME_RE.fullmatch(identifier):
        raise ValueError(f"unsafe scratch database name: {identifier!r}")
    return '"' + identifier.replace('"', '""') + '"'


def _scratch_databases(run_key: str) -> dict[str, str]:
    base = re.sub(r"[^a-z0-9]+", "_", run_key.lower()).strip("_")
    suffix = f"p{os.getpid()}_{uuid.uuid4().hex[:8]}"
    prefix = f"pmfi_{base}"[:38].strip("_")
    return {
        "source": f"{prefix}_{suffix}_source",
        "restored": f"{prefix}_{suffix}_restored",
        "rebuilt": f"{prefix}_{suffix}_rebuilt",
        "fresh": f"{prefix}_{suffix}_fresh",
    }


async def _admin_connect() -> asyncpg.Connection:
    dsn = os.environ.get("PMFI_DB_URL")
    if not dsn:
        raise RuntimeError("PMFI_DB_URL is required for DQ-5 restore trial")
    return await asyncpg.connect(_admin_dsn(dsn))


async def _drop_database(conn: asyncpg.Connection, name: str) -> None:
    await conn.execute(
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = $1 AND pid <> pg_backend_pid()",
        name,
    )
    await conn.execute(f"DROP DATABASE IF EXISTS {_quote_ident(name)}")


async def _create_database(conn: asyncpg.Connection, name: str) -> None:
    await _drop_database(conn, name)
    await conn.execute(f"CREATE DATABASE {_quote_ident(name)}")


async def cleanup_dq5_scratch_databases(scratch_databases: dict[str, str]) -> None:
    conn = await _admin_connect()
    try:
        for name in scratch_databases.values():
            await _drop_database(conn, name)
    finally:
        await conn.close()


async def _prepare_scratch_databases(names: dict[str, str]) -> None:
    conn = await _admin_connect()
    try:
        for name in names.values():
            await _create_database(conn, name)
    finally:
        await conn.close()


async def _init_schema(db_url: str) -> None:
    conn = await asyncpg.connect(db_url, server_settings={"search_path": "pmfi,public"})
    try:
        for path in sorted((ROOT / "sql").glob("*.sql")):
            await conn.execute(path.read_text(encoding="utf-8"))
    finally:
        await conn.close()


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith(("{", "[")):
            try:
                return _jsonable(json.loads(stripped))
            except json.JSONDecodeError:
                return value
        return value
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, list | tuple):
        return [_jsonable(v) for v in value]
    return value


def _hash_rows(rows: list[dict[str, Any]]) -> str:
    payload = json.dumps(_jsonable(rows), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _payload_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        loaded = json.loads(value) if value else {}
        return dict(loaded) if loaded else {}
    return dict(value) if value else {}


async def _schema_fingerprint_from_db(conn: asyncpg.Connection) -> str:
    columns = await conn.fetch(
        """SELECT table_name, column_name, ordinal_position, data_type, udt_name,
                  is_nullable, column_default
           FROM information_schema.columns
           WHERE table_schema = 'pmfi'
           ORDER BY table_name, ordinal_position"""
    )
    indexes = await conn.fetch(
        """SELECT tablename, indexname, indexdef
           FROM pg_indexes
           WHERE schemaname = 'pmfi'
           ORDER BY tablename, indexname"""
    )
    constraints = await conn.fetch(
        """SELECT c.conname, t.relname AS table_name, pg_get_constraintdef(c.oid) AS definition
           FROM pg_constraint c
           JOIN pg_class t ON t.oid = c.conrelid
           JOIN pg_namespace n ON n.oid = t.relnamespace
           WHERE n.nspname = 'pmfi'
           ORDER BY t.relname, c.conname"""
    )
    payload = {
        "columns": [dict(row) for row in columns],
        "indexes": [dict(row) for row in indexes],
        "constraints": [dict(row) for row in constraints],
    }
    return _hash_rows([payload])


async def collect_dq5_state(db_url: str, *, source_channel: str) -> dict[str, Any]:
    conn = await asyncpg.connect(db_url, server_settings={"search_path": "pmfi,public"})
    try:
        counts = {
            "raw_events": int(
                await conn.fetchval(
                    "SELECT COUNT(*) FROM raw_events WHERE source_channel = $1",
                    source_channel,
                )
                or 0
            ),
            "normalized_trades": int(
                await conn.fetchval(
                    """SELECT COUNT(*)
                       FROM normalized_trades nt
                       JOIN raw_events re ON re.raw_event_id = nt.raw_event_id
                       WHERE re.source_channel = $1""",
                    source_channel,
                )
                or 0
            ),
            "metric_windows": int(
                await conn.fetchval(
                    """SELECT COUNT(*)
                       FROM metric_windows mw
                       JOIN markets m ON m.market_id = mw.market_id
                       WHERE m.venue_market_id IN (
                           SELECT DISTINCT venue_market_id
                           FROM raw_events
                           WHERE source_channel = $1
                       )""",
                    source_channel,
                )
                or 0
            ),
            "alerts": int(
                await conn.fetchval(
                    """SELECT COUNT(*)
                       FROM alerts a
                       JOIN raw_events re ON re.raw_event_id = a.raw_event_id
                       WHERE re.source_channel = $1""",
                    source_channel,
                )
                or 0
            ),
        }
        raw_rows = [
            dict(row)
            for row in await conn.fetch(
                """SELECT venue_code, source_channel, source_event_type, source_event_id,
                          venue_market_id, exchange_ts, received_at, payload, payload_hash
                   FROM raw_events
                   WHERE source_channel = $1
                   ORDER BY source_event_id, received_at, venue_market_id""",
                source_channel,
            )
        ]
        trade_rows = [
            dict(row)
            for row in await conn.fetch(
                """SELECT re.source_event_id, nt.venue_code, nt.venue_trade_id,
                          m.venue_market_id, nt.outcome_key, nt.aggressor_side,
                          nt.directional_side, nt.side_confidence, nt.price, nt.contracts,
                          nt.capital_at_risk_usd, nt.payout_notional_usd, nt.fee_usd,
                          nt.exchange_ts, nt.received_at, nt.normalization_version,
                          nt.warnings, nt.source_payload
                   FROM normalized_trades nt
                   JOIN raw_events re ON re.raw_event_id = nt.raw_event_id
                   JOIN markets m ON m.market_id = nt.market_id
                   WHERE re.source_channel = $1
                   ORDER BY re.source_event_id, nt.venue_trade_id, nt.outcome_key""",
                source_channel,
            )
        ]
        metric_rows = [
            dict(row)
            for row in await conn.fetch(
                """SELECT m.venue_market_id, mw.venue_code, mw.outcome_key,
                          mw.window_start, mw.window_seconds, mw.trade_count,
                          mw.gross_capital_at_risk_usd, mw.payout_notional_usd,
                          mw.net_yes_flow_usd, mw.net_no_flow_usd, mw.price_open,
                          mw.price_close, mw.price_change, mw.max_trade_capital_at_risk_usd,
                          mw.sample_size, mw.data_quality, mw.metric_version
                   FROM metric_windows mw
                   JOIN markets m ON m.market_id = mw.market_id
                   WHERE m.venue_market_id IN (
                       SELECT DISTINCT venue_market_id
                       FROM raw_events
                       WHERE source_channel = $1
                   )
                   ORDER BY m.venue_market_id, mw.outcome_key, mw.window_start""",
                source_channel,
            )
        ]
        alert_rows = [
            dict(row)
            for row in await conn.fetch(
                """SELECT re.source_event_id, a.rule_key, a.rule_version, a.venue_code,
                          a.outcome_key, a.severity, a.confidence, a.score,
                          a.title, a.summary, a.evidence, a.data_quality, a.status
                   FROM alerts a
                   JOIN raw_events re ON re.raw_event_id = a.raw_event_id
                   WHERE re.source_channel = $1
                   ORDER BY re.source_event_id, a.rule_key, a.outcome_key""",
                source_channel,
            )
        ]
        schema_hash = await _schema_fingerprint_from_db(conn)
    finally:
        await conn.close()

    return {
        "counts": counts,
        "hashes": {
            "raw_events": _hash_rows(raw_rows),
            "normalized_trades": _hash_rows(trade_rows),
            "metric_windows": _hash_rows(metric_rows),
            "alerts": _hash_rows(alert_rows),
        },
        "schema_fingerprint": schema_hash,
    }


def evaluate_dq5_pass_invariants(measurements: dict[str, Any]) -> dict[str, bool]:
    return {
        "restore_preserves_all_canonical_state_without_loss": (
            measurements["source_counts"] == measurements["restored_counts"]
            and measurements["source_hashes"] == measurements["restored_hashes"]
        ),
        "rebuild_from_raw_is_deterministic_and_identical_to_source": (
            measurements["source_counts"] == measurements["rebuilt_counts"]
            and measurements["source_hashes"] == measurements["rebuilt_hashes"]
        ),
        "restored_schema_dump_fidelity_matches_fresh_init": (
            measurements["restored_schema_fingerprint"]
            == measurements["fresh_schema_fingerprint"]
        ),
        "no_secrets_in_fixtures_logs_or_evidence": bool(
            measurements.get("no_secrets_in_fixtures_logs_or_evidence")
        ),
    }


def _event_from_spec(
    manifest: dict[str, Any],
    name: str,
    *,
    base_ts: datetime,
    offset_seconds: int,
) -> RawEvent:
    spec = manifest["events"][name]
    event_ts = base_ts + timedelta(seconds=offset_seconds)
    return RawEvent(
        venue_code=manifest["venue_code"],
        source_channel=manifest["source_channel"],
        source_event_type="last_trade_price",
        source_event_id=spec["source_event_id"],
        venue_market_id=spec["venue_market_id"],
        exchange_ts=event_ts,
        received_at=event_ts,
        payload={
            "trade_id": spec["source_event_id"],
            "market": spec["venue_market_id"],
            "outcome": "yes",
            "side": "buy",
            "price": spec["price"],
            "size": spec["size"],
        },
    )


async def _seed_source_db(db_url: str, manifest: dict[str, Any], started_at: datetime) -> None:
    pool = await create_pool(db_url)
    engine = AlertEngine()

    async def _noop_alert_handler(*_args: object) -> None:
        return None

    try:
        for idx, name in enumerate(manifest["events"], start=1):
            raw = _event_from_spec(
                manifest,
                name,
                base_ts=started_at,
                offset_seconds=idx,
            )
            await process_event(raw, pool, engine, _noop_alert_handler)
    finally:
        await pool.close()


async def _copy_raw_events(source_url: str, target_url: str, source_channel: str) -> None:
    source = await asyncpg.connect(source_url, server_settings={"search_path": "pmfi,public"})
    target_pool = await create_pool(target_url)
    try:
        rows = await source.fetch(
            """SELECT venue_code, source_channel, source_event_type, source_event_id,
                      venue_market_id, exchange_ts, received_at, payload
               FROM raw_events
               WHERE source_channel = $1
               ORDER BY received_at, raw_event_id""",
            source_channel,
        )
        async with target_pool.acquire() as conn:
            from pmfi.db.repos.raw_events import insert_raw_event

            for row in rows:
                raw = RawEvent(
                    venue_code=row["venue_code"],
                    source_channel=row["source_channel"],
                    source_event_type=row["source_event_type"],
                    source_event_id=row["source_event_id"],
                    venue_market_id=row["venue_market_id"],
                    exchange_ts=row["exchange_ts"],
                    received_at=row["received_at"],
                    payload=_payload_dict(row["payload"]),
                )
                await insert_raw_event(conn, raw)
    finally:
        await source.close()
        await target_pool.close()


async def _rebuild_from_raw(db_url: str, source_channel: str) -> None:
    pool = await create_pool(db_url)
    try:
        await replay_from_db(
            pool,
            limit=0,
            persist=True,
            seed=False,
            print_summary=False,
            venue="polymarket",
        )
    finally:
        await pool.close()


async def _postgres_version(pool: Any) -> str:
    async with pool.acquire() as conn:
        return str(await conn.fetchval("SELECT version()"))


async def run_dq5_restore_trial(
    pool: Any,
    manifest_path: Path = DEFAULT_MANIFEST,
    *,
    keep_scratch: bool = False,
) -> dict[str, Any]:
    manifest_path = Path(manifest_path)
    manifest = load_dq5_manifest(manifest_path)
    started_at = datetime.now(timezone.utc).replace(microsecond=0)
    db_url = os.environ.get("PMFI_DB_URL")
    if not db_url:
        raise RuntimeError("PMFI_DB_URL is required for DQ-5 restore trial")
    if not is_loopback_db_url(db_url):
        raise RuntimeError("DQ-5 restore trial requires a loopback PMFI_DB_URL")

    names = _scratch_databases(manifest["run_key"])
    await _prepare_scratch_databases(names)
    urls = {key: _database_dsn(db_url, name) for key, name in names.items()}
    backup_size = 0
    try:
        await _init_schema(urls["source"])
        await _init_schema(urls["rebuilt"])
        await _init_schema(urls["fresh"])
        await _seed_source_db(urls["source"], manifest, started_at)
        source_state = await collect_dq5_state(
            urls["source"],
            source_channel=manifest["source_channel"],
        )

        with tempfile.TemporaryDirectory(prefix="pmfi-dq5-backup-") as tmp_dir:
            backup_path = create_backup(
                backup_dir=tmp_dir,
                source_db=names["source"],
                configured_db_url=db_url,
            )
            backup_size = backup_path.stat().st_size
            restore_backup(
                backup_file=backup_path,
                target_db=names["restored"],
                configured_db_url=db_url,
            )

        restored_state = await collect_dq5_state(
            urls["restored"],
            source_channel=manifest["source_channel"],
        )
        await _copy_raw_events(
            urls["restored"],
            urls["rebuilt"],
            manifest["source_channel"],
        )
        await _rebuild_from_raw(urls["rebuilt"], manifest["source_channel"])
        rebuilt_state = await collect_dq5_state(
            urls["rebuilt"],
            source_channel=manifest["source_channel"],
        )
        fresh_state = await collect_dq5_state(
            urls["fresh"],
            source_channel=manifest["source_channel"],
        )

        measurements = {
            "source_counts": source_state["counts"],
            "restored_counts": restored_state["counts"],
            "rebuilt_counts": rebuilt_state["counts"],
            "source_hashes": source_state["hashes"],
            "restored_hashes": restored_state["hashes"],
            "rebuilt_hashes": rebuilt_state["hashes"],
            "restored_schema_fingerprint": restored_state["schema_fingerprint"],
            "fresh_schema_fingerprint": fresh_state["schema_fingerprint"],
            "backup_size_bytes": backup_size,
            "no_secrets_in_fixtures_logs_or_evidence": False,
        }
        evidence: dict[str, Any] = {
            "version": "pmfi-data-plane-scenario-run.v1",
            "scenario_id": manifest["scenario_id"],
            "scenario_version": manifest["scenario_version"],
            "profile": manifest["profile"],
            "outcome": "PASS",
            "completeness_classifications": {
                "restore_rebuild": "PROVEN_OFFLINE_DB_GATED",
                "long_horizon_soak": "ACCEPTED_DEBT",
            },
            "repository": {
                "remote": sanitize_git_remote(_git_value(["config", "--get", "remote.origin.url"])),
                "branch": _git_value(["rev-parse", "--abbrev-ref", "HEAD"]),
                "commit": _git_value(["rev-parse", "HEAD"]),
                "worktree_status": "not_recorded_by_db_test",
            },
            "runtime": {
                "python_version": platform.python_version(),
                "postgres_version": await _postgres_version(pool),
                "schema_version": schema_fingerprint(ROOT / "sql"),
                "environment": "offline_db_gated",
            },
            "time": {
                "started_at": started_at.isoformat(),
                "ended_at": datetime.now(timezone.utc).isoformat(),
                "input_bounds": {
                    "first_exchange_ts": started_at.isoformat(),
                    "last_exchange_ts": (
                        started_at + timedelta(seconds=len(manifest["events"]))
                    ).isoformat(),
                },
            },
            "expected_truth": {
                "manifest": _manifest_rel(manifest_path),
                "artifact_hash": _sha256_path(manifest_path),
                "expected_counts": manifest["expected_counts"],
            },
            "evidence": {
                "required_facets": manifest["required_facets"],
                "actual_facets": [],
                "deferred_facets": [
                    item["facet"] for item in manifest["manual_deferred_facets"]
                ],
                "commands": [
                    "python -m pytest -q tests\\test_dq5_restore_trial_db.py",
                ],
                "artifacts": [_manifest_rel(manifest_path)],
                "artifact_hashes": [_sha256_path(manifest_path)],
                "scratch_databases": names,
                "backup_size_bytes": backup_size,
            },
            "measurements": measurements,
            "pass_invariants": {},
            "fail_conditions": [],
            "blocker_or_inconclusive_reason": None,
            "incidents": {"unresolved_p0": [], "unresolved_p1": []},
            "accepted_debt": manifest["manual_deferred_facets"],
            "next_action": "orchestrator_verify_pr",
        }
        secret_free = not evidence_contains_secret(manifest_path, evidence)
        measurements["no_secrets_in_fixtures_logs_or_evidence"] = secret_free
        pass_invariants = evaluate_dq5_pass_invariants(measurements)
        actual_facets: list[str] = []
        if source_state["counts"]["raw_events"] > 0:
            actual_facets.append("POSTGRES_INTEGRATION")
        if pass_invariants["restore_preserves_all_canonical_state_without_loss"] and backup_size > 0:
            actual_facets.append("RESTORE")
        if pass_invariants["restored_schema_dump_fidelity_matches_fresh_init"]:
            actual_facets.append("SCHEMA_DUMP_FIDELITY")
        evidence["evidence"]["actual_facets"] = actual_facets
        evidence["pass_invariants"] = pass_invariants

        expected = manifest["expected_counts"]
        for key, expected_value in expected.items():
            actual_value = measurements["source_counts"].get(key)
            if actual_value != expected_value:
                evidence["fail_conditions"].append(
                    f"source count {key} expected {expected_value}, got {actual_value}"
                )
        if not all(evidence["pass_invariants"].values()) or evidence["fail_conditions"]:
            evidence["outcome"] = "FAIL"
        return evidence
    finally:
        if not keep_scratch:
            await cleanup_dq5_scratch_databases(names)
