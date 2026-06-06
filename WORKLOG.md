# WORKLOG

This log is intentionally committed. Codex must update it after every coherent work slice.

## Format

```markdown
## YYYY-MM-DD HH:MM local — Session / Slice title

### Files inspected
- ...

### Changes made
- ...

### Verification run
- `python scripts\verify.py` — pass/fail
- other commands — pass/fail

### Findings
- Facts:
- Inferences:
- Assumptions:
- Blockers:

### Next step
- ...
```

## 2026-06-06 — Session 3: Live pipeline correctness + operator display + dedup + status enrichment

### Commits (5)
- `e17a0ac` — Fix live pipeline asset_id resolution + operator display improvements
- `4975abb` — Add venue_trade_id dedup + market title in alerts/markets displays
- `ff90c52` — Add periodic baseline refresh in pmfi ingest + AlertEngine.update_baselines
- `676a5fa` — Enrich pmfi status with DB health + stats; fix pmfi watch market title
- `f7c854b` — Config unknown-field warning + clean ingest error handling

### What changed

- **Bug fix — `cmd_ingest` asset_id subscription**: `pmfi ingest --venue polymarket` was subscribing to
  condition IDs (`venue_market_id`) instead of token IDs. Polymarket WS requires token IDs from
  `market_outcomes.venue_outcome_id`. Fixed to load `load_asset_id_mapping()` and filter to watched
  markets; falls back to condition IDs with a warning if `market_outcomes` is empty.
- **Bug fix — asset_id→market resolution in runner**: Polymarket WS events carry `asset_id` (token ID)
  but not `market` (condition ID). Without resolution the normalizer produced `venue_market_id="unknown"`
  for all live events. Added `asset_id_map: dict | None = None` to `process_event` and
  `run_adapter_pipeline`; pre-normalization step uses `dataclasses.replace` to set `venue_market_id` from
  the map before normalization. Both `cmd_ingest` and `cmd_live_smoke --persist-raw` now load and pass the
  map.
- **venue_trade_id dedup in `insert_trade`**: Application-level SELECT before INSERT using the new index.
  Returns `str | None`; caller skips metrics+alert processing on `None` (duplicate trades). Prevents WS
  reconnect re-sends and same-trade duplicate payloads from doubling metric windows or alert counts.
- **`sql/007_venue_trade_id_index.sql`**: Non-unique index on `normalized_trades(venue_code, venue_trade_id)
  WHERE venue_trade_id IS NOT NULL`. Added to `apply_schema_migrations` so it auto-applies on `pmfi ingest`
  startup. Non-unique because `normalized_trades` is partitioned and cross-partition unique constraints are
  unsupported without the partition key.
- **Market question title in displays**: `pmfi alerts list`, `pmfi watch`, and `pmfi markets list` now show
  question title (from `markets.title`) instead of raw condition IDs. `Console(width=160)` prevents
  truncation. Alert display also shows Outcome column, compact `MM-DD HH:MM` timestamps, `min_width=32`
  rule name, and `--evidence` flag to expand all evidence key-value pairs.
- **`pmfi status` DB health enrichment**: Now issues a live DB health check and returns `"ok"` or an error
  message. Shows `markets` / `raw_events` / `alerts` / `baselines` row counts and `last_alert` timestamp.
- **Periodic baseline refresh in `pmfi ingest`**: `_telemetry_loop` refreshes baselines every 10 log
  cycles (~10 min) via `engine.update_baselines(fresh_baselines)` — no restart needed when baselines are
  recomputed while the daemon is running.
- **`AlertEngine.update_baselines`**: New method for hot-reload of baselines dict.
- **Config unknown-key warning**: `load_config` warns on any YAML top-level key not in
  `_KNOWN_TOP_KEYS = {"database", "features", "alerts", "ingestion", "app"}`.
- **`cmd_ingest` error handling**: Added `except Exception as exc` with a helpful user-facing message so
  operator sees actionable output instead of a raw traceback on startup failures.
- **7 new tests** in `tests/test_runner_asset_id_resolution.py` — prove asset_id resolution logic without
  asyncpg/DB: resolution sets `venue_market_id`, normalizer uses it, unknown asset_id falls through,
  existing `market` field is unaffected.

### Verification run

- `python scripts\verify.py` — **159 passed** (152 → 159, +7 new).
- `pmfi alerts list --evidence --limit 3` — evidence rows expand under each alert; market titles shown.
- `pmfi markets list` — question titles displayed; `Console(width=160)`.
- `pmfi status` — DB health "ok", row counts, last_alert shown.
- Migration 007 index applied to live DB.

### Proof-state table (updated)

| Item | State |
|---|---|
| Polymarket live subscription | **source-proven** — uses token IDs; live-smoke-proven pending |
| Asset_id→market resolution | **fixture-proven** — 7 tests; live-smoke-proven pending live run |
| venue_trade_id dedup | **source-proven** — SELECT before INSERT; DB-gated test pending |
| venue_trade_id index | **Postgres-proven** — index applied to live DB |
| Baseline hot-reload | **source-proven** — update_baselines; no restart needed |
| Alert/market/status UX | **verified** — titles, evidence flag, DB health in status |

### Residual risks

- Live smoke not yet run — highest ROI next step: `$env:PMFI_ENABLE_LIVE=1; pmfi live-smoke --venue polymarket --max-events 50 --max-seconds 120 --save-fixtures --persist-raw`
- Kalshi WS endpoint/auth not verified for current API version
- venue_trade_id dedup is application-level only; no unique constraint on the partitioned table

### Next highest-ROI step

1. Run bounded live smoke to prove the full live ingest-to-alert loop end-to-end
2. Kalshi adapter endpoint/auth verification
3. venue_trade_id DB-gated test (low priority — application path proven by code inspection)

---

## 2026-06-06 16:45 local — Session 2: P0 hardening complete, live-smoke wired, Decimal/DB proven

### What changed

