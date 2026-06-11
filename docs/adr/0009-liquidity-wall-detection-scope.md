# ADR 0009 — Liquidity wall/vacuum detection scope and limitations

## Status
Accepted (v3, still intentionally bounded).

## Context
Product scope lists "liquidity wall/vacuum" as a later local-only alert type. The
repo already captures Polymarket orderbook snapshots (`orderbook_snapshots` /
`orderbook_levels`) on the opt-in capture path. We want a useful first version
without over-claiming.

## Decision
Add `liquidity_wall_v1`, evaluated by `assess_liquidity` in
`src/pmfi/pipeline/liquidity.py` and emitted from the orderbook-capture path in
`process_event`. It flags:
- a **wall**: top-N resting USD on the heavier side of book >= `min_wall_usd`;
- optionally a **vacuum**: spread >= `min_spread` (off by default).

It is config-gated (`config/alert_rules.yaml: liquidity_wall_v1`) and runs only when
orderbook capture is enabled. Emission is wrapped by the existing non-fatal handler,
so it can never break trade ingestion.

The daemon ingest path also runs periodic orderbook polling when
`features.enable_orderbook_reconstruction` is true for active ingest venues:

- **Polymarket:** polls `/book` for watched token IDs from `market_outcomes`.
- **Kalshi:** polls the REST market orderbook endpoint for watched tickers. The
  response returns YES and NO bid ladders only, so PMFI reconstructs implied asks
  from complementary bids before writing the existing bid/ask snapshot contract.

Both venues write the same `orderbook_snapshots` / `orderbook_levels` tables and
may emit the same `liquidity_wall_v1` alert with evidence noting the snapshot
source. Operators can tune the periodic poll cadence with
`ingestion.orderbook_poll_interval_seconds`; Kalshi REST depth is controlled by
`ingestion.kalshi_orderbook_depth` and clamped by the fetcher to the
venue-supported range.

## Known limitations (deliberate, documented)
1. **Partial quiet-period coverage.** `pmfi ingest` can observe watched venue
   books periodically when that venue is active and orderbook reconstruction is
   enabled, but `pmfi live --orderbook` remains Polymarket trade-coupled capture.
2. **Kalshi implied asks.** Kalshi REST returns YES/NO bids, not explicit asks.
   PMFI stores reconstructed ask levels and marks snapshots as reconstructed.
3. **Truncation.** Only the levels the venue endpoint returns are considered; very
   deep walls beyond the returned depth are not seen.
4. **Staleness.** Snapshots are sampled and rate-limited per token; the book may
   have moved. Alerts carry `data_quality=orderbook_snapshot` and a note so
   operators treat them as a prompt to investigate, not a confirmation.

## Consequences
A useful, honest liquidity signal with clearly-bounded confidence. Future work
(adaptive per-venue polling, deeper historical orderbook analysis, WebSocket
orderbook deltas) can extend it without changing the alert contract.
