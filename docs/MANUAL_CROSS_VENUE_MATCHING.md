# Manual cross-venue matching

The `cross_venue_divergence_v1` monitor alerts when the same real-world event,
priced on two venues (Polymarket and Kalshi), shows a price spread above a
threshold. It is a useful signal: a persistent divergence between two markets
that should agree can indicate stale pricing, a liquidity gap, or directional
flow that has not yet propagated across venues.

## Why matching is manual

Automatic title matching across venues has a high false-match rate (see
`experiments/03_cross_venue_matching.md`). A wrong match produces noise alerts
that erode operator trust. So PMFI does **not** auto-match. Instead, an operator
records reviewed matches in the `market_aliases` table, each with a confidence
score and a rationale. Only active aliases at or above the monitor's confidence
floor are evaluated.

## Recording a match

1. Discover markets on both venues so they exist in the local DB:

   ```powershell
   pmfi markets discover --venue polymarket
   pmfi markets discover --venue kalshi
   ```

2. Find the `venue_market_id` of each side:

   ```powershell
   pmfi markets list --search "bitcoin 100k"
   ```

3. Link them (the `venue_market_id` strings come from step 2):

   ```powershell
   pmfi markets link <polymarket_market_id> <kalshi_ticker> `
     --source-venue polymarket --target-venue kalshi `
     --confidence 0.9 --rationale "Both resolve on BTC >= 100k by 2025-12-31" --by you
   ```

   - `--confidence` is `0..1`. Use `1.0` only when the two markets are exactly
     the same resolution criteria; lower it when the wording differs slightly.
   - `--rationale` is required — it documents *why* the markets are equivalent.

4. List recorded aliases:

   ```powershell
   pmfi markets links
   ```

## How the alert fires

On the ingest daemon's monitoring cycle, the monitor reads each active alias,
takes the latest `last_price` from `market_snapshots` for both markets, and
emits a `cross_venue_divergence_v1` alert when the absolute spread is at or above
`cross_venue_min_spread_cents` (default 3 cents). Alerts appear in
`pmfi alerts list`, the dashboard, and `pmfi report`.

## Caveats

- **Outcome mapping.** The monitor compares each market's latest price directly.
  If the two venues label outcomes differently (e.g. one prices YES and the other
  prices the complementary side), the raw spread is misleading. Only link markets
  whose `last_price` is expressed on the same side; note the mapping in the
  rationale.
- **Snapshot freshness.** Divergence is computed from the most recent snapshot of
  each market. If one venue updates infrequently (Kalshi is polled), a flagged
  divergence may reflect a stale price rather than a live one. Treat cross-venue
  alerts as a prompt to investigate, not a confirmed signal.
- **No automatic suppression or matching.** Matches are operator-curated; remove
  or deactivate an alias by editing `market_aliases.is_active` if a match proves
  wrong.
