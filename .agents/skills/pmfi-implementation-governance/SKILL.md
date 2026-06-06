---
name: pmfi-implementation-governance
description: Use when implementing this prediction-market flow intelligence project. Guides adaptive bottom-up development, local-first/Postgres-first constraints, raw-before-derived data handling, verification gates, and non-fragility requirements.
---

# PMFI Implementation Governance

Use this skill whenever you are about to implement, refactor, or expand functionality in this repo.

## Working loop

1. Identify the relevant milestone or vertical slice using `FAST_ADVANCE.md` and the active plan.
2. Verify the closest lower-level contract exists or record the blocker.
3. Implement a small local slice.
4. Add deterministic fixture-based tests where behavior changes.
5. Run `python scripts\verify.py` when feasible.
6. Update `WORKLOG.md`.

## Defaults

- Raw payloads before normalized records.
- Normalized records before metrics.
- Metrics before alert decisions.
- Alert decisions before delivery.
- Replay before live confidence.
- Postgres before specialized infrastructure.
- Fixture tests before live API checks.

These are defaults for non-fragility, not excuses to avoid a bounded spike that clarifies missing contracts.

## Stop internally

Stop and record a blocker if a step requires guessing venue semantics for persisted fields, live trading, private data, hosted/SaaS assumptions, billing, user-account systems, external secret managers, registry publication, or weakening tests.

## Windows-native constraint

Use Python commands and the Windows wrappers (`pmfi.cmd`, `pmfi.ps1`). Do not add Unix-only scripts or automatic agent-side command-trigger automation.

## Local-only scope boundary

Use `docs/governance/08_local_only_exclusion_policy.md` as binding. Do not implement or scaffold SaaS, hosted runtime, billing, full user auth/RBAC/OIDC, registry push/signing/attestation, external secret managers, automatic key rotation, or external notification SaaS unless a future human-approved ADR proves functional necessity.
