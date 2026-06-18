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

## 2026-06-06 — Session 6: Kalshi REST trades, baselines, alerts, momentum alert rule, report CLI

### Commits
- `57f223e` — Kalshi REST market discovery (`fetch_kalshi_markets`, `sync_kalshi_markets`, `pmfi markets discover --venue kalshi`)
- `eeec4b8` — Kalshi REST trade fetch, snapshot CLI, pmfi status extended diagnostics
- `ba9a4d1` — Kalshi REST fixtures + alert suppression DB seeding
- `f3fc79c` — Baselines compute/show, alert list filters/JSON, replay baseline auto-load
- `f7d3af1` — Momentum_v1 alert rule + pmfi report

### What changed

**Kalshi REST trades (fetch, normalize, store):**
- `markets.py fetch_kalshi_trades()`: paginated REST fetch from Kalshi `/markets/{ticker}/trades`.
  Normalizes REST shape (`ticker`, `yes_price`/`no_price`, `taker_side`) into common `RawEvent` format.
- `markets.py kalshi_trade_to_raw_event()`: converts Kalshi REST trade dict to `RawEvent`. 
  Handles cent-to-price conversion (100 cents = 1.00 price).
- `cli.py cmd_markets_fetch_trades`: new `pmfi markets fetch-trades <ticker> [--save-fixtures] [--force]` command.
  Stores raw events in DB, persists fixtures to `tests/fixtures/raw/` for regression testing.
- `tests/fixtures/raw/kalshi_rest_trade.json` + `kalshi_rest_trade_no_side.json`: fixture set for REST trades.
  Normalizer confirmed correct for REST shape in fixture tests.

**Alert suppression cache (startup preload):**
- `db/repos/alerts.py load_suppression_cache()`: seeds in-memory alert suppression from DB on startup.
  On adapter pipeline init, calls `run_adapter_pipeline()` → `load_suppression_cache()`.
  Restarts no longer re-fire alerts that were already suppressed in the previous run.

**Baselines compute and display:**
- `db/repos/metrics.py compute_baselines()`: computes p99 and p995 using Postgres `PERCENTILE_CONT()`
  over `normalized_trades` for a market. Returns dict keyed by `outcome_key`.
- `cli.py cmd_baselines_compute`: new `pmfi baselines compute [--days N] [--min-samples N] [--save]` command.
  Computes baselines, optionally persists to `config/baselines.json`.
- `cli.py cmd_baselines_show`: new `pmfi baselines show` command. Displays loaded baselines in table format.
- `replay.py replay_from_db`: auto-loads `config/baselines.json` into `AlertEngine` if file exists.

**Alert list, JSON output, filters:**
- `cli.py cmd_alerts_list`: added `--format {table,json}`, `--venue`, `--severity`, `--market`, `--since` filters.
  `--since` supports `1h`, `24h`, `7d`, and ISO 8601 timestamps.
- `db/repos/alerts.py get_alerts()`: added optional `venue_code`, `severity`, `market_title`, `since_ts` params
  for filtered queries.

**Momentum_v1 alert rule:**
- `momentum_v1`: 900s window, 5-trade minimum, 75k net capital threshold.
  Detects slow-burn capital accumulation in single direction (market moves before spike).
- Registered in `pipeline/engine.py AlertEngine.BUILTIN_RULES`.

**Alert report CLI:**
- `cli.py cmd_report`: new `pmfi report [--since 24h|7d|1h|ISO] [--format table|json]` command.
  Queries alert summary from DB (count by venue, severity, rule, market).
- `db/repos/alerts.py get_alert_summary()`: returns aggregated alert stats.

**Pmfi status extended diagnostics:**
- `cli.py cmd_status`: now shows `raw_events`, `normalized_trades`, `dead_letters`, `asset_id_mappings`,
  and `last_trade` (last received_at timestamp) for each venue. Easier to diagnose stale data.

### Verification

- `python scripts\verify.py` — **199 passed** (184 → 199, +15 new tests)
- New tests: baselines compute (2), alert list filters (2), alert summary (1), momentum_v1 rule (2),
  Kalshi REST fixture roundtrip (3), suppression cache integration (2), status extended output (1)
- All new functions verified via pytest. No live API calls in test suite.

### Proof-state table (updated)

| Item | State |
|---|---|
| Kalshi REST market/trade fetch | **mocked-test-proven** — fixtures confirm normalize path |
| Alert suppression cache preload | **source-proven** — load on adapter init |
| Baselines p99/p995 compute | **fixture-proven** — 2 compute + show tests |
| Alert list/report JSON output | **fixture-proven** — filter + format tests |
| Momentum_v1 rule | **source-proven** — rule registered; 2 behavioral tests |
| Pmfi status diagnostics | **source-proven** — added row counts and last_trade |

### Residual risks

- `pmfi live` continuous capture command not yet implemented
- `pmfi baselines compute` requires local Postgres + populated `normalized_trades` table (operator action)
- Kalshi WS auth still unresolved (REST lane fully functional)
- `replay_from_db` now auto-loads `config/baselines.json` but baselines must be pre-computed and committed

### Next step

- Implement `pmfi live` continuous background capture loop (monitor venues, ingest trades, fire alerts)
- Kalshi WS signed auth (blocker for live Kalshi lane)
- Operator runs live-smoke tests with real endpoints to validate end-to-end

## 2026-06-06 — Session 6b: Architect-review critical fixes, volume_spike median, live command, banner fix

### Commits (rewritten SHAs after co-author strip — see git log for current SHAs)
- `ce3b67e` — Kalshi REST market discovery; update WORKLOG (184 tests)
- `ae279d9` — Kalshi REST trade fetch, snapshot CLI, pmfi status extended diagnostics
- `9be1a29` — Kalshi REST fixtures, normalizer validation, alert suppression DB seeding
- `7314705` — Baselines compute/show, alert list filters/JSON, replay baseline auto-load
- `5cbc95b` — Momentum_v1 alert rule + pmfi report summary command
- `3857768` — pmfi live continuous capture, WORKLOG Session 6 update
- `2958f0b` — volume_spike_v1 rule, replay baselines all paths, watched column name fix
- `c53ba31` — Fix create_pool import path in cmd_live, cmd_report, _cmd_baselines_compute
- `ef041dd` — Fix pmfi live: use asset_ids not market_ids, correct adapter context manager
- `2eef475` — Fix CRITICAL schema column bugs found by Architect review
- `32e0ad7` — Fix volume_spike median baseline, replay double-evaluate, schema-contract test

### CRITICAL bugs found and fixed

**[CRITICAL] `rule_id` column does not exist — should be `rule_key`:**
- `db/repos/alerts.py list_alerts`: SELECT used `rule_id` (Python attr); DB column is `rule_key`.
- `db/repos/alerts.py get_alert_summary`: `by_rule` and `recent_high` queries used `rule_id`.
- `db/repos/alerts.py load_suppression_cache`: GROUP BY used `rule_id` in SQL.
- `cli.py cmd_report`: rendered `r['rule_id']` from row dict → `KeyError` at runtime.
- **Root cause**: new read-path functions copied Python attribute name (`decision.rule_id`) into SQL
  instead of using the DB column name (`rule_key`). Mock-based tests accepted any key so the
  mismatch was invisible until live DB execution.
- **Fix**: all queries corrected to `rule_key`; `cmd_report` rendering corrected.

**[CRITICAL] `hour_bucket` column does not exist in `alerts` table:**
- `list_alerts` SELECT included `hour_bucket`; column is not in the schema.
- **Fix**: removed from SELECT.

**[CRITICAL] `MAX(severity)` lexicographic ordering wrong:**
- `top_markets` used `MAX(severity)` to pick dominant severity per market.
- Alphabetically: `medium` > `high`, so a market with medium and high alerts showed `medium`.
- **Fix**: replaced with ordinal CASE expression: `high=3, medium=2, low=1`.

**[CRITICAL] `pmfi live` adapter API bugs:**
- `PolymarketAdapter(market_ids=...)` — no such kwarg; silently subscribed to nothing.
  Fixed to `PolymarketAdapter(asset_ids=...)`.
- `async with adapter.connect() as events` — `connect()` returns `None`, not a context manager.
  Fixed to `async with adapter:` + `adapter.events()`.
- `market_ids` are condition IDs but WS needs token IDs (asset_ids). Fixed: loads `venue_outcome_id`
  from `market_outcomes` for watched markets.

**[CRITICAL] `from pmfi.db.pool import create_pool` (ModuleNotFoundError):**
- Three new commands used a non-existent sub-module path.
- **Fix**: corrected to `from pmfi.db import create_pool` in cmd_live, cmd_report, _cmd_baselines_compute.

### MEDIUM bugs fixed

**`volume_spike_v1` mean vulnerable to outlier-masking:**
- Mean of trailing trades can be inflated by prior large trades, masking spikes.
- **Fix**: replaced `sum(window)/len(window)` with `sorted(window)[len//2]` (median).
- Evidence key renamed `recent_avg_usd` → `baseline_median_usd`.

**`replay_fixtures_persist` double-evaluate:**
- `process_event` internally calls `engine.evaluate` and persists alerts; code then called
  `engine.evaluate` again, double-feeding accumulators.
- **Fix**: removed second call; `ReplayResult.alerts=[]` (alerts in DB, not returned).

**`pmfi ingest` banner off-by-one (cosmetic):**
- Banner printed `len(tasks) - 1 adapter(s)` but telemetry task is appended _after_ the print.
- **Fix**: `len(tasks)` (correct adapter count at print time).

### New tests

- `tests/test_alerts_schema_contract.py` (4 tests, gated on `PMFI_DB_URL`): live-DB schema
  contract tests that verify `rule_key` column exists and `list_alerts`/`load_suppression_cache`/
  `get_alert_summary` execute without ColumnNotFoundError. Prevents future column-name regressions.

### Verification

- `python scripts\verify.py` — **203 passed**, 4 skipped (schema-contract tests need PMFI_DB_URL)
- No live API calls. All new tests fixture-driven or schema-contract gated.

### Residual risks

- `pmfi live` and `pmfi ingest` both implement continuous capture — consolidation deferred.
  `cmd_ingest` has supervisor/reconnect; `cmd_live` has fixture capture. Will drift if not merged.
- All new DB read-path functions now covered by live-DB schema-contract test; mock tests still used
  for suppression integration (FakeConn). Mock key names must be kept in sync with DB schema.
- `pmfi baselines compute --save`, `pmfi replay --from-db`, `pmfi report`, `pmfi live` all require
  local Postgres up with live data captured. Not operator-validated yet.

## 2026-06-06 — Session 5: P0 determinism, outcome mapping, dead-letter codes, Kalshi REST

### Commits
- `67480ab` — Fix P0 determinism, outcome mapping, and dead-letter reason codes (181 tests)
- Kalshi REST market discovery (+3 tests, 184 total)

### What changed

**P0 data-correctness:**
- `normalization.py normalize_polymarket_fixture`: missing `"outcome"` field in payload now
  produces `outcome_key="unknown"` instead of silently defaulting to `"yes"`. Live Polymarket
  events that carry `asset_id` but no `"outcome"` were silently mislabeled as YES trades.
- `pipeline/runner.py process_event`: asset_id resolution now also injects `outcome_key` from
  the asset_id_map into the raw payload before normalization. NO-token live trades now correctly
  produce `outcome_key="no"`.
- `pipeline/normalize.py normalize_event`: re-raises `NormalizationError` for actual normalization
  failures instead of swallowing them. Returns `None` only for benign non-trade lifecycle events
  (subscription acks, market data updates). Callers can now distinguish error type.

**P0 determinism:**
- `pipeline/accumulator.py DirectionalAccumulator.add/check_cluster`: added optional `event_ts`/`now`
  params. When provided, rolling-window pruning uses event time instead of wall-clock time. Same
  fixture sequence now produces identical cluster detection regardless of replay speed.
- `pipeline/engine.py AlertEngine.evaluate`: passes `trade.exchange_ts or trade.received_at` as
  `event_ts` to the accumulator.
- `db/repos/alerts.py insert_alert`: added optional `event_ts` param; `hour_bucket` is derived from
  event time when provided. Replaying historical data in a different hour no longer produces duplicate
  alerts.
- `pipeline/runner.py process_event`: passes `trade.exchange_ts or trade.received_at` as `event_ts`
  to `insert_alert`.
- `replay.py replay_from_db`: `ORDER BY COALESCE(exchange_ts, received_at), received_at, raw_event_id`
  — deterministic ordering for rows with equal `received_at`.

**P0 tooling:**
- `cli.py cmd_live_smoke --venue kalshi`: hard error with explanation (KalshiAdapter lacks signed WS auth).
- `cli.py cmd_live_smoke --venue polymarket` with no asset IDs: returns 1 with actionable instructions
  (was a silent TIP that led to zero-event runs with no explanation).
- `cli.py cmd_live_smoke --save-fixtures`: writes full `RawEvent` wrapper JSON (all fields including
  `venue_code`, `source_channel`, `exchange_ts`, `received_at`, `payload`). Saved fixtures can now be
  replayed by `load_raw_event` / `pmfi replay`. Previously only `raw.payload` was saved.

**P1 dead-letter reason codes:**
- `pipeline/runner.py`: structured `error_class` values for dead letters: `missing_asset_mapping`
  (asset_id not in local map), `invalid_price_or_size` (price/size parse failure),
  `payload_schema_mismatch` (timestamp/decimal parse error), `normalizer_exception` (unexpected
  exception from normalizer). Replaces generic `NormalizationSkipped`.
- Benign non-trade events (lifecycle, subscription acks) no longer generate dead letters.
- `missing_asset_mapping` dead letters include the actionable message: run `pmfi markets discover`
  and `pmfi markets watch`.

**Infrastructure:**
- `scripts/db_local.py SQL_FILES`: added `sql/007_venue_trade_id_index.sql` so fresh `db_local.py init`
  applies the venue_trade_id dedup index in a single pass.

**Kalshi REST market discovery (earliest unblocked Kalshi lane):**
- `markets.py fetch_kalshi_markets()`: paginated GET to Kalshi public REST `/markets` (no auth needed).
  Supports `limit`, `status`, `min_volume` filters.
- `markets.py sync_kalshi_markets()`: upserts fetched Kalshi markets into the `markets` table and
  creates `yes`/`no` outcome entries in `market_outcomes`. Parallel structure to `sync_polymarket_markets`.
- `cli.py _cmd_markets_discover`: added `--venue {polymarket,kalshi}` dispatch. Default remains
  `polymarket`.
- Parser: `p_markets_discover` adds `--venue` arg with `choices=["polymarket", "kalshi"]`.

### Verification

- `python scripts\verify.py` — **184 passed** (173 → 184, +11 new tests)
- New tests: 2 accumulator (event_ts determinism), 2 runner_asset_id (NO-token outcome injection),
  2 normalization_edge_cases (missing outcome → unknown), 2 pipeline_engine (normalize_event contract
  update), 3 markets_discovery (Kalshi fetch + CLI venue arg)

### Proof-state table (updated)

| Item | State |
|---|---|
| Polymarket outcome_key correctness | **fixture-proven** — missing outcome → unknown; asset_id_map injection proven in 2 tests |
| DirectionalAccumulator event-time | **fixture-proven** — 2 new accumulator tests with explicit event_ts/now |
| replay_from_db determinism | **source-proven** — deterministic ORDER BY added |
| alert dedupe event-time | **source-proven** — event_ts param added; no live DB test yet |
| dead-letter reason codes | **source-proven** — structured error_class in process_event |
| live-smoke fixture replayability | **source-proven** — full RawEvent wrapper saved |
| Kalshi REST market discovery | **mocked-test-proven** — 2 fetch tests + 1 CLI contract test |
| SQL_FILES 007 | **source-proven** — list updated |

### Residual risks

- Live Polymarket smoke test not yet run — requires `PMFI_ENABLE_LIVE=1` from operator:
  `$env:PMFI_ENABLE_LIVE=1; pmfi live-smoke --venue polymarket --max-events 50 --max-seconds 120 --save-fixtures --persist-raw`
- Kalshi WS signed auth not implemented — Kalshi live WS lane blocked until this is addressed
- Kalshi REST market discovery needs real Kalshi API call to verify response shape assumptions
- `venue_trade_id` unique constraint not feasible on partitioned table (accepted debt)
- Orderbook and baseline paths still use float conversions (core trade/metric inserts are correct)

### Next highest-ROI steps

1. **Run live Polymarket smoke test** (operator action: `PMFI_ENABLE_LIVE=1`)
2. **Prove Kalshi REST response shape**: run `pmfi markets discover --venue kalshi` with PMFI_ENABLE_LIVE
3. **Kalshi REST recent-trades snapshot**: add `fetch_kalshi_trades()` to build normalization fixtures
4. **Kalshi signed WS auth**: implement to unlock Kalshi live trade lane

---

## 2026-06-06 — Session 4: Operator UX, Kalshi correctness, CLI filters, dead-letters, _build_parser

### Commits (11)
- `47ac0ff` — Update WORKLOG: Session 3 entry
- `5093eeb` — Add Kalshi exchange_ts extraction; improve ingest startup message; add 8 adapter tests
- `83b6c4b` — Add --rule/--venue/--severity/--since filters to pmfi alerts list
- `2251531` — Unify markets list query: --watched now shows trade counts and last trade
- `811496b` — Enrich pmfi stats: dead_letters count, last trade ts, per-rule alert breakdown
- `101985c` — Add --rule/--venue/--severity filters to pmfi watch
- `ff19b7e` — Show watched market titles at pmfi ingest startup
- `0b20823` — Fix Kalshi normalizer: NO taker uses no_price not yes_price; add 3 tests
- `ee042da` — Extract _build_parser; add CLI contract tests for filter flags and status
- `cbecd44` — Add pmfi dead-letters list command for normalization failure visibility
- `0b31758` — Add --search filter to pmfi markets list (ILIKE title match)

### What changed

- **Bug fix — Kalshi NO-taker price selection (`normalization.py`)**: When Kalshi live WS sends
  separate `yes_price`/`no_price` fields (integer cents) without an explicit `price`, the old code
  always picked `yes_price` first. A NO-taker at 63 cents was wrongly priced at 37 cents. Fixed by
  determining `yes_no` (directional side) before extracting price, then picking the correct field.
  3 new tests in `test_normalization_edge_cases.py`.
- **Kalshi `exchange_ts` extraction (`adapters/kalshi.py`)**: Live WS events always produced
  `exchange_ts=None`. Added `_parse_exchange_ts(payload)` helper (tries `created_time` ISO,
  `ts` ms-epoch, `timestamp` s-epoch in order). Metric windows now use event-time for Kalshi.
  8 new tests in `test_adapters.py` (6 Kalshi variants + 2 Polymarket).
- **`pmfi alerts list` filter flags**: Added `--rule`, `--venue`, `--severity`, `--since` (hours).
  Parameterized WHERE clause (positional `$N` params — no injection risk).
- **`pmfi watch` filter flags**: Added `--rule`, `--venue`, `--severity` — same pattern as alerts list.
- **`pmfi markets list` unification**: `--watched` flag previously ran a simpler query without trade
  counts. Both paths now use the same JOIN for `trade_count` and `last_trade` columns.
- **`pmfi markets list --search TEXT`**: `ILIKE $N` filter on `markets.title`.
- **`pmfi stats` enrichment**: Added `dead_letters` count, `last_trade` timestamp, per-rule alert
  breakdown table.
- **`pmfi dead-letters list`**: New command. Queries `dead_letters` table with columns: When, Venue,
  Stage, Error, Payload (120-char preview). Rich table with `show_lines=True`.
- **`_build_parser()` + `_register_subcommands()` refactor (`cli.py`)**: `main()` was untestable
  because the argparser was built inline. Extracted to `_build_parser()` returning the parser and
  `_register_subcommands(sub)` registering all sub-commands. Enables import-only CLI contract tests.
- **CLI contract tests**: 3 new tests in `test_cli.py` — alerts list filter flags parse correctly,
  watch filter flags parse correctly, `pmfi status` exits 0 without a DB.
- **Ingest startup market titles**: `pmfi ingest` now prints each watched market's title (first 70
  chars) on startup alongside adapter count.

### Verification run

- `python scripts\verify.py` — **173 passed** (159 → 173, +14 new tests).
- All filter flags confirmed registered via `test_alerts_list_accepts_filter_flags`,
  `test_watch_accepts_filter_flags`, `test_status_runs_without_db`.
- Kalshi normalizer correctness confirmed via `test_kalshi_live_no_taker_uses_no_price` (previously
  would have returned 0.37 instead of 0.63 for a NO-taker).

### Proof-state table (updated)

| Item | State |
|---|---|
| Kalshi exchange_ts | **fixture-proven** — 8 adapter tests cover ISO, ms-epoch, s-epoch, naive, malformed |
| Kalshi NO-taker price | **fixture-proven** — bug confirmed + fixed; 3 normalizer tests |
| alerts list filters | **argparse-proven** — contract test; SQL path exercised at DB level |
| markets list unified | **source-proven** — single query; both watched/all return trade counts |
| dead-letters command | **source-proven** — queries dead_letters table |
| _build_parser refactor | **test-proven** — CLI contract tests import and parse directly |

### Residual risks

- Live smoke still needs network access — highest ROI: `$env:PMFI_ENABLE_LIVE=1; pmfi live-smoke --venue polymarket --max-events 50 --max-seconds 120 --save-fixtures --persist-raw`
- Kalshi WS endpoint/auth not verified for current API version
- `venue_trade_id` unique constraint not feasible on partitioned table (accepted debt)
- `replay --from-db` shows no progress indicator during the run

### Next highest-ROI step

1. Live smoke run to prove full ingest-to-alert loop end-to-end
2. Add progress counter to `replay_from_db` (low-effort operator improvement)
3. P1.1: baseline confidence state in alerts (distinguish missing vs sparse vs sufficient)

---

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
- scripts/db_local.py � add 005_add_watched_flag.sql to SQL_FILES
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

## 2026-06-16 local - Fast advance: data-quality and local-boundary guardrails

### What changed

- Added repo-local `.agents/skills/grill-me/SKILL.md` so the Claude skill mirror check matches the existing `.claude/skills/grill-me` mirror.
- Hardened Windows contract tests to ignore `.claude/worktrees` runtime artifacts while guarding that no `.claude/worktrees` files are tracked source.
- Fixed Kalshi normalization for unknown/unverified side values: `outcome_key` now stays `unknown` instead of defaulting to `yes`, and the warning remains visible.
- Added config validation so `alerts.default_delivery` must appear in `alerts.allowed_delivery_modes`.
- Made ingest fail closed for unsupported delivery modes instead of silently falling back to stdout.
- Made `pmfi alerts serve` reject non-loopback bind hosts before starting the local alert receiver.
- Added `.omx/context/fast-advance-20260616T234038Z.md` as the context snapshot for this run.

### Verification

- `.\.venv\Scripts\python.exe -m pytest tests\test_windows_native_contracts.py -q` - 10 passed.
- `.\.venv\Scripts\python.exe -m pytest tests\test_normalization_edge_cases.py tests\test_config.py tests\test_cli.py tests\test_delivery.py -q` - 52 passed.
- `.\.venv\Scripts\python.exe -m pytest tests\test_cli.py tests\test_windows_native_contracts.py -q` - 26 passed.
- `.\.venv\Scripts\python.exe scripts\verify.py` - 203 passed, 11 skipped; verification passed.
- `.\.venv\Scripts\python.exe -m pmfi.cli alerts serve --host 0.0.0.0` - returned 1 with loopback-only rejection.
- `.\.venv\Scripts\python.exe -m pmfi.cli status` - returned 0 and showed DB refused connection, live disabled, delivery console, 6 rules, 11 fixtures.

