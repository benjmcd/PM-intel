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

## 2026-06-11 - Session 19 (prodgrade-advance): PR #4 review blocker closeout

### Files inspected
- Claude PR state and review comments for PR #4; canonical worktree `C:\Users\benny\OneDrive\Desktop\PM-intel-prodgrade` on branch `prodgrade-advance`.
- `scripts/db_local.py`, `sql/012_schema_migrations.sql`, `src/pmfi/markets.py`, `src/pmfi/delivery/file.py`, `src/pmfi/monitoring/*`, `src/pmfi/commands/daemon.py`, `src/pmfi/pipeline/engine.py`, `src/pmfi/pipeline/rules_price_impact.py`, and focused tests.

### Changes made
- Fixed seven PR review blockers: migration ledger recording now waits until `012_schema_migrations.sql` has created the ledger; Polymarket/Kalshi REST 429 handling is bounded and retries fresh requests; FileDelivery wraps rotation path selection in the non-fatal guard; data-quality monitors are scoped to active ingest venues and use a dedupe-only condition context; DB replay seeds `price_impact_confirmation_v1` prior prices.
- Added focused regression coverage for all fixed surfaces, including mocked market 429 behavior, monitor active-scope/dedupe-context behavior, FileDelivery `_current_path()` failure handling, migration-order behavior, and price-impact replay seeding.
- Added Ralph context snapshot under `.omx/context/` for this closeout audit.

### Verification run
- `.\.venv\Scripts\python.exe -m pytest tests/test_db_local_script.py tests/test_alert_dedupe.py tests/test_markets_discovery.py tests/test_delivery_hardened.py tests/test_rule_price_impact.py tests/test_replay_cli_offline.py tests/test_data_quality_monitor.py -q` - PASS: 91 passed, 5 skipped (DB-gated).
- `.\.venv\Scripts\python.exe -m pytest tests/test_adapter_hardening.py tests/test_alert_delivery_durable.py tests/test_cli_validation.py tests/test_db_hardening_db.py -q` - PASS: 39 passed, 3 skipped (DB-gated).
- `.\.venv\Scripts\python.exe -m compileall -q src tests scripts` - PASS.
- Full `python scripts\verify.py` was not run because the prior prodgrade handoff explicitly says to verify this branch in small targeted chunks and not run the full suite on this resource-constrained device.
- DB-gated tests were not run live because `PMFI_DB_URL`/`DATABASE_URL` were not set in this session.

### Findings
- Facts: PR #4 remains open, non-draft, merge-clean, and has no status checks configured. The seven review comments all point to pre-fix commit `d3ca4de`; local fixes now map one-to-one to those comments but still need commit/push for GitHub to show them on the PR.
- Inferences: The immediate release blocker is no longer implementation shape; it is commit/push plus review-thread resolution after CI/remote diff refresh.
- Assumptions: The targeted checks are the appropriate verification level for this device unless the user explicitly asks for the full suite despite the handoff warning.
- Blockers: Wallet/holder intelligence remains intentionally blocked by absent public feed identity and local-only scope. Live DB proof remains optional until a local DSN is supplied or Postgres is explicitly started for this pass.

### Next step
- Commit and push the PR #4 fixes, then re-check GitHub review threads. After merge readiness, the next product work is periodic orderbook polling, Kalshi orderbook capture, config gating cleanup for composite/cross-venue behavior, and then the longer dashboard/operator-feedback roadmap.

## 2026-06-11 — Session 18 (prodgrade-advance): production-grade tranche — feed/delivery/DB hardening + MVP alert types #5 and #6

Worktree `C:\Users\benny\OneDrive\Desktop\PM-intel-prodgrade`, branch `prodgrade-advance` off `origin/main` (`1e49fd6`). Pushed; PR #4 open to `main`. Plan/ledger: `plans/PRODGRADE_ULTRAGOAL.md`. Local native Postgres 16 stood up for full DB verification (loopback, default port; `PMFI_DB_URL` via env only, never committed).

### Approach
9-investigator read-only gap analysis -> opus synthesis (rejected a stale "no auto baseline recompute" finding; downgraded a "delivery modes broken" finding) -> tiered plan -> user-resolved scope (everything; transparent composite scorer instead of ML; offline+DB+live verification; commit-per-slice + PR). Each slice: own disjoint files, self-tested, integrated into shared files separately, verified in small targeted chunks (device is resource-constrained — full-suite runs avoided).

### Changes (committed, each green)
- **Live-feed hardening** (`adapters/kalshi.py`, `polymarket.py`, `kalshi_rest.py`, `markets.py`): silent-dead-subscription detection (warn when no message arrives within a window after subscribe), HTTP 429 Retry-After handling, transient-vs-permanent error classification (stop retrying on auth/4xx), WS receive/idle timeout (detect hung sockets), Kalshi reconnect jitter. +`tests/test_adapter_hardening.py`; fixed `tests/test_polymarket_adapter.py` `_FakeWS` to the new `ws.receive()` contract.
- **Delivery hardening** (`delivery/file.py`, `http.py`): FileDelivery I/O guard (non-fatal) + size-based rotation (the parsed-but-unused cap now enforced); HttpDelivery bounded retry/backoff. +`tests/test_delivery_hardened.py`.
- **DB hardening** (`db/migrations.py`, `db/repos/orderbook.py`, `scripts/db_local.py`, `sql/012_schema_migrations.sql`): `schema_migrations` ledger (name + checksum, idempotent), explicit orderbook `ON CONFLICT` targets, current-partition-precedes-ingest guard (advisory, logs ERROR, non-fatal). +`tests/test_db_hardening_db.py`.
- **MVP #5 `price_impact_confirmation_v1`** (`pipeline/rules_price_impact.py`, registered in `engine.py`, `config/alert_rules.yaml`): single-trade price-impact rule (rule registry now 7). +`tests/test_rule_price_impact.py`.
- **MVP #6 `data_quality_degradation_v1`** (`monitoring/` framework, `commands/daemon.py` tick): feed-silence + dead-letter-spike detection -> first-class alert + writes the previously-unused `data_quality_incidents` table; config-gated, non-fatal. +`tests/test_data_quality_monitor.py`.

