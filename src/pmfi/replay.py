from __future__ import annotations
import asyncio
from dataclasses import dataclass
from pathlib import Path
from pmfi.domain import RawEvent, NormalizedTrade, AlertDecision
from pmfi.fixtures import load_raw_event
from pmfi.normalization import NormalizationError
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
    baselines: dict | None = None,
    enable_corroboration: bool = False,
) -> list[ReplayResult]:
    engine = AlertEngine(
        rules_path=rules_path,
        baselines=baselines,
        enable_corroboration=enable_corroboration,
    )
    results: list[ReplayResult] = []
    for path in sorted(fixture_dir.glob("*.json")):
        try:
            raw = load_raw_event(path)
        except Exception as exc:
            if verbose:
                print(f"  skip {path.name}: {exc}")
            continue
        try:
            trade = normalize_event(raw)
        except NormalizationError as exc:
            if verbose:
                print(f"  norm error {path.name}: {exc}")
            continue
        if trade is None:
            continue  # benign non-trade / unsupported venue event
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
    baselines: dict | None = None,
    enable_corroboration: bool = False,
) -> list[ReplayResult]:
    """Replay fixtures through the full async DB pipeline (proves M2-M4 write path)."""
    from pmfi.pipeline.runner import process_event
    from pmfi.baseline import load_baselines
    from pmfi.db.migrations import startup_maintenance

    await startup_maintenance(pool)  # type: ignore[arg-type]

    if baselines is None:
        baselines = {}
        try:
            baselines = await load_baselines(pool)  # type: ignore[arg-type]
            if verbose and baselines:
                print(f"  loaded {len(baselines)} baseline(s) from DB")
        except Exception:
            pass

    engine = AlertEngine(
        rules_path=rules_path,
        baselines=baselines,
        enable_corroboration=enable_corroboration,
    )
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

        try:
            trade = normalize_event(raw)
        except NormalizationError as exc:
            if verbose:
                print(f"  norm error (skipping result) {path.name}: {exc}")
            continue
        if trade is not None:
            # Alerts were already evaluated and persisted inside process_event.
            # Do not re-call engine.evaluate() here — it would double-feed the
            # accumulator and cause cluster/momentum/volume_spike to fire on
            # half the real trade count.
            results.append(ReplayResult(fixture_path=str(path), trade=trade, alerts=[]))
            if verbose:
                print(f"  [persist] {path.name} → persisted")

    return results