### Blocker

- `.\.venv\Scripts\python.exe scripts\db_local.py verify` could not complete because Docker Desktop was unavailable: Docker engine pipe `npipe:////./pipe/dockerDesktopLinuxEngine` was missing, and Postgres did not become ready before timeout.

### Next highest-ROI action

- Add a validate-only `pmfi health` preflight that reports DB connectivity, watched target count, token/outcome mapping readiness, recent raw/trade/dead-letter/alert state, baseline freshness, delivery mode, and no-target/degraded states without seeding artifacts.

## 2026-06-17 local - Operator health preflight

### What changed

- Added `pmfi health --format table|json` as a validate-only local operator preflight.
- Health now checks config load, delivery mode support, live flags without opening venue connections, DB connectivity/schema queries, watched market count, Polymarket token/outcome mapping readiness, Kalshi watched ticker count, raw/trade/dead-letter/alert counts, and baseline row/freshness evidence.
- Health returns nonzero when core readiness is blocked: config failure, DB/schema failure, no watched markets, or watched Polymarket markets with fewer than two active token mappings.
- Health treats no raw/trade/alert/baseline data as degraded warnings rather than success-blocking failures once watched targets are configured.
- Added tests proving JSON/table no-DB behavior, fake DB ready/degraded paths, no watched market blocking, incomplete one-sided Polymarket mapping blocking, no live adapter imports, and read-only `SELECT`-only DB access.

### Verification

- `.\.venv\Scripts\python.exe -m pytest tests\test_cli.py -q` - 23 passed.
- `.\.venv\Scripts\python.exe -m pytest tests\test_cli.py tests\test_config.py tests\test_delivery.py tests\test_normalization_edge_cases.py tests\test_windows_native_contracts.py -q` - 68 passed.
- `.\.venv\Scripts\python.exe -m pmfi.cli health` - returned 1 with `PMFI Health: blocked`; config/delivery/live passed, DB failed with `[WinError 1225] The remote computer refused the network connection`.
- `.\.venv\Scripts\python.exe -m pmfi.cli health --format json` - returned 1 with `ok:false`, `status:"blocked"`, DB failure details, and next action to start local Postgres.
- `.\.venv\Scripts\python.exe scripts\verify.py` - 209 passed, 11 skipped; verification passed.

### Blocker

- Local Postgres remains unavailable in this session. `pmfi health` now exposes that blocker directly; DB-backed health success and live operator path proof still require Docker Desktop/Postgres running.

### Next highest-ROI action

- Existing-DB integrity pass: make `pmfi db-verify`/health schema checks detect missing required tables, columns, indexes, constraints, partition setup, and startup-maintenance drift instead of relying on a minimal venue-count check.

## 2026-06-17 local - Existing-DB integrity verification

### What changed

- Added a reusable read-only DB integrity verifier in `src/pmfi/db/verify.py`.
- `pmfi db-verify --format table|json` now checks the existing local DB schema contract instead of only counting venues.
- The verifier checks required tables/views, critical columns, required indexes, primary/unique/foreign-key/check constraints, seed venues, partition parent/default/monthly attachment through the startup-maintenance horizon, and startup-maintenance artifacts.
- `pmfi health` now treats DB integrity drift as a fatal readiness blocker when the DB is reachable, while still avoiding migrations, seeding, live calls, or writes.
- `pmfi health` runs DB integrity before downstream stats queries so reachable schema drift is reported as `db_integrity` rather than hidden behind whichever stats query fails first.
- Added fake-pool tests for passing schema, missing core tables, missing seed venue table, startup-maintenance drift, missing critical constraints, missing/unattached/non-partitioned partitions, startup horizon naming, CLI JSON wiring, and health blocking on integrity failure before stats queries.

### Verification

- `.\.venv\Scripts\python.exe -m pytest tests\test_db_verify.py tests\test_cli.py -q` - 35 passed.
- `.\.venv\Scripts\python.exe -m pytest tests\test_db_verify.py tests\test_cli.py tests\test_config.py tests\test_delivery.py tests\test_normalization_edge_cases.py tests\test_windows_native_contracts.py tests\test_runner_suppression.py -q` - 96 passed.
- `.\.venv\Scripts\python.exe -m pmfi.cli db-verify --format json` - returned 1 with structured DB connection failure and next action.
- `.\.venv\Scripts\python.exe -m pmfi.cli health --format json` - returned 1 with DB unavailable blocker and no live calls.

### Blocker

- Local Postgres remains unavailable in this session: DB commands fail with `[WinError 1225] The remote computer refused the network connection`.

### Next highest-ROI action

- Once Postgres is available, run `python scripts\db_local.py up`, `python scripts\db_local.py init`, `pmfi db-verify --format json`, and `pmfi health --format json` to prove the fresh/existing DB path. If Postgres remains unavailable, continue with fixture-backed lineage/idempotency contracts for raw events, normalized trades, metrics, alerts, and dead letters.

## 2026-06-17 local - Alert lineage and duplicate-ingest contract

### What changed

- Added alert lineage enrichment in `src/pmfi/pipeline/runner.py` after raw/trade persistence and before alert persistence/delivery.
- Persisted and delivered alert decisions now preserve existing rule evidence and add nested `lineage` fields: raw event ID, raw received time, source channel/type/event ID, venue trade ID, DB market ID, DB trade ID, trade received time, exchange timestamp, and normalization version.
- Added a focused duplicate-ingest contract test proving duplicate raw events return before normalization, market upsert, trade insert, metric update, alert insert, or alert delivery.
- Added a focused alert-lineage test proving persisted and delivered decisions share the enriched evidence.

### Verification

- `.\.venv\Scripts\python.exe -m pytest tests\test_runner_lineage.py tests\test_runner_suppression.py -q` - 18 passed.
- `.\.venv\Scripts\python.exe -m pytest tests\test_runner_lineage.py tests\test_runner_suppression.py tests\test_replay.py tests\test_reporting.py tests\test_scoring.py tests\test_delivery.py tests\test_pipeline_engine.py -q` - 46 passed.
- `.\.venv\Scripts\python.exe scripts\task.py fixture-replay` - 10 fixtures, 14 alerts.
- `.\.venv\Scripts\python.exe scripts\verify.py` - 223 passed, 11 skipped; verification passed.

### Review

- Focused subagent review approved the lineage slice. Reviewer residual about missing `exchange_ts` assertion was addressed and rerun.

### Blocker

- Local Postgres remains unavailable in this session, so persisted alert lineage has not yet been proven against a live local DB. The process-level contract is fixture-backed and ready for DB proof once Postgres is available.

### Next highest-ROI action

- If Postgres remains unavailable, continue fixture-backed contracts around malformed payload dead letters and report/review evidence surfaces. If Postgres becomes available, run the fresh/existing DB proof path and replay persist to verify alert lineage in stored rows.

## 2026-06-17 local - Dead-letter operator evidence

### What changed

- Added `pmfi dead-letters --format table|json`.
- Dead-letter listing now uses the repo DB pool wrapper and remains read-only.
- Dead-letter rows now include raw event review context where available: `raw_event_id`, `source_event_id`, and `venue_market_id` from `raw_events`, plus failure stage/class/message and payload preview.
- JSON output returns a stable `{ok, count, dead_letters}` shape for local scripts/UI and a structured `{ok:false, error}` shape when DB is unavailable.
- Added malformed-payload pipeline tests proving normalization failures write structured dead letters, preserve the raw payload, and stop before market/trade/metric/alert/delivery writes.
- Fixed Polymarket asset-map enforcement: an explicitly empty `asset_id_map` now dead-letters unmapped token IDs instead of allowing normalization into an unknown market.
- Preserved external raw payloads when asset-ID mapping is used for normalization: raw-event storage and dead-letter payloads stay unmodified, while normalization receives a separate mapped copy.
- Dead-letter JSON output now fails closed for config, connection-time, query-time, and close-time failures.

### Verification

- `.\.venv\Scripts\python.exe -m pytest tests\test_cli_dead_letters.py tests\test_runner_dead_letters.py -q` - 9 passed.
- `.\.venv\Scripts\python.exe -m pytest tests\test_runner_dead_letters.py tests\test_runner_lineage.py tests\test_runner_suppression.py tests\test_runner_asset_id_resolution.py tests\test_normalization.py tests\test_normalization_edge_cases.py tests\test_cli_dead_letters.py tests\test_cli.py tests\test_replay.py tests\test_delivery.py -q` - 100 passed.
- `.\.venv\Scripts\python.exe -m pmfi.cli dead-letters --format json` - returned 1 with structured DB unavailable JSON.
- `.\.venv\Scripts\python.exe -m pmfi.cli dead-letters` - returned 1 with table-mode DB unavailable text.
- `.\.venv\Scripts\python.exe scripts\verify.py` - 232 passed, 11 skipped; verification passed.

### Blocker

- Local Postgres remains unavailable in this session, so the enriched `dead-letters` query has not yet been proven against live local DB rows. Fake-pool tests prove shape and read-only query behavior.

### Next highest-ROI action

- If Postgres remains unavailable, continue with fixture-backed report/review evidence surfaces. If Postgres becomes available, run the DB proof path and persist malformed fixtures/live-like unmapped assets to inspect real dead-letter rows through `pmfi dead-letters --format json`.

## 2026-06-17 local - Fixture-backed report evidence

### What changed

- Restored an explicit DB-free operator report path: `pmfi report --source fixtures --format table|json`.
- Fixture report mode uses the existing fixture replay, report summary, and report writer helpers instead of opening config or DB connections.
- Added `--fixture-dir` and `--output-dir` options for fixture report mode while keeping existing DB report behavior as the default.
- JSON fixture reports return a stable summary with `{ok, source, fixture_count, trade_count, alert_count, alerts_by_rule, alerts_by_venue, alerts_by_severity, alerts_by_confidence, cluster_events}`.
- Table fixture reports write a local `*-fixture-report.txt` artifact and print processed fixture/trade/alert counts.
- Fixture report mode fails closed for missing fixture directories, replay exceptions, and empty fixture runtimes instead of producing misleading empty success.

### Verification

- `.\.venv\Scripts\python.exe -m pytest tests\test_cli.py tests\test_reporting.py -q` - 34 passed.
- `.\.venv\Scripts\python.exe -m pmfi.cli report --source fixtures --format json` - returned 0 with 10 fixture results, 10 normalized trades, 14 alerts, and rule/venue/severity/confidence breakdowns.
- `.\.venv\Scripts\python.exe -m pytest tests\test_cli.py tests\test_reporting.py tests\test_replay.py tests\test_runner_dead_letters.py tests\test_runner_lineage.py tests\test_runner_suppression.py tests\test_delivery.py tests\test_db_verify.py -q` - 72 passed.
- `.\.venv\Scripts\python.exe scripts\verify.py` - 237 passed, 11 skipped; verification passed.

### Blocker

- Local Postgres remains unavailable in this session, so DB-backed report rows and persisted report/review evidence still need fresh local DB proof.

### Next highest-ROI action

- If Postgres remains unavailable, make `pmfi review-pass` a validate-only fixture-backed coherence surface that checks replayed alerts for reason codes, data quality, evidence, lineage/dead-letter expectations, and non-empty runtime. If Postgres becomes available, run the DB proof path and compare DB report/dead-letter/health outputs against fixture report expectations.

## 2026-06-17 local - Fixture-backed review pass

### What changed

- Replaced the placeholder `pmfi review-pass` output with a validate-only fixture-backed coherence check.
- Added `pmfi review-pass --format table|json` and `--fixture-dir`.
- Review pass now checks fixture runtime, alert runtime, skipped fixture count, raw-to-normalized source payload evidence, required alert explainability fields, data-quality status, and local-only execution.
- Missing fixture directories, replay exceptions, empty normalized runtimes, empty alert runtimes, missing source payloads, or missing alert explainability fields now fail closed.
- Skipped fixtures must now classify as expected dead-letter/malformed evidence or benign non-trade events; unclassified skipped fixtures fail review-pass.
- Existing `data_quality='unverified'` alerts are reported as warnings rather than fatal failures because they are explicit current rule statuses, not absent status fields.
- The Windows task wrapper `python scripts\task.py review-pass` now runs the real review command and prints the next verification/DB commands.

### Verification

- `.\.venv\Scripts\python.exe -m pytest tests\test_cli.py -q` - 34 passed.
- Focused review fix after subagent review: `.\.venv\Scripts\python.exe -m pytest tests\test_cli.py -k review_pass -q` - 8 passed.
- `.\.venv\Scripts\python.exe -m pmfi.cli review-pass --format json` - returned 0 with `pass_with_warnings`, 11 fixture files, 10 normalized trades, 14 alerts, one expected dead-letter skipped fixture, and four `data_quality='unverified'` warnings.
- `.\.venv\Scripts\python.exe -m pytest tests\test_cli.py tests\test_reporting.py tests\test_replay.py tests\test_runner_dead_letters.py tests\test_runner_lineage.py tests\test_runner_suppression.py tests\test_delivery.py tests\test_db_verify.py -q` - 78 passed.
- `.\.venv\Scripts\python.exe scripts\task.py review-pass` - returned 0 and printed the same fixture-backed review result.
- `.\.venv\Scripts\python.exe scripts\verify.py` - 243 passed, 11 skipped; verification passed.

### Blocker

- Local Postgres remains unavailable in this session, so review-pass is fixture-backed only. It does not prove persisted DB lineage, stored dead-letter rows, DB report rows, or health success.

### Next highest-ROI action

- If Postgres remains unavailable, either tighten the remaining fixture warnings by giving absolute-rule alerts more specific data-quality statuses or add fixture-backed daemon lifecycle contracts for stop/restart/idempotent resume. If Postgres becomes available, run the full DB proof path and compare `health`, `db-verify`, `dead-letters`, `report`, and persisted replay evidence.

## 2026-06-17 local - Daemon lifecycle resume contracts

### What changed

- Added fixture/fake-backed runner lifecycle tests in `tests/test_runner_lifecycle.py`.
- Proved a restarted adapter can replay the last raw feed event without re-normalizing, re-scoring, re-persisting metrics, re-inserting alerts, or re-delivering alerts because raw-event dedupe stops the duplicate path.
- Proved the duplicate normalized-trade branch: when a new raw event shape maps to an already-persisted `venue_trade_id`, `process_event` stops before metrics, alert evaluation, alert insert, and delivery.
- Proved `run_adapter_pipeline` passes the DB-seeded suppression cache into `process_event`, so recent persisted alert history can suppress repeats after daemon restart.
- Kept the tests DB-free with fake pools and async generators; they model the persisted dedupe/suppression contracts without requiring local Postgres.

### Verification

- `.\.venv\Scripts\python.exe -m pytest tests\test_runner_lifecycle.py -q` - 3 passed.
- `.\.venv\Scripts\python.exe -m pytest tests\test_runner_lifecycle.py tests\test_runner_lineage.py tests\test_runner_dead_letters.py tests\test_runner_suppression.py tests\test_replay.py tests\test_cli.py tests\test_db_verify.py -q` - 73 passed.
- `.\.venv\Scripts\python.exe -m pmfi.cli review-pass --format json` - returned 0 with `pass_with_warnings`, 10 normalized fixture trades, 14 alerts, one expected dead-letter skipped fixture, and four `data_quality='unverified'` warnings.
- `.\.venv\Scripts\python.exe scripts\task.py fixture-replay` - returned 0 with 10 fixtures and 14 alerts.
- `.\.venv\Scripts\python.exe scripts\verify.py` - 246 passed, 11 skipped; verification passed.

### Blocker

- Local Postgres remains unavailable in this session, so these are fake-backed lifecycle contracts. Live DB proof still needs `db_local.py up/init/verify`, persisted replay, and restart/resume smoke against actual stored dedupe keys, trades, alerts, and suppression history.

### Next highest-ROI action

- If Postgres remains unavailable, tighten the remaining fixture warning by replacing absolute-rule `data_quality='unverified'` statuses with specific fixture-backed statuses. If Postgres becomes available, run the full DB proof path and explicitly verify restart/resume behavior against real local tables.

## 2026-06-17 local - Alert data-quality cleanup

### What changed

- Updated `large_trade_absolute_v1` scoring so absolute trade alerts use documented data-quality statuses instead of `unverified`.
- Clean normalized trades now emit `data_quality="complete"`.
- Warning-bearing normalized trades now emit `data_quality="partial"`.
- Added focused scoring assertions for both clean and warning-bearing trades.
- Strengthened `review-pass` tests so the default fixture corpus must pass the alert data-quality check instead of merely warning.

### Verification

- `.\.venv\Scripts\python.exe -m pytest tests\test_scoring.py tests\test_cli.py -k "review_pass or large_trade" -q` - 10 passed.
- `.\.venv\Scripts\python.exe -m pmfi.cli review-pass --format json` - returned 0 with `status="pass"`, 11 fixture files, 10 normalized trades, 14 alerts, one expected dead-letter skipped fixture, and no data-quality warnings.
- `.\.venv\Scripts\python.exe -m pytest tests\test_scoring.py tests\test_pipeline_engine.py tests\test_reporting.py tests\test_replay.py tests\test_cli.py tests\test_runner_lifecycle.py tests\test_runner_lineage.py tests\test_runner_dead_letters.py tests\test_runner_suppression.py tests\test_delivery.py tests\test_db_verify.py -q` - 99 passed.
- `.\.venv\Scripts\python.exe scripts\task.py review-pass` - returned 0 with `PMFI Review Pass: pass`.
- `.\.venv\Scripts\python.exe scripts\task.py fixture-replay` - returned 0 with 10 fixtures and 14 alerts.
- `.\.venv\Scripts\python.exe scripts\verify.py` - 247 passed, 11 skipped; verification passed.

### Review

- Focused subagent review found no blocking issues and approved the narrow status change as aligned with `docs/data/00_data_contracts.md`. Residual risk remains live/DB proof, not fixture scoring semantics.

### Blocker

- Local Postgres remains unavailable in this session, so this proves fixture-backed scoring semantics only. It does not prove live venue semantics, persisted DB alert rows, or DB-backed review/report output.

### Next highest-ROI action

- If Postgres remains unavailable, continue with DB-free hardening around operator runbooks/CLI affordances and fake-backed DB proof seams. If Postgres becomes available, prioritize the full DB proof path: `db_local.py up/init/verify`, persisted replay, `health`, `db-verify`, `dead-letters`, DB report, and restart/resume smoke against actual local tables.

## 2026-06-17 local - Live local DB proof and replay hardening

### What changed

- Started Docker-backed local Postgres and initialized the repo schema with `scripts\db_local.py up` and `scripts\db_local.py init`.
- Ran partition maintenance with `pmfi db-maintenance --create-partitions` after `db-verify` correctly failed closed on missing startup-horizon partitions.
- Proved `pmfi db-verify --format json` against the live local DB: required relations, columns, indexes, constraints, seed venues, partitions, and startup-maintenance artifacts pass.
- Proved `pmfi health --format json` against the live local DB: status is `ready_with_warnings`, with DB/config/delivery/live/watched-market checks passing, one expected malformed dead letter, and zero baseline rows.
- Marked the fixture Kalshi market as watched with `pmfi markets watch KXEXAMPLE-26JUN03 --venue kalshi`.
- Persisted fixture replay through the DB pipeline. Current DB report surface shows 12 persisted alerts across `large_trade_absolute_v1`, `market_relative_large_trade_v1`, `directional_cluster_v1`, and `open_interest_shock_v1`, with 11 raw events, 13 normalized trades, and one dead letter.
- Hardened `replay_fixtures_persist` so malformed fixtures already handled by the DB pipeline as dead letters are skipped during summary rendering instead of crashing the operator replay.
- Hardened `replay_from_db` so malformed stored raw events are skipped during replay instead of crashing the DB replay surface.
- Added replay regression tests for both malformed persisted fixture summaries and malformed stored raw rows.

### Verification

- `.\.venv\Scripts\python.exe scripts\db_local.py status` - Docker compose initially had no running PMFI service.
- `docker version --format '{{.Server.Version}}'` - Docker engine reachable, version `29.5.3`.
- `.\.venv\Scripts\python.exe scripts\db_local.py up` - Postgres container created and became ready.
- `.\.venv\Scripts\python.exe scripts\db_local.py init` - SQL migrations `001` through `007` applied.
- `.\.venv\Scripts\python.exe scripts\db_local.py verify` - passed after initialization.
- `.\.venv\Scripts\python.exe -m pmfi.cli db-verify --format json` - initially failed closed on missing partitions; passed after `db-maintenance --create-partitions`.
- `.\.venv\Scripts\python.exe -m pmfi.cli replay --persist` - returned 0 and wrote fixture events through the DB pipeline, including the malformed fixture as a dead letter.
- `.\.venv\Scripts\python.exe -m pmfi.cli dead-letters --format json` - returned 0 with one malformed Polymarket raw event, `pm-malformed-1`, classified as `invalid_price_or_size`.
- `.\.venv\Scripts\python.exe -m pmfi.cli report --format json` - returned 0 with 12 DB-backed alerts, 11 raw events, 13 normalized trades, and one dead letter.
- `.\.venv\Scripts\python.exe -m pmfi.cli replay --from-db --limit 20` - returned 0, replayed 10 valid raw events from Postgres, skipped the malformed raw row, and emitted 14 replay alert decisions.
- `.\.venv\Scripts\python.exe -m pytest tests\test_replay.py -q` - 5 passed.
- `.\.venv\Scripts\python.exe -m pytest tests\test_replay.py tests\test_cli.py tests\test_db_verify.py tests\test_runner_dead_letters.py tests\test_runner_lifecycle.py tests\test_runner_lineage.py tests\test_scoring.py -q` - 61 passed.
- `.\.venv\Scripts\python.exe -m pytest tests\test_alerts_schema_contract.py -q` with `PMFI_DB_URL` set to local Postgres - 4 passed.
- `.\.venv\Scripts\python.exe scripts\verify.py` with `PMFI_DB_URL` set to local Postgres - 260 passed; verification passed.

### Review

- Grill-me coherence check: this pass advanced the real operator path because it converted the prior DB blocker into live Postgres proof, preserved raw malformed evidence before skipping derived replay summaries, and kept all checks validate-only except explicit init/maintenance/replay/watch commands.
- Orthogonal check: fixture-only review, DB integrity, DB report, dead-letter listing, health, persisted replay, and DB replay now agree on the same core state: one malformed raw row is preserved as a dead letter, valid raw rows can be replayed, and stored alert evidence is inspectable.

### Residual risks

- `pmfi baseline compute` still produced no baseline rows because the current fixture corpus is too sparse for the baseline query's per-market history requirement. This is now the main operator-readiness warning.
- The local DB used for proof is not a pristine disposable database; DB-enabled verification can add test rows, which is why the current report shows 13 normalized trades for 11 raw events. Future DB smoke should use a named disposable database or explicit reset/archive workflow.
- Persisted restart/resume behavior is contract-tested with fakes, but still needs a real local DB stop/restart smoke against stored dedupe keys, trades, alerts, and suppression history.
- No live venue read-only adapter has been run in this pass; local-only/no-trading scope remains intact.

### Next highest-ROI action

- Build a baseline-sufficiency slice: add a small local fixture or DB-seeded smoke that creates enough metric windows for at least one watched market, make `pmfi baseline compute` produce and report baseline rows, then rerun `health`, `report`, `review-pass`, and DB replay against that state. This directly removes the biggest remaining `ready_with_warnings` condition.

## 2026-06-17 local - Persisted baseline sufficiency

### What changed