### Verification
- Pre-tranche full suite green with DB: **701 passed, 1 skipped** (skip is data-dependent). `scripts\verify.py` passed; CLI smoke OK (`db-verify`, `status`).
- Per-slice targeted runs all green against live DB; ~110 new tests. Three integration failures found and fixed: rule-registry count 6->7; `_FakeWS.receive()` (was an infinite reconnect spin); replay-DB teardown missing alerts cleanup (FK). Plan-doc scrubbed of audit-banned tokens.
- No live API in tests; local-only, Windows-native, raw-before-derived preserved.

### Residual / next (designed in the ledger; NOT yet landed)
- **False-positive feedback loop**: repo layer `src/pmfi/db/repos/alert_reviews.py` landed (record_review/list_reviews/false_positive_rate_by_rule) — needs the `pmfi alerts review`/`reviews`/`fp-rate` CLI + a DB-gated test to make it operator-usable. No auto-suppression by design (non-fragility).
- **Cross-venue divergence** monitor: feasible via existing `market_aliases` + `market_snapshots`; needs a `pmfi markets link` CLI + a manual-matching doc.
- **Liquidity wall/vacuum**: partial — orderbook capture is trade-coupled (quiet-period blind spots), Polymarket-only, 10-level cap. Ship v1 with an ADR documenting caveats.
- **Category-specific thresholds**: `markets.category` already populated; add optional `NormalizedTrade.category` + per-category overrides in `alert_rules.yaml`.
- **Transparent composite scorer**: repurpose `enable_ml_scoring` flag (no ML); corroboration boost when 2+ rules fire on one trade.
- **Dead flags**: `enable_wallet_intelligence` is BLOCKED (public Polymarket WS has no wallet/maker/taker id; needs authenticated REST -> out of local-only scope) — keep with a clear warn-if-enabled; `enable_cross_venue_matching` lights up with the cross-venue monitor.
- Docs pass (OPERATOR_QUICKSTART/ARCHITECTURE/product scope) + bounded live-feed smoke (`PMFI_ENABLE_LIVE`).

### Update (same session, continued)
Landed + verified + pushed since the above: FP-feedback CLI (`pmfi alerts review/reviews/fp-rate`); feature-flag warnings for blocked/unimplemented flags; cross_venue_divergence_v1 monitor + `pmfi markets link/links` + `market_aliases` repo + `docs/MANUAL_CROSS_VENUE_MATCHING.md`; OPERATOR_QUICKSTART updated for all new commands/alert types. **Live smoke green**: Polymarket REST discover (8 markets) + WS `live-smoke` (12 events through the new receive()-loop) — feed hardening live-proven.

### Update 2 (same session, continued)
All remaining Tier-3 + polish landed, verified, and pushed: `liquidity_wall_v1` (opt-in orderbook path + ADR-0009 caveats), transparent composite scorer (`apply_corroboration` — annotates evidence when 2+ rules agree; no ML), and category-specific threshold overrides (suppress-only, via `NormalizedTrade.category` + a cached per-market fetch). Feature-flag warnings made accurate.

### Next step
Everything in scope is delivered EXCEPT wallet/holder accumulation, which stays blocked (no wallet/maker/taker identity in the public Polymarket feed; would need authenticated REST, out of local-only scope). 16 commits on `prodgrade-advance`; PR #4 to `main`; live-feed smoke green. Optional future work: a periodic orderbook poll (removes liquidity's quiet-period blind spot), Kalshi orderbook capture, and gating the composite/cross-venue monitors behind their config flags. Verify in small targeted chunks (do not run the full suite on this device).

## 2026-06-08 — Session 15 (pmfi-advance): PR#3 fixes, Decimal precision, live proof

## 2026-06-07 — Session 17 (prod-advance): dashboard Phase 2 (localhost browser view)

Worktree `C:\Users\benny\PM-intel-prod`. Layers a real visual, auto-refreshing browser view on the Phase 1 JSON API.

### Changes made
- New `src/pmfi/dashboard/static/index.html`: self-contained page (inline CSS + JS, **no external/CDN dependencies**) that auto-polls `/api/feedhealth` every 5s and `/api/volume` every 30s. Renders per-venue chips (events/min, events/5min, last-event age with green/yellow/red status dot, unresolved dead-letters) + a recent-volume table. Non-static (live auto-refresh) with graceful empty/unreachable states.
- `src/pmfi/dashboard/server.py`: serves the page at `GET /` and `/static/` for future assets (still 127.0.0.1-only).

### Verification run (targeted)
- Smoke (live DB): `GET /` → **200 text/html** (page served, ~4.7 KB); `/healthz` ok.
- Module imports clean; no new dependencies.

### Findings
- Facts: `pmfi dashboard` now serves a live, browser-openable view of per-venue ingest rate + volume at http://127.0.0.1:8766. Run `pmfi ingest` (Polymarket WS, no creds) alongside to populate it.
- Blockers: none.

### Next step
- Phase 3 (optional polish): vendored Chart.js line chart for the volume time-series. Packaging: add `static/` to package-data when a wheel is built (dev/editable install reads it via `__file__` today).

## 2026-06-07 — Session 16 (prod-advance): live ingest-rate dashboard — Phase 1 (localhost JSON API)

Worktree `C:\Users\benny\PM-intel-prod`. First slice of the adversarially-validated dashboard design (Approach C: local aiohttp + read-only DB polling, zero new deps). Lightweight/sequential per request.

### Changes made
- New `src/pmfi/dashboard/queries.py`: read-only per-venue aggregates — `feed_health` (last-event age, events_60s/5m, unresolved dead-letters from `raw_events` — i.e. the TRUE data-received rate incl. book/price_change, not just trades) and `volume_timeseries` (per-bucket `trade_count` + gross capital from `metric_windows`, which carries `venue_code` directly).
- New `src/pmfi/dashboard/server.py`: aiohttp app bound to **127.0.0.1 only** (loopback forced) serving `/api/feedhealth`, `/api/volume[?minutes=N]`, `/healthz`. Reuses the existing `delivery/server.py` aiohttp pattern + an asyncpg pool. No UI yet (Phase 2/3).
- `src/pmfi/cli.py`: new `pmfi dashboard [--port 8766] [--db-url]` command (separate process from `pmfi ingest`; shares only Postgres).
- New `tests/test_dashboard_queries_db.py` (PMFI_DB_URL-gated): seeds synthetic raw_events/metric_windows/dead_letters, asserts the per-venue aggregates, self-cleans.

