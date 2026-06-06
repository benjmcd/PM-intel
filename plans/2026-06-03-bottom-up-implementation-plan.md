# Adaptive bottom-up implementation plan

## Goal

Build quickly toward a production-grade local tool while preserving evidence, replayability, and local-only scope. The default implementation path is bottom-up, but the repo explicitly permits bounded top-down spikes when they remove uncertainty or accelerate a usable local workflow.

Build a Windows-native, local-only, Postgres-backed PMFI implementation that can ingest fixture and later opt-in live public prediction-market events, preserve raw payloads, normalize executed trades, compute rolling baselines, and emit explainable local anomaly alerts.

## Windows-native command surface

Canonical commands for this plan are:

```powershell
python scripts\verify.py
python scripts\db_local.py up
python scripts\db_local.py init
python scripts\db_local.py verify
python scripts\task.py fixture-replay
```

Optional wrappers are `pmfi.cmd` and `pmfi.ps1`. Do not add Unix-only wrappers or automatic agent-side command-trigger automation.

## Local-only boundary

This plan is governed by `docs/governance/08_local_only_exclusion_policy.md` and `docs/adr/0007-local-only-scope-and-exclusions.md`.

Explicitly excluded unless a future human-approved ADR proves functional necessity:

- SaaS billing, subscriptions, payments, entitlements, invoices, or hosted billing reconciliation.
- Hosted runtime/application work.
- Hosted deployment attestation.
- Published registry image attestation.
- Registry push/signing/provenance requirements.
- Automatic key rotation systems.
- External secret-manager integrations beyond local untracked secrets.
- Full user auth, RBAC, OIDC, organizations, teams, tenants, or user-account systems.
- External notification SaaS adapters as default delivery.
- Public multi-user dashboard or API productization.

## Non-goals

- No automated trading or order placement.
- No private account-data collection.
- No paid-data redistribution.
- No ML scoring before transparent rule baselines are proven.
- No Kafka/Kubernetes/hosted requirement before local Postgres throughput is measured and an ADR accepts the change.
- No full all-market order-book reconstruction before executed-trade and snapshot workflows are proven.
- No hosted/SaaS preparation while local-only functionality can progress.

## Current state

The scaffold contains Python package code under `src/pmfi`, tests under `tests`, SQL under `sql`, local Postgres Docker Compose, configs, durable docs, work orders, and repo-local agent skills. The current code is a minimal nucleus and not a complete product.

## Assumptions and ambiguities

- Official venue feed semantics must be re-verified before live adapters are treated as reliable.
- Fixture schemas are examples, not complete venue contracts.
- Postgres remains the primary DB until benchmarks show a concrete bottleneck.
- Cross-venue market matching is out of scope until normalization and baselines are reliable.
- Alert thresholds must be validated through replay/backtesting, not selected by intuition alone.
- Local-only scope remains binding even if future hosted use cases look plausible.

## Advancement policy

Use `FAST_ADVANCE.md`, `docs/governance/12_decision_methods.md`, and `docs/implementation/06_adaptive_milestone_map.md` when choosing the next action. Milestone order is a default dependency map, not a hard lock. If a lower milestone is blocked by local environment constraints, advance the nearest fixture-backed interface, test, CLI workflow, or report shape, then record the blocker. If a top-down spike is used, convert it into executable evidence before downstream code depends on it.

## Decision method for unclear work

When the next step is unclear, choose through orthogonal lenses rather than only milestone order: data lineage, operator utility, failure modes, module boundaries, and local-only/Postgres constraints. For non-trivial choices, use the compact Talmudic-style debate method from `docs/governance/12_decision_methods.md` and end with a consensus action, payback artifact, and next check. Avoid low-impact planning work when a verified local slice would settle the issue faster.

## Proposed design

Use a layered local-first architecture:

```text
raw feed fixtures/live opt-in
  -> raw_events in local Postgres
  -> venue-specific normalizers
  -> normalized_trades
  -> rolling metrics/baselines
  -> alert scoring + suppression
  -> local console/file/localhost outputs
```

The key design choice is raw-before-derived. Every derived event must be traceable to a raw payload and parser version.

## Files likely to change

| File/path | Expected change |
|---|---|
| `sql/*.sql` | schema, indexes, views, seed data, migrations |
| `src/pmfi/` | domain models, repository layer, normalizers, collectors, scoring, CLI |
| `tests/` | unit/contract/integration fixtures |
| `config/*.yaml` | app, market, alert, logging configuration |
| `docs/adr/` | decisions affecting storage, feed semantics, architecture, local-only exceptions |
| `WORKLOG.md` | every session/milestone handoff |



## Milestones

### M0 — Repository sanity and agent harness

- [x] Minimal Python package exists.
- [x] Fixture tests exist.
- [x] `python scripts\verify.py` passes.
- [x] Thin `AGENTS.md`, Claude adapter, explicit checks, plans, and local verification scaffold exist.
- [x] Local-only/SaaS-exclusion governance exists.

Acceptance: fresh agent can run `python scripts\verify.py`, run `python scripts\task.py status`, and identify the highest-leverage next action without chat history.

Verification: `python scripts\verify.py`.

### M1 — Postgres schema and migration runner

- [ ] Prove `sql/001_init.sql` through `sql/004_seed_dev.sql` against local Docker Postgres.
- [ ] Add idempotent migration runner or documented migration command.
- [ ] Add DB smoke test that verifies required tables/views/indexes.
- [ ] Add failure-safe rollback/reset guidance for local dev.

