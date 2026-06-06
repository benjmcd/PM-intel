# 00 — Product Scope

## Product statement

A Windows-native, local-only anomaly triage system for prediction-market order flow. It monitors market data, identifies unusual executed flow, and emits explainable local alerts for manual investigation.

## The product is not

- a trading bot;
- a financial adviser;
- an insider detector;
- a copy-trading tool;
- a public market-data resale product;
- a hosted/SaaS product;
- a multi-user account system;
- a replacement for human interpretation.

## Local-only non-goals

Current implementation must not include SaaS billing, hosted billing reconciliation, hosted runtime/application work, hosted attestation, published registry image attestation, registry push/signing/provenance requirements, automatic key rotation systems, external secret-manager integrations, RBAC/OIDC, or full user-account systems.

## Useful alert definition

A useful alert is not merely large. It is large or abnormal in context.

Required context includes at least some of:

- absolute capital-at-risk;
- payout-notional;
- market-relative percentile;
- open-interest-relative size;
- 24h-volume-relative size;
- price impact;
- directional clustering;
- liquidity/spread context;
- proximity to resolution;
- data-quality status.

## MVP alert types

1. Absolute large executed trade.
2. Market-relative trade-size anomaly.
3. Directional flow cluster.
4. Open-interest-relative shock.
5. Price-impact confirmation.
6. Data-quality degradation alert.

## Later local-only alert types

- liquidity wall/vacuum;
- Polymarket wallet/holder accumulation from public data;
- cross-venue divergence;
- category-specific anomaly rules.

News/calendar enrichment is deferred until the local data path is proven and an ADR defines acceptable data sources.