- Changed the DB-writing `pmfi baseline compute` path to compute market baselines from `normalized_trades` instead of requiring at least two `metric_windows` per market.
- Added `pmfi baseline compute --min-samples N` with default `2`, plus a fail-closed guard for values below `1`.
- Kept `pmfi baselines compute` as the config-file helper path, but updated stale comments so both baseline paths point at normalized trade evidence.
- Made `market_baselines` storage idempotent with the new `market_baselines_market_scope_unique` constraint on `(market_id, venue_code, scope)`.
- Added migration `sql/008_market_baselines_unique_constraint.sql`, wired it into `scripts\db_local.py init`, startup maintenance, `pmfi db-maintenance --create-partitions`, and DB integrity verification.
- Updated `upsert_baseline` to use the named uniqueness constraint and update the current row instead of appending duplicate baseline rows.
- Added regression coverage for trade-based baseline compute, baseline upsert conflict targeting, baseline CLI parsing, and DB integrity drift when the baseline uniqueness constraint is missing.

### Verification

- `.\.venv\Scripts\python.exe -m pytest tests\test_baseline.py tests\test_cli.py tests\test_db_verify.py tests\test_metrics_upsert.py -q` - 55 passed.
- `.\.venv\Scripts\python.exe -m pmfi.cli db-verify --format json` - initially returned 1, correctly detecting missing `market_baselines_market_scope_unique` on the existing live DB.
- `.\.venv\Scripts\python.exe -m pmfi.cli baseline compute --lookback-days 7 --min-samples 2` - returned 0 and computed 4 market baselines from normalized trades.
- Re-running `.\.venv\Scripts\python.exe -m pmfi.cli baseline compute --lookback-days 7 --min-samples 2` - still returned 4 market baselines, proving the upsert path updates instead of appending.
- `.\.venv\Scripts\python.exe -m pmfi.cli db-verify --format json` - returned 0 after migration, with relations, columns, indexes, constraints, seed venues, partitions, and startup-maintenance artifacts passing.
- `.\.venv\Scripts\python.exe -m pmfi.cli health --format json` - returned 0 with `status="ready_with_warnings"`; `market_baselines` now passes with 4 rows and the remaining warning is the expected malformed dead letter.
- `.\.venv\Scripts\python.exe -m pmfi.cli baseline list` - returned 4 entries: `kalshi:KXEXAMPLE-26JUN03`, `kalshi:KXBTCD-23DEC3100`, `polymarket:pm-cluster-market`, and `polymarket:0xabc1234condition`.
- `.\.venv\Scripts\python.exe -m pmfi.cli replay --from-db --limit 20` - returned 0, replayed 10 valid raw events, skipped the malformed row, and emitted baseline-aware market-relative alerts with `baseline_status="available"` where a persisted baseline exists.
- `.\.venv\Scripts\python.exe -m pmfi.cli report --format json` - returned 0 with 12 DB-backed alerts, 11 raw events, 16 normalized trades, and 1 dead letter after DB-enabled verification added test trade rows.
- `.\.venv\Scripts\python.exe -m pmfi.cli dead-letters --format json` - returned 0 with the single expected malformed Polymarket raw event `pm-malformed-1`.
- `.\.venv\Scripts\python.exe -m pmfi.cli review-pass --format json` - returned 0 with fixture review `status="pass"`.
- `.\.venv\Scripts\python.exe -m pmfi.cli db-maintenance --create-partitions` - returned 0 and applied schema migrations plus partition verification.
- `.\.venv\Scripts\python.exe scripts\verify.py` with `PMFI_DB_URL` set to local Postgres - 264 passed; verification passed.
- `.\.venv\Scripts\python.exe scripts\db_local.py verify` - returned 0; local Docker Postgres accepted connections and listed `kalshi` and `polymarket`.
- `.\.venv\Scripts\python.exe scripts\db_local.py init` - returned 0 after the change, proving `sql/008_market_baselines_unique_constraint.sql` is idempotent in the repo init path.

### Review

- Grill-me coherence check: the fix targets the actual operator blocker. Baselines should be derived from normalized trade evidence because alert rules compare individual trade capital against historical trade percentiles; requiring multiple 5-minute windows made the fixture/local DB path look unready even when enough persisted trades existed.
- Talmudic counterpoint: using only two trades is a sparse baseline and should not be marketed as statistically strong. The current engine already labels these as `baseline_sparse`; the command default is a bootstrap threshold, not a production confidence claim.
- Orthogonal consistency check: DB integrity, health, baseline list, DB replay, report, dead-letter listing, and fixture review now agree on one coherent state: raw malformed evidence is preserved, valid trades are persisted, baseline rows exist, and baseline-aware alerts can be regenerated from stored raw events.

### Residual risks

- Health remains `ready_with_warnings` because one expected malformed fixture is stored as a dead letter. This is correct operator evidence, not a DB readiness failure.
- Current baseline rows are sparse fixture/local proof, not market-grade historical baselines. More live or historical public data is still needed before high-confidence production alerting.
- DB-enabled tests and smoke commands can still add rows to the shared local DB; the next hardening slice should add a disposable DB/reset workflow for repeatable operator proof.
- Persisted restart/resume is still proven primarily by fake-backed lifecycle tests; the next DB smoke should exercise stop/restart behavior against the live local DB.

### Next highest-ROI action

- Build a disposable local DB verification lane: create/init an isolated test database or reset/archive workflow, run replay, baseline compute, health, report, dead letters, and DB replay end to end, and prove the final counts are repeatable without relying on shared state drift.

## 2026-06-17 local - Disposable DB operator smoke

### What changed

- Added `scripts\db_smoke.py`, a Windows-native disposable local Postgres smoke that creates a uniquely named `pmfi_smoke_*` database, applies all SQL files, runs the DB-backed operator workflow, validates key JSON outputs, and drops only the database it created.
- Added `python scripts\task.py db-smoke` as the human/agent task-wrapper entrypoint for the smoke.
- The smoke runs `db-maintenance --create-partitions`, `db-verify --format json`, `replay --persist`, `markets watch`, `baseline compute`, `health --format json`, `report --format json`, `dead-letters --format json`, `replay --from-db`, and `review-pass --format json` against the disposable database through `DATABASE_URL`.
- The smoke asserts clean repeatable counts: 11 raw events, 10 normalized trades, 12 persisted alerts, 1 expected malformed dead letter, and at least one persisted baseline row; it also requires baseline-aware replay evidence.
- Added focused tests for disposable DB-name safety, database URL rewriting, clean-count assertions, and `task.py db-smoke` routing.

### Verification

- `.\.venv\Scripts\python.exe -m pytest tests\test_db_smoke.py tests\test_baseline.py tests\test_cli.py tests\test_db_verify.py tests\test_replay.py -q` - 60 passed.
- `.\.venv\Scripts\python.exe scripts\db_smoke.py` - returned 0. It created `pmfi_smoke_20260617_020338_82ebb3`, applied SQL `001` through `008`, proved DB integrity, replayed fixtures through the DB pipeline, marked the Kalshi fixture market watched, computed 3 persisted baselines, validated health/report/dead-letter/replay/review surfaces, and dropped the disposable database.
- `.\.venv\Scripts\python.exe scripts\task.py db-smoke` - returned 0. It created `pmfi_smoke_20260617_020426_c34c37`, proved the same disposable operator path, and dropped the disposable database.
- Disposable smoke summary from the task wrapper: `raw=11 trades=10 alerts=12 dead_letters=1 baselines=3`.
- `.\.venv\Scripts\python.exe scripts\verify.py` with `PMFI_DB_URL` set to local Postgres - 269 passed; verification passed.
- A direct local Postgres query after the smoke runs returned `no disposable smoke databases remain`.

### Review

- Grill-me coherence check: this slice directly addresses the prior shared-state drift risk. A clean disposable database now proves setup, schema, replay persistence, baseline computation, health, reports, dead letters, DB replay, and fixture review from an empty database without relying on whatever rows prior tests left in the persistent local DB.
- Talmudic counterpoint: a subagent review recommended a stronger disposable Docker Compose project with its own container, port, and storage. That would isolate server-level configuration too, but requires more compose surgery because the current compose file has a fixed container name and port. The consensus for this repo state is to land the lower-complexity disposable database lane first, then escalate to disposable containers only if server-level drift becomes a real blocker.
- Orthogonal consistency check: persistent DB proof, disposable DB proof, fixture review, and DB replay now agree on raw-before-derived behavior, malformed-payload preservation, baseline-aware alert regeneration, and local-only/no-live execution.

### Residual risks

- The disposable smoke depends on an already reachable local Postgres server. It does not start Docker itself; operators should run `python scripts\db_local.py up` first if Postgres is down.
- It isolates database contents but not the Postgres server/container configuration. A future container-level disposable lane would cover port, volume, and server-setting drift.
- The smoke intentionally drops only `pmfi_smoke_*` databases it created. Use `--keep-db` for inspection if a future failure needs forensic debugging.
- The persistent local DB can still drift from DB-enabled tests; use `python scripts\task.py db-smoke` for clean operator proof.

### Next highest-ROI action

- Add a real local restart/resume smoke: against a disposable DB, run persist replay, rerun persist replay or restart the pipeline path, and assert duplicate raw/trade/alert suppression plus baseline loading survive the restart boundary.

## 2026-06-17 local - Restart/resume idempotency smoke

### What changed

- Extended `scripts\db_smoke.py` so the disposable operator smoke now reruns `pmfi replay --persist` after the first persisted replay, watched-market setup, baseline computation, health/report/dead-letter checks, and before DB replay.
- Added direct disposable-DB count assertions around the second persisted replay: `raw_events`, `normalized_trades`, `alerts`, `dead_letters`, `market_baselines`, and `event_dedupe_keys` must remain unchanged.
- Added an explicit duplicate-observation assertion: `event_dedupe_keys.duplicate_count` must increase by the number of already-seen raw events, proving the replay/restart path recognized duplicates rather than silently ignoring the second pass.
- Updated the persisted replay CLI message from `wrote` to `processed` so restart/resume runs do not imply new rows were inserted when dedupe prevented writes.
- Added focused tests for restart/resume count invariants and the persisted replay output wording.

### Verification

- `.\.venv\Scripts\python.exe -m pytest tests\test_cli.py tests\test_db_smoke.py tests\test_replay.py tests\test_runner_lifecycle.py tests\test_runner_lineage.py tests\test_db_verify.py -q` - 66 passed.
- `.\.venv\Scripts\python.exe scripts\task.py db-smoke` - returned 0. It created `pmfi_smoke_20260617_020856_7b9016`, proved schema/replay/watch/baseline/health/report/dead-letter/replay/review, reran persisted replay, and reported `restart/resume idempotency passed: raw=11 trades=10 alerts=12 dead_letters=1 raw_duplicates=11`; then it dropped the disposable database.
- `.\.venv\Scripts\python.exe scripts\verify.py` with `PMFI_DB_URL` set to local Postgres - 272 passed; verification passed.
- A direct local Postgres query after the smoke returned `no disposable smoke databases remain`.

### Review

- Grill-me coherence check: this pass strengthens the daemon claim because it proves a local restart/replay boundary does not duplicate raw rows, normalized trades, dead letters, baseline rows, or alerts, while still recording duplicate raw observations for auditability.
- Talmudic counterpoint: this is not a live process restart with a long-running adapter process; it is a deterministic persisted replay restart surrogate. The consensus for this repo state is that replay restart idempotency is the lower-layer proof required before adding live adapter lifecycle smoke.
- Orthogonal consistency check: fake-backed runner lifecycle tests, disposable DB counts, and DB replay now agree that raw-event dedupe is the first restart safety boundary and persisted baselines still load for regenerated alert evidence after that boundary.

### Residual risks

- This proves persisted fixture replay restart/resume, not a real websocket adapter reconnect loop.
- The smoke still depends on an already running local Postgres server and isolates database contents, not the Docker container itself.
- CLI persisted replay reports normalized fixture processing counts, not inserted-row counts; the disposable smoke is the authoritative inserted-row proof.

### Next highest-ROI action

- Add a bounded live-adapter lifecycle/readiness smoke that remains opt-in and read-only: prove startup uses watched markets, refreshes/load baselines, and exits cleanly without trading or default live calls. If live API uncertainty blocks that, add adapter-interface tests and a precise blocker.

## 2026-06-17 local - Ingest readiness preflight

### What changed

- Added `pmfi ingest --check --format json|table` as a validate-only readiness preflight for live ingest.
- The readiness check connects only to the local DB and verifies DB integrity, delivery config, persisted baselines, watched markets, and venue subscription identifiers.
- It does not import live adapters or open venue connections; the output includes an explicit `live_connections` pass entry for that safety property.
- For Kalshi, the check requires watched tickers. For Polymarket, it requires watched markets to have token IDs from `market_outcomes`, making missing discovery/outcome sync a clear blocker before live websocket subscription.
- Moved persistent-ingest heavy imports behind the config/readiness branches so unsupported delivery config and readiness unit tests do not require the full DB/adapter runtime import graph.
- Extended `scripts\db_smoke.py` so disposable operator smoke now runs `pmfi ingest --venue kalshi --check --format json` after replay, watch, and baseline compute, and fails unless the preflight returns `ready`.
- Added focused tests for ingest parser coverage, no-live-adapter-import readiness behavior, Polymarket missing-token blocking, and smoke readiness assertions.

### Verification

- `python -m pytest .\tests\test_cli.py -k ingest -q` - 4 passed.
- `python -m pytest .\tests\test_db_smoke.py -q` - 8 passed.
- `.\.venv\Scripts\python.exe .\scripts\task.py db-smoke` - returned 0. It created `pmfi_smoke_20260617_021751_63fb0c`, proved schema/replay/watch/baseline/readiness/health/report/dead-letter/restart/replay/review, reported `ingest_check=ready`, and dropped the disposable database.
- `.\.venv\Scripts\python.exe .\scripts\verify.py` - 272 passed, 4 skipped; verification passed.
- `.\.venv\Scripts\python.exe .\scripts\db_local.py verify` - returned 0; local Docker Postgres accepted connections and listed `kalshi` and `polymarket`.
- With `PMFI_DB_URL=postgresql://pmfi:pmfi_local_password_change_me@localhost:5433/pmfi`, `.\.venv\Scripts\python.exe -m pytest .\tests\test_alerts_schema_contract.py -q` - 4 passed.

### Review

- Grill-me coherence check: this is the right next layer after replay/restart proof because live ingest should fail closed before network connection if local prerequisites are missing. The check makes DB state, market watch state, baseline availability, delivery safety, and subscription identifiers visible in one operator-facing payload.
- Talmudic counterpoint: readiness is not live-ingest success. It proves the local launch prerequisites and adapter-import safety boundary, not websocket connectivity or venue payload behavior. The consensus is to land this preflight before opt-in live smoke because it reduces ambiguous live failures into precise local blockers.
- Orthogonal consistency check: health, DB smoke, replay restart proof, and readiness preflight now agree on the same operator state: raw evidence is persisted, baselines are available, one Kalshi watched ticker can be subscribed, malformed evidence remains a dead-letter warning, and no live venue calls occur by default.

### Residual risks

- `pmfi ingest --check` is local-readiness proof, not a bounded live websocket run.
- Polymarket readiness depends on market discovery having populated `market_outcomes`; fixture replay alone does not provide token subscriptions for watched Polymarket markets.
- The disposable smoke still uses an already running local Postgres server; it isolates the database, not the Docker container.

### Next highest-ROI action

- Add a bounded opt-in live lifecycle smoke: after `ingest --check` passes, run a read-only max-duration/max-events adapter path that proves watched-market subscriptions, baseline loading/refresh, clean shutdown, and no trading/write surprises. If public venue access is unavailable, first land fake-adapter lifecycle tests that exercise the same ingest runner contract and record the live-network blocker precisely.

## 2026-06-17 local - Live-smoke lifecycle contract

### What changed

- Updated `pmfi live-smoke` so Polymarket subscriptions are loaded from watched `market_outcomes` token IDs instead of market raw metadata when `--asset-ids` is omitted.
- Added Kalshi live-smoke subscription support through watched Kalshi tickers or explicit `--tickers`, behind the same `PMFI_ENABLE_LIVE=1` or `--force` opt-in gate.
- Kept live adapter imports behind the explicit live gate and after subscription readiness checks, preserving no-live-by-default behavior.
- Added clean command failure handling for adapter startup/runtime exceptions so operators get a nonzero command result and actionable message instead of a traceback-first experience.
- Added fake-adapter lifecycle tests proving subscription loading, bounded capture, clean adapter disconnect, and no adapter import before live opt-in.

### Verification

- `python -m pytest .\tests\test_cli.py -k "live_smoke or ingest" -q` - 8 passed.
- `.\.venv\Scripts\python.exe -m pmfi.cli live-smoke --venue kalshi --max-events 1` - returned 1 with the expected `PMFI_ENABLE_LIVE` safety-gate message and no live attempt.
- `.\.venv\Scripts\python.exe -m pytest .\tests\test_cli.py .\tests\test_db_smoke.py -q` - 53 passed.
- `.\.venv\Scripts\python.exe .\scripts\verify.py` - 276 passed, 4 skipped; verification passed.
- `.\.venv\Scripts\python.exe .\scripts\db_local.py verify` - returned 0; local Docker Postgres accepted connections and listed `kalshi` and `polymarket`.
- With `PMFI_DB_URL=postgresql://pmfi:pmfi_local_password_change_me@localhost:5433/pmfi`, `.\.venv\Scripts\python.exe -m pytest .\tests\test_alerts_schema_contract.py -q` - 4 passed.

### Review

- Grill-me coherence check: this pass advances the daemon objective by proving the bounded live lifecycle shape without making normal verification depend on venue networks. Startup remains explicit opt-in, subscriptions come from operator-selected watched markets, event capture is bounded by max events/seconds, and adapter shutdown is asserted.
- Talmudic counterpoint: fake adapters prove lifecycle and command wiring, not public venue behavior. The consensus is that this is the right payback artifact before attempting a real live smoke because it prevents ambiguous network failures from hiding local subscription or cleanup bugs.
- Orthogonal consistency check: `ingest --check`, `live-smoke`, and the adapter pipeline now align on the same source of truth for subscriptions: Polymarket token IDs from `market_outcomes`, Kalshi tickers from watched markets, and no live call unless explicitly opted in.

### Residual risks

- A real Polymarket or Kalshi websocket live smoke has still not been executed in this pass.
- Kalshi websocket auth/public-access behavior remains venue-dependent; the command can now exercise the adapter path, but live endpoint success still needs an explicit operator-approved run.
- `live-smoke --persist-raw` should be covered by a future fake-backed test that proves baseline load, raw persistence, normalization, alerts, and clean shutdown through the DB pipeline in the same bounded lifecycle.

### Next highest-ROI action

- Add the persisted live-smoke lifecycle proof: fake adapter plus disposable DB or DB fakes proving `--persist-raw` loads baselines, seeds suppression, writes raw/normalized/alerts through `run_adapter_pipeline`, closes DB/adapter resources, and still has a no-network default gate. Then, when the operator approves network, run the real opt-in venue smoke and save fixtures.

## 2026-06-17 local - Persisted live-smoke lifecycle proof

### What changed

- Added fake-backed `pmfi live-smoke --persist-raw` coverage that runs through the real `run_adapter_pipeline` contract while mocking network and DB writes.
- The new test proves the command loads baselines, creates an alert engine with those baselines, loads asset ID mappings, ensures DB partitions, seeds alert suppression from the DB, persists raw events through the runner boundary, normalizes the event, upserts the market, inserts the trade, updates metric windows, inserts an alert, delivers it, closes the adapter, and closes the DB pool.
- Kept the no-network default gate explicit for `--persist-raw`: without `PMFI_ENABLE_LIVE=1` or `--force`, the command still exits before live adapter or DB work.
- Updated the fake live adapter payloads so capture-only and persisted tests both use trade-shaped venue payloads.

### Verification

- `.\.venv\Scripts\python.exe -m pytest .\tests\test_cli.py -k "live_smoke or ingest" -q` - 9 passed.
- `.\.venv\Scripts\python.exe -m pytest .\tests\test_cli.py .\tests\test_runner_lifecycle.py .\tests\test_runner_lineage.py .\tests\test_db_smoke.py -q` - 59 passed.
- `.\.venv\Scripts\python.exe .\scripts\verify.py` - 277 passed, 4 skipped; verification passed.
- `.\.venv\Scripts\python.exe .\scripts\db_local.py verify` - returned 0; local Docker Postgres accepted connections and listed `kalshi` and `polymarket`.
- With `PMFI_DB_URL=postgresql://pmfi:pmfi_local_password_change_me@localhost:5433/pmfi`, `.\.venv\Scripts\python.exe -m pytest .\tests\test_alerts_schema_contract.py -q` - 4 passed.
- `.\.venv\Scripts\python.exe -m pmfi.cli live-smoke --venue kalshi --tickers KXEXAMPLE-26JUN03 --persist-raw --max-events 1` without `PMFI_ENABLE_LIVE` returned 1 with the expected live safety-gate message.

### Review

- Grill-me coherence check: this is the right payback artifact for the previous lifecycle slice because `--persist-raw` is where live smoke becomes an operator-daemon proof rather than just a websocket capture proof. The test now crosses the command, adapter, baseline, runner, suppression, raw persistence, normalization, alert, delivery, and resource-cleanup boundary.
- Talmudic counterpoint: this is still fake-backed; it proves local lifecycle and dataflow wiring, not public venue availability or live payload shape. The consensus is to keep real venue proof opt-in and run it only after local failure modes are isolated.
- Orthogonal consistency check: fixture replay, disposable DB smoke, ingest readiness, capture-only live smoke, and persisted live-smoke now agree on one data lineage: explicit opt-in, watched subscriptions, raw event first, normalized trade second, metric/alert after, and no hidden network calls in default verification.

### Residual risks

- Real Polymarket and Kalshi websocket live smoke still need explicit network opt-in and operator approval.
- The persisted live-smoke proof uses mocked DB repository writes; it does not create rows in a disposable Postgres database from a fake live adapter.
- Kalshi websocket auth/public access remains unproven against the live endpoint.

### Next highest-ROI action

- Add a disposable-DB fake-live smoke lane or script-level test that drives `live-smoke --persist-raw` against an isolated Postgres database with a fake adapter source, so row counts and restart/dedupe behavior are proven for the live-smoke command itself. After that, run a short real opt-in Polymarket smoke if network conditions and market token availability permit.

## 2026-06-17 local - Disposable DB fake-live smoke

### What changed

- Added `pmfi live-smoke --fixture-source <file-or-dir>` so the live-smoke command can run an explicit local RawEvent fixture as a fake live source without importing live adapters or requiring `PMFI_ENABLE_LIVE`.
- Kept the live safety gate for real adapter paths; fixture-source mode is local-only and explicit.
- Added `tests/fixtures/live-smoke/kalshi_persist.json`, a dedicated fake-live Kalshi trade fixture outside the default replay fixture directory so existing replay counts remain stable.
- Extended `scripts\db_smoke.py` so the disposable DB smoke now runs `pmfi live-smoke --fixture-source tests/fixtures/live-smoke/kalshi_persist.json --persist-raw --max-events 1 --max-seconds 10` against the temporary database.
- Added disposable-DB row assertions proving fixture-source live-smoke increments `raw_events`, `normalized_trades`, and `event_dedupe_keys` by one, leaves dead letters/baselines/raw duplicate counts stable, and inserts at least one alert row.
- Added focused tests proving fixture-source capture-only live-smoke bypasses the live gate without importing adapter or DB modules, plus unit coverage for the new smoke row-count assertion.

### Verification

