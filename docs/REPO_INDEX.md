# PM-intel (PMFI) — Repository Index & State Report

> **Provenance:** orchestrator assessment, 2026-06-20, against `origin/main` @ `0368f33d1e283ccfb67352fa8cb6cca6a53a6588` (clean). Gates independently re-run: offline `scripts/verify.py` = **1090 passed / 37 skipped** ("verification passed"); `scripts/db_local.py verify` = schema readiness passed (`pmfi-postgres` on `127.0.0.1:5433`; the 37 skips are `PMFI_DB_URL`-unset, not a down DB). Built from a 10-agent source-grounded assessment; every non-trivial claim is anchored to `file:line`. This is the canonical index — `.omc/ultragoal/plan.md` statuses were stale and have been reconciled against it.

## 1. System overview
PMFI is a **Windows-native, local-only** prediction-market flow-intelligence pipeline. It captures public market events from Polymarket (WebSocket) and Kalshi (REST polling), preserves raw payloads before deriving anything, normalizes trades, computes rolling baselines, emits explainable local anomaly alerts, and exposes operator review/replay/dashboard workflows. Hard scope fence: **no hosted/SaaS, no trading/order execution**; the only network posture is read-only capture.

**End-to-end data path:**
`venue adapter → RawEvent → insert_raw_event (dedup) → normalize_event → insert_trade (dedup) → upsert_metric_window → AlertEngine.evaluate → insert_alert (+ lineage) → delivery (file/http/stdout) → dashboard / alerts review / replay`

## 2. Architecture & module map
Clean layered design with frozen dataclasses (`RawEvent`, `NormalizedTrade`, `AlertDecision`) as inter-layer contracts.

| Layer | Modules |
|---|---|
| Domain | `domain.py` (RawEvent/NormalizedTrade/AlertDecision; VenueCode/Side/Confidence) |
| Adapters | `adapters/base.py` (VenueAdapter protocol, FixtureAdapter); `adapters/polymarket.py` (WS); `adapters/kalshi_rest.py` (REST polling, in use); `adapters/kalshi.py` (WS+RSA, **orphaned/unused**) |
| Normalize | `normalization.py` (parse_decimal/ts/price, make_trade); `pipeline/normalize.py` (venue dispatcher) |
| Pipeline | `pipeline/engine.py` (AlertEngine, rule registry, baselines); `pipeline/rules.py` (AlertRule protocol + 6 rules); `pipeline/accumulator.py` (DirectionalAccumulator); `pipeline/runner.py` (process_event, suppression, asset resolution); `pipeline/supervisor.py` (PoolManager, supervise loop) |
| Delivery | `delivery/http.py` (localhost:8765); `delivery/file.py` (JSONL); `delivery/stdout.py`; `delivery/server.py` (alert receiver) |
| DB repos | `db/repos/{raw_events,trades,markets,alerts,baselines,metrics,dead_letters,orderbook}.py`; `db/migrations.py` |
| Discovery | `markets.py` (Polymarket Gamma + Kalshi REST catalog → markets table) |
| Commands | `commands/{ingest,reporting,alerts,markets,dashboard,daemon,soak,review_pass,_shared}.py` |
| Config/CLI | `config.py` (App/Database/Features/Ingestion/AlertsConfig); `cli.py` (40+ subcommands) |
| Dashboard | `dashboard/server.py`, `dashboard/queries.py` |
| Schema | `sql/` (13 migrations: schema, partitions, views, dedup guards, lineage) |

**Modularity verdict:** adapters and delivery are cleanly isolated (only depend on contracts); rules are protocol-based and extend without touching the eval loop; DB repos are focused (no mega-DAOs). **Weak seam:** venue identity is a first-class conditional sprayed through the core — `pipeline/normalize.py:16-31` if/elif dispatch, plus `pipeline/runner.py:72,139,223,254` hard-coded `venue_code=='polymarket'` guards and `resolve_asset_outcome` (`runner.py:64-120`). Adding a third venue touches 4+ files. `_alert_outcome_key` (`runner.py:47-61`) hard-codes rule names.

## 3. Data lineage & storage
Raw-before-derived is enforced: `insert_raw_event` is atomic-dedup via `event_dedupe_keys` (ON CONFLICT + backfill); `insert_trade` dual-guards via `normalized_trade_dedupe_keys` (venue_trade_id OR fingerprint). `upsert_metric_window` is idempotent (ON CONFLICT DO UPDATE). Partitioned monthly: `raw_events`, `normalized_trades`, `metric_windows`, `market_snapshots`, `orderbook_snapshots`. Replay determinism via `engine.seed_from_db()` pre-warming accumulators.

**Gaps:** retention is **warn-only** (`drop_old_partitions` exists at `migrations.py:79` but is manual-only; daemon `daemon.py:146-155` only logs). Alert lineage cols (`raw_event_id`, `trade_id`) are **non-FK** (raw_events is partitioned) → dangling-reference risk once retention drops partitions (`sql/009_alert_lineage.sql`). Orderbook levels insert is **non-atomic** per snapshot (`orderbook.py:39-80`). Accumulator state lost on restart (in-process; reseeded from DB on start).

