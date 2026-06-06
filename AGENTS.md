# AGENTS.md

## Purpose
Canonical operating contract for AI coding agents working in this repository. Keep this file short; durable detail belongs in `docs/`, active execution detail belongs in `plans/`, and repeatable workflows belong in skills/scripts.

## Project summary
- Product: Windows-native, local-only prediction-market flow intelligence for Polymarket/Kalshi-style venues.
- Goal: capture public market events, preserve raw external payloads, normalize trades, compute baselines, and emit explainable local anomaly alerts.
- Runtime: Python 3.11+.
- Storage: Postgres-first. Do not add another durable store until Postgres constraints are measured and documented.
- Operating environment: Windows local directory first; Docker Desktop for local Postgres.

## Operating intent
- Optimize for rapid progress toward a usable local tool, not ceremony. Favor material results over low-velocity process artifacts.
- Build bottom-up by default because data lineage matters.
- Use `FAST_ADVANCE.md` when the user asks to move quickly, reduce process, or maximize forward progress.
- For unclear architectural, organizational, orchestration, data-shape, or product-utility decisions, use `docs/governance/12_decision_methods.md`.
- Use orthogonal framing and compact Talmudic debate only when it improves implementation choices; it is not a paperwork requirement.
- A bounded top-down spike is allowed when it removes uncertainty, clarifies product utility, or unblocks lower-layer design. Convert the spike into tests/contracts/schema/docs before relying on it.
- Older rigid sequencing language is advisory, not binding. Prefer `FAST_ADVANCE.md` and `docs/implementation/06_adaptive_milestone_map.md` when choosing the next slice.

## Non-negotiables
- This is local-only for the current build horizon. Follow `LOCAL_ONLY_SCOPE.md` and `docs/governance/08_local_only_exclusion_policy.md`.
- Local secret handling is limited to ignored local config and environment variables for opt-in read-only live checks.
- Do not implement or scaffold SaaS, hosted runtime, billing, tenant/user-account systems, RBAC/OIDC, registry publishing/signing/attestation, external secret managers, automatic key rotation, or external notification SaaS unless a future human-approved ADR proves it is unavoidable.
- Raw external payloads are stored before derived/normalized records are trusted.
- Normal verification command: `python scripts\verify.py`.
- Windows task wrapper: `python scripts\task.py <command>`, `pmfi.cmd <command>`, or `.\pmfi.ps1 <command>` if PowerShell is allowed.
- DB verification command: `python scripts\db_local.py verify` after local Postgres is up.
- No Unix-only scripts, automatic agent-side command-trigger automation, or non-Windows command-runner dependency.
- No live API calls in default tests or verification.
- No trading/order-placement features without a new plan, ADR, and explicit approval.
- Do not weaken tests, type checks, or validation gates to make a run pass.
- Do not commit secrets, credentials, private keys, `.env`, or local database dumps.

## Repo map
- `src/pmfi/`: implementation nucleus.
- `tests/`: fixture-driven tests.
- `sql/`: Postgres schema, indexes, views, dev seed data.
- `config/`: example local config and alert rules.
- `scripts/`: Python-only local task/verification utilities.
- `docs/`: durable architecture, data, product, ops, governance docs.
- `plans/`: active executable plans and handoff state.
- `.agents/skills/`: portable repo-local skills.
- `.claude/`: Claude Code skills/subagents; empty settings file; verification remains explicit.
- `.codex/`: Codex project config/rules.

## Windows setup
```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
python scripts\verify.py
```

If PowerShell activation is blocked, use:

```cmd
.venv\Scripts\python.exe -m pip install -e ".[dev]"
.venv\Scripts\python.exe scripts\verify.py
```

## Local Postgres
```powershell
python scripts\db_local.py up
python scripts\db_local.py init
python scripts\db_local.py verify
```

## Fresh-session protocol
Read `AGENT_START_HERE.md`, run `python scripts\verify.py`, inspect `FAST_ADVANCE.md`, run `python scripts\task.py status`, and check `WORKLOG.md`. Do not load every planning file into context unless the current task or failing check requires it.

## Work protocol
For trivial fixes: make the smallest safe change, run the narrowest relevant check, then report changed files and verification result.

For non-trivial work:
1. Explore first. Identify relevant files, existing patterns, risks, and tests.
2. If the change is ambiguous or cross-cutting, briefly examine at least one orthogonal framing. Use compact Talmudic debate for non-trivial architecture/data/orchestration decisions, then record only the consensus and validation target needed by the next agent.
3. Add or update tests before implementation when behavior changes and the test target is clear.
4. Implement in small slices that either prove a lower layer or deliver a thin end-to-end local slice.
5. Run narrow checks after each slice and `python scripts\verify.py` before handoff when feasible.
6. Update docs/ADRs only when architecture, storage, commands, setup, local-only boundaries, product behavior, or a material decision changed.
7. Update `WORKLOG.md` with verified progress and residual risks.

## Planning threshold
Create or update a plan when data schema changes, external feed behavior changes, architecture changes, security/scope boundaries change, or the task is ambiguous. Do not create plans for obvious one-file fixes or when doing so would slow a clear verified slice.

## Definition of done
- Tests added/updated for changed behavior, or explicitly justified as unnecessary.
- `python scripts\verify.py` passes, or the failure is recorded with the narrow next fix.
- DB work also passes `python scripts\db_local.py verify` when local Postgres is available.
- Relevant plan/worklog/docs updated.
- No unrelated formatting churn.
- Final handoff includes changed files, checks run, residual risk, and any material decision consensus.
