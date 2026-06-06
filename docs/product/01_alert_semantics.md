# 01 — Alert Semantics

## Alert payload standard

Each alert should include:

```json
{
  "title": "Large directional flow detected",
  "venue": "kalshi",
  "market": "...",
  "side": "yes",
  "severity": "medium",
  "confidence": "medium",
  "score": 0.82,
  "capital_at_risk_usd": "29520.00",
  "payout_notional_usd": "72000.00",
  "reason_codes": ["large_absolute", "price_impact", "directional_cluster"],
  "data_quality": "complete",
  "rule_id": "flow_cluster_v1",
  "evidence": { }
}
```

## Severity levels

- `info`: notable but low urgency.
- `low`: probably worth logging only.
- `medium`: worth human review.
- `high`: likely urgent/high-signal.
- `critical`: reserved for extreme abnormality or system/data-quality emergencies.

## Confidence levels

- `low`: sparse data, partial semantics, weak baseline, or degraded feed.
- `medium`: enough data for review but some uncertainty remains.
- `high`: strong baseline, complete data, clear abnormality.

## Required wording discipline

Use:

- "abnormal flow detected"
- "large relative to baseline"
- "worth review"

Do not use:

- "insider detected"
- "smart money guarantee"
- "trade this"
- "certain signal"