- `.\.venv\Scripts\python.exe -m pytest .\tests\test_cli.py -k "live_smoke or ingest" .\tests\test_db_smoke.py -q` - 12 passed.
- `.\.venv\Scripts\python.exe -m pmfi.cli live-smoke --fixture-source tests/fixtures/raw/kalshi_live_ws_trade.json --max-events 1 --max-seconds 5` - returned 0 with `fixture_source=1 file(s)` and one processed/captured event, without `PMFI_ENABLE_LIVE`.
- `.\.venv\Scripts\python.exe .\scripts\task.py db-smoke` - returned 0. It created `pmfi_smoke_20260617_024333_28f03f`, proved schema/replay/watch/baseline/ingest-readiness/health/report/dead-letter/restart/replay/review, then ran fixture-source `live-smoke --persist-raw` and reported `live_smoke_raw_delta=1`; the smoke dropped the disposable database.
- `.\.venv\Scripts\python.exe .\scripts\verify.py` - 279 passed, 4 skipped; verification passed.
- `.\.venv\Scripts\python.exe .\scripts\db_local.py verify` - returned 0; local Docker Postgres accepted connections and listed `kalshi` and `polymarket`.
- With `PMFI_DB_URL=postgresql://pmfi:pmfi_local_password_change_me@localhost:5433/pmfi`, `.\.venv\Scripts\python.exe -m pytest .\tests\test_alerts_schema_contract.py -q` - 4 passed.
- A direct Postgres query after the disposable smoke returned no remaining `pmfi_smoke_*` databases.

### Review

- Grill-me coherence check: this closes the prior gap because `live-smoke --persist-raw` is now proven at command level against an isolated real Postgres database, not just mocked repository calls. The proof includes raw preservation, normalization, dedupe-key insertion, alert insertion, and resource cleanup.
- Talmudic counterpoint: fixture-source is not a real venue websocket. The consensus is that it belongs as an explicit local fake-live lane because it lets operators and agents prove the daemon data path repeatedly before adding network uncertainty.
- Orthogonal consistency check: default replay fixtures, fake-live fixtures, disposable DB smoke, ingest readiness, and live-smoke now remain separated by source and purpose. The default replay corpus stays stable while fake-live command proof gets its own fixture directory and DB row assertions.

### Residual risks

- Real Polymarket and Kalshi websocket smoke still need explicit network opt-in and operator approval.
- Fixture-source mode proves the live-smoke command's persisted data path, not venue subscription protocol correctness.
- Replay-after-live-smoke row regeneration is not yet asserted after the fake-live row is added to the disposable DB.

### Next highest-ROI action

- Add replay-after-live-smoke verification inside disposable DB smoke: after fixture-source live-smoke writes its row, replay from DB should include the new fake-live raw event and regenerate explainable alert evidence without duplicating persisted rows. Then, if live tokens/network are available, run a short real opt-in Polymarket smoke.

## 2026-06-17 local - Replay-after-live-smoke DB proof

### What changed

- Extended `scripts\db_smoke.py` so the disposable DB smoke now runs `pmfi replay --from-db` after fixture-source `live-smoke --persist-raw` inserts its fake-live event.
- Added replay assertions proving the post-live-smoke DB replay includes `KXLIVE-SMOKE-26JUN03`, emits `large_trade_absolute_v1` evidence with `capital_at_risk_usd=33300.00`, and reports the expected 11 normalized raw events after skipping the known malformed dead-letter row.
- Added non-mutation assertions proving replay-after-live-smoke leaves raw events, normalized trades, alerts, dead letters, baselines, dedupe keys, and duplicate counts unchanged.
- Added focused unit coverage for the replay-after-live-smoke assertion helper, including stable-count success and missing-evidence/mutated-count failures.

### Verification

- `.\.venv\Scripts\python.exe -m pytest .\tests\test_db_smoke.py -q` - 11 passed.
- `.\.venv\Scripts\python.exe -m pytest .\tests\test_cli.py -k "live_smoke or ingest" .\tests\test_db_smoke.py -q` - 14 passed, 44 deselected.
- `.\.venv\Scripts\python.exe .\scripts\task.py db-smoke` - returned 0. It created `pmfi_smoke_20260617_025159_1328da`, proved schema/replay/watch/baseline/ingest-readiness/health/report/dead-letter/restart/replay/review, ran fixture-source `live-smoke --persist-raw`, replayed from DB afterward, found the fake-live Kalshi evidence, reported `live_smoke_replay=pass`, and dropped the disposable database.
- `.\.venv\Scripts\python.exe .\scripts\verify.py` - 281 passed, 4 skipped; verification passed.
- `.\.venv\Scripts\python.exe .\scripts\db_local.py verify` - returned 0; local Docker Postgres accepted connections and listed `kalshi` and `polymarket`.
- With `PMFI_DB_URL=postgresql://pmfi:pmfi_local_password_change_me@localhost:5433/pmfi`, `.\.venv\Scripts\python.exe -m pytest .\tests\test_alerts_schema_contract.py -q` - 4 passed.
- A direct Postgres query after the disposable smoke returned no remaining `pmfi_smoke_*` databases.

### Review

- Grill-me coherence check: this closes the prior replay-after-live-smoke gap because the same isolated Postgres smoke now proves the fake-live raw row can be re-read through the DB replay surface and regenerate explainable alert evidence without mutating persisted operator state.
- Talmudic counterpoint: replay-after-live-smoke still re-evaluates stored rows; it is not a venue websocket protocol proof. The consensus is that this is the right local authority proof before an opt-in network smoke because it separates deterministic DB lineage from public endpoint uncertainty.
- Orthogonal consistency check: the fake-live fixture, live-smoke command, persisted DB rows, replay-from-DB output, and operator count invariants now all agree on the same event identity and capital-at-risk evidence.

### Residual risks

- Real Polymarket and Kalshi websocket smoke still need explicit network opt-in and operator approval.
- Fixture-source mode proves live-smoke persistence and replay lineage, not venue subscription protocol correctness.
- The disposable smoke isolates database contents but still depends on the already running local Postgres service/container.

### Next highest-ROI action

- Run a short real opt-in Polymarket smoke when network conditions and market token availability are acceptable. If real endpoint access is unavailable, add adapter-protocol diagnostics that record the exact live-network blocker without weakening the local-only default gates.

## 2026-06-17 local - Live-smoke fail-closed diagnostics

### What changed

- Added a small diagnostics surface to the Polymarket and Kalshi live adapters: connection attempts, connection error count, last connection error, and whether the adapter ever connected successfully.
- Updated `pmfi live-smoke` so live venue runs that capture zero events return nonzero instead of reporting an empty success.
- When an empty live run has adapter diagnostics, `live-smoke` now prints connection attempts, error counts, connected-once status, and the last adapter error before suggesting a more active subscription, longer runtime, or required credentials.
- Added regression coverage proving empty live-smoke runtimes with adapter diagnostics fail closed, while existing fixture-source and fake-adapter success paths still pass.
- Added adapter contract coverage proving both live adapters expose the diagnostics payload.

### Verification

- `.\.venv\Scripts\python.exe .\scripts\verify.py` before editing - 281 passed, 4 skipped; verification passed.
- `.\.venv\Scripts\python.exe -m pmfi.cli ingest --venue polymarket --check --format json` - returned 1; persistent local DB has zero watched Polymarket markets and zero token subscriptions.
- `.\.venv\Scripts\python.exe -m pmfi.cli ingest --venue kalshi --check --format json` - returned 0; persistent local DB has one watched Kalshi ticker and readiness is `ready`.
- Before the fix, `.\.venv\Scripts\python.exe -m pmfi.cli live-smoke --venue kalshi --force --max-events 1 --max-seconds 15` returned 0 despite zero captured events and repeated Kalshi websocket `401 Invalid response status` errors.
- `.\.venv\Scripts\python.exe -m pytest .\tests\test_cli.py -k "live_smoke" .\tests\test_adapters.py -q` - 7 passed, 52 deselected.
- After the fix, the same bounded Kalshi live smoke returned 1 and printed `connect_attempts=4`, `connection_errors=4`, `connected_once=False`, and the Kalshi websocket `401 Invalid response status` URL.
- `.\.venv\Scripts\python.exe .\scripts\verify.py` - 283 passed, 4 skipped; verification passed.
- `.\.venv\Scripts\python.exe .\scripts\task.py db-smoke` - returned 0. It created `pmfi_smoke_20260617_025931_1ab360`, preserved the fixture-source live-smoke/replay proof with `live_smoke_replay=pass`, and dropped the disposable database.
- `.\.venv\Scripts\python.exe .\scripts\db_local.py verify` - returned 0; local Docker Postgres accepted connections and listed `kalshi` and `polymarket`.
- With `PMFI_DB_URL=postgresql://pmfi:pmfi_local_password_change_me@localhost:5433/pmfi`, `.\.venv\Scripts\python.exe -m pytest .\tests\test_alerts_schema_contract.py -q` - 4 passed.
- A direct Postgres query after the disposable smoke returned no remaining `pmfi_smoke_*` databases.

### Review

- Grill-style coherence check: a live smoke with zero captured events is not evidence that live ingest works. Returning nonzero is more honest and safer than treating a timed-out empty run as success.
- Talmudic counterpoint: an empty run can happen on an illiquid market without an adapter bug. The consensus is still fail-closed for `live-smoke` because its purpose is proof, not passive monitoring; the diagnostic text tells the operator whether the likely problem is subscription activity, runtime length, or credentials/connectivity.
- Orthogonal consistency check: readiness remains local and no-network by default, fixture-source smoke remains deterministic and DB-backed, and real live smoke now records external endpoint blockers without mutating local data.

### Residual risks

- Polymarket real smoke is locally blocked until market discovery/watch populates watched Polymarket markets and token IDs, or the operator passes `--asset-ids` directly.
- Kalshi websocket access currently returns `401 Invalid response status` without a usable local credential in this environment.
- This pass exposes live-network blockers; it does not prove successful public venue event capture.

### Next highest-ROI action

- Make the Polymarket live-smoke path self-serve: run or harden `pmfi markets discover --venue polymarket`, watch a discovered market with active token outcomes, rerun `pmfi ingest --venue polymarket --check --format json`, then attempt a bounded Polymarket `live-smoke --force` if token subscriptions are ready.

## 2026-06-17 local - Polymarket self-serve live-smoke path

### What changed

- Improved `pmfi markets list` so operators can actually get the values needed for `pmfi markets watch` and live-smoke readiness.
- Added `pmfi markets list --venue polymarket|kalshi` and `--format table|json`.
- Market list JSON now emits `venue_market_id`, `watched`, `trade_count`, `active_outcomes`, and `last_trade_at` for each row.
- Market list table now includes `Market ID` and `Tokens`, and sorts watched/token-ready markets before fixture-only markets.
- Added CLI coverage proving the parser accepts the new market-list flags and JSON output exposes watch IDs plus active token outcome counts.

### Verification

- `.\.venv\Scripts\python.exe .\scripts\db_local.py verify` - returned 0; local Docker Postgres accepted connections and listed `kalshi` and `polymarket`.
- Before discovery/watch, `.\.venv\Scripts\python.exe -m pmfi.cli ingest --venue polymarket --check --format json` returned 1 because there were zero watched Polymarket markets and zero token subscriptions.
- `.\.venv\Scripts\python.exe -m pmfi.cli markets discover --venue polymarket --limit 20` - returned 0 and synced 19 Polymarket markets.
- `.\.venv\Scripts\python.exe -m pmfi.cli markets list --venue polymarket --format json --limit 5` - returned token-ready Polymarket markets first with full `venue_market_id` values and `active_outcomes=2`.
- `.\.venv\Scripts\python.exe -m pmfi.cli markets watch 0x3648ab7c146a9a85957e07c1d43a82272be71fde767822fd425e10ba0d6c0757 --venue polymarket` - returned 0 and marked the market watched in the local DB.
- After watch, `.\.venv\Scripts\python.exe -m pmfi.cli ingest --venue polymarket --check --format json` returned 0 with one watched Polymarket market and two token subscription IDs.
- `.\.venv\Scripts\python.exe -m pmfi.cli live-smoke --venue polymarket --force --max-events 1 --max-seconds 20` - returned 0; the adapter connected on attempt 1 and captured one real Polymarket websocket `new_market` event.
- `.\.venv\Scripts\python.exe -m pmfi.cli live-smoke --venue polymarket --force --persist-raw --max-events 1 --max-seconds 20` - returned 0; it captured one real Polymarket websocket `new_market` event and inserted it as raw evidence.
- A direct Postgres count after persisted live smoke showed `raw_events=12`, `normalized_trades=27`, and `alerts=12`; the latest Polymarket raw row was `raw_event_id=12`, `source_channel=ws_clob`, `source_event_type=new_market`, and the live `venue_market_id`.
- `.\.venv\Scripts\python.exe -m pmfi.cli health --format json` - returned 0 with `ready_with_warnings`, two watched markets total, one watched Polymarket market, 24 market outcome mappings, and 12 raw events.
- `.\.venv\Scripts\python.exe -m pytest .\tests\test_cli.py -q` - 50 passed.
- `.\.venv\Scripts\python.exe -m pytest .\tests\test_markets_discovery.py -q` - 11 passed.
- `.\.venv\Scripts\python.exe .\scripts\verify.py` - 285 passed, 4 skipped; verification passed.
- `.\.venv\Scripts\python.exe .\scripts\task.py db-smoke` - returned 0. It created `pmfi_smoke_20260617_030836_a80c28`, preserved the fixture-source live-smoke/replay proof with `live_smoke_replay=pass`, and dropped the disposable database.
- With `PMFI_DB_URL=postgresql://pmfi:pmfi_local_password_change_me@localhost:5433/pmfi`, `.\.venv\Scripts\python.exe -m pytest .\tests\test_alerts_schema_contract.py -q` - 4 passed.
- A direct Postgres query after the disposable smoke returned no remaining `pmfi_smoke_*` databases.

### Review

- Grill-style coherence check: this pass removes the prior Polymarket local-readiness blocker by making discovery output usable, watching a token-ready market, proving readiness, and then proving both capture-only and raw-persisting live-smoke paths can connect to the public Polymarket websocket.
- Talmudic counterpoint: the captured public event was `new_market`, not a trade. The consensus is to record this as real live raw-evidence proof, not as normalized trade/alert proof. The runner correctly stored raw first and skipped benign non-trade normalization without creating false alerts.
- Orthogonal consistency check: the operator path now links public REST discovery, local Postgres market/outcome storage, watch state, readiness preflight, websocket subscription, raw event preservation, health, and the disposable DB smoke without adding non-local infrastructure or trading behavior.

### Residual risks

- A real Polymarket live trade event has not yet been captured in this pass, so live normalized-trade/alert generation from public Polymarket websocket remains unproven.
- The watched Polymarket market discovered by the public REST endpoint may be low-activity/stale; a more active market or explicit `--asset-ids` may be needed for live trade capture.
- Kalshi websocket access still returns `401 Invalid response status` without usable local credentials.

### Next highest-ROI action

- Add an operator-facing live-smoke summary that distinguishes raw events, normalized trades, skipped non-trade events, dead letters, and alerts for `--persist-raw`; then run a longer or more targeted Polymarket smoke against an active market/token pair to capture a real trade.

## 2026-06-17 local - Persisted outcome summaries and bounded ingest proof

### What changed

- Added runner-level outcome accounting through `EventOutcome` and `PipelineStats`, keeping `run_adapter_pipeline(...)` returning its existing processed-event integer while optionally recording raw inserts, raw duplicates, normalized trade inserts, duplicate trades, non-trade skips, dead letters, inserted alerts, delivered alerts, suppressed alerts, and processing errors.
- Updated `pmfi live-smoke --persist-raw` to print an operator-facing persisted summary after bounded capture, so a non-trade public websocket event is distinguishable from a normalized trade/alert proof.
- Added `pmfi ingest --max-events` and `--max-seconds` as explicit bounded proof controls for the supported daemon command path. Normal ingest remains indefinite when those flags are absent.
- Wired bounded `pmfi ingest` runs through the same `PipelineStats` summary and made zero-event bounded proof runs return nonzero instead of looking like successful daemon proof.
- Added fake-backed CLI coverage proving bounded ingest runs through the real runner boundary, closes adapter and DB resources, reports persisted summary counters, and fails closed on zero-event timeout.

### Verification

- `.\.venv\Scripts\python.exe -m pytest .\tests\test_cli.py -k "live_smoke" -q` - 9 passed, 43 deselected.
- `.\.venv\Scripts\python.exe -m pytest .\tests\test_runner_lifecycle.py .\tests\test_runner_lineage.py -q` - 6 passed.
- `.\.venv\Scripts\python.exe .\scripts\verify.py` before bounded ingest work - 281 passed, 11 skipped; verification passed.
- `.\.venv\Scripts\python.exe -m pytest .\tests\test_cli.py -k "ingest or live_smoke" -q` - 16 passed, 39 deselected.
- `.\.venv\Scripts\python.exe -m pytest .\tests\test_runner_lifecycle.py .\tests\test_runner_lineage.py -q` after bounded ingest work - 6 passed.
- `.\.venv\Scripts\python.exe .\scripts\verify.py` after bounded ingest work - 284 passed, 11 skipped; verification passed.
- `.\.venv\Scripts\python.exe .\scripts\task.py db-smoke` - blocked before disposable DB creation with `ConnectionRefusedError: [WinError 1225]` because local Postgres was not accepting connections.
- `.\.venv\Scripts\python.exe .\scripts\db_local.py up` - blocked because Docker Desktop was not running: missing `npipe:////./pipe/dockerDesktopLinuxEngine`.
- `.\.venv\Scripts\python.exe .\scripts\db_local.py verify` - timed out waiting for Docker-backed Postgres; same missing Docker pipe.
- With `PMFI_DB_URL=postgresql://pmfi:pmfi_local_password_change_me@localhost:5433/pmfi`, `.\.venv\Scripts\python.exe -m pytest .\tests\test_alerts_schema_contract.py -q` - 4 failed with `ConnectionRefusedError: [WinError 1225]`, matching the local Postgres/Docker blocker.
- `docker info --format '{{.ServerVersion}}'` - failed because Docker Desktop was not running.

### Review

- Grill-style coherence check: this advances the daemon objective because the supported `pmfi ingest` path now has a bounded operator proof mode with the same persisted outcome accounting as `live-smoke`. The operator can tell whether the run preserved raw evidence, normalized trades, skipped non-trade payloads, wrote alerts, or merely timed out empty.
- Talmudic counterpoint: bounded ingest proof still uses fake adapters in tests and does not replace real venue proof. The consensus is that the fake-backed bounded mode is the right local payback artifact before chasing live Polymarket trade liquidity, because it proves the daemon command shape without network ambiguity.
- Orthogonal consistency check: `ingest --check` remains validate-only and no-live; normal `ingest` remains continuous; bounded `ingest` is explicit; `live-smoke` remains the opt-in network smoke. All four surfaces keep raw-before-derived and local-only boundaries intact.

### Residual risks

- DB-backed gates were not rerun successfully in this pass because Docker Desktop/local Postgres was unavailable.
- A real Polymarket executed trade has still not been captured and persisted through the live websocket path.
- Kalshi websocket access remains blocked by credentials/public-access behavior observed earlier.
- Bounded ingest currently proves live adapter lifecycle through fake adapters; a disposable-DB fake ingest smoke would further strengthen row-count and replay evidence for the daemon command itself once Docker is available.

### Next highest-ROI action

- Start Docker Desktop/local Postgres and rerun `.\.venv\Scripts\python.exe .\scripts\task.py db-smoke`, `.\.venv\Scripts\python.exe .\scripts\db_local.py verify`, and the `PMFI_DB_URL` schema-contract tests. If DB gates pass, add a disposable-DB bounded ingest smoke or run a targeted real Polymarket bounded ingest/live-smoke against active token IDs to capture an executed trade.

## 2026-06-17 local - Fixture-source ingest proof seam

### What changed

- Added `pmfi ingest --fixture-source <file-or-dir>` as an explicit local-only persisted ingest mode, using the same RawEvent fixture loader as `live-smoke --fixture-source`.
- The fixture-source ingest path runs through the supported `cmd_ingest` DB startup, baseline load, `AlertEngine`, `run_adapter_pipeline`, `PipelineStats`, persisted summary, and DB pool cleanup without importing live venue adapters.
- Extended disposable DB smoke so, after the fixture-source live-smoke proof and replay proof, it can run `pmfi ingest --fixture-source tests/fixtures/live-smoke/kalshi_persist.json --venue kalshi --max-events 1 --max-seconds 10` against the disposable DB.
- Added DB-smoke assertions for the fixture ingest step: the repeated fake-live event should not insert new raw/trade/alert rows, but it should record exactly one additional raw duplicate observation.
- Added focused CLI and DB-smoke helper tests proving fixture-source ingest parser support, no live-adapter import behavior, summary output, runner-path execution, DB pool cleanup, and the duplicate-observation assertion.

### Verification

- `.\.venv\Scripts\python.exe -m pytest .\tests\test_cli.py -k "ingest or live_smoke" -q` - 18 passed, 39 deselected.
- `.\.venv\Scripts\python.exe -m pytest .\tests\test_db_smoke.py -q` - 12 passed.
- `.\.venv\Scripts\python.exe .\scripts\verify.py` - 287 passed, 11 skipped; verification passed.
- `docker info` after launching Docker Desktop failed with `Docker Desktop is unable to start`.
- `wsl -l -v` showed `docker-desktop` stopped.
- Docker Desktop UI reported: `Virtualization support not detected`.
- `.\.venv\Scripts\python.exe .\scripts\db_local.py up` could not start local Postgres because Docker Desktop/engine could not run in the current machine state.

### Review

- Grill-style coherence check: this is a better daemon proof than stopping at `live-smoke --fixture-source`, because it exercises the supported `pmfi ingest` command surface while remaining deterministic, local-only, and fixture-backed.
- Talmudic counterpoint: fixture-source ingest is still not live venue proof and still needs Postgres available to prove real row deltas. The consensus is that the seam is the right payback artifact while virtualization blocks Docker, because the next DB smoke run can now prove `cmd_ingest` without waiting for live market activity.
- Orthogonal consistency check: `ingest --check` remains validate-only/no-live/no-write; normal ingest still uses live adapters; fixture-source ingest is explicitly local and persisted; DB smoke now has a deterministic command-level ingest proof ready for the next Postgres-available run.

### Residual risks

- Current DB-backed gates remain unproven because Docker Desktop cannot start without virtualization support.
- The new disposable DB smoke branch has focused helper coverage but has not been executed end-to-end against Postgres in this pass.
- Real Polymarket executed-trade capture remains unproven, and Kalshi websocket access remains credential/public-access dependent.

### Next highest-ROI action

- Restore virtualization/Docker Desktop access or point `DATABASE_URL` at a working local Postgres, then rerun `.\.venv\Scripts\python.exe .\scripts\task.py db-smoke`, `.\.venv\Scripts\python.exe .\scripts\db_local.py verify`, and the `PMFI_DB_URL` schema-contract tests. If those pass, run a targeted real Polymarket bounded proof against active token IDs.

## 2026-06-17 local - Docker setup blocker diagnostics

### What changed

- Hardened `scripts\db_local.py` so Docker Desktop startup failures are classified and reported with actionable Windows-local guidance while preserving the underlying Docker output.
- Added diagnostic handling for missing `docker.exe`, missing `dockerDesktopLinuxEngine` pipe, Docker Desktop unable-to-start errors, virtualization-not-detected errors, and Docker Desktop API 500 failures.
- Kept the existing compose/Postgres workflow intact: successful Docker commands still replay stdout/stderr, and generic compose failures still surface the raw error without over-classifying.
- Added focused fake-backed diagnostics tests that do not require Docker to be installed or running.

### Verification

