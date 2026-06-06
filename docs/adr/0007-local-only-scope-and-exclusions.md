# ADR 0007: Local-only scope and SaaS exclusions

## Status
Accepted

## Context

The project is being advanced inside a Windows local directory by AI coding agents. Its near-term purpose is a local research/alerting system, not a hosted product. Hosted/SaaS scaffolding would create context bloat, premature architecture, unnecessary security surface, and irrelevant implementation work.

## Decision

Until a future explicit user-approved ADR changes scope, PMFI will remain local-only. Agents must not add SaaS packaging, hosted runtime/application work, billing or payment systems, hosted billing reconciliation, registry publication/signing/attestation, external secret-management, automatic key rotation, full user-auth/RBAC/OIDC/account systems, external notification SaaS integrations, or public multi-user dashboards.

Allowed scope remains: local Postgres, fixture replay, local CLI/operator workflows, local verification, local alert outputs, local reports, localhost-only receiver tests, and optional live read-only venue probes behind explicit local flags.

## Consequences

- Postgres remains the primary local state engine.
- Verification remains Python-command based and local.
- Remote platform concerns are stop gates, not roadmap items.
- Local secret handling is limited to `.env.example`, gitignored local config, and environment variables.
- Agents must prefer local replacements before proposing any excluded category.
- Delivery work starts with console/file/JSONL/localhost-only outputs.
- Any future exception requires a new user-approved ADR explaining why local-only progress is blocked.


## Scope phrase guard

Required local-only exclusions: local-only, billing, hosted deployment, registry image attestation, automatic key rotation, external secret-manager, RBAC, OIDC.
