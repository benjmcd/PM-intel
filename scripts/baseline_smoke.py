"""Validate DB-free baseline and baseline-aware alert contracts."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import types
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, NoReturn

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

try:
    import asyncpg as _asyncpg  # noqa: F401
except ModuleNotFoundError:
    asyncpg_stub = types.ModuleType("asyncpg")
    asyncpg_stub.Connection = object
    asyncpg_stub.Pool = object
    asyncpg_stub.Record = dict
    sys.modules["asyncpg"] = asyncpg_stub

SOURCE = "db_free_baseline_contracts"
EXPECTED_CHECKS = (
    "compute_path_uses_normalized_trades",
    "baseline_upsert_conflict_constraint",
    "baseline_available_alert",
    "baseline_stale_alert",
    "baseline_missing_alert",
    "volume_spike_uses_prior_history",
)


@dataclass(frozen=True)
class _Check:
    name: str
    details: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "status": "pass", "details": self.details}


class _Acquire:
    def __init__(self, conn: Any):
        self.conn = conn

    async def __aenter__(self) -> Any:
        return self.conn

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> bool:
        return False


class _Pool:
    def __init__(self, conn: Any):
        self.conn = conn

    def acquire(self) -> _Acquire:
        return _Acquire(self.conn)


class _FetchConn:
    def __init__(self, rows: list[dict[str, Any]]):
        self.rows = rows
        self.sql = ""
        self.args: tuple[Any, ...] = ()

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        self.sql = sql
        self.args = args
        return self.rows


class _FetchRowConn:
    def __init__(self):
        self.sql = ""
        self.args: tuple[Any, ...] = ()

    async def fetchrow(self, sql: str, *args: Any) -> dict[str, Any]:
        self.sql = sql
        self.args = args
        return {"baseline_id": "baseline-smoke-id"}


def _fail(message: str) -> NoReturn:
    raise RuntimeError(f"baseline-smoke failed: {message}")


def _require(condition: bool, message: str) -> None:
    if not condition:
        _fail(message)


def _market_trade(
    *,
    venue_code: str,
    venue_market_id: str,
    capital_at_risk_usd: str,
    contracts: str = "30000",
    received_at: datetime | None = None,
) -> Any:
    from pmfi.domain import NormalizedTrade

    return NormalizedTrade(
        venue_code=venue_code,
        venue_market_id=venue_market_id,
        outcome_key="yes",
        price=Decimal("0.50"),
        contracts=Decimal(contracts),
        capital_at_risk_usd=Decimal(capital_at_risk_usd),
        payout_notional_usd=Decimal(contracts),
        directional_side="yes",
        **({"received_at": received_at} if received_at is not None else {}),
    )


async def _check_compute_path() -> _Check:
    import pmfi.baseline as baseline_module

    row = {
        "market_id": "11111111-1111-1111-1111-111111111111",
        "venue_code": "kalshi",
        "venue_market_id": "KXBASE-26JUN03",
        "sample_size": 2,
        "p50_trade_usd": Decimal("10000"),
        "p95_trade_usd": Decimal("25000"),
        "p99_trade_usd": Decimal("26000"),
        "p995_trade_usd": Decimal("26500"),
        "median_5m_flow_usd": None,
        "p99_5m_flow_usd": None,
    }
    conn = _FetchConn([row])
    upserts: list[dict[str, Any]] = []

    async def fake_upsert_baseline(_conn: Any, **kwargs: Any) -> str:
        upserts.append(kwargs)
        return "baseline-smoke-id"

    original_upsert = baseline_module.upsert_baseline
    baseline_module.upsert_baseline = fake_upsert_baseline
    try:
        results = await baseline_module.compute_market_baselines(
            _Pool(conn),
            lookback_seconds=86400,
            min_samples=2,
        )
    finally:
        baseline_module.upsert_baseline = original_upsert

    _require("FROM normalized_trades nt" in conn.sql, "compute query must read normalized_trades")
    _require("metric_windows" not in conn.sql, "compute query must not read metric_windows")
    _require(conn.args == ("86400", 2), "compute query must bind lookback seconds and min samples")
    _require(len(upserts) == 1, "compute path must upsert exactly one baseline for the fixture row")
    upsert = upserts[0]
    _require(upsert.get("scope") == "market", "upsert scope must be market")
    _require(upsert.get("sample_size") == 2, "upsert sample size must match source row")
    _require(upsert.get("p99_trade_usd") == 26000.0, "upsert p99 must match percentile payload")
    _require(
        upsert.get("baseline_payload") == {"venue_market_id": "KXBASE-26JUN03"},
        "upsert must carry venue_market_id in baseline payload",
    )
    _require(results and results[0].get("baseline_id") == "baseline-smoke-id", "compute result missing baseline id")
    return _Check(
        "compute_path_uses_normalized_trades",
        {
            "source_table": "normalized_trades",
            "excluded_table": "metric_windows",
            "upsert_scope": upsert["scope"],
            "sample_size": upsert["sample_size"],
            "p99_trade_usd": upsert["p99_trade_usd"],
            "baseline_payload": upsert["baseline_payload"],
        },
    )


async def _check_upsert_constraint() -> _Check:
    from pmfi.db.repos.baselines import upsert_baseline

    conn = _FetchRowConn()
    baseline_id = await upsert_baseline(
        conn,
        market_id="11111111-1111-1111-1111-111111111111",
        venue_code="kalshi",
        scope="market",
        lookback_seconds=86400,
        sample_size=2,
        p50_trade_usd=10000.0,
        p95_trade_usd=25000.0,
        p99_trade_usd=26000.0,
        p995_trade_usd=26500.0,
        median_5m_flow_usd=None,
        p99_5m_flow_usd=None,
        baseline_payload={"venue_market_id": "KXBASE-26JUN03"},
    )

    constraint = "market_baselines_market_scope_unique"
    _require(baseline_id == "baseline-smoke-id", "upsert must return the DB baseline id")
    _require(
        f"ON CONFLICT ON CONSTRAINT {constraint}" in conn.sql,
        f"upsert must use {constraint}",
    )
    _require("DO UPDATE SET" in conn.sql, "upsert conflict path must update existing baselines")
    return _Check("baseline_upsert_conflict_constraint", {"constraint": constraint})


def _market_relative_decision(decisions: list[Any]) -> Any:
    matches = [decision for decision in decisions if decision.rule_id == "market_relative_large_trade_v1"]
    _require(matches, "market_relative_large_trade_v1 alert must be emitted")
    return matches[0]


def _check_baseline_available_alert() -> _Check:
    from pmfi.pipeline.engine import AlertEngine

    engine = AlertEngine(
        baselines={
            "polymarket:baseline-ready": {
                "p99_trade_usd": 10000.0,
                "p995_trade_usd": 14000.0,
                "sample_size": 20,
            }
        }
    )
    decision = _market_relative_decision(
        engine.evaluate(
            _market_trade(
                venue_code="polymarket",
                venue_market_id="baseline-ready",
                capital_at_risk_usd="15000",
            )
        )
    )
    _require(decision.data_quality == "baseline_available", "baseline alert data_quality must be baseline_available")
    _require(decision.evidence.get("baseline_status") == "available", "baseline_status must be available")
    _require(
        decision.evidence.get("baseline_state") == "baseline_sufficient",
        "baseline_state must be baseline_sufficient for sample_size >= 10",
    )
    _require(
        "exceeds_p995_baseline" in decision.reason_codes,
        "baseline-aware alert must explain the percentile threshold",
    )
    return _Check(
        "baseline_available_alert",
        {
            "rule_id": decision.rule_id,
            "data_quality": decision.data_quality,
            "baseline_status": decision.evidence["baseline_status"],
            "baseline_state": decision.evidence["baseline_state"],
            "reason_codes": list(decision.reason_codes),
        },
    )


def _check_baseline_missing_alert() -> _Check:
    from pmfi.pipeline.engine import AlertEngine

    decision = _market_relative_decision(
        AlertEngine().evaluate(
            _market_trade(
                venue_code="kalshi",
                venue_market_id="baseline-missing",
                capital_at_risk_usd="12000",
            )
        )
    )
    _require(decision.data_quality == "baseline_pending", "missing baseline data_quality must be baseline_pending")
    _require(
        decision.evidence.get("baseline_status") == "baseline_missing",
        "missing baseline evidence must include baseline_missing status",
    )
    _require(
        decision.evidence.get("baseline_state") == "baseline_missing",
        "missing baseline evidence must include baseline_missing state",
    )
    return _Check(
        "baseline_missing_alert",
        {
            "rule_id": decision.rule_id,
            "data_quality": decision.data_quality,
            "baseline_status": decision.evidence["baseline_status"],
            "baseline_state": decision.evidence["baseline_state"],
        },
    )


def _check_baseline_stale_alert() -> _Check:
    from pmfi.pipeline.engine import AlertEngine

    now = datetime(2026, 6, 17, 12, 0, tzinfo=timezone.utc)
    stale_computed_at = now - timedelta(days=8)
    engine = AlertEngine(
        baselines={
            "polymarket:baseline-stale": {
                "p99_trade_usd": 10000.0,
                "p995_trade_usd": 14000.0,
                "sample_size": 20,
                "computed_at": stale_computed_at.isoformat().replace("+00:00", "Z"),
            }
        }
    )
    decision = _market_relative_decision(
        engine.evaluate(
            _market_trade(
                venue_code="polymarket",
                venue_market_id="baseline-stale",
                capital_at_risk_usd="15000",
                received_at=now,
            )
        )
    )
    _require(decision.confidence == "low", "stale baseline alert confidence must be low")
    _require(decision.data_quality == "baseline_stale", "stale baseline data_quality must be baseline_stale")
    _require(decision.evidence.get("baseline_status") == "baseline_stale", "baseline_status must be stale")
    _require(decision.evidence.get("baseline_state") == "baseline_stale", "baseline_state must be stale")
    _require(
        decision.reason_codes == ("capital_above_minimum_threshold",),
        "stale baseline must not use percentile reason codes",
    )
    _require(
        not {"exceeds_p99_baseline", "exceeds_p995_baseline"}.intersection(decision.reason_codes),
        "stale baseline must not masquerade as fresh percentile evidence",
    )
    return _Check(
        "baseline_stale_alert",
        {
            "rule_id": decision.rule_id,
            "confidence": decision.confidence,
            "data_quality": decision.data_quality,
            "baseline_status": decision.evidence["baseline_status"],
            "baseline_state": decision.evidence["baseline_state"],
            "baseline_age_seconds": decision.evidence["baseline_age_seconds"],
            "baseline_max_age_seconds": decision.evidence["baseline_max_age_seconds"],
            "reason_codes": list(decision.reason_codes),
        },
    )


def _check_volume_spike_history() -> _Check:
    from pmfi.pipeline.engine import AlertEngine

    engine = AlertEngine()
    for _ in range(20):
        engine.evaluate(
            _market_trade(
                venue_code="polymarket",
                venue_market_id="spike-history",
                capital_at_risk_usd="100",
                contracts="200",
            )
        )

    decisions = engine.evaluate(
        _market_trade(
            venue_code="polymarket",
            venue_market_id="spike-history",
            capital_at_risk_usd="6000",
            contracts="12000",
        )
    )
    matches = [decision for decision in decisions if decision.rule_id == "volume_spike_v1"]
    _require(matches, "volume_spike_v1 must emit after prior in-memory baseline history")
    decision = matches[0]
    evidence = decision.evidence
    _require(evidence.get("baseline_trades") == 20, "volume spike baseline must use exactly 20 prior trades")
    _require(evidence.get("baseline_median_usd") == 100.0, "volume spike median must exclude the spike trade")
    _require(evidence.get("this_trade_usd") == 6000.0, "volume spike evidence must include current trade size")
    _require(evidence.get("spike_multiplier") == 60.0, "volume spike multiplier must be based on prior median")
    return _Check(
        "volume_spike_uses_prior_history",
        {
            "baseline_trades": evidence["baseline_trades"],
            "baseline_median_usd": evidence["baseline_median_usd"],
            "this_trade_usd": evidence["this_trade_usd"],
            "spike_multiplier": evidence["spike_multiplier"],
        },
    )


async def _run_checks() -> list[_Check]:
    return [
        await _check_compute_path(),
        await _check_upsert_constraint(),
        _check_baseline_available_alert(),
        _check_baseline_stale_alert(),
        _check_baseline_missing_alert(),
        _check_volume_spike_history(),
    ]


def validate_baseline_payload(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict):
        _fail("proof payload must be a JSON object")
    _require(payload.get("ok") is True, "ok must be true")
    _require(payload.get("status") == "pass", "status must be pass")
    _require(payload.get("source") == SOURCE, f"source must be {SOURCE}")
    checks = payload.get("checks")
    _require(isinstance(checks, list) and checks, "checks must be a non-empty list")
    names: list[str] = []
    for index, check in enumerate(checks, start=1):
        _require(isinstance(check, dict), f"check {index} must be an object")
        name = check.get("name")
        _require(isinstance(name, str) and name, f"check {index} missing name")
        names.append(name)
        _require(check.get("status") == "pass", f"{name} status must be pass")
        _require(isinstance(check.get("details"), dict), f"{name} details must be an object")
    _require(tuple(names) == EXPECTED_CHECKS, "checks must match exactly: " + ", ".join(EXPECTED_CHECKS))
    return payload


def run_baseline_smoke() -> dict[str, Any]:
    payload = {
        "ok": True,
        "status": "pass",
        "source": SOURCE,
        "checks": [check.as_dict() for check in asyncio.run(_run_checks())],
    }
    return validate_baseline_payload(payload)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="baseline_smoke.py")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args(argv)

    try:
        payload = run_baseline_smoke()
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 1

    if args.format == "json":
        print(json.dumps(payload, sort_keys=True))
    else:
        print("baseline-smoke passed: " + ", ".join(check["name"] for check in payload["checks"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
