r"""Disposable local Postgres smoke for the PMFI operator path.

Creates a temporary database on the configured local Postgres server, initializes
the PMFI schema, runs the fixture-backed DB operator workflow, validates the
outputs, then removes only the database created by this run unless --keep-db is
specified.
"""

from __future__ import annotations

import argparse
import asyncio
import errno
import json
import os
import re
import secrets
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from db_local import SQL_FILES  # noqa: E402
from pmfi.config import load_config  # noqa: E402

DB_NAME_RE = re.compile(r"^pmfi_smoke_[a-z0-9_]{1,40}$")
LIVE_SMOKE_MARKET_ID = "KXLIVE-SMOKE-26JUN03"
LIVE_SMOKE_ALERT_RULE_ID = "large_trade_absolute_v1"


@dataclass(frozen=True)
class CommandResult:
    args: list[str]
    stdout: str
    stderr: str


POSTGRES_UNAVAILABLE_ERRNOS = {
    errno.ECONNABORTED,
    errno.ECONNREFUSED,
    errno.ECONNRESET,
    errno.ENETUNREACH,
    errno.ETIMEDOUT,
}
POSTGRES_UNAVAILABLE_WINERRORS = {10053, 10054, 10060, 10061, 1225}


def _iter_exception_chain(exc: BaseException):
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def _is_missing_asyncpg(exc: BaseException) -> bool:
    for item in _iter_exception_chain(exc):
        if isinstance(item, ModuleNotFoundError):
            missing_name = getattr(item, "name", None)
            if missing_name == "asyncpg" or "asyncpg" in str(item):
                return True
        elif isinstance(item, ImportError) and "asyncpg" in str(item):
            return True
    return False


def _is_postgres_unavailable(exc: BaseException) -> bool:
    for item in _iter_exception_chain(exc):
        if isinstance(item, (ConnectionRefusedError, TimeoutError)):
            return True
        if isinstance(item, OSError):
            err_no = getattr(item, "errno", None)
            winerror = getattr(item, "winerror", None)
            if err_no in POSTGRES_UNAVAILABLE_ERRNOS or winerror in POSTGRES_UNAVAILABLE_WINERRORS:
                return True
    return False


def _describe_exception(exc: BaseException) -> str:
    text = str(exc).strip()
    if text:
        return f"{type(exc).__name__}: {text}"
    return type(exc).__name__


def _print_failure(exc: BaseException) -> int:
    if _is_missing_asyncpg(exc):
        print("db-smoke preflight failed: asyncpg is not installed.", file=sys.stderr)
        print("Next actions:", file=sys.stderr)
        print(r'  .\.venv\Scripts\python.exe -m pip install -e ".[dev]"', file=sys.stderr)
        print(r"  .\.venv\Scripts\python.exe .\scripts\task.py db-smoke", file=sys.stderr)
        return 2

    if _is_postgres_unavailable(exc):
        print("db-smoke preflight failed: local Postgres is not reachable.", file=sys.stderr)
        print(f"Cause: {_describe_exception(exc)}", file=sys.stderr)
        print("Next actions:", file=sys.stderr)
        print(r"  .\.venv\Scripts\python.exe .\scripts\db_local.py up", file=sys.stderr)
        print(r"  .\.venv\Scripts\python.exe .\scripts\db_local.py verify", file=sys.stderr)
        print(r"  .\.venv\Scripts\python.exe .\scripts\task.py db-smoke", file=sys.stderr)
        return 2

    print(f"db-smoke failed: {_describe_exception(exc)}", file=sys.stderr)
    return 1


def make_db_name(now: datetime | None = None) -> str:
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%d_%H%M%S")
    return f"pmfi_smoke_{stamp}_{secrets.token_hex(3)}"


def validate_smoke_db_name(name: str) -> None:
    if not DB_NAME_RE.fullmatch(name):
        raise ValueError(f"unsafe smoke database name: {name!r}")


def database_url_for(base_url: str, db_name: str) -> str:
    validate_smoke_db_name(db_name)
    parts = urlsplit(base_url)
    if not parts.scheme.startswith("postgres"):
        raise ValueError(f"unsupported database URL scheme: {parts.scheme!r}")
    return urlunsplit((parts.scheme, parts.netloc, f"/{db_name}", parts.query, parts.fragment))


def _quote_ident(name: str) -> str:
    validate_smoke_db_name(name)
    return '"' + name.replace('"', '""') + '"'


