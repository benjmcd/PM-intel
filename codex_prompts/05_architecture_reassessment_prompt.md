# Architecture Reassessment Prompt

```text
Perform an architecture reassessment. Re-read docs/architecture/*, docs/governance/04_non_fragility_rules.md, docs/data/00_data_contracts.md, and current code. Identify any fragility, leakage of venue-specific logic, premature infrastructure, missing replayability, weak data-quality handling, or docs drift.

Produce a prioritized list of fixes in WORKLOG.md. Apply only the smallest required correction unless the current milestone explicitly calls for broader refactoring.
```


Also check local-only exclusions: no SaaS/billing/hosted deployment/registry publication/signing/managed-secret/full auth/RBAC/OIDC/cloud/external CI work unless explicitly approved by a new ADR.
