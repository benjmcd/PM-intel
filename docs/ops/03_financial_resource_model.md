# 03 — Financial and Resource Model

## MVP cost profile

The current build horizon should be materially local and cheap:

- Windows local machine;
- Docker Desktop local Postgres;
- no paid data provider by default;
- local console/file/report alert delivery;
- no hosted runtime, hosted database, external notification provider, or SaaS control plane.

## Cost drivers to measure locally

Ranked:

1. Historical data/backfill depth.
2. Full order-book capture and retention.
3. Paid third-party indexed/on-chain data, if ever justified by signal quality.
4. Local machine CPU, memory, and disk requirements.
5. Live-feed reconnection/backfill complexity.
6. Human maintenance cost from false positives.

## Explicitly excluded cost categories for current implementation

Do not model or implement costs for SaaS billing, hosted billing reconciliation, hosted runtime operation, registry publishing/signing/attestation, external secret managers, user-account systems, RBAC/OIDC, SMS, email-provider delivery, Slack/Discord/Telegram delivery, or public multi-user dashboards.

## Go/no-go economic test

Before heavy investment, prove locally:

- alerts are materially better than existing whale feeds;
- false-positive rate is acceptable;
- useful alert frequency justifies maintenance;
- local Postgres remains operationally sufficient;
- any paid data source improves signal enough to justify cost and has a local-only integration path.
