# Execution Plans

An execution plan is a self-contained implementation spec. A new agent must be able to resume from the plan alone.

Create `plans\YYYY-MM-DD-<slug>.md` for:

- significant features;
- refactors/migrations;
- security-sensitive changes;
- local runtime, storage, or external-feed boundary changes;
- cross-cutting changes;
- tasks with unresolved ambiguity.

Each plan must include:

## Goal
What user-visible or system-visible outcome this delivers.

## Non-goals
What is intentionally out of scope. Include explicit confirmation that the work does not add SaaS, hosted runtime, billing, full user auth/RBAC/OIDC, registry publication, external secret manager, automatic key rotation, or external notification SaaS work unless a future human-approved ADR allows it.

## Current state
Files, flows, and behavior discovered during exploration.

## Proposed design
The chosen approach and why alternatives were rejected.

## Files likely to change
| File | Expected change |
|---|---|

## Milestones
- [ ] Milestone 1
- [ ] Milestone 2
- [ ] Milestone 3

## Tests / verification
List exact Windows-native commands and expected signals.

## Risks
Known migration, security, performance, local-only, or compatibility risks.

## Decision log
Append dated decisions here.

## Progress log
Append progress after each meaningful step.
