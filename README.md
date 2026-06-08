# PM Flow Intel

Windows-native, local-only prediction-market flow intelligence scaffold for Polymarket/Kalshi-style markets. The project is scoped as a local research/alerting system, not an automated trading system and not a hosted/SaaS product.

## Windows quickstart
PowerShell:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
python scripts\verify.py
```

Command Prompt:

```cmd
py -3.11 -m venv .venv
.venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
python scripts\verify.py
```

## Local Postgres with Docker Desktop
```powershell
python scripts\db_local.py up
python scripts\db_local.py init
python scripts\db_local.py verify
```

Wrapper alternatives:

```powershell
.\pmfi.ps1 verify
.\pmfi.ps1 db-up
.\pmfi.ps1 db-init
.\pmfi.ps1 db-verify
```

```cmd
pmfi.cmd verify
pmfi.cmd db-up
pmfi.cmd db-init
pmfi.cmd db-verify
```

## Agent entrypoints
- Cross-agent contract: `AGENTS.md`.
- Fresh-session entrypoint: `AGENT_START_HERE.md`.
- Windows setup: `WINDOWS_START_HERE.md`.
- Claude adapter: `CLAUDE.md`.
- Codex adapter: `CODEX_START_HERE.md`.
- Active implementation plan: `plans/2026-06-03-bottom-up-implementation-plan.md`.
- Fast advancement guide: `FAST_ADVANCE.md`.
- Adaptive milestone map: `docs/implementation/06_adaptive_milestone_map.md`.
- Local-only boundary: `docs/governance/08_local_only_exclusion_policy.md`.


## Fast advancement mode

When using Codex, Claude Code, or another coding agent to move quickly, start with:

- `AGENT_START_HERE.md`
- `FAST_ADVANCE.md`
- `docs/implementation/06_adaptive_milestone_map.md`
- `plans/2026-06-03-bottom-up-implementation-plan.md`

The repo uses bottom-up implementation as the default, but it is not a rigid milestone lock. Agents may use bounded top-down spikes or parallel fixture-backed work when that is the fastest way to remove uncertainty or make the local tool usable. The payoff requirement is executable evidence: tests, schema, fixtures, interfaces, or a precise blocker.

## Orthogonal/Talmudic decision mode

For unclear architecture, organization, orchestration, or product-utility decisions, agents should briefly examine orthogonal lenses and use a compact Talmudic debate when the choice is non-trivial. The goal is a coherent consensus that produces material local progress, not a longer process trail. See `FAST_ADVANCE.md` and `docs/governance/12_decision_methods.md`.

## Build philosophy
Production-grade means local reliability and operator utility, not hosted/SaaS readiness.

- Windows local directory first.
- Local-only product boundary for the current build horizon.
- Adaptive bottom-up implementation: bottom-up by default, bounded top-down spikes allowed when they accelerate verified local utility.
- Postgres-first durable storage.
- Raw external payloads before derived records.
- Fixture-first validation before live feeds.
- Local console/file/localhost outputs before any external delivery.
- No Unix-only scripts or automatic agent-side command-trigger automation.
- No live API calls during default verification.

## Explicit non-goals for current implementation
- SaaS billing, hosted billing reconciliation, subscription entitlements, or payment systems.
- Hosted runtime/application deployment.
- Hosted deployment attestation.
- Published registry image attestation.
- Registry push/signing/provenance requirements.
- Automatic key rotation or external secret-manager integration.
- Full user auth, RBAC, OIDC, organizations, tenants, or user-account systems.
- External notification SaaS as default alert delivery.

## Useful docs
- Architecture: `docs/ARCHITECTURE.md` and `docs/architecture/`.
- Data contracts: `docs/data/00_data_contracts.md`.
- Postgres requirements: `docs/data/03_postgres_requirements.md`.
- Alert semantics: `docs/product/01_alert_semantics.md`.
- Local-only boundary: `docs/governance/08_local_only_exclusion_policy.md`.
- Verification cadence: `docs/governance/02_verification_cadence.md`.
- Local ops: `docs/ops/00_local_setup.md`.
- Operator quick-start (discover → watch → ingest → alerts): `docs/ops/OPERATOR_QUICKSTART.md`.