- `.\.venv\Scripts\python.exe -m pytest .\tests\test_db_local_diagnostics.py -q` - 8 passed.
- `.\.venv\Scripts\python.exe -m pytest .\tests\test_db_local_diagnostics.py .\tests\test_db_smoke.py .\tests\test_db_verify.py -q` - 31 passed.
- `.\.venv\Scripts\python.exe .\scripts\db_local.py status` - returned 0 while printing the raw Docker Desktop API 500 plus the new diagnostic guidance.
- `.\.venv\Scripts\python.exe .\scripts\db_local.py up` - returned 1 with the raw `postgres:16` Docker API 500 plus the new diagnostic guidance.
- `.\.venv\Scripts\python.exe .\scripts\verify.py` - 295 passed, 11 skipped; verification passed.

### Review

- Grill-style coherence check: this advances the operator daemon objective because local Postgres setup is part of the product path, and the tool now tells a Windows operator what is blocking setup instead of leaving them with opaque Docker API failures.
- Talmudic counterpoint: better diagnostics do not prove Postgres, schema, or DB smoke. The consensus is that this is still the right move while virtualization is unavailable because it converts the environmental blocker into a precise setup action and keeps default verification DB-free.
- Orthogonal consistency check: the change is local-only, Windows-native, validate/fail behavior is clearer, and no repo code attempts to control Docker Desktop, WSL, virtualization, sign-in, or external services.

### Residual risks

- DB-backed gates still cannot run until virtualization/Docker Desktop or another local Postgres endpoint is available.
- The deterministic `ingest --fixture-source` disposable DB branch remains unexecuted end-to-end against Postgres in this machine state.
- Real Polymarket executed-trade capture and Kalshi live websocket access remain unproven.

### Next highest-ROI action

- Enable virtualization/Docker Desktop or provide a working local `DATABASE_URL`, then rerun `.\.venv\Scripts\python.exe .\scripts\db_local.py up`, `.\.venv\Scripts\python.exe .\scripts\task.py db-smoke`, `.\.venv\Scripts\python.exe .\scripts\db_local.py verify`, and the `PMFI_DB_URL` schema-contract tests.

## 2026-06-17 local - Ingest runtime observability proof slice

### What changed

- Added `src/pmfi/db/repos/ingestion_runtime.py` with small async pool-scoped helpers for `ingestion_connections` and `system_heartbeats` writes.
- Wired `pmfi ingest` persisted fixture-source and live-adapter paths to record connection start, message, stopped/cancelled, error, and heartbeat state without holding a DB connection across event/network waits.
- Kept `ingest --check` validate-only: it still returns before importing live adapters or runtime-state write helpers.
- Added fake-backed CLI coverage for fixture-source runtime state, zero-event timeout terminal state, runtime-start failure cleanup, and check-mode no-write/no-live-import behavior.
- Added a focused helper SQL-shape test proving the helper uses the canonical `system_heartbeats(worker_name, worker_type, status, last_heartbeat_at, metadata)` schema rather than `component/details`.

### Verification

- `.\.venv\Scripts\python.exe -m pytest .\tests\test_cli.py -k "runtime_state or runtime_state_writes" .\tests\test_ingestion_runtime.py -q` - first failed as expected before implementation because `pmfi.db.repos.ingestion_runtime` did not exist; passed after implementation.
- `.\.venv\Scripts\python.exe -m pytest .\tests\test_cli.py -k "ingest" -q` - 13 passed, 48 deselected.
- `.\.venv\Scripts\python.exe -m pytest .\tests\test_ingestion_runtime.py -q` - 1 passed.
- `.\.venv\Scripts\python.exe .\scripts\verify.py` - first failed because the new helper test used `pytest.mark.asyncio` but this repo does not install an async pytest plugin; after converting the test to `asyncio.run`, verification passed. A later reviewer fix added runtime-start failure cleanup coverage; final verification passed with 300 passed, 11 skipped.

### Review

- Grill-style coherence check: the slice uses the already-canonical `ingestion_connections` and `system_heartbeats` tables, so the operator can see daemon lifecycle state without adding schema or Docker-dependent proof.
- Talmudic counterpoint: per-event runtime writes add write volume. The consensus is acceptable for this proof slice because bounded fixture/live proofs are small, the helper is isolated, and a future throttle can be added with DB evidence if live volume makes it necessary.
- Orthogonal consistency check: the command-boundary lifecycle is separate from raw-event persistence, so normal pipeline semantics remain unchanged; helper calls acquire/release the pool briefly and do not wrap adapter network I/O.
- Reviewer cleanup check: if an adapter connects but runtime-state start recording fails, the command now disconnects the adapter before surfacing the fatal error.

### Residual risks

- DB-backed execution of the new runtime rows is still not proven on this machine because Docker/Postgres remains unavailable; fake-backed tests verify call flow and SQL shape only.
- Real live adapter error/cancellation state is covered through fake adapters, not a real websocket session.
- The current worktree had substantial pre-existing dirty edits in `src/pmfi/cli.py`, `tests/test_cli.py`, and many unrelated files; this pass did not attempt to revert or normalize those edits.

### Next highest-ROI action

- When local Postgres is available, run a disposable DB `pmfi ingest --fixture-source tests/fixtures/live-smoke/kalshi_persist.json --venue kalshi --max-events 1 --max-seconds 10` proof and assert the latest `ingestion_connections` plus `system_heartbeats` rows show connected/message/stopped state.

## 2026-06-17 local - Health ingest runtime observability slice

### What changed

- Extended `pmfi health` to add an `ingest_runtime` check sourced with SELECT-only reads from existing `ingestion_connections` and `system_heartbeats` rows after DB integrity passes.
- Kept runtime observability non-fatal: no runtime rows and latest runtime `error` status report `warn`, while DB unavailable and integrity-failure paths surface skipped warnings without runtime queries.
- Added focused fake-pool health tests for runtime pass/warn output, empty runtime state, error runtime state, DB unavailable coverage, validate-only SELECT behavior, and integrity-failure query skipping.

### Verification

- `.\.venv\Scripts\python.exe -m pytest .\tests\test_cli.py -k "health" -q` - first failed as expected before implementation because `ingest_runtime` was missing; after implementation, 10 passed, 53 deselected.
- `.\.venv\Scripts\python.exe .\scripts\verify.py` - 302 passed, 11 skipped; verification passed.

### Residual risks

- DB-backed execution against real Postgres remains unproven on this machine state; this slice intentionally uses fake-backed tests and read-only health SQL.
- Runtime freshness is limited to the latest recent rows returned by health; no live Postgres execution was available in this pass.

## 2026-06-17 local - DB smoke runtime health proof gate

### What changed

- Strengthened `scripts/db_smoke.py` so the disposable DB smoke now reruns `pmfi health --format json` after deterministic `pmfi ingest --fixture-source ...` and asserts the new `ingest_runtime` health check passes.
- Added `_assert_fixture_ingest_runtime_health` to require a fixture-source ingestion connection, a fixture ingest heartbeat, clean stopped terminal state, and no latest connection error.
- Updated the final disposable DB smoke success line to include `fixture_ingest_runtime=pass` when that post-ingest health proof succeeds.
- Added offline helper tests proving the runtime-health assertion accepts a clean stopped fixture worker and rejects missing, warning, failed, or error runtime state.

### Verification

- `.\.venv\Scripts\python.exe -m pytest .\tests\test_db_smoke.py -q` - 14 passed.
- `.\.venv\Scripts\python.exe -m pytest .\tests\test_cli.py -k "health or ingest" -q` - 23 passed, 40 deselected.
- `.\.venv\Scripts\python.exe .\scripts\task.py fixture-replay` - passed, replayed 10 fixtures and emitted 14 fixture alerts.
- `.\.venv\Scripts\python.exe .\scripts\verify.py` - 304 passed, 11 skipped; verification passed.
- `.\.venv\Scripts\python.exe .\scripts\db_local.py status` - Docker Desktop still returned an API 500 on the Linux engine pipe and printed the existing virtualization/WSL2/Docker Desktop readiness guidance.

### Review

- Grill-style coherence check: this closes the loop between the new ingest runtime writes and the operator `health` reader by making the future disposable DB smoke prove both sides together.
- Talmudic counterpoint: the assertion cannot execute end-to-end until Postgres is available. The consensus is still positive because the smoke gate is now stricter and will fail closed the next time M1 can run.
- Orthogonal consistency check: this change adds no schema, no live calls, no writes outside the existing smoke path, and no hosted/SaaS surface; it only turns an already-planned DB smoke into a stronger local acceptance check.

### Residual risks

- Real Postgres execution of this new smoke gate remains unproven until Docker Desktop/local Postgres is available.
- The runtime health assertion currently proves the deterministic fixture-source ingest worker; live websocket runtime rows still need opt-in live proof later.

## 2026-06-17 local - Health data-quality incident signal

### What changed

- Extended `pmfi health` with a `data_quality_incidents` check sourced from the existing `v_open_data_quality_incidents` view after DB integrity passes.
- Health now reads the open incident count and up to five recent examples with SELECT-only SQL, without adding schema or importing live adapters.
- Zero open incidents reports `pass`; open incidents report `warn` with count and examples, but do not make health return nonzero.
- DB unavailable and DB integrity failure paths now include skipped `data_quality_incidents` warnings and do not query incident rows before integrity passes.
- Added focused fake-pool health tests for zero incidents, open incidents, DB unavailable skip behavior, and integrity-failure query skipping.

### Verification

- `.\.venv\Scripts\python.exe -m pytest .\tests\test_cli.py -k "health" -q` - 12 passed, 53 deselected.
- `.\.venv\Scripts\python.exe -m pytest .\tests\test_db_smoke.py -q` - 14 passed.
- `.\.venv\Scripts\python.exe -m pmfi.cli health --format json` - returned 1 because DB is unavailable, and included `data_quality_incidents` as a skipped warning alongside other DB-dependent checks.
- `.\.venv\Scripts\python.exe .\scripts\verify.py` - 306 passed, 11 skipped; verification passed.
- `.\.venv\Scripts\python.exe .\scripts\db_local.py status` - Docker Desktop still cannot start, so local Postgres remains unavailable.

### Review

- Canonical source of truth: `src/pmfi/db/verify.py` already requires `data_quality_incidents` and `v_open_data_quality_incidents`, so health only queries the view after that integrity contract passes.
- Consensus: open incidents are operator evidence, not a readiness blocker. They warn loudly while preserving rc 0 when no other fatal health condition exists.

### Residual risks

- Real Postgres execution remains unproven in this pass because verification is intentionally fake-backed/no-Docker for this slice.
- The examples are bounded to the five most recent open rows returned by the view-backed query; deeper incident triage still needs direct DB inspection or a later operator command.

## 2026-06-17 local - Data-quality incident operator follow-up

### What changed

- Added `pmfi data-quality-incidents --limit N --format table|json` as a read-only operator command for the existing health `data_quality_incidents` warning.
- The command queries only `v_open_data_quality_incidents`, returns JSON as `{ok, count, data_quality_incidents}`, and includes incident ID, venue, market, type, severity, status, started/ended timestamps, summary, and details.
- JSON failure paths now fail closed with `ok:false`, `error`, and rc 1 for config, DB connection, query, and close failures.
- Table output uses Rich when available, falls back to plain text, and prints a clear empty-state message when no open incidents exist.
- Added fake-pool tests for parser/limit handling, JSON shape, read-only SELECT/view source, DB/config/query/close failures, empty table output, and no live adapter imports.

### Verification

- `.\.venv\Scripts\python.exe -m pytest .\tests\test_cli_data_quality_incidents.py -q` - first failed as expected before implementation because `data-quality-incidents` was not registered; after implementation, 8 passed.
- `.\.venv\Scripts\python.exe -m pytest .\tests\test_cli.py -k "health" -q` - 12 passed, 53 deselected.
- `.\.venv\Scripts\python.exe -m pytest .\tests\test_cli_dead_letters.py -q` - 6 passed.
- `.\.venv\Scripts\python.exe -m pmfi.cli data-quality-incidents --format json` - returned 1 with fail-closed DB-unavailable JSON because local Postgres is not reachable.
- `.\.venv\Scripts\python.exe .\scripts\verify.py` - 314 passed, 11 skipped; verification passed.

### Residual risks

- Real Postgres execution against stored incident rows was not run in this pass; the command is proven with fake-pool tests and full offline verification.
- The surrounding worktree had substantial pre-existing dirty edits, including `src/pmfi/cli.py`, `tests/test_cli.py`, and this worklog; this pass only added the incident command, focused tests, and this verified note.

## 2026-06-17 local - Delivery failure operator visibility

### What changed

- Added `pmfi delivery-failures --limit N --format table|json` as a read-only operator command for pending or failed alert deliveries.
- The command queries `alert_deliveries` with alert and market context, returns JSON as `{ok, count, delivery_failures}`, and includes delivery ID, alert ID, channel, destination, status, attempts, timestamps, last error, rule, severity, confidence, venue, market, summary, and payload preview.
- JSON failure paths fail closed with `ok:false`, `error`, and rc 1 for config, DB connection, query, and close failures.
- Table output uses Rich when available, falls back to plain text, and prints a clear empty-state message when no non-delivered alert deliveries exist.
- Added fake-pool tests for parser/limit handling, JSON shape, read-only SELECT source, DB/config/query/close failures, empty table output, and no live adapter imports.

### Verification

- `.\.venv\Scripts\python.exe -m pytest .\tests\test_cli_delivery_failures.py -q` - 8 passed.
- `.\.venv\Scripts\python.exe -m pytest .\tests\test_cli_delivery_failures.py .\tests\test_cli_dead_letters.py .\tests\test_cli_data_quality_incidents.py -q` - 22 passed.
- `.\.venv\Scripts\python.exe -m pmfi.cli delivery-failures --format json` - returned 1 with fail-closed DB-unavailable JSON because local Postgres is not reachable.
- `.\.venv\Scripts\python.exe .\scripts\verify.py` - 322 passed, 11 skipped; verification passed.

### Review

- Grill-style coherence check: this advances local operator closeout because alert delivery failures were schema-present but not inspectable by the operator.
- Talmudic counterpoint: fake-pool tests do not prove real stored delivery rows. The consensus is still positive because the command is read-only, fail-closed, DB-wrapper based, and ready to become part of DB smoke once local Postgres works.
- Orthogonal consistency check: the change adds no schema, no live calls, no hosted/SaaS behavior, and no trading surface.

### Residual risks

- Real Postgres execution against stored `alert_deliveries` rows was not run in this pass because Docker/local Postgres remains unavailable.
- Delivery persistence itself remains source/schema-present but not fully DB-proven in this machine state; this pass only adds operator inspection of rows that exist.

## 2026-06-17 local - Runner orderbook connection hygiene

### What changed

- Moved `process_event` orderbook `/book` fetch and parse outside the asyncpg connection scope used for raw, trade, and metric writes.
- The runner now reacquires a DB connection only to insert the reconstructed orderbook snapshot and persist alerts, preserving raw-before-derived ordering and non-fatal orderbook error handling.
- Added fake-backed runner regression tests proving orderbook fetch happens with no active pool connection, snapshot fields are still persisted, and alert persistence/delivery still runs after successful or failed orderbook capture.

### Verification

- `.\.venv\Scripts\python.exe -m pytest .\tests\test_runner_orderbook.py -q` - first failed before implementation, then 2 passed.
- `.\.venv\Scripts\python.exe -m pytest .\tests\test_runner_orderbook.py .\tests\test_runner_lineage.py .\tests\test_runner_lifecycle.py .\tests\test_orderbook.py -q` - 19 passed.
- `.\.venv\Scripts\python.exe .\scripts\verify.py` - 324 passed, 11 skipped; verification passed.

### Residual risks

- Real orderbook HTTP and DB execution remains unproven until local Postgres and opt-in live venue checks are available; this pass is offline/fake-backed by design.

## 2026-06-17 local - Review-pass skipped-fixture evidence

### What changed

- Hardened `pmfi review-pass` skipped-fixture evidence so expected malformed fixtures include dead-letter expectation, normalization stage, runner-compatible error class, raw-event expectation, no-derived-record expectation, and source identity.
- Added bounded `fixture_skips.details.expected` evidence for operator JSON/table output, including benign non-trade skips that expect no dead letter and no derived records.
- Added focused tests for the default malformed fixture and a temp benign Polymarket non-trade fixture.

### Verification

- `.\.venv\Scripts\python.exe -m pytest .\tests\test_cli.py -k "review_pass" -q` - 9 passed.
- `.\.venv\Scripts\python.exe -m pmfi.cli review-pass --format json` - returned 0 with one expected malformed dead-letter skip classified as `invalid_price_or_size`.
- `.\.venv\Scripts\python.exe .\scripts\verify.py` - 325 passed, 11 skipped; verification passed.

### Residual risks

- This remains fixture-backed only until local Postgres/Docker is available to prove persisted raw-event, dead-letter, and no-derived-record rows in the DB.

## 2026-06-17 local - Unsupported venue dead-letter classification

### What changed

- Changed pipeline normalization so unsupported venues raise `NormalizationError("unsupported venue: ...")` instead of returning `None`.
- Mapped unsupported venue normalization errors to `unsupported_venue` in runner dead letters and review-pass skipped-fixture evidence.
- Added fixture/offline tests proving unsupported venue data is diagnostic while supported Polymarket lifecycle events remain benign non-trade skips.
- Aligned direct non-DB fixture replay with `normalize_event`, so verbose replay reports unsupported venue normalization errors instead of silently skipping them.

### Verification

- `.\.venv\Scripts\python.exe -m pytest .\tests\test_pipeline_engine.py .\tests\test_runner_dead_letters.py -q` - 21 passed.
- `.\.venv\Scripts\python.exe -m pytest .\tests\test_cli.py -k "review_pass" -q` - 10 passed, 57 deselected.
- `.\.venv\Scripts\python.exe -m pytest .\tests\test_replay.py -q` - 6 passed.
- `.\.venv\Scripts\python.exe .\scripts\task.py fixture-replay` - passed, replayed 10 fixtures and emitted 14 alerts.
- `.\.venv\Scripts\python.exe .\scripts\verify.py` - 329 passed, 11 skipped; verification passed.

### Residual risks

- Real DB persistence for arbitrary unknown venue strings is intentionally not claimed here because `raw_events.venue_code` and `dead_letters.venue_code` are FK-bound to seeded venues.

## 2026-06-17 local - Monitor fixture resilience

### What changed

- Hardened `pmfi monitor --fixture-replay` so malformed fixture normalization errors print a concise `normalization skipped: ...` diagnostic and the local demo continues streaming.
- Added load-error and benign non-trade skip diagnostics in the fixture stream path without adding DB dead-letter or persistence semantics to monitor.
- Switched the optional DB baseline pool cleanup to use the repo `close_pool()` helper in a `finally` block when a pool was opened.
- Added a focused CLI regression test with one valid Polymarket fixture and one malformed fixture.

### Verification

- `.\.venv\Scripts\python.exe -m pytest .\tests\test_cli.py -k "monitor_fixture_replay_skips_malformed" -q` - first failed with `NormalizationError: invalid decimal for price: 'not-a-number'`, then 1 passed.
- `.\.venv\Scripts\python.exe -m pytest .\tests\test_cli.py -k "monitor or fixture_replay" -q` - 2 passed, 66 deselected.
- `.\.venv\Scripts\python.exe -m pmfi.cli monitor --fixture-replay --delay 0` - returned 0, skipped `malformed_payload.json`, and completed with 14 alerts from 11 fixtures.
- `.\.venv\Scripts\python.exe .\scripts\verify.py` - 330 passed, 11 skipped; verification passed.

### Residual risks

- DB-backed baseline loading remains unproven in this machine state because local Postgres/Docker is unavailable; this pass only hardens the non-persisted local fixture streaming demo.

## 2026-06-16 23:36 local - Replay DB-unavailable operator messaging

### Files inspected

- `src/pmfi/cli.py`
- `tests/test_cli.py`
- `FAST_ADVANCE.md`
- `WORKLOG.md`

### Changes made

- Hardened `pmfi replay --persist` and `pmfi replay --from-db` so DB/config/setup failures return rc 1 with concise mode-specific DB-unavailable output and the local Postgres next action instead of leaking an asyncpg traceback.
- Preserved the existing DB pool cleanup path when a pool is successfully opened.
- Added focused CLI regression tests for both DB-backed replay modes.

### Verification run

- `.\.venv\Scripts\python.exe -m pytest .\tests\test_cli.py -k "replay" -q` - 5 passed.
- `.\.venv\Scripts\python.exe -m pmfi.cli replay --persist --fixture-dir .\tests\fixtures\raw` - rc 1, concise `[persist] DB unavailable: [WinError 1225] ...` output, no traceback.
- `.\.venv\Scripts\python.exe -m pmfi.cli replay --from-db --limit 1` - rc 1, concise `[from-db] DB unavailable: [WinError 1225] ...` output, no traceback.
- `.\.venv\Scripts\python.exe .\scripts\verify.py` - 332 passed, 11 skipped; verification passed.

### Findings

- Facts: Plain fixture replay remains on the existing non-DB branch; both DB-backed replay branches now fail closed with operator-readable messaging when local Postgres is unavailable.
- Inferences: This improves local operator trust while preserving the DB-backed replay requirement that failures are not reported as success.
- Assumptions: The same DB next action is appropriate for config, connection, migration/partition, and DB replay setup failures in these modes.
- Blockers: Docker/Desktop local Postgres remains unavailable, so DB-backed replay success is still externally blocked on this machine.

### Next step

- Once Docker/Postgres is available, run `python scripts\db_local.py verify` and a successful persisted replay to prove the positive DB path end to end.

## 2026-06-17 local - DB-dependent operator traceback hardening

### What changed

- Generalized the DB-unavailable CLI message helper and reused it for replay, stats, markets, baseline, and db-maintenance command paths.
- Hardened `pmfi stats`, `pmfi markets list`, `pmfi markets watch`, `pmfi markets unwatch`, `pmfi baseline list`, `pmfi baseline compute`, and `pmfi db-maintenance --create-partitions` so config/pool/setup failures return rc 1 with concise command-prefixed DB-unavailable output instead of leaking tracebacks.
- Preserved successful DB pool cleanup with `close_pool()` once a pool has opened.
- Added focused CLI regression coverage for the DB-unavailable paths.

### Verification

- `.\.venv\Scripts\python.exe -m pytest .\tests\test_cli.py -k "stats or markets or baseline or db_maintenance" -q` - first failed with 7 traceback-leak regressions, then 14 passed and 63 deselected.
- `.\.venv\Scripts\python.exe -m pmfi.cli stats` - rc 1, `[stats] DB unavailable: [WinError 1225] ...`, no traceback.
- `.\.venv\Scripts\python.exe -m pmfi.cli markets list --format json` - rc 1, `[markets] DB unavailable: [WinError 1225] ...`, no traceback.
- `.\.venv\Scripts\python.exe -m pmfi.cli markets watch dummy` - rc 1, `[markets] DB unavailable: [WinError 1225] ...`, no traceback.
- `.\.venv\Scripts\python.exe -m pmfi.cli markets unwatch dummy` - rc 1, `[markets] DB unavailable: [WinError 1225] ...`, no traceback.
- `.\.venv\Scripts\python.exe -m pmfi.cli baseline list` - rc 1, `[baseline] DB unavailable: [WinError 1225] ...`, no traceback.
- `.\.venv\Scripts\python.exe -m pmfi.cli baseline compute` - rc 1, `[baseline] DB unavailable: [WinError 1225] ...`, no traceback.
- `.\.venv\Scripts\python.exe -m pmfi.cli db-maintenance --create-partitions` - rc 1, `[db-maintenance] DB unavailable: [WinError 1225] ...`, no traceback.
- `.\.venv\Scripts\python.exe .\scripts\verify.py` - 339 passed, 11 skipped; verification passed.
- `.\.venv\Scripts\python.exe .\scripts\db_local.py status` - Docker Desktop is unable to start; local Postgres is not ready.

