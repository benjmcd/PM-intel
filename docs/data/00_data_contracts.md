# 00 — Data Contracts

## Normalized units

All probability prices should be normalized to decimal probability-like units in `[0, 1]`.

All contract counts should be stored as numeric quantities.

All timestamps should store:

- exchange timestamp where available;
- received timestamp;
- processed timestamp for derived objects.

## Notional definitions

Prediction-market notional can be misleading. Store both:

```text
capital_at_risk = contracts × price
payout_notional = contracts × 1.00
```

For a NO-side trade, normalize carefully according to the venue's representation. If the side/outcome semantics are uncertain, set `side_confidence = low` and add a warning instead of guessing confidently.

## Required normalized trade fields

- `venue_code`
- `venue_trade_id` where available
- `market_id` or `venue_market_id`
- `outcome_key`
- `price`
- `contracts`
- `capital_at_risk_usd`
- `payout_notional_usd`
- `directional_side`
- `aggressor_side`
- `side_confidence`
- `exchange_ts`
- `received_at`
- `source_raw_event_id` or fixture source
- `warnings`

## Data-quality statuses

Use these labels consistently:

- `complete`
- `partial`
- `degraded`
- `stale`
- `unverified`
- `reconstructed`
- `failed`

Do not emit high-confidence alerts from `degraded`, `stale`, or `failed` data without explicit rule-level permission.