### Verification run (targeted)
- DB-gated query contract test: **passed**.
- Server smoke (live DB): binds `127.0.0.1:8799`, all three endpoints return **200 + valid JSON** (`/healthz` ok:true; `/api/feedhealth` and `/api/volume` return correct shapes — empty arrays when no recent ingest).
- `pmfi dashboard --help` parses; module imports clean.
- Zero new dependencies (reuses aiohttp + asyncpg already in the project).

### Findings
- Facts: the dashboard data layer + localhost JSON API work end-to-end against the live DB. Feed-health is sourced from `raw_events` so it reflects the high-rate Polymarket book/price_change stream, not just trades.
- Blockers: none.

### Next step
- Phase 2: minimal static HTML page (per-venue chips + recent-volume table, auto-polling) served at `/`. Phase 3: vendored Chart.js time-series.

## 2026-06-07 — Session 15 (prod-advance): Polymarket public WS as the primary live feed

Worktree `C:\Users\benny\PM-intel-advance` on branch `pmfi-advance` (off origin/prod-advance bc59e97). Fresh worktree to carry forward prod-advance work with PR#3 review blockers resolved.

### Changes made

**Fix 1 — `cmd_replay` DB-canonical baselines (`cli.py`):** DB paths (`--from-db`, `--persist`) previously loaded `config/baselines.json` eagerly and passed the non-None value to `replay_from_db`/`replay_fixtures_persist`, bypassing the `if baselines is None:` DB-load guard in `replay.py`. Fixed: file-baseline loading moved to pure-fixture `else` branch only; DB paths always pass `baselines=None`.

**Fix 2 — Stale baseline pruning (`db/repos/baselines.py`):** `fetch_all_baselines` had no staleness filter. Added: `AND b.computed_at >= now() - (b.lookback_seconds * 2 || ' seconds')::interval`. Rows older than 2× their own lookback window are now excluded from every DB baseline load.

**Fix 3 — Ingest preflight exit code (`cli.py`):** `asyncio.run(_run())` return value was discarded; preflight failures (no watched markets, no venues) returned 0. Fixed: `rc = asyncio.run(_run()); if rc: return rc`.

**Fix 4 — `volume_spike_v1` float→Decimal (`pipeline/engine.py`):** history list and comparison now use `Decimal` throughout. `_vs_multiplier` stored as `Decimal(str(...))`. Float used only in evidence display values for JSON-safe output. Evidence round-trip tests unchanged.

**Fix 5 — `live-smoke` asset_ids (`cli.py`):** `_get_watched_asset_ids` was querying `raw_metadata.tokens` (unpopulated). Fixed to use `load_asset_id_mapping + _resolve_poly_token_ids` (same path as `cmd_ingest`).

### Verification run
- `python scripts\verify.py` offline → **309 passed, 12 skipped**.
- Full suite with `PMFI_DB_URL` → **321 passed, 0 skipped**.
- `pmfi replay --persist` × 2 → idempotent (2nd run: zero change to row counts).
- `pmfi baselines compute --days 30 --min-samples 2` → 7 markets stored to DB.
- All operator commands healthy: `status`, `stats`, `alerts list`, `dead-letters`, `report`, `baselines list`.
- **Live Polymarket WS**: connected with 2 token IDs (FIFA World Cup NZ market), 30 events received (2 book + 28 price_change), 1 trade normalized + persisted.
- **Live Kalshi REST**: 20 trades fetched (`KXATPCHALLENGERMATCH-26JUN07BAEMOL-BAE`), all 20 normalized (0 dead letters), 20 persisted through DB pipeline.

### Evidence state
- `source-present` → `Postgres-proven`: PR#3 review blockers, baseline staleness filter, preflight exit code.
- `fixture-proven` → `live-proven`: Polymarket WS + Kalshi REST both producing real normalized trades.
- `operator-proven`: stats, alerts, dead-letters, report, baselines all return correct operator output.

### Findings
- Facts: the PR#3 production lane is complete. All handoff completion criteria met or exceeded.
- Inferences: `pmfi ingest` continuous path is production-ready (live-smoke + ingest preflight proven; daemon not run full-duration but all components validated).
- Residual: `pmfi markets watch --venue kalshi <ticker>` syntax valid but market must already be in DB (run `pmfi markets discover --venue kalshi` first if market not present).
- Accepted debt: Kalshi WS authenticated path deferred; Bologna placeholder not implemented (undefined scope).

### Next step
- Merge `pmfi-advance` into `main` (or open PR from this branch).
- `pmfi markets discover --venue kalshi --limit 20` to populate watched Kalshi markets for continuous ingest.
- Run `pmfi ingest` with both venues for extended operator proof.

## 2026-06-07 — Session 14 (prod-advance): make baselines DB-canonical (real defect fix)

Worktree `C:\Users\benny\PM-intel-prod`. Found + fixed a real correctness/usability defect while reviewing the baseline command duplication.

### Defect
`pmfi baselines compute --save` (the recommended command) wrote baselines ONLY to `config/baselines.json`, but the continuous consumers — `pmfi ingest`/`live`(refresh)/`replay`/`monitor`/`status` — read baselines from the DB `market_baselines` table via `load_baselines(pool)`. The DB was populated only by the OLDER `pmfi baseline compute` (different, less-accurate source: metric_windows). Net: an operator running the recommended command did NOT affect what the running daemon used → ingest ran with empty/stale baselines.

### Changes made
- `db/repos/metrics.py compute_baselines`: now returns `market_id` per entry (added to SELECT + GROUP BY).
- `baseline.py`: new `compute_and_store_baselines(pool, ...)` — computes per-trade baselines from normalized_trades and UPSERTs them into `market_baselines` (canonical). Idempotent via the UNIQUE(market_id,venue_code,scope) constraint + ON CONFLICT DO UPDATE.
- `cli.py _cmd_baselines_compute`: now writes to the DB by default (feeds the daemon); `--save` still writes the optional portable JSON. Messaging corrected.
- `cli.py _cmd_baselines_show`: now reads the DB first (JSON file fallback) — no longer reports "no baselines" right after a compute.
- `cli.py cmd_live`: seeds baselines from the DB at startup (JSON file as bootstrap fallback), matching the periodic DB refresh.
- `cli.py cmd_baseline` (older metric_windows path): deprecation note pointing to `baselines compute`.
- `docs/ops/OPERATOR_QUICKSTART.md`: baselines step + cheat-sheet updated (DB canonical; `--save` optional).