def _print_command(result: CommandResult) -> None:
    print("==", " ".join(result.args), "==", flush=True)
    if result.stdout:
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
    if result.stderr:
        print(result.stderr, end="" if result.stderr.endswith("\n") else "\n", file=sys.stderr)


def run_command(args: list[str], *, env: dict[str, str]) -> CommandResult:
    completed = subprocess.run(
        args,
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    result = CommandResult(args=args, stdout=completed.stdout, stderr=completed.stderr)
    _print_command(result)
    if completed.returncode != 0:
        raise RuntimeError(f"command failed with exit {completed.returncode}: {' '.join(args)}")
    return result


def parse_json_stdout(result: CommandResult) -> dict:
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"expected JSON stdout from {' '.join(result.args)}: {exc}") from exc


async def create_database(admin_url: str, db_name: str) -> None:
    import asyncpg

    validate_smoke_db_name(db_name)
    conn = await asyncpg.connect(admin_url)
    try:
        await conn.execute(f"CREATE DATABASE {_quote_ident(db_name)}")
    finally:
        await conn.close()


async def drop_database(admin_url: str, db_name: str) -> None:
    import asyncpg

    validate_smoke_db_name(db_name)
    conn = await asyncpg.connect(admin_url)
    try:
        await conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = $1 AND pid <> pg_backend_pid()",
            db_name,
        )
        await conn.execute(f"DROP DATABASE IF EXISTS {_quote_ident(db_name)}")
    finally:
        await conn.close()


async def apply_schema(target_url: str) -> None:
    import asyncpg

    conn = await asyncpg.connect(target_url)
    try:
        for rel in SQL_FILES:
            path = ROOT / rel
            print(f"applying {rel}", flush=True)
            await conn.execute(path.read_text(encoding="utf-8"))
    finally:
        await conn.close()


async def fetch_operator_counts(target_url: str) -> dict[str, int]:
    import asyncpg

    conn = await asyncpg.connect(target_url)
    try:
        row = await conn.fetchrow(
            """
            SELECT
              (SELECT COUNT(*) FROM raw_events)::int AS raw_events,
              (SELECT COUNT(*) FROM normalized_trades)::int AS normalized_trades,
              (SELECT COUNT(*) FROM alerts)::int AS alerts,
              (SELECT COUNT(*) FROM dead_letters)::int AS dead_letters,
              (SELECT COUNT(*) FROM market_baselines)::int AS market_baselines,
              (SELECT COUNT(*) FROM event_dedupe_keys)::int AS event_dedupe_keys,
              (SELECT COALESCE(SUM(duplicate_count), 0)::int FROM event_dedupe_keys) AS raw_duplicate_count
            """
        )
        return dict(row)
    finally:
        await conn.close()


def _assert_health(payload: dict) -> None:
    if not payload.get("ok"):
        raise RuntimeError(f"health failed: {payload}")
    checks = {check["name"]: check for check in payload.get("checks", [])}
    for name in ["db", "db_integrity", "watched_markets", "raw_events", "normalized_trades", "alerts", "market_baselines"]:
        if checks.get(name, {}).get("status") != "pass":
            raise RuntimeError(f"health check {name!r} did not pass: {checks.get(name)}")
    dead_letters = checks.get("dead_letters", {})
    if dead_letters.get("status") != "warn" or dead_letters.get("details", {}).get("count") != 1:
        raise RuntimeError(f"expected one dead-letter warning, got: {dead_letters}")


def _assert_ingest_check(payload: dict) -> None:
    if not payload.get("ok") or payload.get("status") != "ready":
        raise RuntimeError(f"ingest readiness failed: {payload}")
    checks = {check["name"]: check for check in payload.get("checks", [])}
    for name in ["db_integrity", "delivery", "baselines", "watched_markets", "kalshi_subscriptions", "live_connections"]:
        if checks.get(name, {}).get("status") != "pass":
            raise RuntimeError(f"ingest readiness check {name!r} did not pass: {checks.get(name)}")
    subscriptions = payload.get("subscriptions", {})
    if subscriptions.get("kalshi_tickers") != 1:
        raise RuntimeError(f"expected one Kalshi readiness ticker, got: {subscriptions}")


def _assert_report(payload: dict) -> None:
    expected = {
        "total": 12,
        "raw_events": 11,
        "normalized_trades": 10,
        "dead_letters": 1,
    }
    for key, value in expected.items():
        if payload.get(key) != value:
            raise RuntimeError(f"expected report {key}={value}, got {payload.get(key)}")