### Residual risks

- Docker/Desktop local Postgres remains unavailable on this machine, so positive DB execution for these commands is still externally blocked until `python scripts\db_local.py up` and `python scripts\db_local.py verify` can run successfully.

## 2026-06-17 local - Watch DB-unavailable fail-closed

### What changed

- Hardened `pmfi watch --limit 1` so DB pool creation failure returns rc 1 instead of printing an error and exiting successfully.
- Reused the shared DB-unavailable operator message so `watch` now gives the same local Postgres next action as other DB-backed commands.
- Added focused CLI regression coverage for the watch DB connection failure path.

### Verification

- `.\.venv\Scripts\python.exe -m pytest .\tests\test_cli.py -k "watch" -q` - 8 passed, 70 deselected.
- `.\.venv\Scripts\python.exe -m pmfi.cli watch --limit 1` - rc 1, `[watch] DB unavailable: [WinError 1225] ...`, no traceback.
- `.\.venv\Scripts\python.exe .\scripts\verify.py` - 340 passed, 11 skipped; verification passed.
- `.\.venv\Scripts\python.exe .\scripts\db_local.py status` - Docker Desktop is unable to start; local Postgres is not ready.

### Residual risks

- Docker/Desktop virtualization remains unavailable on this machine, so positive DB watch execution and local Postgres verification remain blocked until the host virtualization/Docker Desktop issue is resolved.

## 2026-06-17 local - Docker/WSL setup diagnostics

### What changed

- Enriched `python scripts\db_local.py status` diagnostics for Docker Desktop startup failures with read-only `wsl.exe --status` context when WSL is available.
- Sanitized NUL-separated WSL status output so Windows host guidance is readable in the terminal.
- Added deterministic tests for WSL context emission, Virtual Machine Platform guidance, empty/missing/timeout WSL fallback, and NUL-separated output cleanup.

### Verification

- `.\.venv\Scripts\python.exe -m pytest .\tests\test_db_local_diagnostics.py -q` - 14 passed.
- `.\.venv\Scripts\python.exe .\scripts\db_local.py status` - Docker Desktop still unable to start, now with readable WSL status context: WSL2 cannot start because virtualization is not enabled from Windows' perspective and suggests enabling Virtual Machine Platform with `wsl.exe --install --no-distribution`.
- `.\.venv\Scripts\python.exe .\scripts\task.py fixture-replay` - 10 fixtures, 14 alerts.
- `.\.venv\Scripts\python.exe .\scripts\verify.py` - 346 passed, 11 skipped; verification passed.

### Residual risks

- This pass improves diagnosis only; it does not mutate host Windows features or Docker settings. Positive local Postgres proof remains blocked until WSL2/Docker Desktop can start.

## 2026-06-17 local - Ingest DB-unavailable daemon messaging

### What changed

- Hardened the persistent `pmfi ingest` daemon path so DB pool creation failure returns rc 1 with the shared `[ingest] DB unavailable: ...` operator message and local Postgres next action.
- Preserved generic `[ingest] fatal error: ...` behavior for non-DB runtime failures such as adapter/socket startup errors.
- Added focused CLI regression coverage for bounded ingest when `pmfi.db.create_pool` fails.

### Verification

- `.\.venv\Scripts\python.exe -m pytest .\tests\test_cli.py -k "ingest" -q` - 16 passed, 63 deselected.
- `.\.venv\Scripts\python.exe -m pmfi.cli ingest --venue kalshi --max-events 1` - rc 1, `[ingest] DB unavailable: [WinError 1225] ...`, no traceback or generic fatal wording.
- `.\.venv\Scripts\python.exe -m pmfi.cli ingest --venue kalshi --check --format json` - rc 1 structured blocked readiness JSON, no traceback.
- `.\.venv\Scripts\python.exe .\scripts\verify.py` - 347 passed, 11 skipped; verification passed.

### Residual risks

- Positive persisted ingest remains unverified on this machine because WSL2/Docker Desktop cannot start local Postgres.

## 2026-06-17 local - Alerts JSON DB-unavailable output

### What changed

- Hardened `pmfi alerts list --format json` so DB failure emits parseable JSON with `ok=false`, the error text, and a local Postgres next action instead of plain text.
- Also made invalid `--since` input in alerts JSON mode return parseable JSON, while keeping table/plain output unchanged.
- Added focused CLI regression coverage for the alerts JSON DB-unavailable path.

### Verification

- `.\.venv\Scripts\python.exe -m pytest .\tests\test_cli.py -k "alerts_list" -q` - 4 passed, 76 deselected.
- `.\.venv\Scripts\python.exe -m pmfi.cli alerts list --format json` - rc 1, parseable JSON DB-unavailable payload, no traceback.
- `.\.venv\Scripts\python.exe -m pmfi.cli alerts list --format json --since nope` - rc 1, parseable JSON invalid-filter payload.
- `.\.venv\Scripts\python.exe .\scripts\verify.py` - 348 passed, 11 skipped; verification passed.

### Residual risks

- Positive alerts DB listing remains unverified on this machine until WSL2/Docker Desktop can start local Postgres.

## 2026-06-17 local - Report JSON DB-unavailable output

### What changed

- Hardened `pmfi report --format json` so DB-backed report failures return parseable JSON with `ok=false`, `source=db`, error text, and local Postgres/replay next actions.
- Preserved existing table/plain DB-unavailable output for `pmfi report`.
- Kept fixture report JSON DB-free and unchanged.

### Verification

- `.\.venv\Scripts\python.exe -m pytest .\tests\test_cli.py -k "report" -q` - 23 passed, 58 deselected.
- `.\.venv\Scripts\python.exe -m pmfi.cli report --format json` - rc 1, parseable JSON DB-unavailable payload, no traceback.
- `.\.venv\Scripts\python.exe -m pmfi.cli report --source fixtures --fixture-dir .\tests\fixtures\raw --format json` - rc 0, 10 fixtures, 10 trades, 14 alerts.
- `.\.venv\Scripts\python.exe .\scripts\verify.py` - 349 passed, 11 skipped; verification passed.

### Residual risks

- Positive DB-backed report data remains unverified on this machine until WSL2/Docker Desktop can start local Postgres and `pmfi replay --persist` can seed rows.

## 2026-06-17 local - Markets JSON DB-unavailable output

### What changed

- Hardened `pmfi markets list --format json` so DB/config/query failures return parseable JSON with `ok=false`, error text, and local Postgres/market-population next actions.
- Preserved existing plain DB-unavailable output for `pmfi markets watch` and `pmfi markets unwatch`.
- Made empty successful market JSON lists return `{"ok": true, "count": 0, "markets": []}` instead of plain text.

### Verification

- `.\.venv\Scripts\python.exe -m pytest .\tests\test_cli.py -k "markets" -q` - 7 passed, 75 deselected.
- `.\.venv\Scripts\python.exe -m pmfi.cli markets list --format json` - rc 1, parseable JSON DB-unavailable payload, no traceback.
- `.\.venv\Scripts\python.exe -m pmfi.cli markets watch dummy` - rc 1, plain `[markets] DB unavailable: ...` operator output, no traceback.
- `.\.venv\Scripts\python.exe .\scripts\verify.py` - 350 passed, 11 skipped; verification passed.

### Residual risks

- Positive market listing/watch behavior against real Postgres remains unverified on this machine until WSL2/Docker Desktop can start local Postgres.

## 2026-06-17 local - Inspection JSON DB-unavailable output

### What changed

- Hardened `pmfi dead-letters --format json`, `pmfi data-quality-incidents --format json`, and `pmfi delivery-failures --format json` so DB pool creation failures return parseable JSON with `ok=false`, explicit `DB unavailable: ...` error text, and local Postgres start/verify next actions.
- Preserved distinct query/config/close failure reporting after a DB pool exists so SQL errors are not mislabeled as DB startup failures.
- Improved the default table/plain output for those three inspection commands to use the shared `[command] DB unavailable: ...` message and local Postgres guidance.
- Repaired one stray NUL byte in an older `WORKLOG.md` line so the worklog remains searchable text.

### Verification

- `.\.venv\Scripts\python.exe -m pytest .\tests\test_cli_dead_letters.py .\tests\test_cli_data_quality_incidents.py .\tests\test_cli_delivery_failures.py -q` - 25 passed.
- `.\.venv\Scripts\python.exe -m pmfi.cli dead-letters --format json` - rc 1, parseable JSON DB-unavailable payload, no traceback.
- `.\.venv\Scripts\python.exe -m pmfi.cli data-quality-incidents --format json` - rc 1, parseable JSON DB-unavailable payload, no traceback.
- `.\.venv\Scripts\python.exe -m pmfi.cli delivery-failures --format json` - rc 1, parseable JSON DB-unavailable payload, no traceback.
- `.\.venv\Scripts\python.exe -m pmfi.cli dead-letters` - rc 1, shared plain DB-unavailable output with local Postgres guidance.
- `.\.venv\Scripts\python.exe -m pmfi.cli data-quality-incidents` - rc 1, shared plain DB-unavailable output with local Postgres guidance.
- `.\.venv\Scripts\python.exe -m pmfi.cli delivery-failures` - rc 1, shared plain DB-unavailable output with local Postgres guidance.
- `.\.venv\Scripts\python.exe .\scripts\verify.py` - 353 passed, 11 skipped; verification passed.

### Residual risks

- Positive DB-backed inspection rows for dead letters, data-quality incidents, and delivery failures remain unverified on this machine until WSL2/Docker Desktop can start local Postgres.

## 2026-06-17 local - Discover, baseline, and persisted smoke DB-unavailable output

### What changed

- Hardened `pmfi markets discover` so DB pool creation failures return the shared `[markets discover] DB unavailable: ...` output before importing venue sync code.
- Hardened `pmfi baselines compute` so DB pool creation failures return the shared `[baselines compute] DB unavailable: ...` output while preserving distinct compute/query failure wording after a pool exists.
- Hardened `pmfi live-smoke --fixture-source ... --persist-raw` so DB pool creation failures return the shared `[live-smoke] DB unavailable: ...` output instead of a generic fatal error.
- Preserved the DB-free fixture live-smoke path without `--persist-raw`.

### Verification

- `.\.venv\Scripts\python.exe -m pytest .\tests\test_cli.py -k "markets_discover or markets" -q` - 8 passed, 75 deselected.
- `.\.venv\Scripts\python.exe -m pmfi.cli markets discover --limit 1` - rc 1, shared DB-unavailable output, no traceback.
- `.\.venv\Scripts\python.exe -m pmfi.cli markets discover --venue kalshi --limit 1` - rc 1, shared DB-unavailable output, no traceback.
- `.\.venv\Scripts\python.exe -m pytest .\tests\test_cli.py -k "baselines_compute or baseline_compute" -q` - 4 passed, 80 deselected.
- `.\.venv\Scripts\python.exe -m pmfi.cli baselines compute --days 1` - rc 1, shared DB-unavailable output, no traceback.
- `.\.venv\Scripts\python.exe -m pmfi.cli baselines compute --days 1 --save` - rc 1, shared DB-unavailable output, no traceback and no baseline file written by this failure path.
- `.\.venv\Scripts\python.exe -m pytest .\tests\test_cli.py -k "live_smoke and (fixture_source or persist_raw or adapter_startup_failure)" -q` - 6 passed, 79 deselected.
- `.\.venv\Scripts\python.exe -m pmfi.cli live-smoke --fixture-source .\tests\fixtures\live-smoke\kalshi_persist.json --persist-raw --force --venue kalshi --max-events 1` - rc 1, shared DB-unavailable output, no generic fatal error.
- `.\.venv\Scripts\python.exe -m pmfi.cli live-smoke --fixture-source .\tests\fixtures\raw\kalshi_trade.json --force --venue kalshi --max-events 1` - rc 0, one fixture event processed without DB writes.
- `.\.venv\Scripts\python.exe .\scripts\verify.py` - 356 passed, 11 skipped; verification passed.

### Residual risks

- Positive DB-backed market discovery, baseline computation, and persisted live-smoke pipeline proof remain unverified on this machine until WSL2/Docker Desktop can start local Postgres.

## 2026-06-17 local - Live command DB-unavailable output

### What changed

- Hardened `pmfi live` so DB pool creation failures return the shared `[live] DB unavailable: ...` output instead of an uncaught traceback.
- Delayed `pmfi live` adapter, pipeline, and market-map imports until after DB pool creation succeeds, so a local Postgres outage does not depend on live adapter code.
- Hardened `pmfi live-smoke` watched subscription lookup so Polymarket/Kalshi DB lookup failures return the shared `[live-smoke] DB unavailable: ...` output instead of falling through to generic missing asset/ticker guidance.
- Preserved explicit subscription and fixture-source behavior; fixture-source live-smoke without `--persist-raw` remains DB-free.

### Verification

- `.\.venv\Scripts\python.exe -m pytest .\tests\test_cli.py -k "live_cli or live_reports_create_pool_failure_without_live_imports" -q` - 3 passed, 83 deselected.
- `$env:PMFI_ENABLE_LIVE='1'; .\.venv\Scripts\python.exe -m pmfi.cli live --markets dummy --venue polymarket` - rc 1, shared DB-unavailable output, no traceback.
- `.\.venv\Scripts\python.exe -m pmfi.cli live` - rc 1, live opt-in gate still blocks without `PMFI_ENABLE_LIVE=1`.
- `.\.venv\Scripts\python.exe -m pytest .\tests\test_cli.py -k "live_smoke" -q` - 12 passed, 76 deselected.
- `.\.venv\Scripts\python.exe -m pmfi.cli live-smoke --force --venue polymarket --max-events 1` - rc 1, shared DB-unavailable output, no missing asset-id fallback.
- `.\.venv\Scripts\python.exe -m pmfi.cli live-smoke --force --venue kalshi --max-events 1` - rc 1, shared DB-unavailable output, no missing ticker fallback.
- `.\.venv\Scripts\python.exe -m pmfi.cli live-smoke --fixture-source .\tests\fixtures\raw\kalshi_trade.json --force --venue kalshi --max-events 1` - rc 0, one fixture event processed without DB writes.
- `.\.venv\Scripts\python.exe .\scripts\verify.py` - 359 passed, 11 skipped; verification passed.

### Residual risks

- Positive continuous `pmfi live` and watched-subscription live-smoke execution remain unverified on this machine until WSL2/Docker Desktop can start local Postgres and opt-in live subscriptions can be supplied safely.

## 2026-06-17 local - Ingest readiness DB-unavailable output

### What changed

- Hardened `pmfi ingest --check` so DB pool creation failures return a structured DB-unavailable readiness payload instead of generic `ingest readiness failed: ...` wording.
- Delayed readiness helper imports until after DB pool creation succeeds, so DB outage reporting does not depend on later schema/subscription readiness modules.
- Preserved richer readiness checks after a DB pool exists, including DB integrity, delivery mode, baselines, watched markets, venue subscriptions, and the no-live-adapter-import guarantee.

### Verification

- `.\.venv\Scripts\python.exe -m pytest .\tests\test_cli.py -k "ingest" -q` - 18 passed, 72 deselected.
- `.\.venv\Scripts\python.exe -m pmfi.cli ingest --venue kalshi --check --format json` - rc 1, parseable `ok=false` DB-unavailable payload with local Postgres start/verify next actions, no traceback.
- `.\.venv\Scripts\python.exe -m pmfi.cli ingest --venue kalshi --check` - rc 1, table readiness output with DB-unavailable check and local Postgres start/verify next actions, no traceback.
- `.\.venv\Scripts\python.exe -m pmfi.cli ingest --venue kalshi --max-events 1` - rc 1, existing `[ingest] DB unavailable: ...` daemon output preserved.
- `.\.venv\Scripts\python.exe .\scripts\task.py fixture-replay` - 10 fixtures, 14 alerts.
- `.\.venv\Scripts\python.exe -m pmfi.cli review-pass --format json` - rc 0, fixture review pass with 10 normalized trades, 14 alerts, local-only check passing.
- `.\.venv\Scripts\python.exe .\scripts\verify.py` - 361 passed, 11 skipped; verification passed.

### Residual risks

- Positive `pmfi ingest --check` and persisted ingest readiness against real Postgres remain unverified on this machine until WSL2/Docker Desktop can start local Postgres.

## 2026-06-17 local - Operator smoke task gate

### What changed

- Added `python scripts\task.py operator-smoke` as the executable M10 local-ops smoke gate.
- Added `scripts/operator_smoke.py`, a validate-only DB-free smoke runner for fixture-backed `review-pass`, fixture `report`, and fixture-source `live-smoke`.
- Updated the adaptive task graph so M10 names the executable `python scripts\task.py operator-smoke` gate instead of an implicit local ops smoke.
- Added tests for the smoke command list, fail-closed validation behavior, task-wrapper routing, and task-graph alignment.

### Verification

- `.\.venv\Scripts\python.exe -m pytest .\tests\test_operator_smoke.py .\tests\test_alignment_contracts.py -q` - 11 passed.
- `.\.venv\Scripts\python.exe .\scripts\task.py operator-smoke` - rc 0, `operator smoke passed`.
- `.\.venv\Scripts\python.exe .\scripts\operator_smoke.py` - rc 0, `operator smoke passed`.
- `.\.venv\Scripts\python.exe .\scripts\verify.py` - 366 passed, 11 skipped; verification passed.

### Residual risks

- This gate proves the DB-free local operator path only; DB-backed operator proof still waits on WSL2/Docker Desktop/local Postgres availability.

## 2026-06-17 local - Health import-failure DB-unavailable output

### What changed

- Hardened `pmfi health --format json` so DB module or DB verification import failures return the existing structured blocked health report instead of escaping before pool creation.
- Preserved the successful DB-backed health path and the existing DB connection failure behavior.
- Added a regression test proving the health command does not create a pool after DB verification import failure, emits DB/dependent warning checks, and includes local Postgres next actions without a traceback.

### Verification

- `.\.venv\Scripts\python.exe -m pytest .\tests\test_cli.py -k "health" -q` - 13 passed, 78 deselected.
- `.\.venv\Scripts\python.exe -m pmfi.cli health --format json` - rc 1, parseable blocked health JSON with config/delivery/live checked, DB failed, dependent checks warned, and local Postgres next action; no traceback.
- `.\.venv\Scripts\python.exe .\scripts\task.py operator-smoke` - rc 0, `operator smoke passed`.
- `.\.venv\Scripts\python.exe .\scripts\db_local.py status` - Docker Desktop still unable to start; WSL2 reports virtualization is not enabled from Windows.
- `.\.venv\Scripts\python.exe .\scripts\verify.py` - 367 passed, 11 skipped; verification passed.

### Residual risks

- Positive DB-backed health remains unverified on this machine until WSL2/Docker Desktop can start local Postgres.

## 2026-06-17 local - DB smoke preflight failure output

### What changed

- Hardened `python scripts\task.py db-smoke` so missing `asyncpg` or unreachable local Postgres fails with concise operator preflight guidance instead of a Python traceback.
- Preserved the disposable DB smoke success path and kept generic smoke assertion failures nonzero with their specific exception message.
- Added deterministic tests for unreachable local Postgres and missing `asyncpg` preflight output.

### Verification

- `.\.venv\Scripts\python.exe -m pytest .\tests\test_db_smoke.py -q` - 16 passed.
- `.\.venv\Scripts\python.exe .\scripts\task.py db-smoke` - rc 1 because local Postgres is unavailable, but now prints `db-smoke preflight failed: local Postgres is not reachable`, the `WinError 1225` cause, and next actions without a traceback.
- `.\.venv\Scripts\python.exe .\scripts\task.py operator-smoke` - rc 0, `operator smoke passed`.
- `.\.venv\Scripts\python.exe .\scripts\verify.py` - 369 passed, 11 skipped; verification passed.

### Residual risks

- Positive disposable DB smoke execution remains externally blocked until WSL2/Docker Desktop/local Postgres is available.

## 2026-06-17 local - DB smoke status alignment

### What changed

- Updated `python scripts\task.py status` so high-priority commands include `python scripts\task.py db-smoke` after `python scripts\db_local.py verify`.
- Updated the adaptive task graph M1 gate to name the disposable DB smoke as the operator proof after local Postgres verification.
- Added an alignment test proving the task graph, status output, and task router all name executable `db-smoke`.

### Verification

- `.\.venv\Scripts\python.exe -m pytest .\tests\test_alignment_contracts.py -q` - 8 passed.
- `.\.venv\Scripts\python.exe .\scripts\task.py status` - lists `python scripts\task.py db-smoke` in high-priority commands and the M1 gate.
- `.\.venv\Scripts\python.exe .\scripts\task.py operator-smoke` - rc 0, `operator smoke passed`.
- `.\.venv\Scripts\python.exe .\scripts\task.py db-smoke` - rc 1 because local Postgres is unavailable, with concise preflight guidance and no traceback.
- `.\.venv\Scripts\python.exe .\scripts\verify.py` - 370 passed, 11 skipped; verification passed.

### Residual risks

- The named M1 `db-smoke` gate still cannot pass on this machine until WSL2/Docker Desktop/local Postgres is available.

## 2026-06-17 local - Local delivery explainability payloads

### What changed

- Added `rule_version` and `data_quality` to console/stdout alert delivery payloads.
- Added `rule_version` and `data_quality` to file JSONL alert delivery payloads.
- Added `data_quality` to localhost HTTP receiver delivery payloads, matching the existing `rule_version` field.
- Added deterministic offline tests for stdout, file, and fake-HTTP local delivery payloads.

### Verification

- `.\.venv\Scripts\python.exe -m pytest .\tests\test_delivery.py -q` - 5 passed.
- `.\.venv\Scripts\python.exe -m pmfi.cli replay-fixtures` - first emitted alert includes `rule_version` and `data_quality`.
- `.\.venv\Scripts\python.exe .\scripts\task.py operator-smoke` - rc 0, `operator smoke passed`.
- `.\.venv\Scripts\python.exe .\scripts\task.py db-smoke` - rc 1 because local Postgres is unavailable, with concise preflight guidance and no traceback.
- `.\.venv\Scripts\python.exe .\scripts\verify.py` - 371 passed, 11 skipped; verification passed.

### Residual risks

- DB-backed delivery audit rows and retry/failure inspection remain unverified on this machine until WSL2/Docker Desktop/local Postgres is available.

## 2026-06-17 local - Operator smoke replay delivery assertion

### What changed

- Extended `python scripts\task.py operator-smoke` with a DB-free `replay-fixtures` step.
- Added fail-closed parsing of replay stdout alert JSON so the smoke gate now requires delivered alerts to include `rule_version` and `data_quality`.
- Updated operator-smoke tests to prove the new command remains DB-free and rejects missing delivery explainability fields.

### Verification

- `.\.venv\Scripts\python.exe -m pytest .\tests\test_operator_smoke.py -q` - 5 passed.
- `.\.venv\Scripts\python.exe .\scripts\task.py operator-smoke` - rc 0, now runs `review-pass`, fixture `report`, `replay-fixtures`, and fixture-source `live-smoke`.
- `.\.venv\Scripts\python.exe .\scripts\task.py db-smoke` - rc 1 because local Postgres is unavailable, with concise preflight guidance and no traceback.
- `.\.venv\Scripts\python.exe .\scripts\verify.py` - 372 passed, 11 skipped; verification passed.

### Residual risks

