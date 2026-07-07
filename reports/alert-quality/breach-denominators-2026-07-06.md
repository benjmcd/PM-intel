# BREACH denominator re-derivation - 2026-07-06

Read-only derivation from the local Postgres DB. This report records the exact denominator/filter for `volume_spike_v1`, `directional_cluster_v1`, and `momentum_v1`; it does not write to the DB and does not change rule config.

- DB transaction: `BEGIN READ ONLY`; fingerprint before `{'alerts': 318, 'alert_reviews': 301, 'raw_events': 661380}`; fingerprint after `{'alerts': 318, 'alert_reviews': 301, 'raw_events': 661380}`.
- Latest-review rule: one review per alert using `DISTINCT ON (alert_id) ... ORDER BY reviewed_at DESC, review_id DESC`.
- Governance numerator: `fp + noise` latest labels. Governance denominator: reviewed latest labels in the named cohort.
- Configured `volume_spike_v1.min_trade_usd`: `850` from `config/alert_rules.yaml`.

## Re-derived Figures

| rule | cohort/filter | reviewed denominator | tp | fp | noise | fp+noise numerator | fp+noise / reviewed | categories in numerator | target | status |
|---|---|---:|---:|---:|---:|---:|---:|---|---:|---|
| volume_spike_v1 | current floor: `this_trade_usd >= 850` | 89 | 61 | 4 | 24 | 28 | 31.5% | live_low_notional_thin_baseline=14, live_low_notional_thin_baseline_not_market_outlier=7, cross_market_hedge=4, uncategorized=2, low_notional_thin_near_threshold=1 | 30.0% | BREACH |
| volume_spike_v1 | all reviewed latest labels | 138 | 61 | 4 | 73 | 77 | 55.8% | live_low_notional_thin_baseline=28, low_notional_thin_baseline=23, superseded_below_current_floor=12, live_low_notional_thin_baseline_not_market_outlier=7, cross_market_hedge=4, uncategorized=2, low_notional_thin_near_threshold=1 | 30.0% | BREACH |
| directional_cluster_v1 | all reviewed latest labels | 61 | 49 | 6 | 6 | 12 | 19.7% | uncategorized=6, cross_market_hedge=5, directional_outcome_mismatch=1 | 15.0% | BREACH |
| momentum_v1 | all reviewed latest labels | 36 | 32 | 3 | 1 | 4 | 11.1% | cross_market_hedge=3, uncategorized=1 | 15.0% | OK |

## What Reproduces 31.5%

`31.5%` is reproducible on the current DB when the denominator is the configured current-floor volume-spike cohort: latest reviewed `volume_spike_v1` alerts with parseable `evidence.this_trade_usd >= 850`. That denominator is `89`; the numerator is `28` latest labels in `fp` or `noise` (`4 fp + 24 noise`); `28 / 89 = 31.5%` rounded to one decimal.

The all-reviewed volume-spike denominator is different: `77 / 138 = 55.8%`. The current DB did not reproduce a `51.6%` floor-conditioned denominator in this sweep.

| threshold on `evidence.this_trade_usd` | denominator | fp+noise | rate |
|---:|---:|---:|---:|
| 0 | 138 | 77 | 55.8% |
| 500 | 115 | 54 | 47.0% |
| 800 | 92 | 31 | 33.7% |
| 850 | 89 | 28 | 31.5% |
| 900 | 77 | 24 | 31.2% |
| 1000 | 55 | 10 | 18.2% |

## SQL Shape

```sql
WITH latest_reviews AS (
  SELECT DISTINCT ON (ar.alert_id)
         ar.alert_id, ar.label, ar.false_positive_category, ar.reviewed_at, ar.review_id
  FROM alert_reviews ar
  ORDER BY ar.alert_id, ar.reviewed_at DESC, ar.review_id DESC
)
SELECT a.rule_key, count(*) AS reviewed,
       count(*) FILTER (WHERE lr.label = 'tp') AS tp,
       count(*) FILTER (WHERE lr.label = 'fp') AS fp,
       count(*) FILTER (WHERE lr.label = 'noise') AS noise,
       count(*) FILTER (WHERE lr.label IN ('fp','noise')) AS not_actionable
FROM alerts a
JOIN latest_reviews lr ON lr.alert_id = a.alert_id
WHERE a.rule_key IN ('volume_spike_v1','directional_cluster_v1','momentum_v1')
GROUP BY a.rule_key;
```

For the current-floor volume-spike row, add `a.rule_key = 'volume_spike_v1'` and `nullif(a.evidence->>'this_trade_usd','')::numeric >= 850` to both the denominator and numerator filters.