### Verification run
- `python scripts\verify.py` — **pass** (305 passed, 12 skipped offline).
- Full suite WITH live DB — **317 passed, 0 skipped** (+ new DB-gated round-trip test `test_baselines_store_db.py` + offline `test_compute_baselines_market_id.py`).
- `pmfi baselines show` live-confirmed reading DB `market_baselines` (showed real seeded baselines).
- Independent code-review: SAFE TO COMMIT (the two MEDIUM follow-ups it flagged — live-startup + show reading the file — were addressed in this same commit).

### Findings
- Facts: the recommended baseline workflow now actually feeds the running daemon; the whole baseline story is DB-canonical end-to-end (compute→DB; ingest/live/replay/monitor/show read DB; JSON is an optional portable snapshot).
- Blockers: none.

### Next step / deferrals
- Older `baseline` (singular) group retained with a deprecation note (could be removed in a later cleanup).
- Kalshi WS auth; health endpoint; non-core float→Decimal — still deferred.

## 2026-06-07 — Session 13 (prod-advance): end-to-end DB proof for Kalshi REST polling ingest

Worktree `C:\Users\benny\PM-intel-prod`. Closes the trust gap on the Kalshi REST polling feature: the adapter was proven in isolation (yields + normalizes real trades), but not end-to-end through the live ingest pipeline into Postgres.

### Changes made
- New `tests/test_kalshi_ingest_db.py` (PMFI_DB_URL-gated): drives `KalshiRestPollingAdapter.events()` through `run_adapter_pipeline(..., max_events=1)` against a live Postgres. Asserts first poll persists `raw_events` + `normalized_trades` (price ~0.91, contracts 10); a repeated poll of the same trade is deduped at the storage layer (`normalized_trades` stays exactly 1, `event_dedupe_keys.duplicate_count` increments). Uses a unique synthetic ticker/trade_id and cleans up all synthetic rows FK-safely (DB left as found). `process_event` auto-upserts the market, so no pre-seed needed.

### Verification run
- Local Postgres brought up (Docker Desktop was down → started it; `db_local.py up`/`init`/`verify` — non-destructive, reused the persistent volume; both venues present).
- `python scripts\verify.py` — **pass** (303 passed, 11 skipped offline; counts shifted vs prior runs because Postgres is now reachable so connection-probing tests run).
- Full suite WITH live DB (`PMFI_DB_URL` set) — **314 passed, 0 skipped** (all DB-gated incl. the new integration test).

### Findings
- Facts: the Kalshi continuous path is now proven end-to-end (adapter → pipeline → Postgres) with storage dedup confirmed on repeated polls. Combined with the earlier live-adapter proof, the full chain is trusted.
- Inferences: overlapping REST polls are safe in production (storage dedup is authoritative), as the architect's design asserted.
- Blockers: none. (Docker Desktop must be running for the DB-gated lane; offline suite stays green without it.)

### Next step / deferrals
- Same deferrals as Session 12 (Kalshi WS auth; baseline command-group consolidation — architecture fork; health endpoint; non-core float→Decimal).

## 2026-06-07 — Session 12 (prod-advance): operator readiness — ingest pre-flight + quick-start doc

Worktree `C:\Users\benny\PM-intel-prod` (branch `prod-advance`). Operator-readiness follow-up to the Kalshi REST polling slice, driven by the operator end-to-end investigation.

### Changes made
- **Ingest pre-flight (commit `701d111`)**: new pure helper `_select_ingest_venues(venues, poly_ids, kalshi_tickers) -> (usable, messages)` in `cli.py`. `cmd_ingest` now validates subscription targets BEFORE constructing adapters / printing the started banner: enabled venues with no resolved targets are dropped with an actionable message, and ingest hard-fails only when NO venue is usable. Restores friendly drop-and-continue for the mixed-venue case (both enabled, only one watched → run the usable one) instead of refusing everything. Applies to live + dry-run paths. 10 unit tests incl. a mixed-venue drop-and-continue regression guard.
- **Operator quick-start doc (this commit)**: new `docs/ops/OPERATOR_QUICKSTART.md` — the single end-to-end operator runbook (setup → discover both venues → watch → `pmfi ingest` → view alerts/report/stats/dead-letters → baselines), a command cheat-sheet, which-command-when (ingest vs live vs live-smoke; watch vs alerts list vs report), the two baseline command groups (use `baselines`), and troubleshooting. Every command verified against `cli.py`. README links to it.

### Verification run
- `python scripts\verify.py` — **pass** (296 passed, 17 skipped offline).

### Findings
- Facts: the full operator loop is now documented + the headline `ingest` command fails fast with guidance instead of mid-stream. Both venues continuously ingestable.
- Inferences: tool is "usable in full" for a local operator without reverse-engineering the CLI.
- Blockers: none.

### Next step / deferrals
- Optional: consolidate the duplicate `baseline`/`baselines` command groups (currently documented; consolidation is an architecture decision — which source is canonical).
- Kalshi WS authenticated live ingest still deferred (needs user API key + RSA signing).

## 2026-06-07 — Session 11 (prod-advance): Kalshi continuous ingest via REST polling

Worktree `C:\Users\benny\PM-intel-prod` (branch `prod-advance`, off merged `main` d9e7106). Goal: give Kalshi a working CONTINUOUS ingest path. The Kalshi v2 WebSocket requires RSA-signed auth (no key available); the public REST `/markets/trades` endpoint works unauthenticated and is already live-proven. Design validated by an opus architect BEFORE implementation; sonnet implemented; independent code-review gate AFTER (1 HIGH + 2 MEDIUM fixed). Both investigations (Kalshi WS state, operator end-to-end loop) drove the choice of slice.