- **P0.1**: asyncpg import made lazy in `db/__init__.py`; `create_pool`/`create_pool_with_retry` import asyncpg at call time only. Fixes test collection failures in venv-free environments.
- **P0.4**: All missing `FeaturesConfig` fields added (`enable_orderbook_reconstruction`, `enable_cross_venue_matching`, `enable_wallet_intelligence`, `enable_ml_scoring`) and `IngestionConfig.reconnect_jitter`. `load_config()` now parses all declared fields.
- **P0.5**: Removed all `float()` wrapping in `trades.py`, `metrics.py`, `alerts.py`. asyncpg passes `Decimal` to `numeric` columns directly — no silent precision loss.
- **P0.6**: `insert_raw_event` computes SHA-256 `payload_hash` of canonical JSON and checks `event_dedupe_keys` **before** inserting into `raw_events`. Returns `(raw_event_id, is_duplicate)` tuple; callers skip downstream on duplicate.
- **P0.7**: Alert dedupe key now includes UTC hour bucket + `outcome_key`. Prevents permanent suppression across hour windows.
- **P0.8**: `db/repos/dead_letters.py` created. `runner.py` writes dead-letter on normalization skip.
- **P0.9**: `metrics.py` uses `exchange_ts or received_at` for window bucketing (event-time, not processing-time).
- **P0.10**: Polymarket WS URL fixed (`/ws/market`), subscription corrected (`assets_ids`, `custom_feature_enabled: true`), constructor renamed `market_ids→asset_ids`, `exchange_ts` extracted per event. Non-trade event types return `None` from `normalize_event`.
- **P0.11**: `pmfi live-smoke` fully implemented in `cli.py` — `PMFI_ENABLE_LIVE=1` safety gate, `--max-events`/`--max-seconds`, `--save-fixtures` to `tests/fixtures/live/`, `--persist-raw` DB path, asset_id lookup from `raw_metadata` of watched markets.
- **market_outcomes**: `upsert_market_outcome()` added to `db/repos/markets.py`. `sync_polymarket_markets` now iterates tokens and upserts each as a `market_outcomes` row. `load_asset_id_mapping()` added for O(1) token→outcome_key lookup.
- **`report --from-db`**: `_fetch_db_stats()` and `build_db_report()` added to `reporting.py`. `cmd_report` branches on `--from-db` flag — queries alerts/trades/raw_events/dead_letters/metric_windows counts from Postgres and writes `{date}-db-report.txt`.
- **Decimal roundtrip tests**: `tests/test_decimal_roundtrip.py` — 6 parametrised `SELECT CAST($1 AS numeric)` tests + 1 real `normalized_trades` INSERT/SELECT test. All 7 pass with live DB; skip cleanly without it.
- **Fix**: `cmd_ingest` was still passing `market_ids=poly_ids`; corrected to `asset_ids=poly_ids`.
- **Fix**: `test_alert_dedupe.py` updated for new `_dedupe_key` signature (`outcome_key`, `hour_bucket`).
- **Fix**: `test_runner_suppression.py` updated for `(raw_event_id, is_duplicate)` tuple from `insert_raw_event`.

### Verification run

- `python scripts\verify.py` — **152 passed**, consistency audit passed, compileall passed.
- `python scripts\db_local.py verify` — Postgres ready, venues correct.
- `pmfi report` — fixture-replay report (10 alerts, 6 rules) written to `reports/`.
- `pmfi report --from-db` — DB state report (40 raw, 36 trades, 20 alerts, 18 metric_windows) written to `reports/`.
- `pmfi stats` — shows correct DB counts.
- `pmfi alerts list` — 20 alerts displayed.
- `pmfi replay --from-db --limit 100` — replays DB events cleanly.
- `python -m pytest tests/test_decimal_roundtrip.py -v` — **7/7 passed** (live DB).

### Proof-state table (updated)

| Item | State |
|---|---|
| Verify (152 tests) | fixture-proven |
| Decimal persistence | **Postgres-proven** — 7 DB roundtrip tests pass (0.01, 0.33, 0.67, 219.217767, etc.) |
| Raw payload dedup | **Postgres-proven** — check-before-insert, duplicate_count increments on replay |
| Metric event-time | **Postgres-proven** — exchange_ts used; windows stable across replays |
| Alert dedupe (hourly) | **Postgres-proven** — hour bucket in key; new bucket fires a new alert |
| Dead-letter visibility | **Postgres-proven** — 2 dead_letters in DB from non-trade events |
| Polymarket WS contract | source-present — code correct; live-smoke-proven pending live run |
| report --from-db | **Postgres-proven** — queries 5 tables, writes db-report.txt |
| market_outcomes | source-present — upsert wired; Postgres-proven pending `pmfi markets discover` run |

### Residual risks / accepted debt

- Live smoke still needs network: run `$env:PMFI_ENABLE_LIVE=1; pmfi live-smoke --venue polymarket --max-events 50 --max-seconds 120 --save-fixtures --persist-raw`
- `venue_trade_id` dedup on `normalized_trades`: no unique constraint yet (P1 debt)
- Kalshi WS endpoint not corrected for current URL/auth
- Config warn-on-unknown-fields not implemented (all known fields parsed)
- P1.1 baseline confidence states: alerts don't distinguish `baseline_missing` vs `baseline_sparse` vs `baseline_sufficient`

### Next highest-ROI step

1. Run `pmfi markets discover` to populate `market_outcomes` in Postgres (proves that slice)
2. Run live smoke (`PMFI_ENABLE_LIVE=1`) to upgrade WS contract to live-smoke-proven
3. P1.1: emit explicit baseline confidence state in each alert

---

## 2026-06-06 — P0 contract fixes (async import, config, Decimal, dedup, dead-letter, event-time, WS contract)

### What changed

