# 01 — Feed Assumptions and Blockers

## Known assumptions

- Polymarket public market data can be read without trading credentials for the relevant MVP read paths.
- Kalshi live WebSocket market data requires credentialed connection setup even for public market-data channels.
- Kalshi account/user identity is not publicly exposed in the same way as Polymarket wallet/holder data.
- Full order-book reconstruction is harder and lower priority than executed-trade monitoring.
- Historical baselines are mandatory for high-quality anomaly alerts.

## Blockers to resolve before live adapter confidence

- Exact current Polymarket payload shapes for trade/book/ticker events.
- Exact current Kalshi payload shapes for trade/ticker/orderbook_delta events.
- Whether stable trade IDs are available per venue/channel.
- Whether exchange timestamps are present and millisecond-granular.
- Whether REST backfill can reconcile all WebSocket trades.
- Current rate limits and retry semantics.
- Redistribution is out of scope. Re-check terms only if the user explicitly approves a future non-local scope change.

## Required feed validation experiment

Before trusting live alerts, run `experiments/01_feed_validation.md` and produce a report comparing:

- WebSocket capture;
- REST recent trades;
- historical/backfill endpoints if available;
- duplicate/missing events;
- timestamp lag;
- schema drift.
