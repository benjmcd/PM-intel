# 02 — Normalization Rules

## Price normalization

- Decimal strings such as `"0.42"` remain `0.42`.
- Cent/integer prices such as `42` should become `0.42` only if venue docs or payload naming confirm cent units.
- Prices outside `[0, 1]` are invalid unless explicitly transformed by a documented venue rule.

## Size normalization

- Store contract count as decimal/numeric.
- Do not confuse contract count with USD risk.
- Compute both capital-at-risk and payout-notional.

## Side normalization

Use two separate fields:

- `aggressor_side`: buy/sell/unknown.
- `directional_side`: yes/no/unknown.

If a venue reports `taker_side`, map it only when semantics are verified. If not verified, use warnings and low confidence.

## Outcome normalization

Outcome keys should be stable internal labels:

- `yes`
- `no`
- or a slugified outcome label for multi-outcome markets.

Do not assume every market is binary forever.

## Timestamp normalization

Normalize to timezone-aware UTC internally. Preserve original raw timestamp in the raw payload.
