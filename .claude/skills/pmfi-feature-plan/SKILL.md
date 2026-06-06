---
name: pmfi-feature-plan
description: Create or revise a concise executable implementation plan for PMFI work when ambiguity or cross-cutting changes require it.
---

# PMFI feature plan

Use this skill when a task changes DB schema/API/security/local-only boundaries, has architectural ambiguity, or affects many files.

## Workflow

1. Read `AGENTS.md`, `FAST_ADVANCE.md`, `.agent/PLANS.md`, and the current active plan in `plans/`.
2. Inspect only relevant code/tests/docs.
3. Write or update a short plan with goal, non-goals, current state, assumptions, design, files likely to change, milestones, verification, risks, decision log, and progress log.
4. Prefer bottom-up milestones, but allow bounded top-down spikes that clarify missing contracts or local utility.
5. Add explicit blockers for uncertain feed semantics, persisted schema changes, live API use, or new infrastructure.
6. If the implementation slice is obvious and low-risk, implement it and update the plan afterward.

## Local-only non-goals

Every feature plan should preserve the exclusion of SaaS billing, hosted runtime, registry publication/signing/attestation, external secret managers, automatic key rotation, full user auth/RBAC/OIDC, or external notification SaaS work unless a future human-approved ADR allows it.
