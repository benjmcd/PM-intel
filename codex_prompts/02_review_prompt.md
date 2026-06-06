# Milestone Review Prompt

```text
Perform a milestone review. Check the completed milestone against docs/implementation/01_bottom_up_work_orders.md and docs/implementation/03_acceptance_gates.md.

Confirm tests pass, docs are synchronized, no stop gates were violated, raw-before-derived and venue isolation still hold, and Postgres remains the primary durable store. Add or update an ADR for any architecture decision made. Update WORKLOG.md with pass/fail and remaining risks.

Do not rely on the next milestone or higher layer until the current slice has a clean acceptance assessment or a documented local-environment blocker with a fixture-backed substitute.
```


Also check local-only exclusions: no SaaS/billing/hosted deployment/registry publication/signing/managed-secret/full auth/RBAC/OIDC/cloud/external CI work unless explicitly approved by a new ADR.
