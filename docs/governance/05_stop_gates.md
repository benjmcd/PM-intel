# 05 — Stop Gates

Agents should stop implementation and record a blocker only when a step crosses one of these boundaries. Do not use this file to stop ordinary local implementation work.

## Product stop gates

- Automated trading or order placement.
- Copy-trading or trading advice claims.
- Claims that alerts identify insiders or guaranteed smart money.
- Paid redistribution of raw market data without license review.
- Collection or inference of private user/account identity.
- Any hosted/SaaS/multi-tenant direction while local-only progress remains possible.

## Local-only stop gates

Stop before implementing or scaffolding:

- SaaS billing, subscription, entitlement, payment, invoice, or hosted billing reconciliation logic.
- Hosted application/runtime work.
- Hosted deployment attestation.
- Published registry image attestation.
- Registry push/signing/provenance requirements.
- External secret manager integration.
- Automatic key rotation systems.
- Full user auth, RBAC, OIDC, organizations, teams, tenants, or user-account systems.
- External notification SaaS adapters as default delivery.
- Cloud databases, queues, object storage, observability, or managed control planes.

## Technical stop gates

- External API semantics would affect persisted schema and are not verified.
- A normalizer cannot distinguish price side, outcome, or contract units with confidence.
- A WebSocket/order-book state machine would be guessed rather than tested.
- A change would make replay impossible without a written temporary-spike note and payback plan.
- A test is weakened or deleted to pass.

## Architecture stop gates

- Kafka, ClickHouse, Kubernetes, hosted services, or ML pipelines added before a written gate permits them.
- Venue-specific fields leak into cross-venue scoring logic without mapping.
- Alert logic depends on live API calls inside unit tests.
- Secrets are committed or required for local tests.

## Required blocker entry

```markdown
### Blocker
- Gate violated or at risk:
- Why this matters:
- Evidence:
- Local substitute or narrow next step:
- Is a future ADR justified:
```
