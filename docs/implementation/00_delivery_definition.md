# 00 — Delivery Definition

## End-state target for current local-only horizon

A working local-first product that can:

1. run in a Windows local directory with local Postgres;
2. ingest or replay raw venue events;
3. normalize executed trades into a stable schema;
4. compute rolling market metrics;
5. detect abnormal flow with transparent rules;
6. emit alerts through local outputs;
7. replay historical data and reproduce alerts;
8. report feed/data-quality issues;
9. avoid external network calls during normal tests.

## Minimum product for first useful release

A first useful local release includes:

- CLI commands:
  - initialize DB;
  - import fixtures;
  - normalize raw events;
  - compute metrics;
  - score alerts;
  - replay a time window;
  - print/export alerts.
- Postgres schema and migrations.
- Fixture-driven tests for Polymarket and Kalshi trade payloads.
- Console and local file alert delivery.
- Optional localhost-only HTTP receiver adapter for local integration tests.
- Documentation for opt-in live API setup; live API usage remains disabled in default verification.

## Not required and explicitly excluded for current implementation

- Web UI before CLI/reports are useful.
- Full order-book reconstruction.
- Wallet clustering.
- Cross-venue semantic matching.
- ML scoring.
- Hosted runtime/application work.
- Hosted deployment attestation.
- Published registry image attestation.
- Registry push/signing/provenance requirements.
- SaaS billing, payment, subscription, or hosted billing reconciliation logic.
- Full user auth, RBAC, OIDC, organization/team/tenant, or user-account system.
- External secret manager or automatic key rotation system.
- External notification SaaS adapters by default.
- SMS/phone alerts.
- Automated trading.