def _assert_dead_letters(payload: dict) -> None:
    if payload.get("count") != 1:
        raise RuntimeError(f"expected one dead letter, got: {payload}")
    item = payload["dead_letters"][0]
    if item.get("source_event_id") != "pm-malformed-1" or item.get("error_class") != "invalid_price_or_size":
        raise RuntimeError(f"unexpected dead letter payload: {item}")


def _assert_review(payload: dict) -> None:
    if not payload.get("ok") or payload.get("status") != "pass":
        raise RuntimeError(f"review-pass failed: {payload}")


def _assert_resume_counts(before: dict[str, int], after: dict[str, int]) -> None:
    stable_keys = [
        "raw_events",
        "normalized_trades",
        "alerts",
        "dead_letters",
        "market_baselines",
        "event_dedupe_keys",
    ]
    for key in stable_keys:
        if after.get(key) != before.get(key):
            raise RuntimeError(f"restart/resume changed {key}: before={before.get(key)} after={after.get(key)}")
    expected_duplicates = before["raw_duplicate_count"] + before["raw_events"]
    if after["raw_duplicate_count"] != expected_duplicates:
        raise RuntimeError(
            "restart/resume did not record duplicate raw-event observations: "
            f"before={before['raw_duplicate_count']} after={after['raw_duplicate_count']} "
            f"expected={expected_duplicates}"
        )


def _assert_live_smoke_counts(before: dict[str, int], after: dict[str, int]) -> None:
    expected_increments = {
        "raw_events": 1,
        "normalized_trades": 1,
        "event_dedupe_keys": 1,
    }
    for key, increment in expected_increments.items():
        expected = before[key] + increment
        if after.get(key) != expected:
            raise RuntimeError(f"live-smoke changed {key} incorrectly: before={before[key]} after={after.get(key)} expected={expected}")
    stable_keys = ["dead_letters", "market_baselines", "raw_duplicate_count"]
    for key in stable_keys:
        if after.get(key) != before.get(key):
            raise RuntimeError(f"live-smoke unexpectedly changed {key}: before={before.get(key)} after={after.get(key)}")
    if after["alerts"] <= before["alerts"]:
        raise RuntimeError(f"live-smoke did not insert any alert rows: before={before['alerts']} after={after['alerts']}")


def _assert_fixture_ingest_counts(before: dict[str, int], after: dict[str, int]) -> None:
    stable_keys = [
        "raw_events",
        "normalized_trades",
        "alerts",
        "dead_letters",
        "market_baselines",
        "event_dedupe_keys",
    ]
    for key in stable_keys:
        if after.get(key) != before.get(key):
            raise RuntimeError(f"fixture ingest unexpectedly changed {key}: before={before.get(key)} after={after.get(key)}")
    expected_duplicates = before["raw_duplicate_count"] + 1
    if after["raw_duplicate_count"] != expected_duplicates:
        raise RuntimeError(
            "fixture ingest did not record the duplicate raw-event observation: "
            f"before={before['raw_duplicate_count']} after={after['raw_duplicate_count']} "
            f"expected={expected_duplicates}"
        )


def _assert_fixture_ingest_runtime_health(payload: dict) -> None:
    if not payload.get("ok"):
        raise RuntimeError(f"post-ingest health failed: {payload}")
    checks = {check["name"]: check for check in payload.get("checks", [])}
    runtime = checks.get("ingest_runtime") or {}
    if runtime.get("status") != "pass":
        raise RuntimeError(f"fixture ingest runtime health did not pass: {runtime}")

    details = runtime.get("details") or {}
    if int(details.get("connection_count") or 0) < 1:
        raise RuntimeError(f"fixture ingest did not record an ingestion connection: {runtime}")
    if int(details.get("heartbeat_count") or 0) < 1:
        raise RuntimeError(f"fixture ingest did not record a system heartbeat: {runtime}")

    latest_connection = details.get("latest_connection") or {}
    if latest_connection.get("source_channel") != "fixture_source":
        raise RuntimeError(f"latest ingestion connection is not fixture_source: {latest_connection}")
    if latest_connection.get("status") != "stopped":
        raise RuntimeError(f"latest ingestion connection did not stop cleanly: {latest_connection}")
    if latest_connection.get("last_error"):
        raise RuntimeError(f"latest ingestion connection has an unexpected error: {latest_connection}")

    latest_heartbeat = details.get("latest_heartbeat") or {}
    if latest_heartbeat.get("worker_name") != "pmfi-ingest:kalshi:fixture_source":
        raise RuntimeError(f"latest ingest heartbeat is for the wrong worker: {latest_heartbeat}")
    if latest_heartbeat.get("status") != "stopped":
        raise RuntimeError(f"latest ingest heartbeat did not stop cleanly: {latest_heartbeat}")