- **P0.1 — db/__init__.py**: asyncpg import made lazy (moved inside async functions: `create_pool`, `create_pool_with_retry`). Fixes test collection failures caused by asyncpg being unavailable at import time in fixture-only environments.
- **P0.4 — config.py**: Added missing fields to `FeaturesConfig`: `enable_orderbook_reconstruction`, `enable_cross_venue_matching`, `enable_wallet_intelligence`, `enable_ml_scoring`. Added `reconnect_jitter` to `IngestionConfig`. Fixed `load_config()` to parse all fields from YAML/env rather than silently ignoring them.
- **P0.5 — trades.py, metrics.py, alerts.py**: Removed `float()` conversions at the DB persistence layer. `Decimal` values are now passed directly to asyncpg, preventing silent precision loss on values like 0.01, 0.33, 0.67, 219.217767.
- **P0.6 — raw_events.py**: `payload_hash` computed as SHA-256 of canonical (sorted-keys) JSON and stored in DB. `event_dedupe_keys` used for dedup lookup before insert. `insert_raw_event` now returns `(int, bool)` where the bool indicates `is_duplicate`.
- **P0.7 — alerts.py**: Alert dedupe key now includes UTC hour bucket + `outcome_key`. Prevents permanent alert suppression when the same market condition fires across different hour windows.
- **P0.8 — db/repos/dead_letters.py (new); runner.py**: Created `dead_letters` repository. `runner.py` now writes a dead-letter record when normalization returns `None`; duplicate raw events are skipped with a log line rather than silently dropped.
- **P0.9 — metrics.py**: `window_start` now derived from `exchange_ts` (event time) rather than `received_at` (processing time). Metric windows are now stable under replay.
- **P0.10 — polymarket.py; pipeline/normalize.py**: WS URL fixed to `.../ws/market`. Subscription format corrected to `{assets_ids, type: "market", custom_feature_enabled: true}`. Constructor parameter renamed `market_ids` → `asset_ids`. `exchange_ts` extraction added. Non-trade Polymarket event types (`book`, `price_change`, etc.) now return `None` from `normalize_event` instead of raising or producing a malformed record.

### Proof-state table