## 4. Ingest & adapter reliability
Production-grade supervisor: `supervise()` + `PoolManager` (generation-tracked safe pool recreation), jittered backoff 1–60s, per-venue heartbeat with went-silent detection (`health.py`), dead-letter capture, 5-consecutive-failure threshold. Kalshi runs via REST polling (gap detection warnings). Asset-id map refreshed ~10 min in the telemetry loop.

**Top fragility (see §11 #1):** Polymarket WS subscription is **fire-and-forget** (`polymarket.py:114-119`, no ACK/timeout) and consumes with `async for msg in ws` **with no receive timeout** (`polymarket.py:120`) — a silently-rejected subscription or quiet venue yields zero data while looking healthy. Connection-loss classification **excludes** `OSError`/`asyncio.TimeoutError` (`runner.py:322-326`). `kalshi.py` WS adapter is complete but **unused/ungated**. Kalshi REST poll-window overflow is warned but not mitigated.

## 5. Alert engine & quality
6 rules (LargeTradeAbsolute, MarketRelative, OpenInterestShock, DirectionalCluster, Momentum, VolumeSpike) via protocol; suppression keyed `(venue, market, rule, outcome)` with config-driven `suppression_window_seconds`; suppression cache reseeded from the durable `alerts` table at startup (`alerts.py:821-848`, `runner.py:344-351`) → **single-daemon restart does not duplicate**. Evidence JSONB + `alert_reviews` (tp/fp/noise) + triage flags + calibration packets.

**The product-truth gap (see §11 #6):** `cmd_alerts_fp_rate` only prints a table and returns 0 — **no per-rule FP threshold in `config/alert_rules.yaml`, no recommendation engine, no closed loop** from measured FP to rule tuning. Evidence lacks decision-margin / baseline-freshness, so operators must re-derive why an alert fired. Multi-outcome Polymarket tokens persist `outcome_key='unknown'`, weakening per-outcome suppression.

## 6. Operator UX & functionality
Comprehensive CLI (40+ subcommands). Key surfaces:

| Group | Commands |
|---|---|
| Ingest | `ingest` (live daemon; `--dry-run`,`--max-seconds`,`--log-file`), `monitor` (fixture replay) |
| Markets | `discover`, `list`, `sync-one`, `recent-trades`, `refresh-watchlist`, `watch`, `unwatch` |
| Alerts | `list`, `explain`, `review`, `review-packet`, `outcome-audit`, `fp-rate`, `serve` |
| Calibration | `volume-spike-calibration`, `calibration-packet-batch`, `-decision`, `-review-queue`, `-cluster-review(-summary)` |
| Baselines | `compute`, `show` (+ in-daemon recompute) |
| Replay | `replay`, `--from-db`, `--persist` |
| Ops | `status`, `health`, `db-verify`, `db-maintenance`, `soak`, `watch` (live TUI), `autostart` (schtasks) |

Health via atomic heartbeat (per-venue staleness). Dashboard = multi-panel alert/calibration browser. `OPERATOR_QUICKSTART.md` is authoritative. **Friction:** ~30% of commands lack purpose help; start-up errors don't point to `--help`; `monitor` (fixture) vs `ingest` (live) easily confused; evidence fields not self-documenting.

## 7. Tests & verification
1090 passing / 37 DB-gated-skipped offline; `asyncio_mode=auto`. Strong: telemetry-loop, supervisor, normalization, type-safety. Critical loops (`test_telemetry_tick.py`, `test_ingest_supervisor.py`, `test_cmd_watch.py`) covered with mocks. **Gaps:** WS adapters are 100% stubbed (no offline message-shape contract for G002/G006); E2E DB test is a single shallow case; DB-gated replay/baseline coverage thin; fixtures (2026-06) not re-validated against current parsers. *(Note: an assessor's "4 collection errors" claim was rejected by the critic — the gate is green and the `scripts` modules import fine; treat as an environment artifact, not a main-branch defect.)*

## 8. Security & local-only boundary
Strong default posture: `docker-compose.local.yml` binds Postgres + Adminer to `127.0.0.1`; dashboard forces loopback (`dashboard/server.py:901-903`) + origin validation; default-password warning (`config.py:83-87`); credentials never logged. **Gaps (see §11 #4):** the **alert receiver** `run_alert_receiver` passes operator `--host` straight to `web.TCPSite` with no loopback guard (`delivery/server.py:9,30`, `cli.py:1535`) — `pmfi alerts serve --host 0.0.0.0` would expose the alert stream to LAN; dashboard `--db-url` override is unvalidated.

## 9. Scalability & non-fragility
Foundational controls present: asyncpg pool (min1/max10), monthly partitions + 90d retention policy, multi-level dedup, jittered backoff. **Risks under load / months of runtime (see §11 #2,#5):** `DirectionalAccumulator` buffers are **unbounded** per market (`accumulator.py:58-59`) → heap bloat at 1000+ markets; partition creation is startup/cycle-only (gap-on-crash risk); **no circuit breaker** — `supervise()` retries forever (`supervisor.py:186-241`); pool size hardcoded; baseline recompute can block ingest under load; replay is serial.

## 10. Progress vs durable goal

| Lane | Status | Evidence anchor |
|---|---|---|
| G001 suppression window | ✅ done | `runner.py:292`, `config.py:40` |
| G003 market discovery REST→markets | ✅ done | `markets.py`, `commands/markets.py:157` |
| G004 `pmfi ingest` daemon | ✅ done | `cli.py:624-1110` |
| G007 partition + retention (prune manual) | ✅ done | `db/migrations.py`, `daemon.py:146` |
| G008 HTTP loopback delivery | ✅ done | `delivery/http.py`, `cli.py:795` |
| G009 orderbook capture at trade time | ✅ done | `orderbook.py`, `runner.py:254-274` |
| G010 watch-list management | ✅ done | `commands/markets.py:631`, `sql/005` |
| G002 Polymarket WS live proof | ⏸ deferred (live) | needs opt-in network |
| G005 normalization audit vs live | ⏸ deferred (live) | needs live capture |
| G006 Kalshi WS proof | ⏸ deferred (live) | needs network + API key |

Audit tranche US-09..US-18 confirmed ancestors of `0368f33`. **Feature lanes are complete**; the binding constraint is alert-truth + unattended durability, not feature breadth.

## 11. Ranked critical gaps (source-verified, adversarially ranked)

| # | Risk | Type | Sev | Anchor |
|---|---|---|---|---|
| 1 | Polymarket WS can't detect silent subscription-reject / quiet venue (no receive timeout) | fragility | **critical** | `polymarket.py:114-146` |
| 2 | Raw retention never auto-enforced (warn-only) → disk exhaustion over time | inadequacy | high | `daemon.py:146-155` |
| 3 | No venue seam — adding/fixing a venue needs scattered edits | underspec | high | `runner.py:64-120,139,223,254` |
| 4 | Alert receiver binds `--host` with no loopback guard (LAN exposure) | inadequacy | high | `delivery/server.py:9,30` |
| 5 | No circuit breaker (retry-forever) + unbounded accumulator memory | fragility | high | `supervisor.py:186-241`, `accumulator.py:58-59` |
| 6 | Alert-quality loop open — FP-rate measured but never feeds tuning | underspec | high | `commands/alerts.py` fp_rate |
| 7 | Alert lineage non-FK refs dangle once retention drops partitions (compounds #2) | fragility | medium | `sql/009_alert_lineage.sql` |

## 12. Recommended next slices & ownership

**Synthesis:** the product is feature-complete; the highest-ROI work is making the existing alert stream *trustworthy* and the daemon *survivable*, then the venue seam, then live proofs against a trustworthy baseline. Chasing the headline live-WS gaps first is backwards — live alerts you can't measure, from a daemon that bloats and re-floods, is a demo not a tool.

**10 ROI-ranked overarching goals:**

| # | Goal | Horizon | Gating | Owner |
|---|---|---|---|---|
| 1 | Alert-truth feedback loop (per-rule FP thresholds + breach detection) | short | offline | M-TRUTH (orchestrator+operator) |
| 2 | Unattended durability (opt-in retention prune, partition-ahead, bounded accumulator) | short | offline | **M-DUR (Codex)** |
| 3 | ~~Suppression DB persistence~~ — dropped (already reseeded from DB) | — | — | — |
| 4 | Operator explainability (margin-to-threshold + baseline-freshness in evidence) | short | offline | M-TRUTH |
| 5 | Loopback closure on alert receiver + `--db-url` validation | short | offline | **M-DUR (Codex)** |
| 6 | Venue extensibility seam (VenueAdapter registry) | mid | offline | M-SEAM (unassigned) |
| 7 | G002 Polymarket WS live proof (subscription-ack + receive timeout) | mid | live | M-LIVE |
| 8 | G005 normalization audit vs freshly-captured live schemas | mid | live | M-LIVE |
| 9 | G006 Kalshi WS decision (retire-via-flag or integrate) | mid | live | M-LIVE |
| 10 | Test-integrity + CLI-discoverability cleanup | long | offline | unassigned |

**Active milestone ownership (2026-06-20):**
- **M-DUR** — *Unattended durability & live-capture integrity* (risks #1,#2,#4,#5,#7; offline detection portion of the WS work): delegated to **Codex**, branch `codex/durability-integrity`, worktree `worktrees/codex-durability`. Sub-lanes SL-1..SL-5 in `state/agent-inbox/for-codex.md`. Retention prune is **opt-in/default-off** (honors no-deletion).
- **M-TRUTH** — *Alert-truth* (goals 1,4): **orchestrator** + operator labeling judgment; read-only on live DB. Output: measured per-rule FP baseline + recommended thresholds + labeled-ready review packet.
- **M-SEAM**, **M-LIVE** — unassigned; sequenced after a trustworthy baseline exists.
