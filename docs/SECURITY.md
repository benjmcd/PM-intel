# Security and compliance boundaries

## Hard boundaries

- No automated trading or order placement in MVP.
- No private account scraping.
- No secrets in git.
- No default live API/network calls during tests.
- No paid data redistribution unless separately reviewed.
- No claims that alerts identify insiders or guarantee predictive value.
- No SaaS, hosted, multi-tenant, or external-control-plane features in the current implementation horizon.

## Secret handling

- `.env*` files are ignored and must not be committed.
- Example configuration lives in `.env.example` and `config/*.example.yaml`.
- API keys, if needed for read-only live smoke checks, must come from environment variables or local untracked config.
- External secret managers and automatic key rotation systems are excluded unless a future ADR proves they are functionally unavoidable.

## External data handling

- Preserve raw public payloads with source, timestamp, and data-quality status.
- Respect venue API terms, rate limits, and geoblocking restrictions.
- Live adapters must be read-only until a future ADR explicitly changes that boundary.

## Excluded security/product systems

Do not scaffold or implement:

- full user authentication;
- RBAC;
- OIDC;
- tenants, organizations, teams, or user-account systems;
- hosted runtime security controls;
- hosted deployment attestation;
- published registry image attestation;
- registry push/signing/provenance workflows;
- external secret-manager integration;
- automatic key rotation.

## Review triggers

Create or update an ADR before adding:

- order placement;
- private user/account data collection;
- paid data/API redistribution;
- third-party persistent data providers;
- ML/LLM scoring that affects alert severity;
- any exception to `docs/governance/08_local_only_exclusion_policy.md`.


## Local-only excluded platform scope

The current phase is local-only. Do not add billing, hosted deployment, registry image attestation, registry push/signing, automatic key rotation, external secret-manager integration, full user auth/RBAC/OIDC, user-account systems, multi-tenancy, or hosted admin/security layers. Sane local secret handling through local untracked config remains allowed.
