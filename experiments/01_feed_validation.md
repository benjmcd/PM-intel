# Experiment 01 — Feed Validation

## Goal
Prove whether live venue feeds are complete and reliable enough for alerting.

## Inputs

- Polymarket live market/trade feed.
- Kalshi live trade/ticker feed.
- REST backfill/recent trade endpoints.
- A watchlist of liquid markets.

## Procedure

1. Run collectors for 24–72 hours.
2. Store raw payloads.
3. Periodically backfill recent trades using REST.
4. Compare WebSocket-captured trades to REST/historical trades.
5. Measure duplicates, missing events, timestamp lag, and parser failures.

## Pass criteria

- At least 99.5% event agreement after dedupe and timestamp tolerance.
- Parser failure rate is explainable and fixture-covered.
- Reconnect gaps are detected and marked degraded.
- No high-confidence alerts during degraded feed periods.

## Output

`reports/feed_validation_YYYYMMDD.md`
