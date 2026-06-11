# ADR 0009 — Liquidity wall/vacuum detection scope and limitations

## Status
Accepted (v1, intentionally bounded).

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

## Known limitations (deliberate, documented)
1. **Trade-coupled capture.** Snapshots are taken when a trade fires for a watched
   market with capture enabled. A wall that forms during a quiet period (no trades)
   is not observed. A periodic background book poll would remove this blind spot but
   is out of scope for v1.
2. **Polymarket-only.** Kalshi has no orderbook capture path; cross-venue wall
   detection is not attempted.
3. **Truncation.** Only the levels the `/book` endpoint returns are considered; very
   deep walls beyond the returned levels are not seen.
4. **Staleness.** The snapshot is fetched shortly after the trade and is rate-limited
   per token; the book may have moved. Alerts carry `data_quality=orderbook_snapshot`
   and a note so operators treat them as a prompt to investigate, not a confirmation.

## Consequences
A useful, honest v1 liquidity signal with clearly-bounded confidence. Future work
(periodic book polling, Kalshi capture, depth beyond top-N) can extend it without
changing the alert contract.
