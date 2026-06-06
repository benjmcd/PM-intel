# Local-only scope

This repository is currently scoped as a Windows-native local research and alerting system. The binding detailed policy is `docs/governance/08_local_only_exclusion_policy.md`; the accepted decision record is `docs/adr/0007-local-only-scope-and-exclusions.md`.

Do not implement, scaffold, configure, or plan SaaS/hosted-platform capabilities unless a later human-approved ADR proves that the local product cannot progress without a narrow exception.

Excluded by default:

- billing, subscriptions, invoices, payments, hosted billing reconciliation, or pricing-plan enforcement;
- hosted deployment, deployment attestation, cloud deployment, production hosting manifests, registry image attestation, registry push/signing, or public image publishing;
- automatic key rotation, external secret-manager integration, managed vault/KMS setup, or service-account provisioning beyond sane local secret handling;
- full user auth, RBAC, OIDC, SSO, user-account systems, tenants, orgs, teams, or hosted admin panels;
- remote release automation, external CI/CD requirements, hosted observability, or cloud-only storage/queues.

Allowed local equivalents:

- local `.env`/untracked config and `.env.example`;
- local Docker Desktop Postgres;
- local files, local reports, local logs, local metrics tables/views;
- optional read-only venue API credentials supplied through local environment variables;
- local stdout/file delivery and operator-owned local receiver tests.
