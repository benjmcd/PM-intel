# 11 — Current Alignment Review

## Scope

Audit target: Windows-native, local-only, Postgres-first PMFI workspace prepared for Codex and Claude Code.

The audit checked startup instructions, governance docs, plans, task graph gates, command references, local-only exclusions, duplicate numbering, stale hosted/SaaS scaffolding, and packaging constraints.

## Confirmed canonical contracts

- Startup contract: `AGENTS.md` plus `AGENT_START_HERE.md`; `CLAUDE.md` imports `AGENTS.md`; `CODEX_START_HERE.md` is Codex-specific.
- Fast-advance contract: `FAST_ADVANCE.md`; bottom-up is the default dependency order, not a rigid lock.
- Scope contract: `LOCAL_ONLY_SCOPE.md`, `docs/governance/08_local_only_exclusion_policy.md`, and ADR 0007.
- Storage contract: Postgres-first; no extra durable store until measured constraints justify it.
- Verification contract: `python scripts\verify.py` for default checks, `python scripts\db_local.py verify` for local Postgres, and `python scripts\task.py fixture-replay` for fixture replay.

## Issues found and corrected

- Removed duplicate governance numbering by resequencing the runtime-compatibility and Codex/Claude interop docs.
- Replaced stale direct fixture-replay gates with the Windows-native task wrapper.
- Confirmed Claude prompt now reads the shared start file rather than the Codex-specific start file.
- Kept bounded top-down spikes explicitly exploratory until repaid with executable evidence.
- Confirmed local-only exclusions remain scoped to governance/planning, not implementation scaffolding.

## Alignment result

The package is internally aligned around:

```text
Windows local directory
+ local-only scope
+ Postgres-first storage
+ raw-before-derived lineage
+ adaptive bottom-up implementation
+ fixture/offline default verification
+ optional read-only live checks behind explicit local flags
```

## Remaining expected external gate

A live Windows Docker Desktop/Postgres run still must be performed on the user's machine:

```powershell
python scripts\db_local.py up
python scripts\db_local.py init
python scripts\db_local.py verify
```

If Docker Desktop is unavailable, the agent should record the blocker and continue with fixture-backed repository contracts, SQL review, normalizers, metrics, alert payloads, and replay scaffolding.

## Follow-up coherence corrections

- Corrected the audit report wording around governance doc resequencing.
- Made receiving-agent handoff instructions use the shared start file rather than Codex-specific startup by default.
- Reduced initial Codex/Claude prompts so they load the thin startup layer first and open durable docs only when needed.
- Softened bottom-up-governance shortcut language so bounded local spikes remain allowed while product-complete claims still require executable evidence.