def _parse_alert_payloads(stdout: str) -> list[dict]:
    payloads: list[dict] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if payload.get("alert") is True:
            payloads.append(payload)
    return payloads


def _assert_live_smoke_replay(
    result: CommandResult,
    before_counts: dict[str, int],
    after_counts: dict[str, int],
    *,
    expected_replayed: int,
) -> None:
    for key, before_value in before_counts.items():
        if after_counts.get(key) != before_value:
            raise RuntimeError(
                f"live-smoke replay mutated {key}: before={before_value} after={after_counts.get(key)}"
            )

    replay_summary = f"[from-db] replayed {expected_replayed} raw_event(s) from Postgres"
    if replay_summary not in result.stdout:
        raise RuntimeError(f"DB replay after live-smoke did not report {expected_replayed} replayed raw events")
    if LIVE_SMOKE_MARKET_ID not in result.stdout:
        raise RuntimeError(f"DB replay after live-smoke did not include {LIVE_SMOKE_MARKET_ID}")

    for payload in _parse_alert_payloads(result.stdout):
        evidence = payload.get("evidence") or {}
        if (
            payload.get("rule_id") == LIVE_SMOKE_ALERT_RULE_ID
            and payload.get("market_id") == LIVE_SMOKE_MARKET_ID
            and evidence.get("venue_market_id") == LIVE_SMOKE_MARKET_ID
            and evidence.get("venue_code") == "kalshi"
            and evidence.get("capital_at_risk_usd") == "33300.00"
            and "capital_at_risk_threshold" in payload.get("reason_codes", [])
        ):
            return
    raise RuntimeError("DB replay after live-smoke did not emit expected alert evidence")


