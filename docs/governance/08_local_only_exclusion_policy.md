# 08 — Local-Only Exclusion Policy

## Scope rule

This repository is a Windows-native, local-only research and alerting system until a future user-approved ADR changes that boundary. Agents must optimize for local usefulness, local Postgres durability, fixture replay, transparent alerting, and manual operation.

The default answer to hosted, multi-user, paid, externally managed, or platformization work is **out of scope**.

## Explicitly excluded implementation categories

Do not plan, scaffold, implement, or add dependencies for any of the following unless current local product functionality cannot progress without it and the user explicitly approves a new ADR:

- SaaS packaging, hosted product architecture, or multi-customer tenancy.
- Billing, subscription management, payment processing, invoicing, entitlement systems, or hosted billing reconciliation.
- Hosted application/runtime deployment, hosted deployment attestation, or remote release promotion.
- Published container registry workflows, registry push steps, registry image attestation, image signing, release signing, provenance, or release-publishing requirements.
- Automatic key rotation, external secret-manager integrations, vault services, or managed secret stores beyond sane local `.env` / ignored local config handling.
- Full user authentication, RBAC, OIDC/OAuth, organization accounts, team accounts, tenant systems, user-account systems, or account-administration systems.
- Cloud databases, cloud-provider infrastructure, managed databases, managed queues, managed object storage, Kubernetes, service meshes, distributed production infrastructure, hosted observability, or external-control-plane services.
- External CI/CD or hosted release automation.
- SMS, email-provider, Slack, Discord, Telegram, or other external notification SaaS integrations as default implementation work.
- Public multi-user dashboards, admin consoles, or public API productization for third-party users.

## Allowed local equivalents

The following are allowed because they directly support local utility:

- Local `.env.example` and gitignored local config.
- Docker Desktop for local Postgres.
- Python verification commands.
- Local database reset/init/verify utilities.
- Console, file, JSONL, or localhost-only HTTP receiver alert delivery.
- Optional live read-only venue probes behind explicit local environment flags.
- Local CLI, local reports, and eventually a local-only dashboard after CLI/reporting are useful.
- Documentation explaining that excluded categories are out of scope.

## Escalation rule

If an agent believes an excluded category is necessary, it must stop and add a blocker entry to `WORKLOG.md` with:

```markdown
### Local-only scope blocker
- Excluded category requested or implied:
- Why local functionality cannot progress without it:
- Local alternative attempted:
- Narrowest proposed exception:
- Files that would be affected:
```

No implementation may proceed until the user approves the exception and an ADR is added.

## Review checklist

During each verification/review pass, confirm:

- Local fixture and Postgres workflows still work without external services.
- No hosted deployment, billing, registry, release-signing, managed-secret, external notification SaaS, or full auth/account subsystem has been introduced.
- Any secrets remain local, optional, and gitignored.
- New dependencies serve current local functionality rather than hypothetical SaaS readiness.


## Scope phrase guard

Required local-only exclusions: local-only, billing, hosted deployment, registry image attestation, automatic key rotation, external secret-manager, RBAC, OIDC.
