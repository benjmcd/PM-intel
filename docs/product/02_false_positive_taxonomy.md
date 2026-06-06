# 02 — False-Positive Taxonomy

## Known false-positive classes

1. Market-maker inventory adjustment.
2. Arbitrage leg across venues or related markets.
3. Public-news reaction.
4. Resolution cleanup.
5. Low-price lottery trade with large contract count but small capital at risk.
6. Cross-market hedge.
7. Duplicated or replayed feed event.
8. Stale market metadata.
9. Thin-market distortion.
10. Resting order cancellation if non-executed book changes are treated as real flow.

## Required false-positive controls

- Prefer executed trades over resting liquidity for MVP.
- Require context fields before high-severity alerts.
- Include data-quality status.
- Use suppression windows.
- Review alert outcomes and store labels.
- Backtest candidate thresholds before trusting them.