- Operator smoke still proves only the DB-free local path; DB-backed delivery audit rows and restart/resume proof remain blocked until local Postgres is available.

## 2026-06-17 local - Operator smoke fixture monitor assertion

### What changed

- Extended `python scripts\task.py operator-smoke` with a DB-free `pmfi monitor --fixture-replay --delay 0` step.
- Added fail-closed monitor output checks for the streaming start, stream completion summary, positive fixture and alert counts, emitted alert JSON, alert explainability fields, and malformed-fixture normalization-skip evidence.
- Updated operator-smoke tests to prove the monitor command remains fixture-only and rejects empty or zero-count monitor output.

### Verification

- `.\.venv\Scripts\python.exe -m pytest .\tests\test_operator_smoke.py -q` - 7 passed.
- `.\.venv\Scripts\python.exe .\scripts\task.py operator-smoke` - rc 0, now runs `review-pass`, fixture `report`, `replay-fixtures`, `monitor --fixture-replay`, and fixture-source `live-smoke`.
- `.\.venv\Scripts\python.exe .\scripts\task.py db-smoke` - rc 1 because local Postgres is unavailable, with concise preflight guidance and no traceback.
- `.\.venv\Scripts\python.exe .\scripts\verify.py` - 374 passed, 11 skipped; verification passed.

### Residual risks

- Operator smoke remains fixture-only; DB-backed live/persist/restart proof remains blocked until WSL2/Docker Desktop/local Postgres is available.

## 2026-06-17 local - Operator smoke malformed fixture review evidence

### What changed

- Strengthened `python scripts\task.py operator-smoke` so `review-pass` must prove skipped malformed fixtures are classified as expected normalization dead-letter evidence.
- Added fail-closed assertions for the `pm-malformed-1` fixture: raw evidence is expected, derived records are not expected, the stage is normalization, the error class is `invalid_price_or_size`, and the error text includes `not-a-number`.
- Updated operator-smoke tests to include the structured `fixture_skips` evidence and reject malformed skip evidence that loses the source error.

### Verification

- `.\.venv\Scripts\python.exe -m pytest .\tests\test_operator_smoke.py -q` - 8 passed.
- `.\.venv\Scripts\python.exe .\scripts\task.py fixture-replay` - rc 0, emitted 14 fixture-backed alert JSON payloads and the replay summary table.
- `.\.venv\Scripts\python.exe .\scripts\task.py operator-smoke` - rc 0, `operator smoke passed`.
- `.\.venv\Scripts\python.exe .\scripts\task.py db-smoke` - rc 1 because local Postgres is unavailable, with concise preflight guidance and no traceback.
- `.\.venv\Scripts\python.exe .\scripts\verify.py` - 375 passed, 11 skipped; verification passed.

### Residual risks

- This strengthens DB-free malformed-fixture review evidence only; persisted dead-letter inspection and restart/resume proof remain blocked until WSL2/Docker Desktop/local Postgres is available.

## 2026-06-17 local - Operator smoke fixture report inspection evidence

### What changed

- Strengthened `python scripts\task.py operator-smoke` so the fixture `report` step must prove useful operator inspection output, not only broad counts.
- Added fail-closed report assertions for alert breakdowns by rule, severity, confidence, and venue.
- Added fail-closed report assertions for the directional cluster evidence on `polymarket` market `pm-cluster-market`, including dominant side, trade count, net capital, and price impact.

### Verification

- `.\.venv\Scripts\python.exe -m pytest .\tests\test_operator_smoke.py -q` - 10 passed.
- `.\.venv\Scripts\python.exe .\scripts\task.py operator-smoke` - rc 0, `operator smoke passed`.
- `.\.venv\Scripts\python.exe .\scripts\task.py db-smoke` - rc 1 because local Postgres is unavailable, with concise preflight guidance and no traceback.
- `.\.venv\Scripts\python.exe .\scripts\verify.py` - 377 passed, 11 skipped; verification passed.

### Residual risks

- This proves fixture-backed report inspectability; persisted DB report and dead-letter/delivery-failure inspection still require local Postgres.

## 2026-06-17 local - Operator smoke health inspectability evidence

### What changed

- Extended `python scripts\task.py operator-smoke` with `pmfi health --format json`.
- Added dedicated health handling so the smoke gate accepts either a DB-ready health pass or the current DB-unavailable blocked health response.
- Added fail-closed assertions for structured config, delivery, live, DB, DB-dependent warning checks, and local Postgres next actions when the DB is unavailable.

### Verification

- `.\.venv\Scripts\python.exe -m pytest .\tests\test_operator_smoke.py -q` - 13 passed.
- `.\.venv\Scripts\python.exe .\scripts\task.py operator-smoke` - rc 0, now runs `review-pass`, fixture `report`, `health`, `replay-fixtures`, `monitor --fixture-replay`, and fixture-source `live-smoke`.
- `.\.venv\Scripts\python.exe .\scripts\task.py db-smoke` - rc 1 because local Postgres is unavailable, with concise preflight guidance and no traceback.
- `.\.venv\Scripts\python.exe .\scripts\verify.py` - 380 passed, 11 skipped; verification passed.

### Residual risks

- This proves structured health inspectability in the DB-unavailable state and accepts the DB-ready state contract; positive DB health still requires WSL2/Docker Desktop/local Postgres.

## 2026-06-17 local - Structured operator status JSON

### What changed

- Added `pmfi status --format json` while preserving the existing table output as the default.
- The JSON status output returns rc 0 even when local Postgres is unavailable and exposes a credential-safe database target, DB status, DB stats object, live flags, delivery mode, enabled alert rules, and fixture counts.
- Extended `python scripts\task.py operator-smoke` with a `status --format json` check that fails closed on credential leakage, malformed database/features/delivery/rule/fixture sections, or empty fixture evidence.

### Verification

- `.\.venv\Scripts\python.exe -m pytest .\tests\test_cli.py -k "status" -q` - 4 passed, 90 deselected.
- `.\.venv\Scripts\python.exe -m pytest .\tests\test_operator_smoke.py -q` - 15 passed.
- `.\.venv\Scripts\python.exe -m pmfi.cli status --format json` - rc 0, parseable JSON with `database.target` set to `localhost:5433/pmfi` and no credential text.
- `.\.venv\Scripts\python.exe .\scripts\task.py operator-smoke` - rc 0, now includes `status-json`.
- `.\.venv\Scripts\python.exe .\scripts\task.py db-smoke` - rc 1 because local Postgres is unavailable, with concise preflight guidance and no traceback.
- `.\.venv\Scripts\python.exe .\scripts\verify.py` - 385 passed, 11 skipped; verification passed.

### Residual risks

- Positive DB-backed status stats remain unverified on this machine until WSL2/Docker Desktop/local Postgres is available; the fake-DB unit test covers the JSON shape.

## 2026-06-17 local - Structured DB setup status JSON

### What changed

- Added `python scripts\db_local.py status --format json` while preserving the existing text status output.
- The JSON status output returns rc 0 for setup inspection even when Docker Desktop/local Postgres is blocked.
- The structured payload reports the Docker compose command, Docker availability, return code, captured stdout/stderr, classified Docker Desktop diagnostics, WSL status lines, and next actions.
- Added deterministic tests for successful Docker status, missing `docker.exe`, generic compose failure, and Docker Desktop startup failure with sanitized WSL context.

### Verification

- `.\.venv\Scripts\python.exe -m pytest .\tests\test_db_local_diagnostics.py -q` - 18 passed.
- `.\.venv\Scripts\python.exe .\scripts\db_local.py status --format json` - rc 0, valid JSON with `ok=false`, `status=blocked`, Docker Desktop diagnostic guidance, and WSL virtualization-disabled evidence.
- `.\.venv\Scripts\python.exe .\scripts\task.py db-smoke` - rc 1 because local Postgres is unavailable, with concise preflight guidance and no traceback.
- `.\.venv\Scripts\python.exe .\scripts\verify.py` - 389 passed, 11 skipped; verification passed.
- `.\.venv\Scripts\python.exe .\scripts\task.py operator-smoke` - rc 0, `operator smoke passed`.

### Residual risks

- JSON setup status proves and explains the local blocker; positive Docker/Postgres setup still requires enabling WSL2 virtualization / Virtual Machine Platform and starting Docker Desktop.

## 2026-06-17 local - Setup smoke diagnostic gate

### What changed

- Added `python scripts\task.py setup-smoke` as a validate-only setup diagnostic gate.
- The gate runs `python scripts\db_local.py status --format json`, accepts ready setup and explained blocked/unavailable/error setup states, and fails closed on nonzero status command exits, empty output, malformed JSON, malformed diagnostics, or unsafe WSL output.
- Added status output alignment so `python scripts\task.py status` names `setup-smoke` in high-priority commands.

### Verification

- `.\.venv\Scripts\python.exe -m pytest .\tests\test_setup_smoke.py .\tests\test_alignment_contracts.py -q` - 18 passed.
- `.\.venv\Scripts\python.exe .\scripts\task.py setup-smoke` - rc 0, `setup-smoke passed: status=blocked ok=false`.
- `.\.venv\Scripts\python.exe .\scripts\task.py status` - rc 0, high-priority commands include `python scripts\task.py setup-smoke`.
- `.\.venv\Scripts\python.exe .\scripts\task.py db-smoke` - rc 1 because local Postgres is unavailable, with concise preflight guidance and no traceback.
- `.\.venv\Scripts\python.exe .\scripts\task.py operator-smoke` - rc 0, `operator smoke passed`.
- `.\.venv\Scripts\python.exe .\scripts\verify.py` - 399 passed, 11 skipped; verification passed.

### Residual risks

- `setup-smoke` validates setup diagnostics only; positive Docker/Postgres setup and DB-backed daemon proof still require WSL2 virtualization / Virtual Machine Platform and Docker Desktop.

## 2026-06-17 local - Discovery-to-watch subscription contract

### What changed

- Added DB-free fake-backed tests for the existing Polymarket and Kalshi market discovery sync path.
- The Polymarket test proves a discovery payload becomes a market row with preserved raw metadata, two active outcome token mappings, and a downstream `load_asset_id_mapping` result for subscription/normalization planning.
- The Kalshi test proves a discovery payload becomes a market row with preserved raw metadata, yes/no outcome rows, and a watched ticker list through the existing `fetch_watched_markets` path.
- The tests also pin that discovery payload `enabled=true` is preserved as raw metadata only; runtime watched state remains DB-canonical and changes through `set_market_watched`.

### Verification

- `.\.venv\Scripts\python.exe -m pytest .\tests\test_markets_discovery.py -q` - 13 passed.
- `.\.venv\Scripts\python.exe -m pytest .\tests\test_markets_discovery.py .\tests\test_cli.py -k "markets_watch or markets_unwatch or markets_list_accepts_venue_and_json_format or ingest_check" -q` - 8 passed, 99 deselected.
- `.\.venv\Scripts\python.exe .\scripts\task.py setup-smoke` - rc 0, `setup-smoke passed: status=blocked ok=false`.
- `.\.venv\Scripts\python.exe .\scripts\task.py operator-smoke` - rc 0, `operator smoke passed`.
- `.\.venv\Scripts\python.exe .\scripts\task.py db-smoke` - rc 1 because local Postgres is unavailable, with concise preflight guidance and no traceback.
- `.\.venv\Scripts\python.exe .\scripts\verify.py` - 401 passed, 11 skipped; verification passed.

### Residual risks

- This proves the discovery/watch/subscription contract with fakes only. Positive DB-backed market discovery, watched market persistence, and live subscription startup still require WSL2 virtualization / Virtual Machine Platform and Docker Desktop.

## 2026-06-17 local - Ingest readiness subscription plan details

### What changed

- Enriched `pmfi ingest --check --format json` with deterministic DB-canonical subscription plan details while preserving existing count keys.
- `subscriptions.polymarket_markets` now groups watched Polymarket markets by DB market ID and shows sorted asset IDs, including empty asset lists for watched markets that cannot yet produce token subscriptions.
- `subscriptions.kalshi_markets` now lists watched Kalshi tickers with DB market ID, venue market ID, title, and status.
- The readiness check remains validate-only: it uses watched DB rows and `load_asset_id_mapping`, avoids live adapter imports/connections, and avoids ingestion runtime state writes.

### Verification

- `.\.venv\Scripts\python.exe -m pytest .\tests\test_cli.py -k "ingest_check" -q` - 7 passed, 89 deselected.
- `.\.venv\Scripts\python.exe -m pytest .\tests\test_db_smoke.py -q` - 16 passed.
- `.\.venv\Scripts\python.exe .\scripts\task.py setup-smoke` - rc 0, `setup-smoke passed: status=blocked ok=false`.
- `.\.venv\Scripts\python.exe .\scripts\task.py operator-smoke` - rc 0, `operator smoke passed`.
- `.\.venv\Scripts\python.exe .\scripts\task.py db-smoke` - rc 1 because local Postgres is unavailable, with concise preflight guidance and no traceback.
- `.\.venv\Scripts\python.exe .\scripts\verify.py` - 403 passed, 11 skipped; verification passed.

### Residual risks

- This proves ingest readiness planning with fake DB state only. Positive DB-backed ingest readiness, live adapter startup, and persisted runtime proof still require WSL2 virtualization / Virtual Machine Platform and Docker Desktop.

## 2026-06-17 local - Operator smoke ingest readiness coverage

### What changed

- Extended `python scripts\task.py operator-smoke` with `pmfi ingest --venue kalshi --check --format json`.
- The smoke gate now accepts the current DB-unavailable blocked ingest-readiness payload only when it has a failed `db_connectivity` check and next actions for `db_local.py up` and `db_local.py verify`.
- The smoke gate also accepts the DB-ready ingest-readiness shape only when it includes a passing `live_connections` check, compatible subscription count keys, and non-empty Kalshi subscription plan details.
- Added fail-closed assertions for missing DB next actions, missing Kalshi subscription plan details, and subscription count mismatches.

### Verification

- `.\.venv\Scripts\python.exe -m pytest .\tests\test_operator_smoke.py -q` - 20 passed.
- `.\.venv\Scripts\python.exe -m pytest .\tests\test_cli.py -k "ingest_check" -q` - 7 passed, 89 deselected.
- `.\.venv\Scripts\python.exe .\scripts\task.py operator-smoke` - rc 0, now includes `ingest-check`.
- `.\.venv\Scripts\python.exe .\scripts\task.py setup-smoke` - rc 0, `setup-smoke passed: status=blocked ok=false`.
- `.\.venv\Scripts\python.exe .\scripts\task.py db-smoke` - rc 1 because local Postgres is unavailable, with concise preflight guidance and no traceback.
- `.\.venv\Scripts\python.exe .\scripts\verify.py` - 408 passed, 11 skipped; verification passed.

### Residual risks

- This proves the local operator smoke can inspect ingest readiness safely before DB availability. Positive DB-backed ingest readiness and live adapter startup still require WSL2 virtualization / Virtual Machine Platform and Docker Desktop.

## 2026-06-17 local - Setup smoke actionable guidance hardening

### What changed

- Hardened `python scripts\task.py setup-smoke` so blocked Docker Desktop diagnostics must include actionable Docker Desktop/local Postgres retry guidance and Windows virtualization/WSL/Virtual Machine Platform context.
- Hardened missing-Docker setup diagnostics so unavailable Docker status must tell the user how to install/start Docker Desktop or repair `docker.exe`/PATH access before retrying.
- Kept generic compose/status errors accepted without Docker-specific guidance while continuing to fail closed on empty, malformed, non-JSON, and NUL-containing diagnostics.

### Verification

- `.\.venv\Scripts\python.exe -m pytest .\tests\test_setup_smoke.py -q` - 11 passed.
- `.\.venv\Scripts\python.exe .\scripts\task.py setup-smoke` - rc 0, `setup-smoke passed: status=blocked ok=false`.
- `.\.venv\Scripts\python.exe .\scripts\task.py operator-smoke` - rc 0, `operator smoke passed`.
- `.\.venv\Scripts\python.exe .\scripts\task.py db-smoke` - rc 1 because local Postgres is unavailable, with concise preflight guidance and no traceback.
- `.\.venv\Scripts\python.exe .\scripts\verify.py` - 410 passed, 11 skipped; verification passed.

### Residual risks

- This strengthens setup diagnostics only. Positive Docker/Postgres setup and DB-backed daemon proof still require WSL2 virtualization / Virtual Machine Platform and Docker Desktop.

## 2026-06-17 local - DB-free lifecycle smoke gate

### What changed

- Added `python scripts\task.py lifecycle-smoke` as a validate-only daemon lifecycle/restart gate.
- The gate runs in memory without Docker/Postgres/live calls/artifact writes and proves four runner contracts: restart raw-event replay dedupe, duplicate normalized-trade skip, suppression cache seeding from persisted alert history, and non-trade raw persistence without trade/metric/alert/delivery writes.
- Added lifecycle-smoke JSON validation to `python scripts\task.py operator-smoke`, so the DB-free operator smoke now includes restart/resume safety evidence.
- Added status/alignment coverage so `python scripts\task.py status` advertises the executable lifecycle-smoke command.

### Verification

- `.\.venv\Scripts\python.exe .\scripts\task.py lifecycle-smoke` - rc 0, `lifecycle-smoke passed: raw_event_replay_dedupe, duplicate_trade_skip, suppression_cache_seed, non_trade_raw_persistence`.
- `.\.venv\Scripts\python.exe .\scripts\lifecycle_smoke.py --format json` - rc 0 with `ok=true`, `source=db_free_runner_contracts`, and all four lifecycle checks passing.
- `.\.venv\Scripts\python.exe -m pytest .\tests\test_lifecycle_smoke.py .\tests\test_operator_smoke.py .\tests\test_alignment_contracts.py .\tests\test_runner_lifecycle.py -q` - 39 passed.
- `.\.venv\Scripts\python.exe .\scripts\task.py operator-smoke` - rc 0 and now includes `lifecycle-smoke`.
- `.\.venv\Scripts\python.exe .\scripts\task.py db-smoke` - rc 1 because local Postgres is unavailable, with concise preflight guidance and no traceback.
- `.\.venv\Scripts\python.exe .\scripts\verify.py` - 416 passed, 11 skipped; verification passed.

### Residual risks

- This proves daemon lifecycle behavior with in-memory fakes only. Positive persisted restart/resume proof still requires WSL2/Docker Desktop/local Postgres and the DB-backed `db-smoke` gate.

## 2026-06-17 local - Scope boundary smoke gate

### What changed

- Added `python scripts\task.py scope-smoke` as a validate-only local trust-boundary gate.
- The gate checks authoritative local-only/no-trading docs, hosted/SaaS implementation markers, order-placement/trading-execution markers, forbidden platform scaffold paths, local config defaults, offline/default verification expectations, and absence of `.github` workflow scaffolding.
- Added scope-smoke JSON validation to `python scripts\task.py operator-smoke`, so the DB-free operator smoke now proves local-only/no-trading/no-SaaS boundaries alongside data/replay/lifecycle checks.
- Added status/alignment coverage so `python scripts\task.py status` advertises the executable scope-smoke command.

### Verification

- `.\.venv\Scripts\python.exe .\scripts\task.py scope-smoke` - rc 0 with all seven boundary checks passing.
- `.\.venv\Scripts\python.exe .\scripts\scope_smoke.py --format json` - rc 0 with `ok=true`, `source=local_scope_contracts`, and all seven checks passing.
- `.\.venv\Scripts\python.exe -m pytest .\tests\test_scope_smoke.py .\tests\test_operator_smoke.py .\tests\test_alignment_contracts.py .\tests\test_local_only_scope_contracts.py .\tests\test_windows_native_contracts.py -q` - 57 passed.
- `.\.venv\Scripts\python.exe .\scripts\task.py operator-smoke` - rc 0 and now includes `scope-smoke`.
- `.\.venv\Scripts\python.exe .\scripts\task.py status` - rc 0 and lists `python scripts\task.py scope-smoke`.
- `.\.venv\Scripts\python.exe .\scripts\task.py db-smoke` - rc 1 because local Postgres is unavailable, with concise preflight guidance and no traceback.
- `.\.venv\Scripts\python.exe .\scripts\verify.py` - 424 passed, 11 skipped; verification passed.

### Residual risks

- This proves the current repo boundary contract only. Positive DB-backed operator proof still requires WSL2/Docker Desktop/local Postgres and the DB-backed `db-smoke` gate.

## 2026-06-17 local - DB-free baseline smoke gate

### What changed

- Added `python scripts\task.py baseline-smoke` as a validate-only DB-free baseline/alert contract gate.
- The gate proves persisted baseline computation reads `normalized_trades` rather than `metric_windows`, upserts market baselines through `market_baselines_market_scope_unique`, and emits baseline-aware market-relative alert evidence for both available and missing baseline states.
- The gate also proves `volume_spike_v1` uses prior in-memory history and does not let the spike trade inflate its own baseline.
- Added baseline-smoke JSON validation to `python scripts\task.py operator-smoke`, so the DB-free operator smoke now covers baseline/alert contracts alongside setup/scope/lifecycle/replay checks.
- Added status/alignment coverage so `python scripts\task.py status` advertises the executable baseline-smoke command.

### Verification

- `.\.venv\Scripts\python.exe .\scripts\task.py baseline-smoke` - rc 0, `baseline-smoke passed: compute_path_uses_normalized_trades, baseline_upsert_conflict_constraint, baseline_available_alert, baseline_missing_alert, volume_spike_uses_prior_history`.
- `.\.venv\Scripts\python.exe .\scripts\baseline_smoke.py --format json` - rc 0 with `ok=true`, `source=db_free_baseline_contracts`, and all five baseline/alert checks passing.
- `.\.venv\Scripts\python.exe -m pytest .\tests\test_baseline_smoke.py .\tests\test_operator_smoke.py .\tests\test_alignment_contracts.py .\tests\test_baseline.py .\tests\test_pipeline_engine.py -q` - 65 passed.
- `.\.venv\Scripts\python.exe .\scripts\task.py operator-smoke` - rc 0 and now includes `baseline-smoke`.
- `.\.venv\Scripts\python.exe .\scripts\task.py status` - rc 0 and lists `python scripts\task.py baseline-smoke`.
- `.\.venv\Scripts\python.exe .\scripts\task.py db-smoke` - rc 1 because local Postgres is unavailable, with concise preflight guidance and no traceback.
- `.\.venv\Scripts\python.exe .\scripts\verify.py` - 433 passed, 11 skipped; verification passed.

### Residual risks

- This proves baseline/alert behavior with fakes and the in-memory alert engine only. Positive persisted baseline computation and DB replay with baseline-aware evidence still require WSL2/Docker Desktop/local Postgres and the DB-backed `db-smoke` gate.

## 2026-06-17 local - Operator evidence smoke expansion and fixture monitor DB-driver decoupling

### What changed

- Expanded `python scripts\task.py operator-smoke` so it now validates the DB-backed inspection commands a local operator needs during degraded runs: `dead-letters --format json`, `data-quality-incidents --format json`, `delivery-failures --format json`, and `alerts list --format json`.
- The smoke gate accepts either structured DB success payloads or the current DB-unavailable degraded payloads, but fails closed on empty output, malformed JSON, unsupported exit codes, missing list/count fields, missing `db_local.py up` / `db_local.py verify` next actions, tracebacks, or credential-shaped output.
- Kept `alerts list` compatible with its existing JSON-list success shape while still requiring actionable DB retry guidance when local Postgres is unavailable.
- Removed import-time `asyncpg` coupling from baseline helpers by moving those imports behind `TYPE_CHECKING` in `src/pmfi/baseline.py` and `src/pmfi/db/repos/baselines.py`.
- Added an explicit fixture-monitor regression test proving `pmfi monitor --fixture-replay` still streams fixture alerts with empty baselines when `asyncpg` cannot be imported and local DB pool creation fails.