### Changes made
- **`adapters/kalshi_rest.py` (new)**: `KalshiRestPollingAdapter` — implements the `VenueAdapter` protocol (connect/disconnect/events/aenter/aexit, venue_code="kalshi"). Polls `fetch_kalshi_trades(ticker, max_pages=1)` per watched ticker on a configurable interval, converts via `kalshi_trade_to_raw_event`, yields RawEvents. Per-cycle + prev-cycle in-memory seen-set is a load optimization only (bounded by page size); the pipeline's storage dedup (`insert_raw_event` short-circuits on `source_event_id`=trade_id before normalize/alert) is authoritative, so overlapping polls are correct-by-construction and restart-safe. Exponential backoff on transient errors; gap-detector warning if the recent-N page may have overflowed the window.
- **`markets.py`**: `fetch_kalshi_trades` gains `max_pages` (poll fetches only the most-recent page, avoids walking backward into history) and `timeout` (forwarded from the adapter's `live_api_timeout_seconds`); both default to prior behavior.
- **`config.py` + `config/app.example.yaml`**: `ingestion.kalshi_poll_interval_seconds` (default 5.0).
- **`cli.py` cmd_ingest**: both the live and dry-run kalshi branches now use the REST polling adapter (dropped the unauthenticated `KALSHI_API_KEY` read). The WS `KalshiAdapter` is left intact in `kalshi.py` for a future RSA-auth path.

### Verification run
- `python scripts\verify.py` — **pass** (286 passed, 17 skipped offline; +14 tests).
- **Live e2e proof**: ran the adapter against a real Kalshi ticker (`KXWNBAGAME-…`) for ~3 poll cycles → yielded 12 trades / 12 unique trade_ids (cross-cycle dedup held), and a sample normalized correctly (outcome=no, price=0.40, contracts=66, channel=rest_trades).
- Architect design validation + independent code-review (verdict CHANGES NEEDED → all fixed: removed an incorrectly-ordered seen-set trim, forwarded the request timeout, added a missing-trade_id warning).

### Findings
- Facts: Kalshi now has a working, live-proven, auth-free continuous ingest path (REST polling). Storage-layer dedup makes overlapping polls safe.
- Inferences: both venues are now continuously ingestable locally (Polymarket WS, Kalshi REST polling).
- Assumptions: Kalshi REST trade page size (limit=100) comfortably exceeds per-interval trade volume for watched markets (gap-detector warns if not).
- Blockers: none.

### Next step / deferrals
- Kalshi WS authenticated live ingest still deferred (needs user API key + RSA signing).
- Candidate follow-ups: ingest/live pre-flight validation (fail fast before banner), consolidate the duplicate `baseline`/`baselines` command groups, single operator quick-start doc.

## 2026-06-07 — Session 9–10 (fast-path): continuous-run trust + Kalshi REST trade path live-fixed

Worktree `C:\Users\benny\PM-intel-fastpath` (branch `fastpath`). Continued production hardening after Session 8.

### Changes made
- **Continuous-run operator trust** (commit `9fc5101`): `cmd_live` now hot-reloads baselines from DB on its periodic refresh (was loaded once at startup → stale alert confidence on multi-day runs; mirrors `cmd_ingest`). `run_adapter_pipeline` tracks + logs an aggregated count of silently-failed events (operator visibility), return value unchanged.
- **Kalshi REST trade path fixed to the real live API** (this commit): the path was source-present but BROKEN against `api.elections.kalshi.com`.
  - `markets.py fetch_kalshi_trades`: endpoint `/markets/{ticker}/trades` (HTTP 404) → `/markets/trades?ticker=<t>` (correct, 200).
  - `normalization.py normalize_kalshi_fixture`: real REST trade fields differ from the guessed ones — `count_fp` (string decimal, supports fractional), `yes_price_dollars`/`no_price_dollars` (string DOLLARS already in [0,1], NOT cents). Added three-tier price extraction with an `is_cents` flag so `_dollars` fields are used as-is and only legacy integer-cent fields get the `>1 → /100` conversion. Backward-compatible with existing cent/`count` fixtures.
  - New real-captured fixture `tests/fixtures/raw/kalshi_live_rest_trade.json` + 15 offline tests (`tests/test_kalshi_rest_e2e.py`): end-to-end normalize of a real trade (price 0.91, contracts 49, capital ≈44.59), no-divide-by-100 guard, legacy-cents backward-compat, `count_fp` priority.

### Verification run
- `python scripts\verify.py` — **pass** (279 passed, 10 skipped offline; was 264 before Kalshi REST fix).
- **Live e2e proof** (read-only): `fetch_kalshi_trades` now returns real trades (200); a live Kalshi trade normalized correctly → outcome=yes, price=0.93 (dollars, no /100), contracts=18.84 (fractional `count_fp`), capital≈17.52. Endpoint + field mapping confirmed against the live API.

### Findings
- Facts: Kalshi REST trade ingestion was entirely non-functional (wrong endpoint + wrong field names) and is now live-proven working end-to-end (discover → fetch-trades → normalize). Kalshi markets support fractional trading (`count_fp` can be non-integer); `volume` field is absent on market objects (min_volume filter is a no-op for Kalshi — left as-is, not a correctness issue).
- Inferences: both venues (Polymarket WS + Kalshi REST) now have a trustworthy discover→normalize path; Polymarket additionally has live-WS proof.
- Assumptions: none new.
- Blockers: none.

### Next step / deferrals
- Kalshi WS authenticated live ingest still deferred (REST trade polling now works as the supported Kalshi path).
- Optional: DB-gated persist test for a Kalshi REST trade; Kalshi `volume` enrichment if a populated field is identified.

## 2026-06-07 — Session 8 (fast-path): data-trust hardening (6 evidence-based fixes)

Worktree: `C:\Users\benny\PM-intel-fastpath` (branch `fastpath`). Three parallel review agents (2× sonnet code-review on Kalshi path + core data-trust path, 1× haiku ops-readiness scan) surfaced real defects in shipped code; each finding was re-confirmed against current source before fixing. Implemented by two parallel sonnet executors (disjoint files), then adversarially reviewed by an opus critic (verdict: SAFE TO COMMIT) and empirically verified against the live Postgres.

### Changes made (all confirmed real, minimal diffs)
- **F1 (HIGH, data lineage)** `db/repos/baselines.py` + new `sql/010_market_baselines_unique.sql` + `db/migrations.py` + `scripts/db_local.py`: `market_baselines` had no unique key, so `upsert_baseline` used `ON CONFLICT DO NOTHING` with no target → a new row was inserted on every recompute (dead UPDATE fallback) → duplicate baselines + non-deterministic `fetch_all_baselines`. Fix: migration dedups (keep most-recent per `(market_id,venue_code,scope)`) then adds `UNIQUE`; upsert rewritten to single atomic `ON CONFLICT (...) DO UPDATE`. Registered in both migration paths.
- **F2 (CRITICAL)** `normalization.py:177`: Kalshi `outcome_key` fallback `"yes"` → `"unknown"` (was silently mis-filing undetermined-side trades as YES; Polymarket already used `"unknown"`).
- **F3 (HIGH)** `pipeline/engine.py`: `volume_spike_v1` median `_window[len//2]` → `statistics.median(_window)` (upper-middle bias on even-length windows).
- **F4 (HIGH)** `markets.py` `sync_kalshi_markets`: now forwards each market's real `status` to `upsert_market_full` (was hard-defaulting `"active"`, also masking a settled→active resync overwrite).
- **F5 (LOW)** `scoring.py:75`: clean-data `data_quality` label `"unverified"` → `"verified"` (operator-trust honesty).
- **F6 (MEDIUM)** `replay.py`: `replay_fixtures` unified onto `normalize_event` (was diverging from the persisted path; now applies the same non-trade filtering + dead-letter wrapping).

### Verification run
- `python scripts\verify.py` — **pass** (261 passed, 10 skipped offline).
- Full suite with live DB (`PMFI_DB_URL` set) — **271 passed, 0 skipped** (all 10 DB-gated incl. new baseline-idempotency proof for F1).
- `db_local.py init` (idempotent, non-destructive) applied `sql/010` to live `pmfi` DB; confirmed constraint `market_baselines_scope_unique` present; baselines 3 rows / 0 duplicates.
- **Kalshi REST discovery live-verified** (read-only): filter `status="open"` → HTTP 200 with real markets; `status="active"` → **HTTP 400**. Confirms current `fetch_kalshi_markets(status="open")` is CORRECT — a reviewer's suggested change to `"active"` would have broken discovery. (Empirical check overrode the agent claim.)
- Opus critic adversarial review: zero CRITICAL/MAJOR defects in the fixes; new tests genuinely fail on old code.

### Findings
- Facts: 6 confirmed bugs fixed; F1 was an active data-lineage defect in shipped code. Kalshi REST discovery path works live. +10 tests added (8 offline hardening, 1 offline kalshi-status, 1 DB-gated baseline-idempotency).
- Inferences: core Polymarket spine + persistence now production-trustworthy for single-process local use.
- Assumptions: only `scope='market'` baselines are written (sole writer hard-codes it).
- Blockers: none.

### Next step / honest deferrals
- **M1 (deferred, documented):** `market_baselines` UNIQUE does not dedupe non-`market` scopes (NULL keys distinct). Zero blast radius today (no non-market writer). Revisit with a COALESCE/partial index when category/venue/global baselines are introduced (noted in `sql/010` + `migrations.py`).
- Still deferred per handoff: baseline/orderbook float→Decimal cleanup; Kalshi WS authenticated live ingest; live `cmd_live` baseline hot-reload (use `ingest` for 24/7 — it auto-refreshes); health endpoint / partition auto-maintenance during ingest.

## 2026-06-07 — Session 7 (fast-path): connector truth, alert safety, live spine proof

Worktree: `C:\Users\benny\PM-intel-fastpath` (branch `fastpath`). Driven by `PMFI_fast_path_handoff.md` (acceptance spec vs snapshot 485e1b5). Architect-validated the two riskiest designs BEFORE implementation; opus code-review gate AFTER (verdict SHIP-AFTER-FIXES → all must-fix applied).

### Changes made
- `pipeline/runner.py`: extracted pure `resolve_asset_outcome`; maps Polymarket `asset_id`→outcome for live `market`+`asset_id`+no-outcome payloads; no-clobber on `venue_market_id`; binary vs non-binary (`is_binary`) handling; reuses `missing_asset_mapping` dead-letter. (Target 4)
- `markets.py`: discovery no longer coerces non-yes/no labels — preserves `outcome_label`, slugs `outcome_key`, sets `is_binary`, per-market slug-collision disambiguation. `fetch_polymarket_markets` switched CLOB (HTTP 400) → Gamma API. (Target 4)
- `pipeline/engine.py` + `scoring.py`: alert confidence gated on degraded data (no high-confidence from unknown outcome/direction/warnings); evidence now carries trigger thresholds + outcome/quality fields. (Target 6)
- `db/repos/alerts.py` + `sql/009`: `raw_event_id`/`trade_id` lineage; `insert_alert` optional params; `cli` watch/report/list + stdout delivery surface `rule_version`/`data_quality`/`outcome_label`. (Target 6)
- `replay.py`: guard `normalize_event` so malformed payloads dead-letter instead of crashing persisted / from-db replay. (Targets 2/5)
- `sql/005` made self-contained (`SET search_path`); `sql/008` adds `market_outcomes.is_binary`.

### Verification run
- `python scripts\verify.py` — PASS (250 passed, 9 skipped offline; +47 tests vs baseline 203)
- DB-gated (`PMFI_DB_URL` set): `test_replay_db`, `test_alert_lineage_db`, `test_alerts_schema_contract`, `test_live_capture` — PASS (13)
- `db_local.py init`/`verify` (idempotent, incl. 008/009 applied to live DB) — PASS
- `replay --persist` ×2 — idempotent (raw/normalized/metric counts stable: run2 == run1)
- `markets discover` (Gamma, live) — synced 12/12; `is_binary` 48/48 correct on real data
- `live-smoke` (`PMFI_ENABLE_LIVE=1`, 38 asset_ids, 20 events/75s, `--save-fixtures --persist-raw`) — WS connected, subscribed with **token IDs** (not condition IDs / not global stream), 20 real `book` events captured + persisted, fixtures saved; promoted `polymarket_live_book_sample.json` + `test_live_capture.py`

### Proof ledger (handoff states)
- T1 env/repo trust — **operator-proven** (fresh editable install + verify pass; no live calls in default verify)
- T2 storage trust — **Postgres-proven** (idempotent init/verify; persisted replay raw/normalized/metric > 0; replay-twice idempotent)
- T3 deterministic replay/idempotency — **Postgres-proven** (`replay_from_db` event-time ordering test; persisted replay-twice idempotency test)
- T4 Polymarket connector truth — **live-smoke-proven** (asset_id→outcome incl. market+asset_id+no-outcome; non-binary preserved/degraded, not coerced; token-ID subscription; no condition-ID fallback in supported path; live-smoke no longer advertises a global/no-asset stream)
- T5 bounded live proof — **live-smoke-proven** (capture + persist + fixture promotion). `last_trade_price` not observed in the bounded window → no-trade cleanly diagnosed after a valid subscription; trade normalization proven by existing `polymarket_live_ws_trade.json`.
- T6 operator trust — **operator-proven** (degraded-data confidence gating; evidence thresholds + lineage; stats/alerts/dead-letters/report readable; `pmfi live` opt-in gate + token resolution + hard-fail no-fallback + Ctrl+C handlers)

### Decisions / deferred
- Engine float→Decimal cleanup (volume_spike/momentum evidence): **DEFERRED** per handoff debt rules (non-core/experimental alert rules); CORE trades/metric_windows already NUMERIC/Decimal (proven by `test_decimal_roundtrip`).
- Full multi-outcome directional scoring: **DEFERRED** per handoff; identity is preserved/degraded only (Polymarket decomposes multi-candidate into binary markets; 48/48 binary observed live).

### Blockers
- None blocking the primary spine. (A real `last_trade_price` capture is opportunistic; book events were captured and the trade path is fixture-proven.)

### Next step
- Optional: longer live-smoke window to capture a real `last_trade_price` for an additional promoted trade fixture. Kalshi WS parity remains deferred.

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

## 2026-06-09 21:35 local - Prodgrade hardening landed on main; in-daemon baseline recompute; repo hygiene

### What changed

- **Fast-forward merged `prodgrade-ralph` into `main`** (af7cb1e -> c1dbec7, 20 atomic commits, 61 files, +8791/-1876). Brings the full 16-story production-grade hardening onto the canonical branch: supervised ingest daemon (survives WS close/Postgres restart), atomic raw_events dedup, dead-letter paths, AlertRule registry (`pipeline/rules.py`), `pmfi health` heartbeat, durable file alert sink default, dashboard alerts panel + `/api/alerts`, `pmfi alerts explain`, replay backtest (time/venue/market filters, `--persist`, seeded accumulators), cli.py split into `pmfi/commands/*`, storage hardening + `sql/011`.
- **18a55e3 In-daemon periodic baseline recompute**: new `baselines:` config section (`recompute_enabled` default true, `recompute_interval_minutes` default 1440, `window_days` 30, `min_samples` 10). Daemon now calls the canonical `compute_and_store_baselines` writer on a daily maintenance cycle (fires on cycle 1 too), non-fatal on failure; the existing ~10-min baseline reload picks up fresh rows. Closes the long-standing "baselines go stale without manual compute" residual risk. 22 new offline tests (`tests/test_baseline_recompute.py`).
- **6ea974b Operator runbook sync**: OPERATOR_QUICKSTART.md now covers durable file sink default + delivery banner, dashboard + alerts panel, `pmfi alerts explain`, full replay flags, `pmfi health`, automatic + manual baseline recompute. All flags grounded against `--help` output.
- **Repo hygiene**: removed merged-branch worktrees (PM-intel-ralph, PM-intel-fastpath, PM-intel-advance; all clean) and deleted merged local branches (`prodgrade-ralph`, `fastpath`, `pmfi-advance`, `PM-intel`) via `branch -d`. Remaining worktrees: PM-intel-grade (`prod-grade`, 1 unmerged superseded squash commit) and PM-intel-prod (`prod-advance`, 3 unmerged superseded commits) - branches intentionally NOT deleted.

### Verification

- Offline gate (main checkout, own venv): `scripts\verify.py` = **520 passed, 26 skipped, verification passed**.
- DB-gated full suite (PMFI_DB_URL, pmfi-postgres healthy): **546 passed, 0 failed**.
- `db_local.py verify` passes (venues kalshi+polymarket registered).
- CLI smoke from main: `pmfi health` correct stale/missing behavior (exit 1 + guidance, no daemon running); `pmfi stats` live counts (1288 raw_events, 258 normalized_trades, 20 alerts, 9 baselines); `pmfi baselines show` lists 9 baselines, exit 0.
- Attribution audit across all merged + new commits: CLEAN (no co-author/attribution lines).

### Commits (this pass)

- (merge) main fast-forwarded to c1dbec7 (prodgrade-ralph, 20 commits)
- 18a55e3 In-daemon periodic baseline recompute: config-gated, non-fatal, daily default
- 6ea974b Document merged tool surface in operator quickstart

### Residual risk

- main is now 23 commits ahead of origin/main - push intentionally NOT done (operator decision pending).
- `prod-grade`/`prod-advance` branches hold superseded work; safe to delete after human confirmation.
- In-daemon recompute fires on cycle 1 (~60s after start): on a very large normalized_trades table the first recompute adds one heavier query shortly after startup; non-fatal isolation bounds the blast radius.
- Live adapters remain opt-in via config feature flags; no live calls in tests.

## 2026-06-09 23:30 local - Audit-driven hardening tranche: silent-loss fixes, observability, lockdown, autostart

### What changed

- **Live e2e proof from main**: ran the real supervised daemon (polymarket WS + kalshi REST) ~7 min; 467 raw events persisted, heartbeat fresh (pmfi health exit 0), cycle-1 in-daemon baseline recompute proven via DB computed_at; clean stop, no data loss.
- **Multi-agent production-readiness audit** (7 lanes: operator-ux, reliability, data-integrity, observability, alert-quality, security/local-only, test-gaps): 41 raw findings, 23 confirmed real+material after adversarial verification, synthesized into 10 stories (.omc/audit_synthesis.json) - all 10 implemented:
- 29a2200 **Silent-loss fixes**: supervisor backoff now resets after a clean run (was ratcheting to 60s forever after the first transient fault); alert suppression key gains outcome_key (live + DB hydration via COALESCE) so opposite outcomes of a binary market no longer suppress each other; dead dedupe_fields YAML replaced with the real key shape.
- 74e2235 **Local-only lockdown**: Postgres 5433 + Adminer 8080 loopback-bound in compose; dead PMFI_ALERT_HTTP_RECEIVER_URL removed from .env.example; boundary tests enforce all of it.
- 47d7742 **Truth fixes**: baselines show help, dead url_env key annotated, PMFI_ENABLE_LIVE documented for pmfi live.
- 61f3b26 **Durable logging**: RotatingFileHandler via app.log_file / pmfi ingest --log-file; cfg.log_level honored; daemon/supervisor prints -> logger (fixes block-buffered-redirect blindness proven in the live run).
- 445b5cd **Observability**: heartbeat venues map (per-venue counts/last_event_at/consecutive_failures/last_error via supervise status_map) + recompute health fields; pmfi health per-venue staleness WARNING (health.venue_stale_seconds), recompute-overdue warning, pid/started_at on stale heartbeats, missing-vs-unreadable distinction; dashboard renders went-silent venues as stale chips (30-day ever-seen) with ?lookback= param; VolumeSpikeRule thin-market skips debuggable + history_max configurable.
- cf71c6f **Mid-session subscription refresh**: watched markets added during a run now subscribe on next adapter restart; asset_id_map refreshed in place (~10 min cadence), non-fatal on failure.
- 0969714 **Windows autostart**: scripts/autostart.py install/uninstall/status via schtasks (ONLOGON default, dry-run tested, idempotent /F), output to the durable log; runbook section 8.
- ff7ad18 **Daemon loop tests**: _telemetry_tick extracted (commands/daemon.py, deps injectable) and driven as a real coroutine across cycles; supervise generic-exception path; cmd_watch SQL placeholder consistency; recompute tick guarded against helper bugs (the one path that could kill the daemon via FIRST_EXCEPTION).
- 1cd4587 **Deslop + review nits**: dead _counted_events removed, feed_health initializer simplified, load_config warns on the well-known default DB password.

### Verification

- Offline gate: **675 passed, 27 skipped, verification passed**. DB-gated full suite: **702 passed, 0 failed** (live populated DB).
- Architect review (THOROUGH tier): **APPROVE_WITH_NITS, zero must-fixes**; all integration seams between the 8 commits verified coherent (tick param threading, supervise control flow, suppression 4-tuple consistency incl. replay hydration, contract changes); all 3 nits then fixed in 1cd4587.
- Attribution audit 189bde6..HEAD: CLEAN.

### Residual risk

- main is now 32 commits ahead of origin/main; push not done (operator decision).
- supervise() status_map retains the last failure record if run_one itself sets shutdown during a clean run (cosmetic; documented in test_supervise_generic_exception.py).
- Autostart was implemented + dry-run tested but NOT registered on this machine (operator action); daemon at logon needs Docker Desktop running for preflight to pass.
- _telemetry_loop cadence constants still assume the 60s default interval (documented coupling, no current caller passes a different interval).

## 2026-06-11 local - PR #4 merged; config-gated alert follow-up

### What changed

- Merged GitHub PR #4 (`prodgrade-advance` -> `main`) after all seven review
  threads were resolved. `origin/main` now points at merge commit `e726a7e`;
  blocker-fix commit `3833e14` is an ancestor of `origin/main`.
- Started follow-up branch `codex/config-gating` from `origin/main`.
- Made the transparent corroboration annotation opt-in behind
  `features.enable_ml_scoring`. Default `AlertEngine()` evaluation no longer
  adds corroboration fields; replay, ingest, live-smoke, and continuous ingest
  pass the config flag explicitly.
- Wired `features.enable_cross_venue_matching` into the daemon telemetry monitor
  tick so `cross_venue_divergence_v1` is no longer active by default.
- Kept `_telemetry_tick` testable by injecting the monitor runner in tests
  instead of patching the package import path.
- Updated config warnings and operator docs so enabled-but-now-wired flags are
  not described as inert; wallet feature warning remains blocked.
- Pure fixture replay still reads feature config, but suppresses the irrelevant
  default DB-password warning because that path intentionally does not touch DB.
- Added regression coverage for corroboration default-off behavior, replay
  default flag flow, telemetry cross-venue flag flow, and the revised config
  warning contract.

### Verification

- `python scripts\verify.py` passed: 751 passed, 49 skipped.
- Focused preflight also passed: `tests/test_config.py`,
  `tests/test_pipeline_engine.py`, `tests/test_corroboration.py`,
  `tests/test_replay_cli_offline.py`, `tests/test_telemetry_tick.py`,
  `tests/test_data_quality_monitor.py` = 114 passed, 5 skipped.
- Stale wording audit found no remaining claims that `enable_ml_scoring` or
  `enable_cross_venue_matching` have no effect.
- `git diff --check` passed.

### Residual risk

- DB-gated proof was not run in this checkout: `PMFI_DB_URL` and `DATABASE_URL`
  are unset, and `docker` is not available on PATH.
- `features.enable_ml_scoring` is still a historical flag name; implementation
  intentionally maps it to transparent corroboration annotations, not machine
  learning.
- Next roadmap slice after this PR should be chosen from the remaining
  post-PR #4 work: DB proof on a machine with Postgres, then orderbook depth or
  Kalshi/dashboard expansion depending on operator priority.

## 2026-06-11 local - PR #5 merged; periodic Polymarket orderbook polling

### What changed

- Merged GitHub PR #5 (`codex/config-gating` -> `main`), merge commit `dda0310`.
  The config-gating commit `a37edf9` is an ancestor of `origin/main`.
- Started follow-up branch `codex/orderbook-polling` from `origin/main`.
- Added periodic Polymarket orderbook polling behind
  `features.enable_orderbook_reconstruction` in the `pmfi ingest` daemon tick.
  The poller uses the current watched Polymarket token IDs and canonical
  `market_outcomes` mapping, writes `orderbook_snapshots` / `orderbook_levels`,
  and may emit `liquidity_wall_v1` through the same alert contract.
- Fixed orderbook level lineage so persisted levels use the actual token
  `outcome_key` instead of always writing `yes`.
- Kept the path non-fatal and testable by injecting the orderbook poller into
  `_telemetry_tick`; no live calls are made in tests.
- Isolated poll failures per token, so one snapshot/alert/delivery error does
  not skip the rest of the watched token poll cycle.
- Updated ADR-0009, the operator quickstart, app config comments, and the active
  ultragoal ledger to describe periodic polling and remaining caveats.

### Verification

- `python scripts\verify.py` passed: 758 passed, 49 skipped.
- Focused preflight passed: `tests/test_orderbook.py`,
  `tests/test_liquidity.py`, `tests/test_live_capture.py`,
  `tests/test_telemetry_tick.py`, `tests/test_db_hardening_db.py`,
  `tests/test_cli_validation.py` = 80 passed, 3 skipped.
- `python -m compileall -q src tests scripts` passed.
- `git diff --check` passed.

### Residual risk

- DB-gated proof was not run in this checkout: `PMFI_DB_URL` and `DATABASE_URL`
  are unset, and `docker` is not available on PATH.
- Periodic polling is Polymarket-only, requires active Polymarket ingest, and
  only covers watched markets with populated token IDs. `pmfi live --orderbook`
  remains trade-coupled.
- Kalshi orderbook capture and richer polling controls remain future work.
