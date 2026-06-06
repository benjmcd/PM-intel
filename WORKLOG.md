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