Acceptance: running `python scripts\db_local.py up`, then `python scripts\db_local.py init`, then `python scripts\db_local.py verify` works on a fresh Windows machine with Docker Desktop.

Verification:

```powershell
python scripts\db_local.py up
python scripts\db_local.py init
python scripts\db_local.py verify
```

### M2 — Raw event store and fixture ingestion

- [ ] Implement DB repository for raw event insertion.
- [ ] Ensure raw events are immutable and deduped by venue/source event key where available.
- [ ] Add fixture replay CLI that inserts raw fixture events.
- [ ] Store parser/source metadata and data-quality state.

Acceptance: fixture replay inserts raw Polymarket and Kalshi sample payloads into Postgres with deterministic IDs and no duplicate insertion on replay.

Verification: unit tests + DB integration test.

### M3 — Normalized trade schema and normalizers

- [ ] Implement explicit normalized trade schema in Python and Postgres.
- [ ] Normalize Polymarket last-trade fixtures.
- [ ] Normalize Kalshi trade fixtures.
- [ ] Store both `capital_at_risk` and `payout_notional`.
- [ ] Record normalizer version and raw event lineage.

Acceptance: fixture replay produces normalized trades traceable to raw events.

Verification: fixture normalization tests + DB integration test.

### M4 — Simulated collectors and adapter contracts

- [ ] Define collector interface and fake/simulated collector.
- [ ] Implement deterministic event stream from fixtures.
- [ ] Add reconnection/dead-letter behavior in simulation.
- [ ] Keep live external calls disabled by default.

Acceptance: collector pipeline can process deterministic fixture streams end-to-end without network.

Verification: pipeline test.

### M5 — Opt-in live-read venue adapters

- [ ] Implement Polymarket public read adapter behind `PMFI_ENABLE_LIVE=1`.
- [ ] Implement Kalshi public read/WebSocket adapter behind `PMFI_ENABLE_LIVE=1`, requiring env-provided credentials where needed.
- [ ] Add opt-in live smoke tests that do not run during default verification.
- [ ] Add rate-limit/reconnect/data-quality handling.

Acceptance: live smoke can run manually and store raw events without affecting normal tests.

Verification:

```powershell
$env:PMFI_ENABLE_LIVE = "1"; python -m pmfi.cli live-smoke
```

### M6 — Rolling metrics and baseline computation

- [ ] Compute market-relative trade-size percentiles.
- [ ] Compute rolling net directional flow windows.
- [ ] Compute price-impact windows when price context exists.
- [ ] Add category/liquidity fallback baselines for sparse markets.

Acceptance: replay over fixture/history input produces deterministic rolling metric rows.

Verification: baseline tests and replay report.

### M7 — Transparent alert scoring and suppression

- [ ] Implement declarative alert rules from `config/alert_rules.yaml`.
- [ ] Add absolute, OI-relative, cluster, and price-impact rules.
- [ ] Add dedupe/suppression windows.
- [ ] Include reason codes and data-quality labels in every alert.

Acceptance: alerts explain why they fired and are reproducible from replay.

Verification: scoring tests + replay alert report.

### M8 — Local alert delivery adapters

- [ ] Implement stdout delivery.
- [ ] Implement local file/JSONL delivery.
- [ ] Implement optional localhost-only HTTP receiver adapter with fake endpoint tests.
- [ ] Keep Slack/Discord/Telegram/email/SMS/external notification providers out of scope.

Acceptance: local delivery works with no secrets and no external service dependency.

Verification: unit tests + local fake-server integration.

### M9 — Replay/backtest harness

- [ ] Replay historical raw events through selected scoring versions.
- [ ] Produce rule-fire frequency, false-positive review queue, and data-gap report.
- [ ] Allow side-by-side comparison of alert-rule versions.

Acceptance: a user can evaluate whether alerts are useful before running live.

Verification: replay report under `reports/`.

### M10 — Operational hardening and local dashboard/CLI

- [ ] Add observability views/CLI commands.
- [ ] Add degraded-data alert suppression.
- [ ] Add local dashboard only after CLI/reports are useful.
- [ ] Add throughput and storage benchmarks.

Acceptance: local operator can diagnose ingestion, normalization, scoring, and alert delivery health.

Verification: ops smoke + report.

## Tests / verification

Canonical:

```powershell
python scripts\verify.py
```

DB-local:

```powershell
python scripts\db_local.py up
python scripts\db_local.py init
python scripts\db_local.py verify
```

Live opt-in only:

```powershell
$env:PMFI_ENABLE_LIVE = "1"; python -m pmfi.cli live-smoke
```

## Fast advancement guidance

- Do not stall on M1 if Docker Desktop is unavailable; record the blocker and advance fixture-backed repositories or SQL tests.
- Do not stall on M5 if live API access is unavailable; improve adapter interfaces, fixtures, and source-map blockers.
- Do not build UI/dashboard before CLI/reports prove alert utility, but a thin CLI/operator workflow spike is allowed when it clarifies missing contracts.
- Keep docs current only where they prevent the next agent from guessing.

## Risks and stop gates

- Persisted schema ambiguity requires ADR before implementation.
- Feed semantic uncertainty requires fixture/source update before normalization.
- New infrastructure dependency requires measured Postgres/local bottleneck evidence.
- Any SaaS/hosted/user-account/external-secret-manager/registry-publication direction requires a blocker entry and future human-approved ADR.