async def replay_from_db(
    pool: object,
    *,
    rules_path: Path | None = None,
    limit: int = 100,
    verbose: bool = False,
    baselines: dict | None = None,
    start_ts=None,
    end_ts=None,
    venue: str | None = None,
    market: str | None = None,
    persist: bool = False,
    seed: bool = True,
    enable_corroboration: bool = False,
) -> list[ReplayResult]:
    """Re-run alert evaluation over raw_events stored in Postgres.

    Args:
        limit: max rows to process; 0 means unlimited (batched fetch).
        start_ts: lower bound on COALESCE(exchange_ts, received_at) (inclusive).
        end_ts: upper bound on COALESCE(exchange_ts, received_at) (inclusive).
        venue: filter by venue_code.
        market: filter by venue_market_id.
        persist: when True, route events through the full process_event DB path
            (idempotent due to per-trade dedup). When False (default), in-memory
            evaluation only; no DB writes.
        seed: when True (default) and start_ts is set, pre-populate accumulators
            from normalized_trades before start_ts, and seed the suppression cache
            from recent DB alerts so persist replay reproduces live decisions
            (cluster/momentum/volume_spike see warm state, suppression mirrors live
            behaviour). Applies to both persist=True and persist=False paths.
            Pass False for cold-start replay (e.g. to compare seeded vs unseeded
            behaviour in tests).
    """
    from datetime import datetime, timezone
    import json as _json

    if baselines is None:
        baselines = {}
        try:
            from pmfi.baseline import load_baselines
            baselines = await load_baselines(pool)  # type: ignore[arg-type]
        except Exception:
            pass

    engine = AlertEngine(
        rules_path=rules_path,
        baselines=baselines,
        enable_corroboration=enable_corroboration,
    )

    # Build parameterized WHERE clause
    conditions: list[str] = []
    params: list = []

    def _add(expr: str, val: object) -> None:
        params.append(val)
        conditions.append(expr.replace("?", f"${len(params)}"))

    if start_ts is not None:
        _add("COALESCE(exchange_ts, received_at) >= ?", start_ts)
    if end_ts is not None:
        _add("COALESCE(exchange_ts, received_at) <= ?", end_ts)
    if venue is not None:
        _add("venue_code = ?", venue)
    if market is not None:
        _add("venue_market_id = ?", market)

    where_sql = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    order_sql = "ORDER BY COALESCE(exchange_ts, received_at), received_at, raw_event_id"

    _BATCH = 2000  # rows per fetch for unlimited/large runs
    unlimited = (limit == 0)

    results: list[ReplayResult] = []
    total_fetched = 0

    # Seed accumulators from historical trades BEFORE the replay window so
    # the first replayed event sees warm cluster/momentum/volume_spike state.
    # Applies to both persist and read-only paths; seed=False opts out.
    if seed and start_ts is not None:
        try:
            await engine.seed_from_db(pool, before_ts=start_ts)  # type: ignore[attr-defined]
        except Exception as _seed_exc:
            if verbose:
                print(f"  [seed] warning: {_seed_exc}")

    _persist_suppression: dict | None = None
    if persist:
        from pmfi.pipeline.runner import process_event

        async def _noop(decision: AlertDecision, venue_code: str, market_id: str | None) -> None:
            pass

        # Seed suppression cache from DB so persist replay reproduces live
        # suppression behaviour (mirrors run_adapter_pipeline's seeding).
        if seed:
            try:
                async with pool.acquire() as _supp_conn:  # type: ignore[attr-defined]
                    from pmfi.db.repos.alerts import load_suppression_cache
                    _persist_suppression = await load_suppression_cache(
                        _supp_conn, window_seconds=300
                    )
                    if verbose and _persist_suppression:
                        print(f"  [seed] suppression cache seeded: {len(_persist_suppression)} entry(ies)")
            except Exception as _supp_exc:
                if verbose:
                    print(f"  [seed] suppression cache warning: {_supp_exc}")
                _persist_suppression = {}
        else:
            _persist_suppression = None

    offset = 0
    while True:
        batch_limit = _BATCH if unlimited else min(_BATCH, limit - total_fetched)
        if batch_limit <= 0:
            break

        fetch_params = list(params) + [batch_limit, offset]
        n_base = len(params)
        batch_sql = (
            "SELECT venue_code, source_channel, source_event_type, source_event_id, "
            "       venue_market_id, exchange_ts, received_at, payload "
            f"FROM raw_events {where_sql} {order_sql} "
            f"LIMIT ${n_base + 1} OFFSET ${n_base + 2}"
        )

        async with pool.acquire() as conn:  # type: ignore[attr-defined]
            rows = await conn.fetch(batch_sql, *fetch_params)

        if not rows:
            break

        for row in rows:
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

            if persist:
                try:
                    await process_event(  # type: ignore[arg-type]
                        raw, pool, engine, _noop,
                        suppression=_persist_suppression,
                        suppression_window_seconds=300,
                    )
                except Exception as exc:
                    if verbose:
                        print(f"  DB error {row['venue_market_id']}: {exc}")
                try:
                    trade = normalize_event(raw)
                except NormalizationError:
                    continue
                if trade is None:
                    continue
                results.append(ReplayResult(fixture_path=f"db:{row['venue_market_id']}", trade=trade, alerts=[]))
                if verbose:
                    print(f"  [persist] {row['venue_market_id']} → persisted")
            else:
                try:
                    trade = normalize_event(raw)
                except NormalizationError as exc:
                    if verbose:
                        print(f"  norm error (skipping) {row['venue_market_id']}: {exc}")
                    continue
                if trade is None:
                    if verbose:
                        print(f"  normalization failed for {row['venue_market_id']}")
                    continue
                decisions = engine.evaluate(trade)
                results.append(ReplayResult(fixture_path=f"db:{row['venue_market_id']}", trade=trade, alerts=decisions))
                if verbose:
                    for d in decisions:
                        print(f"  ALERT {d.rule_id} {d.severity} score={d.score} [from_db]")

        total_fetched += len(rows)
        offset += len(rows)

        if verbose and total_fetched % 1000 == 0:
            print(f"  replay: {total_fetched} processed...", flush=True)

        if len(rows) < _BATCH:
            # Last batch: fewer rows than requested means we've exhausted the result set
            break
        if not unlimited and total_fetched >= limit:
            break

    if total_fetched:
        print(f"  replay: {total_fetched} raw_event(s) processed")
    return results


async def _noop_callback(decision: AlertDecision, venue_code: str, market_id: str | None) -> None:
    pass