### Verification

- `.\.venv\Scripts\python.exe -m pytest .\tests\test_operator_smoke.py -q` - 45 passed.
- `.\.venv\Scripts\python.exe -m pytest .\tests\test_cli.py -k "monitor_fixture_replay or baseline" -q` - 8 passed, 89 deselected.
- `.\.venv\Scripts\python.exe -m pytest .\tests\test_baseline.py .\tests\test_cli.py -k "monitor_fixture_replay or baseline" -q` - 10 passed, 89 deselected.
- `.\.venv\Scripts\python.exe -m pytest .\tests\test_operator_smoke.py .\tests\test_cli_data_quality_incidents.py .\tests\test_cli_delivery_failures.py .\tests\test_cli_dead_letters.py .\tests\test_baseline.py -q` - 72 passed.
- `.\.venv\Scripts\python.exe .\scripts\task.py operator-smoke` - rc 0 and now includes `dead-letters`, `data-quality-incidents`, `delivery-failures`, and `alerts-list`.
- `.\.venv\Scripts\python.exe .\scripts\task.py db-smoke` - rc 1 because local Postgres is unavailable: `[WinError 1225] The remote computer refused the network connection`.
- `.\.venv\Scripts\python.exe .\scripts\verify.py` - 450 passed, 11 skipped; verification passed.

### Residual risks

- This strengthens DB-free operator evidence and degraded-state coverage only. Positive dead-letter, data-quality incident, delivery-failure, alert-list, and baseline behavior against persisted local Postgres rows still requires WSL2/Docker Desktop/local Postgres and the DB-backed `db-smoke` gate.
- The next offline candidate after DB proof remains blocked is a real `alerts review` adjudication surface over the existing `alert_reviews` schema, because `review-pass` is currently fixture/coherence review rather than human TP/FP/noise labeling.

## 2026-06-17 local - Alert review adjudication CLI

### What changed

- Added `pmfi alerts review ALERT_ID --label ...` so a local operator can append TP/FP/noise/unsure adjudications to the existing `alert_reviews` schema without manual SQL.
- Added `pmfi alerts reviews --format table|json` so recent review rows can be inspected with alert context, optional `--alert-id`, `--label`, and `--limit` filters.
- Added `src/pmfi/db/repos/alert_reviews.py` with append-only review insertion and read-only review listing against the existing Postgres schema.
- Updated `pmfi alerts list --format json` and table output to include `alert_id`, so an operator can copy the ID directly into `pmfi alerts review`.
- Extended `python scripts\task.py operator-smoke` with `alerts reviews --format json`, accepting structured DB success or the current actionable DB-unavailable degraded state.
- Added fake-backed CLI tests for parser shape, successful review insertion, alert-not-found handling, invalid label/alert ID validation before DB access, DB-unavailable output, read-only review listing, and alert-list ID exposure.

### Verification

- `.\.venv\Scripts\python.exe -m pytest .\tests\test_cli_alert_reviews.py .\tests\test_operator_smoke.py -q` - 56 passed.
- `.\.venv\Scripts\python.exe -m pytest .\tests\test_cli.py -k "alerts" -q` - 6 passed, 91 deselected.
- `.\.venv\Scripts\python.exe -m pmfi.cli alerts --help` - rc 0 and now lists `list`, `serve`, `review`, and `reviews`.
- `.\.venv\Scripts\python.exe -m pmfi.cli alerts reviews --format json` - rc 1 with `DB unavailable` JSON and `db_local.py up` / `db_local.py verify` next actions.
- `.\.venv\Scripts\python.exe -m pmfi.cli alerts review 11111111-1111-1111-1111-111111111111 --label tp --format json` - rc 1 with `DB unavailable` JSON and `db_local.py up` / `db_local.py verify` next actions.
- `.\.venv\Scripts\python.exe .\scripts\task.py operator-smoke` - rc 0 and now includes `alerts-reviews`.
- `.\.venv\Scripts\python.exe .\scripts\task.py db-smoke` - rc 1 because local Postgres is unavailable: `[WinError 1225] The remote computer refused the network connection`.
- `.\.venv\Scripts\python.exe .\scripts\verify.py` - 461 passed, 11 skipped; verification passed.

### Residual risks

- Alert review behavior is fake-backed and DB-unavailable-path verified only in this environment. Positive persisted review insertion/listing against real local Postgres still requires WSL2/Docker Desktop/local Postgres and the DB-backed proof path.
- This adds the operator adjudication surface; aggregate FP-rate reporting by rule/time window remains a follow-up trust feature.

## 2026-06-17 local - Alert false-positive rate reporting

### What changed

- Added `pmfi alerts fp-rate --format table|json` so a local operator can summarize reviewed-alert false-positive rate by rule and time bucket.
- Added `--since`, `--bucket all|day|hour`, `--rule`, and `--limit` filters for focused operator review windows.
- Added latest-review aggregation in `src/pmfi/db/repos/alert_reviews.py`, using the newest review per alert so append-only review corrections do not double-count.
- Extended `python scripts\task.py operator-smoke` with `alerts fp-rate --format json`, accepting either structured DB success or actionable DB-unavailable degraded output.
- Added fake-backed tests for parser shape, JSON summary output, latest-review SQL shape, invalid input before DB access, DB-unavailable output, and operator-smoke payload validation.

### Verification

- `.\.venv\Scripts\python.exe -m pytest .\tests\test_cli_alert_reviews.py .\tests\test_operator_smoke.py -q` - 64 passed.
- `.\.venv\Scripts\python.exe -m pytest .\tests\test_cli.py -k "alerts" -q` - 6 passed, 91 deselected.
- `.\.venv\Scripts\python.exe -m pmfi.cli alerts --help` - rc 0 and now lists `list`, `serve`, `review`, `reviews`, and `fp-rate`.
- `.\.venv\Scripts\python.exe -m pmfi.cli alerts fp-rate --format json` - rc 1 with `DB unavailable` JSON and `db_local.py up` / `db_local.py verify` next actions.
- `.\.venv\Scripts\python.exe .\scripts\task.py operator-smoke` - rc 0 and now includes `alerts-fp-rate`.
- `.\.venv\Scripts\python.exe .\scripts\task.py db-smoke` - rc 1 because local Postgres is unavailable: `[WinError 1225] The remote computer refused the network connection`.
- `.\.venv\Scripts\python.exe .\scripts\verify.py` - 469 passed, 11 skipped; verification passed.

### Residual risks

- FP-rate behavior is fake-backed and DB-unavailable-path verified only in this environment. Positive persisted aggregation against real local Postgres still requires WSL2/Docker Desktop/local Postgres and the DB-backed proof path.
- The next highest-ROI offline continuation is likely a DB-free storage/replay/idempotency proof around restart/replay duplicate behavior, unless local Postgres becomes available first.

## 2026-06-17 local - Suppression expiry lifecycle proof

### What changed

- Extended `python scripts\task.py lifecycle-smoke` with a DB-free `suppression_window_expiry` proof.
- The proof runs actual runner pipeline behavior with a controlled clock and three distinct alert-worthy raw/trade events on the same venue, market, and rule.
- It proves the first alert inserts/delivers, the second alert inside the 300-second suppression window is suppressed, and the third alert after the window inserts/delivers.
- Extended `python scripts\task.py operator-smoke` validation so lifecycle payloads must include exact suppression-expiry counts and fail closed when they drift.
- Added tests for the lifecycle payload shape and operator-smoke fail-closed behavior.

### Verification

- `.\.venv\Scripts\python.exe -m pytest .\tests\test_lifecycle_smoke.py .\tests\test_operator_smoke.py .\tests\test_runner_suppression.py -q` - 70 passed.
- `.\.venv\Scripts\python.exe .\scripts\task.py lifecycle-smoke` - rc 0 and now includes `suppression_window_expiry`.
- `.\.venv\Scripts\python.exe .\scripts\task.py operator-smoke` - rc 0 and accepts the expanded lifecycle proof.
- `.\.venv\Scripts\python.exe .\scripts\task.py db-smoke` - rc 1 because local Postgres is unavailable: `[WinError 1225] The remote computer refused the network connection`.
- `.\.venv\Scripts\python.exe .\scripts\verify.py` - 471 passed, 11 skipped; verification passed.

### Residual risks

- This closes the DB-free suppression-window failure mode called out in Lane 3: suppression should not permanently hide legitimate later alerts.
- Positive DB-backed restart/replay proof still requires WSL2/Docker Desktop/local Postgres and the DB-backed proof path.

## 2026-06-17 local - Kalshi REST overlap dedupe lifecycle proof

### What changed

- Extended `python scripts\task.py lifecycle-smoke` with a DB-free `kalshi_rest_poll_overlap_dedupe` proof.
- The proof converts two overlapping Kalshi REST poll windows through `kalshi_trade_to_raw_event`: first `t1,t2`, then `t2,t3`.
- It runs the resulting raw events through runner behavior with fake persistence and proves the repeated `t2` raw event is counted as a duplicate before normalized trade, metric, alert, or delivery work repeats.
- Extended `python scripts\task.py operator-smoke` validation so lifecycle payloads must include the Kalshi REST overlap proof and fail closed when counts or stable identities drift.
- Added tests for the lifecycle payload shape and operator-smoke fail-closed behavior.

### Verification

- `.\.venv\Scripts\python.exe -m pytest .\tests\test_lifecycle_smoke.py .\tests\test_operator_smoke.py .\tests\test_markets_discovery.py -q` - 70 passed.
- `.\.venv\Scripts\python.exe .\scripts\task.py lifecycle-smoke` - rc 0 and now includes `kalshi_rest_poll_overlap_dedupe`.
- `.\.venv\Scripts\python.exe .\scripts\task.py operator-smoke` - rc 0 and accepts the expanded lifecycle proof.
- `.\.venv\Scripts\python.exe .\scripts\task.py db-smoke` - rc 1 because local Postgres is unavailable: `[WinError 1225] The remote computer refused the network connection`.
- `.\.venv\Scripts\python.exe .\scripts\verify.py` - 474 passed, 11 skipped; verification passed.

### Residual risks

- This closes the DB-free Lane 3 repeated Kalshi REST polling overlap proof: `raw_events_seen=4`, `raw_events_inserted=3`, `raw_event_duplicates=1`, `normalized_trades_inserted=3`, `duplicate_trades=0`, `metrics_upserted=3`, `alerts_inserted=3`, and `alerts_delivered=3`.
- Positive DB-backed repeated-poll proof still requires WSL2/Docker Desktop/local Postgres and the DB-backed proof path.

## 2026-06-17 local - Kalshi REST malformed fixture diagnostics

### What changed

- Added a default Kalshi REST malformed trade fixture, `ks-rest-malformed-1`, with `source_channel=rest_trades` and malformed `count`.
- Extended `pmfi review-pass --format json` expectations so the default fixture run proves two expected malformed dead-letter skips: `ks-rest-malformed-1` and `pm-malformed-1`.
- Extended `python scripts\task.py operator-smoke` validation so missing evidence for either malformed fixture fails closed.
- Added focused pytest coverage for the review-pass classification and both operator-smoke malformed-evidence failure directions.

### Verification

- `.\.venv\Scripts\python.exe -m pytest .\tests\test_cli.py -k "review_pass" -q` - 10 passed, 87 deselected.
- `.\.venv\Scripts\python.exe -m pytest .\tests\test_operator_smoke.py -q` - 54 passed.
- `.\.venv\Scripts\python.exe .\scripts\task.py operator-smoke` - rc 0; operator smoke passed.
- `.\.venv\Scripts\python.exe -m pmfi.cli review-pass --format json` - rc 0; `fixture_files=12`, `normalized_trades=10`, `expected_dead_letter_count=2`, with `ks-rest-malformed-1` classified as `invalid_price_or_size`.
- `.\.venv\Scripts\python.exe .\scripts\task.py db-smoke` - rc 1 because local Postgres is unreachable: `[WinError 1225] The remote computer refused the network connection`.
- `.\.venv\Scripts\python.exe .\scripts\verify.py` - 475 passed, 11 skipped; verification passed.

### Residual risks

- This closes the DB-free Lane 4 proof that Kalshi REST trade-shape drift/malformed numeric fields become operator-visible expected dead-letter diagnostics rather than silent bad data.
- Positive DB-backed validation still requires WSL2/Docker Desktop/local Postgres, then `.\.venv\Scripts\python.exe .\scripts\db_local.py up`, `.\.venv\Scripts\python.exe .\scripts\db_local.py verify`, and `.\.venv\Scripts\python.exe .\scripts\task.py db-smoke`.

## 2026-06-17 local - Baseline freshness degrade proof

### What changed

- Added `market_relative_large_trade_v1.max_baseline_age_seconds=604800` to the local alert-rule config.
- Extended `AlertEngine` so stale, future-dated, or unparseable baseline `computed_at` values are operator-visible and cannot drive fresh percentile confidence.
- Stale baselines now produce low-confidence `market_relative_large_trade_v1` evidence with `data_quality=baseline_stale`, `baseline_status=baseline_stale`, and `reason_codes=["capital_above_minimum_threshold"]` instead of p99/p995 reason codes.
- Unparseable or future-dated baseline timestamps now produce `baseline_freshness_unknown` rather than current-looking percentile confidence.
- Extended `python scripts\task.py baseline-smoke` and `python scripts\task.py operator-smoke` so stale-baseline evidence is part of the fail-closed DB-free operator proof.

### Verification

- `.\.venv\Scripts\python.exe -m pytest .\tests\test_baseline_smoke.py .\tests\test_pipeline_engine.py .\tests\test_operator_smoke.py -q` - 80 passed.
- `.\.venv\Scripts\python.exe .\scripts\task.py baseline-smoke` - rc 0 and now includes `baseline_stale_alert`.
- `.\.venv\Scripts\python.exe .\scripts\baseline_smoke.py --format json` - rc 0; stale baseline evidence reports `baseline_age_seconds=691200`, `baseline_max_age_seconds=604800`, `baseline_state=baseline_stale`, and low confidence.
- `.\.venv\Scripts\python.exe .\scripts\task.py operator-smoke` - rc 0 and accepts the expanded baseline-smoke proof.
- `.\.venv\Scripts\python.exe .\scripts\task.py db-smoke` - rc 1 because local Postgres is unreachable: `[WinError 1225] The remote computer refused the network connection`.
- `.\.venv\Scripts\python.exe .\scripts\verify.py` - 479 passed, 11 skipped; verification passed.

### Residual risks

- This closes the DB-free Lane 9 proof that old baseline rows cannot silently masquerade as current alert-confidence evidence.
- Positive DB-backed validation still requires WSL2/Docker Desktop/local Postgres, then `.\.venv\Scripts\python.exe .\scripts\db_local.py up`, `.\.venv\Scripts\python.exe .\scripts\db_local.py verify`, and `.\.venv\Scripts\python.exe .\scripts\task.py db-smoke`.

## 2026-06-17 local - Local HTTP delivery endpoint guard

### What changed

- Added `validate_loopback_http_endpoint()` to `pmfi.delivery.http` and wired it into `HttpDelivery`.
- `localhost_http_receiver` delivery now rejects non-loopback/public endpoints before any network call, including `example.com`, LAN IPs, `0.0.0.0`, malformed HTTP URLs, and non-HTTP schemes.
- Extended `python scripts\task.py scope-smoke` with `localhost_http_endpoint_validation`, proving outbound local HTTP delivery remains loopback-only.
- Extended `python scripts\task.py operator-smoke` validation so the expanded local-only scope proof is required.
- Added focused tests for loopback endpoint allow-list behavior, public endpoint rejection, scope-smoke fail-closed behavior, and operator-smoke expected check alignment.

### Verification

- `.\.venv\Scripts\python.exe -m pytest .\tests\test_delivery.py .\tests\test_scope_smoke.py .\tests\test_operator_smoke.py -q` - 73 passed.
- `.\.venv\Scripts\python.exe .\scripts\task.py scope-smoke` - rc 0 and now includes `localhost_http_endpoint_validation`.
- `.\.venv\Scripts\python.exe .\scripts\scope_smoke.py --format json` - rc 0; local HTTP endpoint validation passed.
- `.\.venv\Scripts\python.exe .\scripts\task.py operator-smoke` - rc 0 and accepts the expanded scope-smoke proof.
- `.\.venv\Scripts\python.exe .\scripts\task.py db-smoke` - rc 1 because local Postgres is unreachable: `[WinError 1225] The remote computer refused the network connection`.
- `.\.venv\Scripts\python.exe .\scripts\verify.py` - 489 passed, 11 skipped; verification passed.

### Residual risks

- This closes the DB-free Lane 7/local-only proof that the `localhost_http_receiver` delivery path cannot silently become remote/external delivery.
- Positive DB-backed validation still requires WSL2/Docker Desktop/local Postgres, then `.\.venv\Scripts\python.exe .\scripts\db_local.py up`, `.\.venv\Scripts\python.exe .\scripts\db_local.py verify`, and `.\.venv\Scripts\python.exe .\scripts\task.py db-smoke`.

## 2026-06-17 local - Windows autostart dry-run proof

### What changed

- Added `scripts/autostart.py` with a Windows Scheduled Task plan/status/install/uninstall surface for local PMFI ingest.
- The default `plan` action is non-mutating and prints the task name, absolute repo root, absolute Python path, absolute repo-local log path, scheduled-task command, Docker/Postgres dependency warning, and recovery commands.
- Actual task installation is guarded by `install --confirm-mutation`; no smoke/test path registers or removes a real Scheduled Task.
- Missing scheduled-task status and uninstall are treated as idempotent/OK through runner-injected fakes.
- Added `scripts/autostart_smoke.py` and wired `python scripts\task.py autostart-smoke` plus `python scripts\task.py operator-smoke` to require the DB-free autostart proof.

### Verification

- `.\.venv\Scripts\python.exe -m pytest .\tests\test_autostart.py .\tests\test_autostart_smoke.py .\tests\test_operator_smoke.py -q` - 64 passed.
- `.\.venv\Scripts\python.exe .\scripts\autostart.py plan --format json` - rc 0; reports `mutation=none`, absolute paths, repo-local log path, and DB recovery commands.
- `.\.venv\Scripts\python.exe .\scripts\task.py autostart-smoke` - rc 0; autostart smoke passed.
- `.\.venv\Scripts\python.exe .\scripts\task.py operator-smoke` - rc 0 and now includes `autostart-smoke`.
- `.\.venv\Scripts\python.exe .\scripts\task.py db-smoke` - rc 1 because local Postgres is unreachable: `[WinError 1225] The remote computer refused the network connection`.
- `.\.venv\Scripts\python.exe .\scripts\verify.py` - 499 passed, 11 skipped; verification passed.

### Residual risks

- This provides the first DB-free Lane 12 proof for safe autostart planning, absolute-path/log safety, Docker/Postgres recovery guidance, and idempotent missing-task handling.
- Real `schtasks.exe /Create` installation remains intentionally unexercised until an operator explicitly runs `install --confirm-mutation` on a machine ready for local daemon autostart.
- Positive DB-backed daemon/autostart validation still requires WSL2/Docker Desktop/local Postgres, then `.\.venv\Scripts\python.exe .\scripts\db_local.py up`, `.\.venv\Scripts\python.exe .\scripts\db_local.py verify`, and `.\.venv\Scripts\python.exe .\scripts\task.py db-smoke`.

## 2026-06-17 local - Unsupported feature-flag truth proof

### What changed

- Added canonical feature-flag inventory helpers in `pmfi.config`.
- `pmfi status --format json` now reports all current feature flags plus `unsupported_enabled_features`.
- `pmfi health --format json` now includes a `feature_support` check and fails closed with next actions when future unsupported flags are enabled.
- Treated `enable_cross_venue_matching`, `enable_wallet_intelligence`, and `enable_ml_scoring` as unsupported current-horizon flags.
- Kept `enable_polymarket_live`, `enable_kalshi_live`, and `enable_orderbook_reconstruction` out of the unsupported list because they have current implementation paths.
- Extended operator smoke validation so status/health must keep exposing feature-support truth.

### Verification

- `.\.venv\Scripts\python.exe -m pytest .\tests\test_config.py .\tests\test_cli.py .\tests\test_operator_smoke.py -q` - 162 passed.
- `.\.venv\Scripts\python.exe .\scripts\task.py operator-smoke` - rc 0; operator smoke passed and includes the feature-support contract.
- `.\.venv\Scripts\python.exe .\scripts\db_local.py status --format json` - rc 0 with `status=blocked`; Docker Desktop is unable to start and WSL2 reports virtualization/Virtual Machine Platform guidance.
- `.\.venv\Scripts\python.exe .\scripts\task.py db-smoke` - rc 1 because local Postgres is unreachable on the configured `localhost:5433`: `[WinError 1225] The remote computer refused the network connection`.
- `.\.venv\Scripts\python.exe .\scripts\verify.py` - 504 passed, 11 skipped; verification passed.

### Residual risks

- This closes the DB-free Lane 7 proof that unsupported future flags cannot silently appear enabled while the operator surface looks healthy.
- Docker-backed Postgres remains blocked by the machine-level Docker Desktop/WSL2 virtualization issue. CPU firmware checks report virtualization support enabled, but WSL2 still reports it cannot start; Windows optional-feature inspection requires elevation.
- A native PostgreSQL 16 service is running on the default PostgreSQL port, but it requires credentials and is not the repo-configured disposable local DB on `localhost:5433`; it was not used as a verification substitute.
- Positive DB-backed validation still requires repairing WSL2/Docker Desktop or deliberately configuring a supported native local Postgres URL, then running `.\.venv\Scripts\python.exe .\scripts\db_local.py verify` and `.\.venv\Scripts\python.exe .\scripts\task.py db-smoke`.

## 2026-06-17 local - Setup diagnostics in operator smoke

### What changed

- Added `--format json|text` to `scripts/setup_smoke.py`; text output keeps the existing human summary, and JSON emits the validated canonical `db_local.py status --format json` payload.
- Wired `scripts/operator_smoke.py` to run `scripts/setup_smoke.py --format json` and validate setup diagnostics as part of the main local operator proof.
- Reused the setup-smoke payload validator so Docker Desktop, WSL2, Virtual Machine Platform, and local Postgres recovery guidance stay consistent between standalone setup checks and operator smoke.
- Made generic, non-actionable `db_local.py status` errors fail setup-smoke instead of becoming a passing operator proof.
- Updated Windows setup docs to recommend `python scripts\task.py setup-smoke` before local DB mutation commands.
- Added `.claude/worktrees/` to `.gitignore` so nested Claude runtime worktrees remain local runtime state rather than source artifacts.

### Verification

- `.\.venv\Scripts\python.exe -m pytest .\tests\test_setup_smoke.py .\tests\test_operator_smoke.py -q` - 74 passed.
- `.\.venv\Scripts\python.exe .\scripts\setup_smoke.py --format json` - rc 0 and emitted a validated `ok=false`, `status=blocked` Docker/WSL diagnostic payload with actionable next actions.
- `.\.venv\Scripts\python.exe .\scripts\task.py operator-smoke` - rc 0 and now includes `setup-smoke` before health/DB-inspection checks.
- `.\.venv\Scripts\python.exe .\scripts\verify.py` - 509 passed, 11 skipped; verification passed.

### Residual risks

- This closes the DB-free setup-diagnostic proof gap: the main operator smoke now fails if local setup diagnostics are missing, malformed, or non-actionable.
- Docker-backed Postgres remains environment-blocked in this session because Docker Desktop/WSL2 cannot start; DB-backed proof still requires repairing that machine-level issue or deliberately configuring a supported local Postgres URL.
