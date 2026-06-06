# 01 — Bottom-Up Work Orders

Agents should use these milestones as the default dependency map. Do not skip evidence requirements, but do not treat the order as a cage. If a lower milestone is blocked, or a bounded top-down spike will clarify missing contracts faster, use `FAST_ADVANCE.md` and `docs/implementation/06_adaptive_milestone_map.md`, then repay the spike with tests/schema/fixtures/interfaces or a precise blocker.

## M0 — Repository sanity and local harness

### Goal
Establish a green baseline and make the repo easy to run locally.

### Tasks
- [ ] Inspect `AGENTS.md`, `AGENT_START_HERE.md`, `FAST_ADVANCE.md`, and the relevant governance docs.
- [ ] Run `python scripts\verify.py`.
- [ ] Confirm Python package imports.
- [ ] Confirm baseline tests pass.
- [ ] Update `WORKLOG.md`.

### Acceptance gate
- `python scripts\verify.py` passes.
- `WORKLOG.md` contains a reconnaissance note.
- No product code behavior is changed without a test.

## M1 — Postgres schema and migration runner

### Goal
Make Postgres schema application repeatable locally.

### Tasks
- [ ] Review `sql/001_init.sql`, `sql/002_partitions_indexes.sql`, and `sql/003_views_and_queries.sql`.
- [ ] Add or verify the Python migration runner in `scripts\db_local.py` applies SQL in order.
- [ ] Add a DB smoke test that can run when Postgres is available.
- [ ] Document local DB startup.

### Acceptance gate
- `python scripts\db_local.py up` starts Postgres.
- `python scripts\db_local.py init` applies migrations idempotently.
- `python scripts\db_local.py verify` confirms core tables exist.
- Normal `python scripts\verify.py` still passes without requiring live Postgres unless explicitly documented.

## M2 — Raw event store and fixture ingestion

### Goal
Persist raw venue payloads before any normalization.

### Tasks
- [ ] Implement raw event domain object and insert path.
- [ ] Import fixture payloads into raw-events table or local fixture replay mode.
- [ ] Generate stable payload hashes/dedupe keys.
- [ ] Dead-letter malformed payloads.

### Acceptance gate
- Raw fixtures can be imported and re-read.
- Duplicate fixture import is idempotent or explicitly deduped.
- Malformed fixture produces a controlled failure/dead-letter.

## M3 — Normalized trade schema and normalizers

### Goal
Convert raw Polymarket/Kalshi trade-like payloads into venue-neutral normalized trades.

### Tasks
- [ ] Define normalized trade contract in code.
- [ ] Implement Polymarket fixture normalizer.
- [ ] Implement Kalshi fixture normalizer.
- [ ] Compute capital-at-risk and payout-notional consistently.
- [ ] Preserve warnings/side-confidence when semantics are uncertain.

### Acceptance gate
- Fixture normalizer tests pass.
- Unknown/missing required fields produce controlled errors.
- Normalized trades include venue, market, outcome, price, contracts, capital-at-risk, payout-notional, timestamps, and source reference.

## M4 — Adapter contracts and simulated collectors

### Goal
Create collector interfaces without relying on live network calls.

### Tasks
- [ ] Define `VenueAdapter` contract.
- [ ] Define `EventSource`/collector contract.
- [ ] Implement fixture/simulated collectors.
- [ ] Ensure normal tests use simulated collectors only.

### Acceptance gate
- Simulated collector can feed fixtures through raw store -> normalizer.
- No live API key is required.
- Adapter contract docs are updated.

## M5 — Live-read venue adapters behind opt-in flags

### Goal
Add real public feed connectors without compromising deterministic tests.

### Tasks
- [ ] Implement Polymarket market/trade read adapter behind `PMFI_ENABLE_LIVE=1`.
- [ ] Implement Kalshi read adapter behind `PMFI_ENABLE_LIVE=1` and API-key config.
- [ ] Add timeout, retry, and degraded-state handling.
- [ ] Add live smoke test target that is skipped by default.

### Acceptance gate
- Unit tests do not call live APIs.
- Live smoke command is documented and opt-in.
- Missing credentials produce a clear skip/blocker, not a crash.

## M6 — Rolling metrics and baseline computation

### Goal
Compute context needed for abnormality scoring.

### Tasks
- [ ] Rolling trade-size percentiles.
- [ ] Rolling directional flow windows.
- [ ] Price-impact calculation using before/after snapshots or fixture approximations.
- [ ] Open-interest/volume relative metrics where available.
- [ ] Baseline fallback hierarchy: market -> category -> liquidity tier.

### Acceptance gate
- Metrics can be computed deterministically from fixture/replay data.
- Sparse data produces explicit low-confidence metrics.
- Baseline code is test-covered.

## M7 — Transparent alert scoring and suppression

### Goal
Emit explainable alerts from normalized trades and metrics.

### Tasks
- [ ] Load declarative alert-rule config.
- [ ] Implement absolute, relative, cluster, and price-impact scoring.
- [ ] Add dedupe/suppression windows.
- [ ] Include reason codes and data-quality status.

### Acceptance gate
- At least three alert examples are produced from fixtures.
- False-positive suppressions are test-covered.
- Every alert includes evidence and rule version.

## M8 — Alert delivery adapters

### Goal
Deliver alerts without entangling scoring logic.

### Tasks
- [ ] Console delivery.
- [ ] File/JSONL delivery.
- [ ] HTTP receiver adapter stub or implementation.
- [ ] Delivery retry/dead-letter behavior.

### Acceptance gate
- Alert delivery tests use fakes.
- Delivery failures do not lose alert records.
- Alert payload is stable and documented.

## M9 — Replay/backtest harness

### Goal
Reproduce alerts from stored/replayed raw events.

### Tasks
- [ ] Implement replay command for fixture or DB time window.
- [ ] Recompute normalized trades/metrics/alerts.
- [ ] Compare expected alert outputs.
- [ ] Add baseline report generation.

### Acceptance gate
- Replay is deterministic on fixtures.
- Alert counts and reason codes match expected snapshots.
- Regression tests protect replay behavior.

## M10 — Operational hardening and local dashboard/CLI

### Goal
Make the tool usable locally beyond tests.

### Tasks
- [ ] CLI command group for init/import/replay/score/alerts.
- [ ] Health check command.
- [ ] Data-quality report command.
- [ ] Optional lightweight local dashboard or static report.

### Acceptance gate
- A user can follow README and run a full local fixture workflow.
- Local-first remains intact.
- No hosted service is required.
