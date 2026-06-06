# 10 — Alignment Audit

## Purpose

This document records the cross-file coherence rules that keep the repo usable by fresh Codex/Claude sessions without relying on chat history. It is intentionally short; executable checks in `scripts/verify_workspace.py` and `tests/` are the enforcement layer.

## Canonical alignment decisions

- The current product horizon is Windows-native and local-only.
- `AGENTS.md` is the canonical cross-agent contract; `CLAUDE.md` imports it rather than duplicating shared rules.
- `FAST_ADVANCE.md` and `docs/implementation/06_adaptive_milestone_map.md` supersede stale rigid milestone wording.
- Bottom-up remains the default implementation model, but bounded top-down spikes are allowed when they accelerate proven local utility and are repaid with tests, schema, fixtures, interfaces, or a blocker.
- Postgres is the primary durable store until measured local constraints justify another store through an ADR.
- Default verification must be offline and fixture-backed.
- Live venue reads are opt-in only and must not affect default tests.
- SaaS, hosted runtime, billing, external secret managers, full auth/RBAC/OIDC, registry publication/signing/attestation, and external notification providers remain excluded unless a future user-approved ADR proves functional necessity.
- No Unix-only wrappers, non-Windows command-runner dependencies, or automatic agent-side command-trigger automation are part of the current repo.

## Latest manual coherence pass

The latest pass corrected stale migration-runner wording, removed non-Windows tool metadata from Claude review subagents, removed duplicate governance numbering, and softened one overly rigid bottom-up rule so it aligns with fast-advance mode.

## What future agents should check

Before major implementation handoff, confirm:

1. `python scripts\verify.py` passes or the failure is recorded with a narrow next step.
2. New docs do not contradict `LOCAL_ONLY_SCOPE.md`, `FAST_ADVANCE.md`, or the adaptive milestone map.
3. New implementation work does not introduce excluded platform/SaaS scaffolding.
4. Any top-down spike has a payback artifact.
5. New command docs use Windows-native Python, CMD, or PowerShell commands.
