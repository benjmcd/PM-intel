# Experiment 02 — Baseline Backtest

## Goal
Determine whether candidate alert rules produce useful alert volume and acceptable false-positive rates.

## Procedure

1. Replay at least 7 days of captured/fixture/historical trades.
2. Compute trade-size percentiles by market, category, and liquidity tier.
3. Run candidate rules.
4. Classify alerts using false-positive taxonomy.
5. Report alert count, severity distribution, and top failure modes.

## Output

`reports/baseline_backtest_YYYYMMDD.md`