async def run_smoke(*, keep_db: bool = False) -> int:
    cfg = load_config()
    admin_url = os.environ.get("DATABASE_URL") or cfg.database.url
    db_name = make_db_name()
    target_url = database_url_for(admin_url, db_name)
    env = os.environ.copy()
    env["DATABASE_URL"] = target_url

    print(f"creating disposable database {db_name}", flush=True)
    await create_database(admin_url, db_name)
    try:
        await apply_schema(target_url)

        run_command([sys.executable, "-m", "pmfi.cli", "db-maintenance", "--create-partitions"], env=env)
        db_verify = parse_json_stdout(run_command([sys.executable, "-m", "pmfi.cli", "db-verify", "--format", "json"], env=env))
        if not db_verify.get("ok"):
            raise RuntimeError(f"db-verify failed: {db_verify}")

        run_command([sys.executable, "-m", "pmfi.cli", "replay", "--persist"], env=env)
        run_command([sys.executable, "-m", "pmfi.cli", "markets", "watch", "KXEXAMPLE-26JUN03", "--venue", "kalshi"], env=env)
        run_command([sys.executable, "-m", "pmfi.cli", "baseline", "compute", "--lookback-days", "7", "--min-samples", "2"], env=env)

        ingest_check = parse_json_stdout(
            run_command([sys.executable, "-m", "pmfi.cli", "ingest", "--venue", "kalshi", "--check", "--format", "json"], env=env)
        )
        _assert_ingest_check(ingest_check)

        health = parse_json_stdout(run_command([sys.executable, "-m", "pmfi.cli", "health", "--format", "json"], env=env))
        _assert_health(health)

        report = parse_json_stdout(run_command([sys.executable, "-m", "pmfi.cli", "report", "--format", "json"], env=env))
        _assert_report(report)

        dead_letters = parse_json_stdout(run_command([sys.executable, "-m", "pmfi.cli", "dead-letters", "--format", "json"], env=env))
        _assert_dead_letters(dead_letters)

        before_resume = await fetch_operator_counts(target_url)
        run_command([sys.executable, "-m", "pmfi.cli", "replay", "--persist"], env=env)
        after_resume = await fetch_operator_counts(target_url)
        _assert_resume_counts(before_resume, after_resume)
        print(
            "restart/resume idempotency passed: "
            f"raw={after_resume['raw_events']} trades={after_resume['normalized_trades']} "
            f"alerts={after_resume['alerts']} dead_letters={after_resume['dead_letters']} "
            f"raw_duplicates={after_resume['raw_duplicate_count']}",
            flush=True,
        )

        replay = run_command([sys.executable, "-m", "pmfi.cli", "replay", "--from-db", "--limit", "20"], env=env)
        if "[from-db] replayed 10 raw_event(s) from Postgres" not in replay.stdout:
            raise RuntimeError("DB replay did not report 10 replayed raw events")
        if '"baseline_status": "available"' not in replay.stdout:
            raise RuntimeError("DB replay did not emit baseline-aware evidence")

        review = parse_json_stdout(run_command([sys.executable, "-m", "pmfi.cli", "review-pass", "--format", "json"], env=env))
        _assert_review(review)

        before_live_smoke = await fetch_operator_counts(target_url)
        live_smoke = run_command(
            [
                sys.executable,
                "-m",
                "pmfi.cli",
                "live-smoke",
                "--fixture-source",
                "tests/fixtures/live-smoke/kalshi_persist.json",
                "--persist-raw",
                "--max-events",
                "1",
                "--max-seconds",
                "10",
            ],
            env=env,
        )
        if "done: 1 event(s) processed, 1 captured" not in live_smoke.stdout:
            raise RuntimeError("fixture-source live-smoke did not report one processed/captured event")
        after_live_smoke = await fetch_operator_counts(target_url)
        _assert_live_smoke_counts(before_live_smoke, after_live_smoke)

        before_live_replay = await fetch_operator_counts(target_url)
        live_replay = run_command(
            [
                sys.executable,
                "-m",
                "pmfi.cli",
                "replay",
                "--from-db",
                "--limit",
                str(after_live_smoke["raw_events"]),
            ],
            env=env,
        )
        after_live_replay = await fetch_operator_counts(target_url)
        _assert_live_smoke_replay(
            live_replay,
            before_live_replay,
            after_live_replay,
            expected_replayed=after_live_smoke["normalized_trades"],
        )

        before_fixture_ingest = await fetch_operator_counts(target_url)
        fixture_ingest = run_command(
            [
                sys.executable,
                "-m",
                "pmfi.cli",
                "ingest",
                "--fixture-source",
                "tests/fixtures/live-smoke/kalshi_persist.json",
                "--venue",
                "kalshi",
                "--max-events",
                "1",
                "--max-seconds",
                "10",
            ],
            env=env,
        )
        if "[ingest] bounded run complete: raw_events_seen=1" not in fixture_ingest.stdout:
            raise RuntimeError("fixture-source ingest did not report one bounded raw event")
        if "raw_event_duplicates=1" not in fixture_ingest.stdout:
            raise RuntimeError("fixture-source ingest did not report the expected duplicate raw event")
        after_fixture_ingest = await fetch_operator_counts(target_url)
        _assert_fixture_ingest_counts(before_fixture_ingest, after_fixture_ingest)
        runtime_health = parse_json_stdout(run_command([sys.executable, "-m", "pmfi.cli", "health", "--format", "json"], env=env))
        _assert_fixture_ingest_runtime_health(runtime_health)

        print(
            "disposable DB smoke passed: "
            f"db={db_name} raw={report['raw_events']} trades={report['normalized_trades']} "
            f"alerts={report['total']} dead_letters={report['dead_letters']} "
            f"baselines={next(c for c in health['checks'] if c['name'] == 'market_baselines')['details']['count']} "
            f"ingest_check={ingest_check['status']} "
            f"live_smoke_raw_delta={after_live_smoke['raw_events'] - before_live_smoke['raw_events']} "
            "live_smoke_replay=pass "
            f"fixture_ingest_duplicate_delta={after_fixture_ingest['raw_duplicate_count'] - before_fixture_ingest['raw_duplicate_count']} "
            "fixture_ingest_runtime=pass",
            flush=True,
        )
        return 0
    finally:
        if keep_db:
            print(f"keeping disposable database {db_name}", flush=True)
        else:
            print(f"dropping disposable database {db_name}", flush=True)
            await drop_database(admin_url, db_name)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="db_smoke.py")
    parser.add_argument("--keep-db", action="store_true", help="Leave the created pmfi_smoke_* database in place for inspection")
    args = parser.parse_args(argv)
    try:
        return asyncio.run(run_smoke(keep_db=args.keep_db))
    except Exception as exc:
        return _print_failure(exc)


if __name__ == "__main__":
    raise SystemExit(main())