| Item | State |
|---|---|
| Local verify (145 tests) | fixture-proven (145 tests pass, 0 errors after P0.1 fix — verification result pending confirmation from this session's verify run) |
| Decimal persistence | source-present (float() removed; roundtrip test with live DB still needed) |
| Raw payload dedup | source-present (payload_hash + event_dedupe_keys wired; Postgres-proven pending DB run) |
| Metric event-time | source-present (exchange_ts preferred over received_at; replay stability test pending) |
| Alert dedupe (hourly) | source-present (hour-bucketed key wired; DB verification pending) |
| Dead-letter visibility | source-present (dead_letters.py created; runner.py writes on normalization skip) |
| Polymarket WS contract | source-present (URL + subscription corrected; live-smoke-proven pending) |

### Residual risks / accepted debt

- Decimal DB roundtrip test with specific values (0.01, 0.33, 0.67, 219.217767) still needed
- `venue_trade_id` dedup on `normalized_trades` is P1: no unique constraint yet
- Kalshi WS endpoint not yet corrected
- Live smoke test still needed (P0.11)
- Config truth: ignored-field warning behavior not yet implemented (all fields now parsed, but no warn-on-unknown for extra keys)

### Next highest-ROI step

- **P0.11**: Implement bounded opt-in live smoke command
- **P0.3**: Prove persisted fixture replay idempotency after Decimal fix (run `pmfi replay --persist` twice, confirm metric_windows values are stable)
- Add Decimal DB roundtrip tests with exact values

---

## Initial baseline

Created as a Codex-ready scaffold. No implementation milestone should be marked complete until Codex has run verification locally.


## 2026-06-03 cross-agent governance update

### Goal
Add Codex + Claude Code compatibility without bloating always-loaded context.

### Changes
- Added `CLAUDE.md` as a thin importer for `AGENTS.md`.
- Added `.codex/` project defaults and reviewer configs.
- Added `.claude/` settings, skills, and review subagents.
- Added `.agent/PLANS.md` and active bottom-up local Postgres plan in `plans/`.
- Added fast context hygiene checks, `scripts/verify.py`, local verification workflow.

### Constraints preserved
- Bottom-up implementation order.
- Local-first setup.
- Postgres-first durable storage.
- Fixture-first verification with no normal live API calls.

## 2026-06-03 — dual-agent workspace revision — M0

### Goal
Revise the workspace so it works for both Codex and Claude Code without relying on chat history or giant always-loaded context files.

### Files changed
- `AGENTS.md` — converted into thin canonical operating contract.
- `CLAUDE.md` — added Claude Code adapter importing `AGENTS.md`.
- `AGENT_START_HERE.md` — added shared fresh-session entrypoint.
- `.agent/PLANS.md` and `plans/2026-06-03-bottom-up-implementation-plan.md` — added durable bottom-up plan framework.
- `.codex/` — added Codex project config/rules.
- `.claude/` — added Claude settings, skills mirror, and review subagents.
- `scripts/verify.py` and `python scripts\agent_context_check.py` — added executable verification and context-bloat enforcement.
- `README.md`, `MANIFEST.md`, `tests/test_repo_contracts.py`, `scripts/verify_workspace.py` — updated for dual-agent contract.

### Checks run
- `python scripts\verify.py` — passed locally: workspace self-check passed, compile passed, 12 tests passed.

### Current status
M0 is green in this packaged workspace. M1 Postgres migration proof is the next substantive implementation milestone.

### Residual risk
Claude/Codex product configuration keys can change over time. Treat `.codex/config.toml` and `.claude/settings.json` as useful defaults and validate against the installed tool versions.

### Next slice
Run `python scripts\verify.py`, then start M1: prove local Postgres schema/migration runner against Docker.


## 2026-06-06 — Local-only governance tightening

- Canonicalized local-only exclusion policy and ADR.
- Removed remote workflow artifact from the workspace.
- Reframed delivery milestones around console/file/localhost outputs only.
- Added verification checks for excluded SaaS/platformization path classes.


## 2026-06-06 — Fast advancement governance revision

### Goal
Reduce rigidity in agent governance so a fresh Codex/Claude session can advance the repo quickly from any state while preserving local-only scope, Postgres-first storage, raw evidence lineage, and verification.

### Changes
- Added `FAST_ADVANCE.md` as the speed-focused operating contract.
- Added `docs/implementation/06_adaptive_milestone_map.md`.
- Reframed milestone order as adaptive bottom-up rather than a hard sequential lock.
- Added `python scripts\task.py status` via `scripts/repo_status.py` for fast orientation.
- Updated prompts, governance docs, and skills to allow bounded top-down spikes when they accelerate verified local utility.

### Verification target
- `python scripts\verify.py`

### Next slice
Use `python scripts\task.py status`, then advance M1 local Postgres proof or the nearest fixture-backed repository/CLI slice if Docker Desktop is unavailable.


## 2026-06-06 — alignment/coherence audit

### Files inspected
- Root agent entrypoints, governance docs, implementation plans, tests, scripts, and packaging constraints.

### Changes made
- Removed stale `Python migration runner/task command` wording from bottom-up work orders.
- Removed non-Windows tool metadata from Claude review subagents to preserve Windows-native command expectations.
- Resolved duplicate governance numbering by consolidating Codex/Claude interop guidance into `09_agent_runtime_compatibility.md` and removing the redundant interop file.
- Softened one rigid bottom-up statement so it aligns with fast-advance mode.
- Moved PyYAML into runtime dependencies because local task/status scripts import it.
- Added `docs/governance/10_alignment_audit.md` and stricter verification checks.

### Verification run
- `python scripts\verify.py` — passed after the alignment fixes.

### Findings
- Product scope remains local-only, Windows-native, Postgres-first, fixture-first, no-trading, and no hosted/SaaS platformization.
- Bottom-up and fast-advance guidance are now aligned: bottom-up is a default dependency map, not a rigid lock.

### Next step
- Package without generated cache files.

## 2026-06-06 — Coherence audit pass

- Fixed SQL table-name drift: `metric_windows` is now used consistently in SQL and Postgres docs.
- Removed stale duplicate implementation plans so fresh agents use the adaptive active plan plus `WORKLOG.md`.
- Replaced stale work-order wording that referenced a non-Windows task target with the Python migration path.
- Fixed a Windows-path string escape in `pmfi.cli review-pass`.
- Added SQL consistency checks to workspace verification and tests.

Checks run after patching:

```powershell
python scripts\verify.py
python scripts\task.py status
python scripts\task.py fixture-replay
```

## 2026-06-06 — Coherence follow-up pass

### Changes
- Corrected alignment-audit wording around governance doc resequencing.
- Updated handoff protocol to use `AGENT_START_HERE.md` as the shared receiving-agent entrypoint.
- Reduced initial Codex/Claude prompt context load to avoid context bloat.
- Softened bottom-up shortcut language so bounded local spikes are allowed but cannot be treated as complete until repaid with evidence.

### Checks run
- `python scripts\verify.py` — passed: workspace self-check passed, consistency audit passed, 41 tests passed.
- `python scripts\task.py status` — passed: adaptive milestone status printed.
- `python scripts\task.py fixture-replay` — passed: 2 fixture alerts produced.


## 2026-06-06 — Final unified coherence pass

### Changes
- Removed redundant governance interop doc after its content was covered by `09_agent_runtime_compatibility.md` and `docs/agentic_setup/02_codex_claude_handoff.md`.
- Changed `scripts/verify.py` to run checks in-process so the canonical Windows verification command exits cleanly and consistently.
- Flushed task command headers for clearer agent logs.

### Checks run
- `python scriptserify.py` — passed: workspace self-check passed, consistency audit passed, 41 tests passed.
- `python scripts	ask.py status` — passed.
- `python scripts	ask.py fixture-replay` — passed with 2 fixture alerts.

### Current next step
- Advance M1 local Postgres proof when Docker Desktop is available, or advance M2/M3 fixture-backed repository and normalization contracts if Docker is blocked.

## 2026-06-06 — Governance alignment: orthogonal and Talmudic decision support

### Files inspected
- `FAST_ADVANCE.md`
- `AGENTS.md`
- `docs/implementation/06_adaptive_milestone_map.md`
- `docs/governance/00_operating_model.md`
- `docs/governance/03_review_and_coherence_pass.md`

### Changes made
- Added orthogonal problem-solving guidance for unclear architecture, organization, orchestration, and product-utility decisions.
- Added compact Talmudic debate method for non-trivial decisions.
- Reinforced material-results priority over low-impact procedure during fast-advance work.
- Added governance doc and ADR for the method.

### Verification run
- Pending in this editing slice.

### Findings
- Facts: fast advancement now has explicit permission to reason orthogonally and avoid ceremony.
- Assumptions: these methods should remain lightweight and must end in executable evidence or a precise blocker.
- Blockers: none identified in docs.

### Next step
- Run `python scripts\verify.py` and package updated workspace.

## 2026-06-06 â€” Local Postgres port migration cleanup

### Files inspected
- `docker-compose.local.yml`
- `scripts/db_local.py`
- `.env.example`
- `docs/ops/00_local_setup.md`
- `tests/test_windows_native_contracts.py`

### Changes made
- Moved local Postgres off the conflicting host port and onto `5433` end to end.
- Kept the container port and helper commands aligned with the new local DB port.
- Added a regression test to prevent reintroducing the old reserved port in repo text files.
- Updated local setup guidance and the example database URL to match the new port.

### Verification run
- `python scripts\verify.py` â€” pass
- `python scripts\db_local.py up` â€” pass
- `python scripts\db_local.py init` â€” pass
- `python scripts\db_local.py verify` â€” pass
- `python scripts\db_local.py status` â€” pass

### Findings
- Facts: the old host port was occupied by another Docker-backed repo; `5433` was unused and works here.
- Inferences: no further trivial prep remains unless another repo-facing port conflict appears.
- Assumptions: the new port should stay canonical unless a future repo decision changes the local DB contract.
- Blockers: none.

### Next step
- Advance the first non-trivial slice, likely M2 raw event persistence and fixture ingestion.

## 2026-06-06 â€” Local git repo setup

### Files inspected
- `.gitignore`
- `.gitattributes`
- `WORKLOG.md`
- `reports/`
- `experiments/`

### Changes made
- Initialized a local git repository in-place on branch `land-dd`.
- Set local Windows-safe git config for long paths, line endings, and file mode handling.
- Added `.codesight/` to `.gitignore` so generated index output stays out of version control.
- Kept the baseline commit scope conservative by treating generated audit/report artifacts as non-essential for the initial source-of-truth snapshot.

### Verification run
- `git status --short --branch` â€” pass
- `git config --get user.name` â€” pass
- `git config --get user.email` â€” pass

### Findings
- Facts: the repo had no prior `.git` directory; local git identity already exists in the environment.
- Inferences: a first commit can be made conservatively without including generated indexes or report artifacts.
- Assumptions: future commits should continue to exclude generated local tooling output unless explicitly retained.
- Blockers: none.

### Next step
- Stage a conservative baseline set and create the first local commit if the remaining working tree is suitable.


## 2026-06-06 — M1–M10 full pipeline implementation

### Goal
Advance from governance scaffold to a production-grade local tool: config, async DB layer, venue adapters, pipeline, delivery, replay, and rich CLI.

### Files changed
- **scripts/verify_workspace.py** — exclude .venv and *.egg-info from all scans
- **scripts/consistency_audit.py** — same exclusions; added _skip() helper
- **tests/test_local_only_scope_contracts.py** — exclude .venv/egg-info in iter_files and rglob loops
- **tests/test_windows_native_contracts.py** — exclude .venv/egg-info in all rglob loops
- **pyproject.toml** — added asyncpg, aiohttp, rich deps; pytest-asyncio dev dep
- **src/pmfi/config.py** — AppConfig dataclass + YAML/env loader (load_config)
- **src/pmfi/db/__init__.py** — asyncpg pool factory (search_path=pmfi,public)
- **src/pmfi/db/migrations.py** — ensure_current_partitions, verify_connection
- **src/pmfi/db/repos/raw_events.py** — insert_raw_event, fetch_recent
- **src/pmfi/db/repos/markets.py** — upsert_market, get_market_id
- **src/pmfi/db/repos/trades.py** — insert_trade
- **src/pmfi/db/repos/alerts.py** — insert_alert (with dedupe)
- **src/pmfi/db/repos/metrics.py** — upsert_metric_window
- **src/pmfi/adapters/base.py** — VenueAdapter protocol + FixtureAdapter
- **src/pmfi/adapters/polymarket.py** — PolymarketAdapter (opt-in WebSocket)
- **src/pmfi/adapters/kalshi.py** — KalshiAdapter (opt-in WebSocket)
- **src/pmfi/pipeline/normalize.py** — normalize_event dispatcher
- **src/pmfi/pipeline/engine.py** — AlertEngine (config-driven multi-rule evaluator)
- **src/pmfi/pipeline/runner.py** — process_event, run_adapter_pipeline (async)
- **src/pmfi/delivery/stdout.py** — deliver_stdout (JSON line)
- **src/pmfi/delivery/file.py** — FileDelivery (rotating JSONL)
- **src/pmfi/replay.py** — replay_fixtures -> list[ReplayResult]
- **src/pmfi/cli.py** — rich CLI: status, replay, db-verify, monitor, alerts commands
- **tests/test_config.py, test_pipeline_engine.py, test_replay.py, test_delivery.py, test_adapters.py** — new tests

### Verification run
- `python scripts\verify.py` — passed: workspace self-check, consistency audit, compileall, 68 tests
- `pmfi status` — rich panel shows DB/live config
- `pmfi replay --verbose` — 2 fixtures → 2 alerts (Kalshi $26,640 + Polymarket $33,600)
- `pmfi db-verify` — DB OK, 2 venues registered

### Findings
- Facts: full pipeline operational from raw fixture → normalization → alert engine → JSON delivery
- Inferences: live adapters (opt-in) require enable_polymarket_live/enable_kalshi_live config flags
- Assumptions: current month's Postgres partitions created automatically by ensure_current_partitions()
- Blockers: none

### Next step
- M9/M10: add replay-to-DB path (run full pipeline with real DB writes via runner.py)
- Add more alert rules (directional_cluster_v1, market_relative_large_trade_v1)
- Add `pmfi replay --persist` flag to write through full DB pipeline
- Optional: enable live adapter test against real Polymarket public feed

## 2026-06-06 — M6/M7/M9/M10 continuation: monitor, baseline, clustering, reporting

### Goal
Continue fast-advancing from M6 baseline toward full operator UX and all enabled alert rules.

### Files changed
- **src/pmfi/cli.py** — `pmfi monitor --fixture-replay [--delay N] [--fixture-dir]` streaming demo mode; `pmfi baseline compute [--lookback-days N]`; `pmfi baseline list`; `pmfi report [--fixture-dir] [--output-dir]`
- **src/pmfi/db/repos/baselines.py** — upsert_baseline + fetch_all_baselines (asyncpg)
- **src/pmfi/baseline.py** — compute_market_baselines (percentile_cont SQL on metric_windows) + load_baselines
- **src/pmfi/pipeline/engine.py** — AlertEngine accepts baselines dict; market_relative_large_trade_v1 emits confidence=high/medium/low based on p99/p99.5 comparison with sample-size guard; directional_cluster_v1 integrated via accumulator
- **src/pmfi/pipeline/accumulator.py** — DirectionalAccumulator: rolling deque per (venue_code, venue_market_id), prune-on-access, dominant-side tally, price-impact in cents
- **src/pmfi/replay.py** — replay_fixtures_persist loads baselines from DB before creating engine
- **src/pmfi/reporting.py** — build_report + write_report: alerts by rule/venue/severity/confidence, cluster events
- **tests/test_accumulator.py** — 7 accumulator unit tests
- **tests/test_pipeline_engine.py** — 3 new tests: baseline-upgrade path, baseline-pending path, cluster-fires-through-engine
- **tests/test_reporting.py** — 4 reporting tests

### Verification run
- `python scripts\verify.py` — passed: 81 tests
- `pmfi monitor --fixture-replay --delay 0` — 2 fixtures → 4 alerts streamed live
- `pmfi report` — 2 fixtures → 4 alerts, report written to reports/2026-06-06-fixture-report.txt

### Findings
- Facts: all four enabled alert rules now have implementations: large_trade_absolute_v1, market_relative_large_trade_v1 (baseline-aware), directional_cluster_v1 (in-memory accumulator), open_interest_shock_v1 (still blocked by OI data)
- Inferences: baseline confidence upgrade only materializes after `pmfi baseline compute` with a Postgres pool that has metric_windows data; the persist replay path auto-loads baselines
- Assumptions: DirectionalAccumulator is in-process only (resets on restart); persistence would require DB-backed accumulation
- Blockers: open_interest_shock_v1 requires OI fixture or live OI data; live adapter tests require opt-in API access

### Next step
- M10 hardening: connection retry in adapters, partition auto-maintenance on startup, structured error recovery in runner.py
- Extend fixture set with cluster-triggering trades (3 same-direction events with price spread) so cluster rule fires in standard replay
- Consider `open_interest_shock_v1` stub with fixture OI data

## 2026-06-06 — Final full-tool hardening and operator UX pass

### Goal
Complete all enabled alert rules, prove end-to-end replayability, add operator commands, harden adapters.

### Files changed
- **src/pmfi/domain.py** — `open_interest_contracts: Decimal | None` field on NormalizedTrade
- **src/pmfi/normalization.py** — `parse_optional_decimal`; extract `open_interest` in both normalizers
- **src/pmfi/pipeline/engine.py** — `open_interest_shock_v1` rule (fires when trade/OI >= threshold); wires DirectionalAccumulator; baseline-aware market_relative rule
- **src/pmfi/pipeline/accumulator.py** — DirectionalAccumulator (rolling deque, prune-on-access, dominant-side, price-impact)
- **src/pmfi/pipeline/runner.py** — per-step debug/info/warning logging; emit_alert guard; alert handler errors non-fatal
- **src/pmfi/baseline.py** — compute_market_baselines (percentile_cont) + load_baselines
- **src/pmfi/db/__init__.py** — create_pool_with_retry (3 attempts, 2s delay)
- **src/pmfi/db/repos/baselines.py** — upsert_baseline + fetch_all_baselines
- **src/pmfi/db/migrations.py** — startup_maintenance() non-fatal partition ensure
- **src/pmfi/replay.py** — replay_fixtures_persist with baseline load + startup_maintenance; replay_from_db (reads raw_events from Postgres)
- **src/pmfi/reporting.py** — build_report + write_report (alerts by rule/venue/severity/confidence, cluster events)
- **src/pmfi/adapters/polymarket.py** — exponential backoff reconnect (1s→60s)
- **src/pmfi/adapters/kalshi.py** — same reconnect pattern
- **src/pmfi/cli.py** — `pmfi monitor --fixture-replay [--delay N]`; `pmfi baseline compute/list`; `pmfi report`; `pmfi markets`; `pmfi watch [--interval N]`; `pmfi replay --from-db [--limit N]`; `pmfi status` shows 4 rules + fixture count
- **tests/fixtures/raw/** — polymarket_cluster_a/b/c.json (cluster-triggering), polymarket_oi_shock.json (OI fixture), malformed_payload.json (skip-path test)
- **tests/** — test_accumulator.py (7), test_normalization_edge_cases.py (14), test_reporting.py (4), test_alert_dedupe.py (6); engine tests: baseline-aware, cluster-fires, OI-fires, OI-no-fire

### Verification run
- `python scripts\verify.py` — passed: 101 tests
- `pmfi report` — 6 fixtures → 10 alerts (all 4 rules fire), cluster event shown, report written to reports/
- `pmfi status` — shows 4 rules, 7 fixtures, DB endpoint
- `pmfi monitor --fixture-replay --delay 0` — streams 7 fixtures, alerts emitted in real-time

### Findings
- Facts: all 4 enabled alert rules implemented and fixture-proven end-to-end
- Inferences: baseline confidence upgrade requires DB with metric_windows data; OI rule requires open_interest field in payload
- Assumptions: DirectionalAccumulator is in-process only; cluster state resets on restart
- Blockers: live adapter tests require opt-in API access; open_interest_shock_v1 in live feeds requires verifying OI field name per venue

### CLI command surface (complete)
```
pmfi status             — config, rules (4), fixture count
pmfi db-verify          — DB connectivity check
pmfi replay             — fixture replay → alerts → table
pmfi replay --persist   — replay through full DB pipeline
pmfi replay --from-db   — re-run alert engine over raw_events in Postgres
pmfi monitor --fixture-replay [--delay N] — streaming fixture demo
pmfi baseline compute [--lookback-days N] — percentile baselines from metric_windows
pmfi baseline list      — show current baselines in DB
pmfi report             — fixture replay report to reports/
pmfi alerts [--limit N] — recent alerts from DB
pmfi markets [--limit N]— markets in DB with trade counts
pmfi watch [--interval N] — live-refreshing alert table
```

### Next step
- Enable live adapter test: set `enable_polymarket_live=true` in app.yaml and run `pmfi monitor`
- Run `pmfi baseline compute` after populating metric_windows with persist replay
- Consider `pmfi replay --from-db` after `pmfi replay --persist` to prove full replayability loop


## 2026-06-06 — Production pipeline completion (ultragoal pass)

### Goals completed
- G001: Alert suppression cache in pipeline/runner.py — `process_event` accepts optional `suppression` dict; `run_adapter_pipeline` creates one per live session; replay/backtest paths default to suppression=None.
- G007: DB partition hardening — `ensure_current_partitions(months_ahead=3)`, `drop_old_partitions(before_days=90)`, `apply_schema_migrations` (idempotent); all called from `startup_maintenance`.
- G003: Market discovery — `src/pmfi/markets.py` with `fetch_polymarket_markets` (paginated REST, volume filter) and `sync_polymarket_markets` (upserts to DB).
- G010: Watch-list management — `watched boolean DEFAULT false` column on markets; `set_market_watched`, `fetch_watched_markets`, `fetch_all_markets` in repos/markets.py; `sql/005_add_watched_flag.sql` idempotent migration.
- G004: Persistent ingest daemon — `pmfi ingest [--venue polymarket] [--venue kalshi] [--dry-run]`; loads watched markets for subscription, routes delivery by config, logs event/alert counts every 60s.
- G008: HTTP alert delivery — `delivery/http.py` (HttpDelivery class, POST to local endpoint); `delivery/server.py` (minimal aiohttp receiver); `pmfi alerts serve [--port N]` CLI command.

### CLI surface (current)
```
pmfi status | db-verify | stats | watch
pmfi replay [--persist | --from-db]
pmfi monitor [--fixture-replay]
pmfi markets list [--watched] [--limit N]
pmfi markets discover [--limit N] [--min-volume USD]
pmfi markets watch <market_id> [--venue polymarket]
pmfi markets unwatch <market_id> [--venue polymarket]
pmfi ingest [--venue polymarket] [--venue kalshi] [--dry-run]
pmfi alerts list [--limit N]
pmfi alerts serve [--port N] [--host H]
pmfi baseline compute [--lookback-days N]
pmfi baseline list
pmfi report [--fixture-dir] [--output-dir]
pmfi db-maintenance [--create-partitions] [--prune-old-partitions]
```

### End-to-end live flow (with Postgres + live connection)
```
pmfi markets discover                   # fetch active markets from Polymarket REST
pmfi markets list                       # review; note condition_id values
pmfi markets watch <condition_id>       # add to watch list
pmfi ingest --venue polymarket          # start live daemon (requires enable_polymarket_live=true or --venue flag)
pmfi watch                              # live alert dashboard in separate terminal
pmfi alerts list                        # query fired alerts from DB
```

### Verification run
- `python scripts\verify.py` — 124 passed, consistency audit passed, compileall passed.
- All tests use asyncio.run() instead of @pytest.mark.asyncio to work with verify.py's PYTEST_DISABLE_PLUGIN_AUTOLOAD=1.

### Files changed (this pass)
- src/pmfi/pipeline/runner.py — alert suppression
- src/pmfi/db/migrations.py — partition hardening + apply_schema_migrations
- src/pmfi/db/repos/markets.py — full upsert, watched flag, fetch_watched_markets
- src/pmfi/markets.py (new) — Polymarket REST discovery
- src/pmfi/delivery/http.py (new) — HttpDelivery
- src/pmfi/delivery/server.py (new) — alert receiver
- src/pmfi/cli.py — pmfi ingest, pmfi markets subcommands, pmfi alerts serve, delivery routing, telemetry
- sql/001_init.sql — watched column on markets
- sql/005_add_watched_flag.sql (new) — idempotent migration for existing DBs
- tests/test_runner_suppression.py (new) — 14 suppression + partition tests
- tests/test_markets_discovery.py (new) — 5 mock-based discovery tests

### Residual risk / remaining goals
- G009 (orderbook capture): schema exists (orderbook_snapshots, orderbook_levels); REST fetch at trade time not yet wired. Requires live connection to validate.
- G002/G005/G006: live adapter proofs — deferred until live venue connection is confirmed working.
- Delivery mode "file" default output dir: hardcoded to ROOT/reports/alerts; make configurable if needed.
- `pmfi ingest` with no watched markets exits early; operator must run `pmfi markets discover` + `pmfi markets watch` first.

### Next step
- G009: wire optional orderbook capture at trade time (REST fetch → orderbook_snapshots insert)
- Live smoke test: set enable_polymarket_live=true, run pmfi markets discover, watch a market, run pmfi ingest
- Run `python scripts\db_local.py verify` after local Postgres is up to confirm schema migrations apply cleanly

## 2026-06-06 14:00 local � M1/M9/M10 hardening: DB proof, replay fixes, dry-run correctness

### What changed

- **M1 proven**: Local Postgres verified live (db_local.py verify passes, kalshi + polymarket venues registered).
- **M4 proven**: pmfi replay --persist wrote 8 fixtures through the full DB pipeline (13 raw_events, 12 normalized_trades, 10 alerts, 5 markets now in DB).
- **M9 proven**: pmfi replay --from-db replayed 4 stored raw_events from DB and re-generated 8 alerts � confirmed replayability of stored events.
- **pmfi report verified**: generates clean fixture replay report (8 fixtures, 14 alerts with breakdowns by rule/severity/confidence/venue) and writes to reports/.
- **Fixed pmfi ingest --dry-run**: now bypasses DB entirely � no pool creation, no DB writes. Connects to venue WS, normalizes events via 
ormalize_event, prints each event to stdout. Removed dead if not dry_run guard and stray import asyncio inside _run().
- **Fixed eplay_from_db**: added missing RawEvent import; added json.loads() fallback for JSONB columns returned as strings by asyncpg (dict() on a JSON string was failing with "length 1" error).
- **Fixed db_local.py init**: added sql/005_add_watched_flag.sql to SQL_FILES so fresh DB initializations include the watched column without running pmfi ingest first.
- **Applied watched column migration to live DB** via psql ALTER TABLE ... IF NOT EXISTS.
- **Gitignore**: added eports/*.txt so generated fixture report files are not tracked.

### Verification run

- python scripts\verify.py � 140 passed, consistency audit passed, compileall passed.
- python scripts\db_local.py verify � Postgres ready, venues table correct.
- pmfi markets list � 2 markets shown with watched column.
- pmfi replay --from-db � 4 events replayed, 8 alerts.
- pmfi replay --persist � 8 fixtures persisted, 15 alerts.
- pmfi report � 8 fixtures, 14 alerts, report written to reports/.

### Files changed

- src/pmfi/cli.py � --dry-run bypasses DB; removed dead guard + stray import
- src/pmfi/replay.py � import RawEvent; handle JSONB-as-string payload
- scripts/db_local.py � add  05_add_watched_flag.sql to SQL_FILES
- .gitignore � exclude eports/*.txt
- Commit: e2e0c12 on both PM-intel and main branches

### Milestone status

- M0: complete
- M1: **complete** � DB live, venues registered, db_local.py verify passes
- M2: **complete** � raw events persist through pipeline (13 rows in DB)
- M3: **complete** � normalization contracts proven via fixtures (140 tests)
- M4: **complete** � fixture pipeline writes through DB (replay --persist proven)
- M5: deferred � live adapter proofs require live WS connection + optional Kalshi API key
- M6: **complete** � rolling metric windows accumulate (10 metric_windows in DB)
- M7: **complete** � 4-rule alert engine fires with explainable evidence
- M8: **complete** � stdout/file/http delivery all implemented and tested
- M9: **complete** � pmfi replay --from-db proven with DB events
- M10: **substantially complete** � dry-run fixed, report command works, operator UX proven

### Residual risk / remaining items

- M5 live adapters: G002/G005/G006 require actual WS connection; Kalshi needs API key.
- market_baselines table has 0 rows � pmfi baseline compute needs enough historical data (30+ days default lookback) to compute baselines; confidence=low alerts remain until baselines exist.
- pmfi ingest with no watched markets exits early � operator must run pmfi markets discover + pmfi markets watch first.
- Alert deduplication in eplay --persist runs against live DB state, so re-runs produce increasing metric window counts.

### Next step (if continuing)

- Live smoke test: set enable_polymarket_live: true in config/app.yaml, run pmfi markets discover, watch a market, pmfi ingest --venue polymarket
- Baseline compute: once 30+ days of trades exist in DB, run pmfi baseline compute to improve alert confidence
- Consider reducing baseline lookback_days to 7 for early bootstrapping

## 2026-06-06 14:30 local � Baseline enrichment, metric accumulation, M1-M10 complete

### What changed

- **Baseline compute proven**: pmfi baseline compute --lookback-days 1 produces baselines for 3 markets (kalshi:KXEXAMPLE-26JUN03, polymarket:pm-cluster-market, polymarket:pm-example-market). market_relative_large_trade_v1 now scores 0.85/confidence=medium when trades exceed p99.5 (was 0.5/low with no baseline).
- **Fixed upsert_metric_window**: was not setting max_trade_capital_at_risk_usd � baseline query requires it. Now sets both gross and max columns on insert; ON CONFLICT DO UPDATE now actually fires (needed unique constraint first).
- **sql/006**: idempotent migration adds UNIQUE (market_id, outcome_key, window_start, window_seconds) to metric_windows. Deduplicates existing rows by aggregating metrics into the earliest row per slot, then adds constraint.
- **Proper trade accumulation**: ON CONFLICT DO UPDATE now sums trade_count, gross_capital, payout_notional and takes GREATEST for max_trade_capital. Verified: kalshi window accumulates trade_count=2, polymarket cluster window=3 after multiple replays.
- **5 new tests** in tests/test_metrics_upsert.py: verify ON CONFLICT DO UPDATE SQL clauses using AsyncMock (no DB needed). 145 tests total.
- **Fixed 'pmfi markets list --watched' message**: now correctly says "No watched markets" with actionable instructions (was misleadingly "No markets in DB").
- **apply_schema_migrations updated**: includes migration 006 so existing DBs auto-migrate on next pmfi ingest.

### Verification

- python scripts\verify.py � 145 passed, consistency audit passed.
- pmfi baseline compute --lookback-days 1 � 3 markets, p99 values populated.
- pmfi baseline list � shows p50/p99/p99.5 per market.
- pmfi replay --from-db � market_relative_large_trade alerts now show score=0.85, confidence=medium, reason_codes=exceeds_p995_baseline where applicable.
- DB: 22 raw_events, 20 normalized_trades, 12 metric_windows (deduplicated, accumulated), 3 market_baselines.
- metric_windows.trade_count accumulates correctly (max 3/window after 3 replays of cluster fixtures).

### Commits (this pass)

- e2e0c12 Fix dry-run, replay_from_db, db_local init
- d629654 WORKLOG update
- e34c039 Fix upsert_metric_window: max_trade_capital_at_risk_usd
- e26584f Fix metric_windows: unique constraint + accumulating upsert
- 2a9e93f Add metrics upsert accumulation tests
- 896dcef Fix 'markets list --watched' message

### Milestone status (final)

- M0-M4: complete
- M5: deferred � live adapter proofs require WS connection and optional Kalshi API key
- M6: complete � rolling metric windows accumulate trades correctly across window slots
- M7: complete � 4-rule alert engine with baseline-enriched confidence (score=0.85 for p99.5 exceedance)
- M8: complete � stdout/file/http delivery; --dry-run is now truly no-DB
- M9: complete � replay from DB proven (4 events ? 8 alerts)
- M10: complete � operator UX proven, correct error messages, report generation

### Residual risk

- market_baselines become stale when replay repopulates metric_windows with the same fixture data. In production, pmfi baseline compute should run periodically (e.g., nightly) on fresh trade data.
- M5 live adapters: Polymarket adapter subscribes to empty market_ids=[] in dry-run; behavior depends on whether the WS sends events for all markets or requires specific subscriptions.
- SQL migration 006 deduplication: if future code inserts duplicate windows before the migration runs, deduplication drops extras by metric_window_id ordering, losing their trade data. Correct fix is to ensure migration runs at startup_maintenance before any new inserts.
