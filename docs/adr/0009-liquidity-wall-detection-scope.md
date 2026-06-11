# ADR 0009 — Liquidity wall/vacuum detection scope and limitations

## Status
Accepted (v2, still intentionally bounded).

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

The daemon ingest path also runs periodic Polymarket `/book` polling when
Polymarket ingest is active and `features.enable_orderbook_reconstruction` is
true. The poller uses the current watched Polymarket token IDs from
`market_outcomes`, writes the same `orderbook_snapshots` / `orderbook_levels`
tables, and may emit the same `liquidity_wall_v1` alert with evidence noting
that the snapshot came from a periodic poll.

## Known limitations (deliberate, documented)
1. **Partial quiet-period coverage.** `pmfi ingest` can observe watched
   Polymarket token books periodically when Polymarket ingest and orderbook
   reconstruction are enabled, but `pmfi live --orderbook` remains trade-coupled
   and no orderbook polling runs unless the operator has watched markets with
   populated token IDs.
2. **Polymarket-only.** Kalshi has no orderbook capture path; cross-venue wall
   detection is not attempted.
3. **Truncation.** Only the levels the `/book` endpoint returns are considered; very
   deep walls beyond the returned levels are not seen.
4. **Staleness.** Snapshots are sampled and rate-limited per token; the book may
   have moved. Alerts carry `data_quality=orderbook_snapshot` and a note so
   operators treat them as a prompt to investigate, not a confirmation.

## Consequences
A useful, honest liquidity signal with clearly-bounded confidence. Future work
(Kalshi capture, depth beyond top-N, richer polling controls) can extend it
without changing the alert contract.
