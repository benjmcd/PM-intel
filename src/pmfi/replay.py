from __future__ import annotations
import asyncio
from dataclasses import dataclass
from pathlib import Path
from pmfi.domain import RawEvent, NormalizedTrade, AlertDecision
from pmfi.fixtures import load_raw_event
from pmfi.normalization import normalize_polymarket_fixture, normalize_kalshi_fixture, NormalizationError
from pmfi.pipeline.engine import AlertEngine
from pmfi.pipeline.normalize import normalize_event

@dataclass
class ReplayResult:
    fixture_path: str
    trade: NormalizedTrade
    alerts: list[AlertDecision]

def replay_fixtures(
    fixture_dir: Path,
    *,
    rules_path: Path | None = None,
    verbose: bool = False,
) -> list[ReplayResult]:
    engine = AlertEngine(rules_path=rules_path)
    results: list[ReplayResult] = []
    for path in sorted(fixture_dir.glob("*.json")):
        try:
            raw = load_raw_event(path)
        except Exception as exc:
            if verbose:
                print(f"  skip {path.name}: {exc}")
            continue
        try:
            if raw.venue_code == "polymarket":
                trade = normalize_polymarket_fixture(raw)
            elif raw.venue_code == "kalshi":
                trade = normalize_kalshi_fixture(raw)
            else:
                continue
        except NormalizationError as exc:
            if verbose:
                print(f"  norm error {path.name}: {exc}")
            continue
        decisions = engine.evaluate(trade)
        results.append(ReplayResult(fixture_path=str(path), trade=trade, alerts=decisions))
        if verbose:
            for d in decisions:
                print(f"  ALERT {d.rule_id} {d.severity} score={d.score}")
    return results


async def replay_fixtures_persist(
    fixture_dir: Path,
    pool: object,
    *,
    rules_path: Path | None = None,
    verbose: bool = False,
) -> list[ReplayResult]:
    """Replay fixtures through the full async DB pipeline (proves M2-M4 write path)."""
    from pmfi.pipeline.runner import process_event
    from pmfi.baseline import load_baselines
    from pmfi.db.migrations import startup_maintenance

    await startup_maintenance(pool)  # type: ignore[arg-type]

    baselines: dict = {}
    try:
        baselines = await load_baselines(pool)  # type: ignore[arg-type]
        if verbose and baselines:
            print(f"  loaded {len(baselines)} baseline(s) from DB")
    except Exception:
        pass

    engine = AlertEngine(rules_path=rules_path, baselines=baselines)
    results: list[ReplayResult] = []

    for path in sorted(fixture_dir.glob("*.json")):
        try:
            raw = load_raw_event(path)
        except Exception as exc:
            if verbose:
                print(f"  skip {path.name}: {exc}")
            continue

        try:
            await process_event(raw, pool, engine, _noop_callback)
        except Exception as exc:
            if verbose:
                print(f"  DB error {path.name}: {exc}")

        trade = normalize_event(raw)
        if trade is not None:
            decisions = engine.evaluate(trade)
            results.append(ReplayResult(fixture_path=str(path), trade=trade, alerts=decisions))
            if verbose:
                for d in decisions:
                    print(f"  ALERT {d.rule_id} {d.severity} score={d.score} [persisted]")

    return results


async def replay_from_db(
    pool: object,
    *,
    rules_path: Path | None = None,
    limit: int = 100,
    verbose: bool = False,
) -> list[ReplayResult]:
    """Re-run alert evaluation over raw_events stored in Postgres."""
    baselines: dict = {}
    try:
        from pmfi.baseline import load_baselines
        baselines = await load_baselines(pool)  # type: ignore[arg-type]
    except Exception:
        pass

    engine = AlertEngine(rules_path=rules_path, baselines=baselines)
    results: list[ReplayResult] = []

    async with pool.acquire() as conn:  # type: ignore[attr-defined]
        rows = await conn.fetch(
            "SELECT venue_code, source_channel, source_event_type, source_event_id, "
            "       venue_market_id, exchange_ts, received_at, payload "
            "FROM raw_events ORDER BY COALESCE(exchange_ts, received_at), received_at, raw_event_id LIMIT $1",
            limit,
        )

    from datetime import datetime, timezone
    import json as _json

    total = len(rows)
    for i, row in enumerate(rows, 1):
        if i % 10 == 0 or i == total:
            print(f"  replay: {i}/{total}", end="\r", flush=True)

        try:
            payload_raw = row["payload"]
            if isinstance(payload_raw, str):
                payload = _json.loads(payload_raw) if payload_raw else {}
            else:
                payload = dict(payload_raw) if payload_raw else {}
            raw = RawEvent(
                venue_code=row["venue_code"],  # type: ignore[arg-type]
                source_channel=row["source_channel"],
                source_event_type=row["source_event_type"],
                source_event_id=row["source_event_id"],
                venue_market_id=row["venue_market_id"],
                exchange_ts=row["exchange_ts"],
                received_at=row["received_at"] or datetime.now(tz=timezone.utc),
                payload=payload,
            )
        except Exception as exc:
            if verbose:
                print(f"  skip db row: {exc}")
            continue

        trade = normalize_event(raw)
        if trade is None:
            if verbose:
                print(f"  normalization failed for {row['venue_market_id']}")
            continue

        decisions = engine.evaluate(trade)
        results.append(ReplayResult(fixture_path=f"db:{row['venue_market_id']}", trade=trade, alerts=decisions))
        if verbose:
            for d in decisions:
                print(f"  ALERT {d.rule_id} {d.severity} score={d.score} [from_db]")

    if total:
        print()
    return results


async def _noop_callback(decision: AlertDecision, venue_code: str, market_id: str | None) -> None:
    pass
