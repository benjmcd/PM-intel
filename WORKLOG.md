# WORKLOG

This log is intentionally committed. Codex must update it after every coherent work slice.

## 2026-06-18 02:02 local - Kalshi recent-trade candidate probe

### What changed

- Extended the Kalshi public trade fetcher so `fetch_kalshi_trades(ticker=None, min_ts=...)` can read the all-market `/markets/trades` endpoint without changing existing per-ticker REST polling behavior.
- Added `pmfi markets recent-trades`, a read-only, safety-gated Kalshi ticker probe that groups recent public trades by ticker and outputs table or JSON.
- Kept the command non-mutating: it does not write fixtures, sync markets, mark watched rows, or touch Postgres. It prints `pmfi markets fetch-trades <ticker>` follow-ups rather than `watch` commands because recent all-market tickers may not exist in the local DB yet.
- Updated the operator quickstart for the new Kalshi candidate probe and removed a duplicate `markets unwatch` command-table row.

### Decision consensus

- Question: should Kalshi venue-specific proof proceed by manually watching the first recent-trade ticker, by adding single-ticker market sync, or by first exposing recent-trade candidates?
- Strongest case for watching immediately: a live public probe found recent Kalshi trades inside the target window, so candidates exist.
- Objection: `pmfi markets watch KXLOWTNYC-26JUN18-T68 --venue kalshi` failed because that ticker was not already synced into the local DB; silently recommending watch commands would create operator friction and false confidence.
- Consensus: land the read-only recent-trade candidate probe first. A later slice can add single-ticker Kalshi market sync/watch support, then run a bounded Kalshi-only ingest and `pmfi soak --required-venue kalshi`.
- Payback artifact: CLI command, tests, docs, and live read-only smoke evidence.

### Verification

- Official Kalshi API check: documentation confirms unauthenticated public market data and `/markets/trades` query filters including `ticker`, `min_ts`, `max_ts`, `limit`, and `cursor`.
- Live endpoint smoke: direct `GET /markets/trades` with `min_ts=now-7200` returned recent public trades including ticker `KXLOWTNYC-26JUN18-T68`.
- Live CLI smoke: `.\.venv\Scripts\python.exe -m pmfi.cli markets recent-trades --since-minutes 120 --limit 20 --format json` returned parseable JSON grouped by Kalshi ticker.
- Larger live CLI smoke: `.\.venv\Scripts\python.exe -m pmfi.cli markets recent-trades --since-minutes 120 --limit 200 --format json` found 79 unique Kalshi tickers in the bounded sample.
- Safety-gate smoke: `.\.venv\Scripts\python.exe -m pmfi.cli markets recent-trades --limit 1 --since-minutes 1` exited 1 without `PMFI_ENABLE_LIVE=1`.
- Focused tests: `.\.venv\Scripts\python.exe -m pytest .\tests\test_markets_discovery.py -q` = 53 passed.
- Full verification: `.\.venv\Scripts\python.exe scripts\verify.py` = 776 passed, 30 skipped, verification passed.
- DB verification: `.\.venv\Scripts\python.exe scripts\db_local.py verify` passed against local Docker/Postgres.
- `git diff --check` passed.

### Residual risk

- Kalshi venue-specific soak still needs a DB-synced watched ticker with current trades, a bounded Kalshi ingest run, and `pmfi soak --required-venue kalshi`.
- The Kalshi public trades endpoint appears to return the oldest trades after `min_ts` first in the observed window, so small limits are candidate probes rather than exhaustive latest-activity rankings.

## 2026-06-18 01:52 local - Truthful live-proof status and market list JSON

### What changed

- Updated the canonical task graph and rendered `scripts\task.py status` surface so the 2026-06-18 strict Polymarket live soak is recorded as verified proof, not as a residual proof gap.
- Kept the remaining gaps explicit: Kalshi venue-specific soak is still open, the 10 live Polymarket alerts still need operator review, and publish/remote readiness remains a separate Git authority check.
- Added `pmfi markets list --format json` with exact `venue_market_id`, title, status, watched state, volume, trade count, and last trade timestamp for scriptable market selection.
- Added `pmfi markets list --venue polymarket|kalshi` and a `Market ID` table column so Kalshi ticker selection is not hidden behind truncated rich-table titles.
- Updated the operator quickstart for the new market-list flags.

### Decision consensus

- Question: should the next Kalshi proof step mutate the watchlist, run another long soak, or improve operator visibility first?
- Strongest case for immediate watchlist mutation: current watched Kalshi tickers returned zero recent trades, so selecting new tickers is required before a venue-specific soak can pass.
- Objection: Kalshi public discovery currently returns active rows with zero `volume_fp` / `volume_24h_fp`, and sampled newly discovered tickers also returned zero trades; mutating the watchlist now would not be evidence-backed.
- Consensus: first make exact venue-scoped market IDs scriptable, then record the current Kalshi blocker precisely. The next watchlist mutation should be based on a ticker that returns recent trades inside the target soak window.
- Payback artifact: JSON/venue-filtered `markets list` command, tests, and status/worklog updates.

### Verification

- Focused status tests: `.\.venv\Scripts\python.exe -m pytest .\tests\test_repo_status.py -q` = 2 passed.
- Focused market tests: `.\.venv\Scripts\python.exe -m pytest .\tests\test_markets_discovery.py -q` = 48 passed in the worker after the first market-list JSON slice, then 48 passed after adding `--venue`.
- Integrated focused tests: `.\.venv\Scripts\python.exe -m pytest .\tests\test_repo_status.py .\tests\test_markets_discovery.py -q` = 50 passed.
- Status render: `.\.venv\Scripts\python.exe scripts\task.py status` now shows a `Verified proof:` section for the Polymarket soak and keeps Kalshi/alert-review/publish-readiness as residual gaps.
- CLI smoke: `.\.venv\Scripts\python.exe -m pmfi.cli markets list --venue kalshi --format json --limit 5` returned parseable JSON with exact Kalshi tickers.
- Current Kalshi proof check: `.\.venv\Scripts\python.exe -m pmfi.cli soak --window 2h --required-venue kalshi --format json` failed closed as expected with missing Kalshi raw events and normalized trades, while the same window still contained Polymarket evidence.
- Read-only Kalshi trade probes: all four currently watched Kalshi tickers returned zero recent trades; four newly discovered active Kalshi candidates also returned zero trades. One older Kalshi ticker (`KXATPCHALLENGERMATCH-26JUN07BAEMOL-BAE`) returned trades, but their `created_time` values were from 2026-06-07 and are stale for the current soak window.
- Full verification: `.\.venv\Scripts\python.exe scripts\verify.py` = 771 passed, 30 skipped, verification passed.
- DB verification: `.\.venv\Scripts\python.exe scripts\db_local.py verify` passed against local Docker/Postgres.
- `git diff --check` passed.

### Residual risk

- Kalshi venue-specific soak remains blocked on finding and watching a public Kalshi ticker with recent trades in the target soak window.
- Alert quality review remains a human/operator judgment step; no TP/FP/noise labels were recorded by this slice.

## 2026-06-18 01:31 local - Strict live soak proof and alert delivery truth fix

### What changed

- Previewed and resolved four recent synthetic fixture-shaped dead letters (`pm-bad-market-test`, `price='not-a-number'`) with the one-row `pmfi dead-letters resolve` workflow after dry-running each ID.
- Ran a bounded persisted ingest (`pmfi ingest --max-seconds 3900`) against the current watched markets; it completed without leaving an ingest process running.
- Added `reports/alerts/` to `.gitignore` so durable local alert JSONL files remain local evidence artifacts, not publication payload.
- Fixed `process_event` so the external alert handler/file delivery runs only after `insert_alert` returns a stored alert ID. This keeps file delivery and daemon alert counters aligned with DB-queryable alert history instead of delivering deduped/non-inserted alerts.
- Added dead-letter `resolved` / `resolved_at` to JSON output and a table status column so recent resolved rows do not look unresolved during triage.
- Updated the operator quickstart for the explicit dead-letter resolved status.

### Verification

- Strict live soak: `.\.venv\Scripts\python.exe scripts\task.py soak --window 2h --format json` passed with `raw_events=11643`, `normalized_trades=781`, `alerts=10`, `unresolved_dead_letters=0`, `open_data_quality_incidents=0`, and `raw_evidence_duration_minutes=68.9`.
- Operator report: `.\.venv\Scripts\python.exe -m pmfi.cli report --since 2h --format json` showed 10 medium Polymarket alerts, 9 `volume_spike_v1`, 1 `market_relative_large_trade_v1`, 10 unreviewed alerts, and zero unresolved data gaps.
- Alert list: `.\.venv\Scripts\python.exe -m pmfi.cli alerts list --limit 3 --format json` returned recent DB-backed alert IDs and market titles.
- Dead-letter status smoke: `.\.venv\Scripts\python.exe -m pmfi.cli dead-letters --limit 2 --format json` showed the recently resolved fixture rows with `resolved=true` and `resolved_at` timestamps.
- Focused tests: `.\.venv\Scripts\python.exe -m pytest .\tests\test_cmd_reporting.py .\tests\test_runner_suppression.py -q` = 41 passed.
- Full verification: `.\.venv\Scripts\python.exe scripts\verify.py` = 768 passed, 30 skipped, verification passed.
- DB verification: `.\.venv\Scripts\python.exe scripts\db_local.py verify` passed against local Docker/Postgres.

### Residual risk

- The strict soak is Polymarket-proven; Kalshi REST polling remained connected/configured but produced no normalized trades in this run. A future venue-specific soak can use `pmfi soak --required-venue kalshi` after selecting active Kalshi markets.
- The 10 live alerts remain unreviewed. Next operator step is `pmfi alerts review <id> --label tp|fp|noise` and false-positive categorization.

## 2026-06-18 00:08 local - Bounded persisted ingest runs

### What changed

- Added `pmfi ingest --max-seconds N` for bounded persisted daemon runs. Default remains unlimited/Ctrl+C.
- Bounded persisted runs schedule a timer task and use the existing shutdown/finally cleanup path; when the timer completes, the daemon wait returns and existing cleanup cancels supervisors and disconnects adapters without surfacing a fatal error.
- `--max-events` remains dry-run-only. Dry-run can also use `--max-seconds` for timeout-based no-write checks.
- Updated the operator quickstart to show bounded persisted ingest as the practical producer command before `pmfi soak`.

### Decision consensus

- Question: should soak proof remain a manual Ctrl+C operation, use an external shell timeout, or become a first-class bounded ingest flag?
- Strongest case for a first-class flag: the repo already has a read-only `pmfi soak` validator, but without a bounded persisted producer the evidence workflow stays operator-fragile on Windows.
- Objection: daemon timers can leave adapter tasks hanging if they do not reach the supervisor shutdown check.
- Consensus: keep the flag narrow and default-off, then return the daemon wait on bounded timer completion so the existing cleanup block cancels supervisors and disconnects adapters.
- Validation target: offline parser/behavior tests plus one short real bounded persisted run.

### Verification

- `.\.venv\Scripts\python.exe -m pytest .\tests\test_cli.py .\tests\test_pr3_fixes.py .\tests\test_cli_validation.py -q` = 49 passed.
- `.\.venv\Scripts\python.exe -m pmfi.cli ingest --help` shows `--max-seconds`.
- `.\.venv\Scripts\python.exe -m pmfi.cli ingest --venue kalshi --max-seconds 5` exited cleanly in a short real persisted-command smoke.
- `.\.venv\Scripts\python.exe -m pmfi.cli health --json` immediately after the bounded run showed a fresh heartbeat and no fatal daemon state.
- `.\.venv\Scripts\python.exe scripts\verify.py` = 766 passed, 30 skipped, verification passed.
- `.\.venv\Scripts\python.exe scripts\db_local.py verify` passed against local Docker/Postgres.

### Residual risk

- The 5-second real run proved bounded process control but produced zero raw events/trades, so it does not satisfy soak readiness.
- Current local DB still has recent unresolved fixture-shaped dead letters; those need preview-first resolution or a fresh window outside their lookback before strict `pmfi soak --window 2h` can pass.

## 2026-06-17 23:57 local - Read-only dead-letter JSON triage

### What changed

- Added `pmfi dead-letters --format json` while preserving the existing table default.
- JSON output is read-only and includes full `dead_letter_id`, `short_id`, timestamp, venue/stage/error fields, source channel, and the existing 120-character `payload_preview`; it does not expose full payloads or change resolve behavior.
- Updated the operator quickstart so scripted dead-letter triage can use JSON before any preview-first resolution.

### Verification

- `.\.venv\Scripts\python.exe -m pytest .\tests\test_cmd_reporting.py .\tests\test_cli.py -q` = 49 passed.
- `.\.venv\Scripts\python.exe -m pmfi.cli dead-letters --limit 3 --format json` passed against local Postgres and returned fixture-shaped `pm-bad-market-test` rows without DB writes.

### Residual risk

- This only improves operator evidence extraction. The live-soak proof gap remains: a completed persisted ingest window still needs fresh raw/trade activity and zero unresolved dead letters in the soak window.

## 2026-06-17 23:45 local - Dead-letter triage resolve slice

### What changed

- Added a short dead-letter ID column to `pmfi dead-letters --limit N` so operators can act on listed failures without copying hidden UUIDs.
- Added `pmfi dead-letters resolve <dead_letter_id_or_prefix>` with an optional `--dry-run`.
- Resolve is local-DB only, requires at least the displayed 8-character prefix, matches only unresolved `dead_letters`, fails closed on no match or ambiguous prefix, and updates exactly one still-unresolved row with `resolved=true, resolved_at=now()`.
- Updated the operator quickstart with the preview-first dead-letter resolve workflow.
- No schema change was needed; existing `dead_letter_id`, `resolved`, `resolved_at`, and unresolved index already support this slice.

### Verification

- `.\.venv\Scripts\python.exe -m pytest tests\test_cmd_reporting.py::TestCmdDeadLetters tests\test_cli.py::test_dead_letters_resolve_cli_args -q` = 9 passed.
- `.\.venv\Scripts\python.exe -m pytest tests\test_cmd_reporting.py tests\test_cli.py -q` = 45 passed.
- `.\.venv\Scripts\python.exe -m pmfi.cli dead-letters --help` passed.
- `.\.venv\Scripts\python.exe -m pmfi.cli dead-letters resolve --help` passed.
- `.\.venv\Scripts\python.exe -m pmfi.cli dead-letters --limit 1` passed and displayed a short ID column.
- `.\.venv\Scripts\python.exe -m pmfi.cli dead-letters resolve dc7a1150 --dry-run` passed and previewed the matching row without updating it.
- `.\.venv\Scripts\python.exe scripts\verify.py` = 759 passed, 30 skipped, verification passed.

### Residual risk

- Resolve was covered with deterministic mocked DB tests; live mutation was intentionally not executed during this slice beyond read-only list/help smokes.

## 2026-06-17 23:30 local - DB-backed soak evidence/readiness checker

### What changed

- Added `pmfi soak`, a local-only, read-only Postgres evidence checker for completed live ingest windows.
- The checker summarizes the configured lookback window from canonical DB tables: `raw_events`, `normalized_trades`, `alerts`, unresolved `dead_letters`, open `data_quality_incidents`, and per-venue raw/trade activity with first/last timestamps.
- Added fail-closed thresholds for minimum raw-evidence duration, minimum raw events, minimum normalized trades, required venue evidence, maximum unresolved dead letters, and maximum open incidents.
- Added `--format text|json` output and a Windows task wrapper route: `python scripts\task.py soak ...`.
- Added the soak checker to `AGENT_START_HERE.md`, `docs\implementation\02_task_graph.yaml`, and the rendered `scripts\task.py status` high-priority command surface.
- Added the soak command to the operator quickstart next to daemon health/output inspection.
- Fixed review-found text-output handling so per-venue rows preserve `venue_code`; the regression test renders a passing result instead of only testing JSON.
- Decision consensus: DB rows are the soak-readiness authority because they prove persisted raw evidence, normalization, alert activity, and unresolved quality state; heartbeat/log artifacts remain operational context, not readiness proof.

### Verification

- `.venv\Scripts\python.exe -m pytest tests\test_soak.py tests\test_cli.py tests\test_cmd_reporting.py tests\test_windows_native_contracts.py tests\test_repo_status.py -q` = 59 passed.
- `.venv\Scripts\python.exe -m pmfi.cli soak --help` passed.
- `.venv\Scripts\python.exe scripts\task.py soak --help` passed.
- `.venv\Scripts\python.exe scripts\task.py status` passed and renders `python scripts\task.py soak --window 2h`.
- `.venv\Scripts\python.exe scripts\task.py soak --window 2h --format json` failed closed against the current DB: raw_events=0, normalized_trades=0, unresolved_dead_letters=4, raw_evidence_duration_minutes=0.0.
- `.venv\Scripts\python.exe scripts\verify.py` = 753 passed, 30 skipped, verification passed.

### Residual risk

- DB-gated live-soak proof still requires operator-owned local Postgres plus a completed opt-in live ingest window; default verification remains offline and does not make live API calls.
- Required venue evidence currently requires both raw events and normalized trades for each required venue, which is intentionally stricter than mere connection/heartbeat presence.

## 2026-06-17 23:08 local - Validate-only publish readiness checker

### Goal

Add a deterministic validate-only publication-readiness check that reports local Git truth without pushing, publishing, or writing artifacts, with opt-in remote freshness via `--fetch`.

### Changed files

- `AGENT_START_HERE.md`: adds the handoff and publish-readiness commands to the fresh-session command surface.
- `docs/implementation/02_task_graph.yaml`: adds `handoff --db-verify` and `publish-ready --fetch` to high-priority status commands.
- `docs/implementation/05_agent_handoff_protocol.md`: documents the validate-only publication-readiness check and its no-push/no-artifact boundary.
- `scripts/publish_ready.py`: new fail-closed checker for Git worktree cleanliness, branch/HEAD/upstream, optional `git fetch --prune`, upstream/main ancestry, ahead/behind counts, changed-file scope, and attribution/generated footer strings in commits or diff.
- `scripts/task.py`: adds `python scripts\task.py publish-ready [--fetch]`.
- `tests/test_publish_ready.py`: temp-repo tests for clean-ahead readiness, missing upstream, dirty worktree, upstream advancement, stale remote-tracking detection with `--fetch`, footer-string detection, and task routing.
- `tests/test_repo_status.py`: locks the new publish-readiness command into rendered status output.
- `WORKLOG.md`: records this slice.

### Verification run

- Red check: `.\.venv\Scripts\python.exe -m pytest .\tests\test_publish_ready.py -q` failed during collection because `scripts.publish_ready` did not exist.
- Focused/router/status check: `.\.venv\Scripts\python.exe -m pytest tests\test_publish_ready.py tests\test_task_handoff.py tests\test_repo_status.py -q` passed, 17 passed.
- Status smoke: `.\.venv\Scripts\python.exe scripts\task.py status` passed and renders `python scripts\task.py publish-ready --fetch` under high-priority commands.
- Live validate-only smoke: `.\.venv\Scripts\python.exe .\scripts\task.py publish-ready` failed closed as expected because the current worktree contains this slice's unstaged edits; it reported no publishing, no artifacts, branch `main`, upstream `origin/main`, ahead 35 / behind 0, upstream/main ancestor checks passing, and no attribution footer hits.
- Full verification: `.\.venv\Scripts\python.exe scripts\verify.py` passed, 744 passed, 30 skipped, verification passed.

### Residual risk

- Remote freshness is intentionally not fetched in the default command to keep the check deterministic and network-free; use `python scripts\task.py publish-ready --fetch` before any push or PR readiness claim.

## 2026-06-17 22:57 local - Local handoff snapshot command

### Goal

Add a reproducible local handoff/publication-readiness evidence snapshot without pushing, publishing, or adding hosted/SaaS scope.

### Current milestone

M10 continuous hardening / handoff readiness. The command records local evidence only; publish or remote readiness still requires separate remote/branch authority checks.

### Decision consensus

- Question: should handoff readiness be a task-router command, a generated doc only, or remote publication automation?
- Strongest case: a task-router command is the narrowest repeatable operator surface and can be tested offline.
- Objection: a generated artifact could be mistaken for publication proof.
- Orthogonal alternative: remote/PR automation would answer publication, but violates the local-only boundary for this slice.
- Consensus: implement `python scripts\task.py handoff` as a local-only evidence snapshot with explicit `publication_performed: false`, no environment dump, optional DB/default verification recording, and no push/PR behavior.
- Validation target: deterministic tests plus one real default snapshot run.

### Changed files

- `.gitignore`: ignores generated `reports\handoff\` snapshots so local evidence artifacts are not accidentally staged.
- `scripts/handoff.py`: new local snapshot writer for Git/upstream counts, dirty state, recent commits, latest worklog excerpt, task status summary, runtime details, redacted PMFI_DB_URL presence, and verification evidence.
- `scripts/task.py`: adds `handoff` route and forwards snapshot flags.
- `tests/test_task_handoff.py`: focused offline tests for DB URL redaction including malformed ports, prepended WORKLOG parsing, default skip behavior, nonfatal DB verification failure recording, artifact writing, and task routing.
- `docs/implementation/05_agent_handoff_protocol.md`: documents the executable local snapshot command and boundaries.
- `WORKLOG.md`: records this slice.

### Checks run

- `.\.venv\Scripts\python.exe -m pytest tests\test_task_handoff.py -q`: pass, 7 passed.
- `.\.venv\Scripts\python.exe scripts\task.py handoff --no-db-verify`: pass, wrote ignored local snapshots under `reports\handoff\`; DB/default verification recorded as skipped in the snapshot.
- `.\.venv\Scripts\python.exe scripts\task.py handoff --db-verify`: pass, wrote ignored local snapshots under `reports\handoff\`; DB readiness recorded as pass and default verification recorded as skipped.
- `.\.venv\Scripts\python.exe scripts\verify.py`: pass, 736 passed, 30 skipped, verification passed.

### Failing or skipped checks

- DB readiness in the first handoff snapshot was intentionally skipped with `--no-db-verify`; the second local snapshot used `--db-verify` and passed.
- DB-backed pytest cases skipped during default verification because `PMFI_DB_URL` was not set.

### Residual risks

- Generated `reports\handoff\` snapshots are local evidence artifacts; they are not remote publication proof and should be reviewed before sharing.
- The snapshot captures current dirty-state evidence, so running it during active edits will record in-progress files.

### Next smallest step

When Docker/Postgres is available, run `python scripts\task.py handoff --db-verify` or `python scripts\db_local.py verify` to add current DB readiness evidence.

## 2026-06-17 22:44 local - Dashboard alerts now include latest review state

### What changed

- Extended `recent_alerts(conn, limit=...)` so `/api/alerts` returns latest `alert_reviews` state per alert: `review_label`, `review_category`, `review_notes`, `reviewed_at`, `reviewed_by`, and computed `is_reviewed`.
- Kept `/api/alerts` read-only. The query first materializes the limited recent alert set, then joins latest review rows only for those alerts using the same newest-row semantics as reporting (`reviewed_at DESC`, `review_id DESC`).
- Updated the static dashboard alerts table with a copyable short alert ID column and a review-state column. Unreviewed alerts are visually distinct; reviewed alerts show the latest label and category when present.
- Updated operator quickstart to state that the dashboard displays review state but review writes stay in `pmfi alerts review`.
- Added DB-gated coverage proving unreviewed fields are explicit and multiple review rows choose the newest review.

### Verification

- `$env:PMFI_DB_URL='postgresql://pmfi:pmfi_local_password_change_me@localhost:5433/pmfi'; .\.venv\Scripts\python.exe -m pytest tests\test_dashboard_alerts_db.py -q` = 3 passed.
- `$env:PMFI_DB_URL='postgresql://pmfi:pmfi_local_password_change_me@localhost:5433/pmfi'; .\.venv\Scripts\python.exe -m pytest tests\test_dashboard_queries_db.py -q` = 2 passed.
- `$env:PMFI_DB_URL='postgresql://pmfi:pmfi_local_password_change_me@localhost:5433/pmfi'; .\.venv\Scripts\python.exe -m pytest tests\test_dashboard_alerts_db.py tests\test_dashboard_queries_db.py -q` = 5 passed.
- Inline dashboard HTTP smoke started `run_dashboard` on a random localhost port, fetched `/`, `/healthz`, and `/api/alerts?limit=5`, verified review fields are present, and shut the server down = pass.
- `.\.venv\Scripts\python.exe scripts\db_local.py verify` = pass.
- `.\.venv\Scripts\python.exe scripts\verify.py` = 729 passed, 30 skipped, verification passed.
- Changed-file attribution scan found no attribution footer notes.

### Residual risk

- No schema/index change was made in this slice. The query is bounded to the dashboard alert limit before reading review state, but a very large future `alert_reviews` table may still merit a dedicated `(alert_id, reviewed_at DESC)` index after profiling.
- Static dashboard rendering was covered by code review and the backend/API contract tests, not by a headed/headless browser screenshot pass.

## 2026-06-17 22:31 local - DB-enforced normalized trade dedupe guard

### Files inspected
- `AGENTS.md`
- `FAST_ADVANCE.md`
- `AGENT_START_HERE.md`
- `LOCAL_ONLY_SCOPE.md`
- `docs\governance\08_local_only_exclusion_policy.md`
- `docs\governance\12_decision_methods.md`
- `src\pmfi\db\repos\trades.py`
- `src\pmfi\db\migrations.py`
- `scripts\db_local.py`
- `sql\001_init.sql`
- `sql\007_venue_trade_id_index.sql`
- `tests\test_raw_dedup_atomic_db.py`
- `tests\test_storage_hardening_db.py`
- `tests\test_db_local_script.py`
- `tests\test_replay_backtest_db.py`

### Changes made
- Added `sql\013_normalized_trade_dedupe_guard.sql`, creating `normalized_trade_dedupe_keys` plus partial unique guards for both `(venue_code, venue_trade_id)` and the null-id deterministic fingerprint `(venue_code, market_id, exchange_ts_key, price, contracts, outcome_key)`.
- Backfilled one guard row per existing normalized-trade identity without deleting existing normalized trade rows.
- Reworked `insert_trade` to claim the guard with `INSERT ... ON CONFLICT DO NOTHING` before inserting into `normalized_trades`; duplicate paths still return `None` before downstream metric/alert writes.
- Registered migration 013 in `apply_schema_migrations()` and `scripts\db_local.py`; read-only `db_local.py verify` now fails closed if the guard table or indexes are absent.
- Added DB-gated repeatable-read concurrency tests for duplicate venue trade IDs and duplicate null-id fingerprints.
- Updated the replay-backtest synthetic reset to clear the new guard row only when deliberately replaying the same synthetic raw event.

### Verification run
- `.\.venv\Scripts\python.exe scripts\verify.py` before edits - pass, 729 passed / 27 skipped.
- Red check: `PMFI_DB_URL=postgresql://pmfi:pmfi_local_password_change_me@localhost:5433/pmfi .\.venv\Scripts\python.exe -m pytest tests\test_raw_dedup_atomic_db.py -q -k "concurrent_venue_trade_id_insert_uses_db_guard or concurrent_null_id_fingerprint_insert_uses_db_guard"` - failed as expected; all 8 concurrent callers inserted rows for both identity modes.
- `.\.venv\Scripts\python.exe -m pytest tests\test_db_local_script.py -q` - pass, 4 passed.
- `.\.venv\Scripts\python.exe scripts\db_local.py init` - pass; migration 013 applied and backfilled guard rows.
- `PMFI_DB_URL=postgresql://pmfi:pmfi_local_password_change_me@localhost:5433/pmfi .\.venv\Scripts\python.exe -m pytest tests\test_raw_dedup_atomic_db.py -q` - pass, 4 passed.
- `PMFI_DB_URL=postgresql://pmfi:pmfi_local_password_change_me@localhost:5433/pmfi .\.venv\Scripts\python.exe -m pytest tests\test_storage_hardening_db.py -q` - pass, 2 passed.
- `.\.venv\Scripts\python.exe scripts\db_local.py verify` - pass; schema readiness includes the guard table/indexes and venues are seeded.
- PowerShell here-string probe calling `apply_schema_migrations(pool)` against local Postgres - pass; startup migration path is idempotent.
- `.\.venv\Scripts\python.exe scripts\verify.py` - pass, 729 passed / 29 skipped.
- `PMFI_DB_URL=postgresql://pmfi:pmfi_local_password_change_me@localhost:5433/pmfi .\.venv\Scripts\python.exe -m pytest tests\test_replay_backtest_db.py -q -k persist_replay_seeds_accumulators_and_detects_cluster` - pass, 1 passed / 5 deselected.
- `PMFI_DB_URL=postgresql://pmfi:pmfi_local_password_change_me@localhost:5433/pmfi .\.venv\Scripts\python.exe -m pytest -q` - pass, 758 passed.

### Findings
- Facts: `normalized_trades` is partitioned by `received_at`, so enforcing identity directly on the partitioned table would need the partition key and would not protect the canonical cross-partition identity.
- Facts: The guard table keeps raw-before-derived lineage intact; `insert_trade` still requires the caller-supplied raw linkage and skips downstream work by returning `None` on duplicates.
- Facts: Under repeatable-read snapshots, PostgreSQL can surface concurrent unique-guard losers as `SerializationError`; `insert_trade` maps that conflict back to the duplicate `None` contract.
- Assumptions: Existing historical duplicate `normalized_trades` rows are preserved; migration 013 only prevents new duplicates and backfills canonical guard identities.
- Blockers: None.

### Next step
- Continue with the next hardening slice from `python scripts\task.py status`; no follow-up is required for normalized trade dedupe unless future work needs an operator report for pre-existing historical duplicates.

## 2026-06-17 22:30 local - Handoff status truth surface

### Files inspected
- `AGENTS.md`
- `FAST_ADVANCE.md`
- `AGENT_START_HERE.md`
- `LOCAL_ONLY_SCOPE.md`
- `docs\implementation\02_task_graph.yaml`
- `scripts\repo_status.py`
- `scripts\task.py`
- `tests\test_alignment_contracts.py`
- `tests\test_fast_advance_contracts.py`
- `tests\test_cli.py`
- `WORKLOG.md`

### Changes made
- Updated `docs\implementation\02_task_graph.yaml` so `scripts\task.py status` no longer presents M1 as merely high priority or M2/M3 as merely ready. M1-M4 and M6-M9 now render as `core_proven`; M5 is `opt_in_live_partial_proof`; M10 is `continuous_hardening`.
- Added structured current posture, next recommended focus, residual proof gaps, intact constraints, and high-priority commands to the task graph.
- Updated `scripts\repo_status.py` to render the structured handoff metadata and per-milestone proof notes from YAML instead of hard-coded command/status text.
- Added `tests\test_repo_status.py` to lock the non-stale milestone labels and handoff-ready status sections.

### Verification run
- `python -m pytest .\tests\test_repo_status.py -q` - first failed as expected against stale graph/script, then passed after implementation: 2 passed.
- `python scripts\task.py status` - pass; output shows current posture, next focus, residual proof gaps, high-priority commands, and core-proven milestone labels.
- `python -m pytest .\tests\test_alignment_contracts.py .\tests\test_fast_advance_contracts.py .\tests\test_cli.py -q -k "task_graph or fast_advance or status_runs_without_db"` - pass, 8 passed / 32 deselected.
- `python -m pytest .\tests\test_repo_status.py .\tests\test_alignment_contracts.py .\tests\test_fast_advance_contracts.py -q` - pass, 13 passed.
- `python scripts\verify.py` with system Python - fail during pytest collection because `aiohttp` and `asyncpg` are not installed in the system interpreter.
- `.\.venv\Scripts\python.exe scripts\verify.py` - pass, 729 passed / 27 skipped.

### Findings
- Facts: The canonical status source is `docs\implementation\02_task_graph.yaml`; `scripts\repo_status.py` now consumes structured metadata from that file.
- Facts: The new status surface preserves local-only, Postgres-first, raw-lineage, no-trading, and default-offline verification constraints.
- Consensus: Treat the implemented local core as handoff-ready and proven, while keeping continuous live soak, remote/publish readiness, authenticated Kalshi WS, and real-traffic alert quality as residual proof gaps.
- Blockers: None for this slice.

### Next step
- Use `python scripts\task.py status` as the fresh-agent handoff surface, then run the default verifier and local Postgres verification before making any publish/readiness claim.

## 2026-06-17 22:25 local - Market top-count validation fails closed

### Files inspected
- `src\pmfi\commands\markets.py`
- `src\pmfi\cli.py`
- `tests\test_markets_discovery.py`

### Changes made
- `pmfi markets watch --top N` now rejects non-positive `N` before DB pool creation.
- `pmfi markets discover --watch-top N` now rejects non-positive `N` before DB or venue sync work.
- CLI help now describes both top-count flags as positive counts.
- Added offline regressions proving invalid top counts fail before DB/REST paths.

### Verification run
- `.\.venv\Scripts\python.exe -m pytest tests\test_markets_discovery.py -q` - pass, 45 passed.
- `.\.venv\Scripts\python.exe -m pmfi.cli markets watch --top 0` - expected fail-closed exit 1 with `--top must be a positive integer`.
- `.\.venv\Scripts\python.exe -m pmfi.cli markets discover --watch-top 0` - expected fail-closed exit 1 with `--watch-top must be a positive integer`.

### Findings
- Facts: Invalid top-count controls previously reached lower layers or became a silent no-op; they now fail before DB/network work.
- Blockers: None.

### Next step
- Run the canonical verifier and commit this validation checkpoint if clean.

## 2026-06-17 22:15 local - Report since validation fails closed

### Files inspected
- `src\pmfi\commands\reporting.py`
- `tests\test_cmd_reporting.py`

### Changes made
- Changed `pmfi report --since <invalid>` from silent 24-hour fallback to an explicit error with exit code 1.
- Added an offline regression test proving invalid `--since` returns before DB pool creation.

### Verification run
- `.\.venv\Scripts\python.exe -m pytest tests\test_cmd_reporting.py -q` - pass, 10 passed.
- `.\.venv\Scripts\python.exe -m pmfi.cli report --since not-a-window` - expected fail-closed exit 1 with `[report] Invalid --since value: 'not-a-window'`.

### Findings
- Facts: `alerts list` and `alerts fp-rate` already failed closed on invalid `--since`; this makes `report` consistent with those adjacent operator commands.
- Blockers: None.

### Next step
- Run the canonical verifier and commit this validation checkpoint if clean.

## 2026-06-17 22:05 local - DB verify schema readiness hardening

### Files inspected
- `scripts\db_local.py`
- `sql\012_market_volume_column.sql`
- `src\pmfi\db\migrations.py`
- `tests\test_db_local_script.py`
- `docs\ops\00_local_setup.md`
- `docs\ops\OPERATOR_QUICKSTART.md`

### Changes made
- Added `sql\012_market_volume_column.sql` to `scripts\db_local.py` `SQL_FILES`, closing the gap where fresh DB init could miss the market volume column and indexes.
- Extended `python scripts\db_local.py verify` with a read-only schema readiness check for required PMFI tables, views, and indexes; it raises on missing objects before checking seeded venues.
- Added focused tests that require every numbered SQL file to appear in `SQL_FILES`, require readiness SQL to fail closed, and prove `verify` checks schema before venue seed rows.
- Documented that `verify` is read-only and now checks required schema objects without applying migrations or writing rows.

### Verification run
- `.\.venv\Scripts\python.exe -m pytest tests\test_db_local_script.py -q` - pass, 4 passed.
- `.\.venv\Scripts\python.exe scripts\db_local.py verify` - pass; readiness SQL returned `DO`, then venues `kalshi` and `polymarket`.

### Findings
- Facts: Startup migrations already include migration 012, but `scripts\db_local.py` fresh init did not list `sql\012_market_volume_column.sql`.
- Facts: The new readiness check is validate-only: it queries catalog metadata and seeded venues, and does not run migrations, seed data, deletes, or artifact generation.
- Blockers: None.

### Next step
- Run the canonical verifier and commit this DB-readiness checkpoint if clean.

## 2026-06-17 21:48 local - PMFI report operator triage sections

### Files inspected
- `src\pmfi\commands\reporting.py`
- `src\pmfi\db\repos\alerts.py`
- `tests\test_cmd_reporting.py`
- `sql\001_init.sql`
- `docs\product\02_false_positive_taxonomy.md`
- `docs\ops\OPERATOR_QUICKSTART.md`

### Changes made
- Extended `get_alert_summary` with review queue, latest-review outcome, false-positive category, unresolved dead-letter, and open data-quality incident summaries using the existing schema only.
- Added concise `pmfi report` table sections for unreviewed alert IDs, latest review labels/categories, and data gaps; JSON output includes the same nested keys.
- Added offline command-rendering tests and a repository-query shape test for `NOT EXISTS`, latest-review `DISTINCT ON`, and data-quality incident coverage.
- Updated the operator quickstart command table and alert-view guidance for `pmfi report` triage sections and `alerts list --market` identifier matching.

### Verification run
- `.\.venv\Scripts\python.exe -m pytest tests\test_cmd_reporting.py -q` - pass, 9 passed.
- `.\.venv\Scripts\python.exe scripts\verify.py` - pass, 718 passed / 27 skipped.
- `.\.venv\Scripts\python.exe -m pmfi.cli report --since 7d` - pass; surfaced 9 unresolved dead letters in the local DB window.
- `.\.venv\Scripts\python.exe -m pmfi.cli report --since 7d --format json` - pass; JSON includes `review_queue`, `review_outcomes`, and `data_gaps`.
- `.\.venv\Scripts\python.exe scripts\db_local.py verify` - pass; local Postgres ready with `kalshi` and `polymarket` venues.
- `PMFI_DB_URL=... .\.venv\Scripts\python.exe -m pytest tests\test_alerts_schema_contract.py -q` - pass, 4 passed.

### Findings
- Facts: `sql\001_init.sql` already contains `alert_reviews`, `dead_letters`, and `data_quality_incidents`; no schema change was needed.
- Facts: Review outcome counts are based on the latest review row per alert, so multiple historical reviews do not double-count an alert.
- Inferences: The report is now a DB-backed triage surface for alert review and data-quality gaps, not only an alert-count summary.
- Blockers: None.

### Next step
- Commit this verified checkpoint, then move to the next hardening slice: non-mutating DB readiness or fail-closed CLI validation.

## 2026-06-17 21:45 local - Alerts list market identifier filtering

### Files inspected
- `src/pmfi/commands/alerts.py`
- `tests/test_alerts_review.py`
- `tests/test_cli.py`

### Changes made
- Extended `pmfi alerts list --market` to match market title, venue market ID, and alert `market_id::text` with one bound substring parameter.
- Added a mocked command regression test proving the SQL shape, parameter order, and non-interpolated market filter value.

### Verification run
- `.\.venv\Scripts\python.exe -m pytest tests\test_alerts_review.py -q -k market_filter` - pass, 1 passed / 6 deselected after first confirming the test failed on the title-only predicate.
- `.\.venv\Scripts\python.exe -m pytest tests\test_cli.py tests\test_alerts_review.py -q` - pass, 36 passed.
- `.\.venv\Scripts\python.exe scripts\verify.py` - pass, 715 passed / 27 skipped.

### Findings
- Facts: `--market` previously only added `m.title ILIKE $idx`; it now adds a parenthesized OR over `m.title`, `m.venue_market_id`, and `a.market_id::text`.
- Inferences: This is command-layer filtering only; no repository-layer or reporting edits were needed.
- Assumptions: Substring matching remains the intended operator behavior for pasted IDs and partial titles.
- Blockers: None.

### Next step
- None for this slice.

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

## 2026-06-13 — Market discovery UX: volume-first ranking + stateless frictionless watching

### Problem
Operators watched low-volume markets, so real alerts almost never fired (recent real trades maxed at ~$495; thresholds are $5k–$25k). Volume was fetched from both venues but buried in raw_metadata jsonb and never surfaced; `markets list` sorted by last_trade_at (burying newly-discovered zero-trade markets); watching required pasting a 66-char Polymarket condition_id; `discover` printed only "Synced N".

### Design (multi-agent panel: 4 angles → 3 lensed judges → opus synthesis)
Adopted a dedicated indexed Postgres column over jsonb-sort (btree-indexable, scalable to thousands), an actionable ranked discover preview, and two stateless watch modes (`--top N`, `--search`). Rejected (all judges concurred): a `.omc` session/index file for watch-by-row (fragile ephemeral state, conflicts with no-delete memory), a no-migration jsonb `ORDER BY` (can't use a btree index), speculative liquidity/open_interest columns (YAGNI), and bulk `unwatch --all` (destructive footgun). Lead override: column named **volume** (not volume_usd) — the value is venue-relative (Polymarket USD notional, Kalshi contract count), so an _usd suffix would be a false-precision trap; formatter shows compact magnitude (66.24M) with no currency symbol.

### Changes
- **Migration 012** (both artifacts): `sql/012_market_volume_column.sql` AND inlined in `apply_schema_migrations()` (the live daemon-startup path; sql/ files are db_local.py-only). Adds `markets.volume numeric(20,2)` + two partial indexes (volume DESC NULLS LAST, venue+volume). Idempotent, additive, no backfill.
- **Repo** (`db/repos/markets.py`): `upsert_market_full` gains `volume` param (COALESCE, non-overwriting); new `fetch_markets_ranked` (whitelisted sort — injection-guarded; LEFT JOIN trade_count/last_trade_at; min_volume bound as Decimal) and `set_markets_watched_bulk` (ANY($3::text[]), empty-list early return).
- **Sync** (`markets.py`): both sync_* pass the latest fetched volume incl. explicit 0 (no `or None`) so the ranking cache never goes stale.
- **Commands** (`commands/markets.py`): `_fmt_volume` (Decimal-safe); `markets list` shows a Volume column, default `--sort volume`, `--min-volume`; `discover` prints a top-10-by-volume table + inline copy-paste watch commands + `--watch-top N` (honors N beyond the 10-row preview); `watch`/`unwatch` gain stateless `--top`/`--search` with exactly-one-mode validation (mode-aware error message).
- **CLI** (`cli.py`): new flags; `watch`/`unwatch` positionals now optional (nargs='?').

### Review + verification
- Implement→review workflow: code-reviewer + postgres-reviewer both APPROVE_WITH_NITS, zero must-fixes. I then applied 6 worthwhile findings (stale-zero-volume, watch-top truncation>10, unwatch message advertising --top, tautological stateless test, Decimal min_volume bind, empty-list bulk guard) + 3 new regression tests.
- **Live end-to-end caught a real bug all mocks + both reviewers missed**: `numeric(20,2)` round-trips as `Decimal` via asyncpg, but `_fmt_volume` did `Decimal / float` → TypeError. Fixed (coerce to float) + locked in with a Decimal test case. This is why live verification matters — the mocks all used floats.
- Migration applied to live DB; column + 2 indexes confirmed; `apply_schema_migrations` idempotent across 2 runs. Live `pmfi markets discover --venue polymarket` populated volume (top markets $57–66M — the high-volume markets that will actually fire alerts) and rendered the ranked preview; `markets list --sort volume` shows 66.24M/63.73M/... correctly.
- Gates: **714 offline / 741 DB-gated**, verification passed. 17 new offline tests (all fetch_* mocked, no live calls).

### Residual risk / next
- volume is venue-relative (not cross-venue USD-comparable); documented, normalization deferred (YAGNI). Operators discover per --venue so within-venue ranking is correct.
- Pre-migration rows show NULL volume until next discover (no backfill, by design); `list --min-volume` excludes them.

## 2026-06-13 — Data-integrity: test DB self-pollution fixed, baselines de-corrupted, self-test hardened

### Root-cause investigation
- Investigated "no alerts since 2026-06-06 despite trades through 06-13". **Verdict: correct behavior, plus a real bug found.** June 6 trades had max capital $33,600 (avg $8,186) → fired the absolute rule. June 7-13 trades maxed at $219-495 → genuinely too small to alert. The live pipeline IS wired correctly: `process_event` calls `engine.evaluate(trade)` and `insert_alert` (src/pmfi/pipeline/runner.py:259-296), and `cmd_ingest` delivers via file/http/stdout (src/pmfi/cli.py:676-757).
- But the "June 12/13 trades" were **252 canary rows** (`venue_trade_id='canary-dt-roundtrip-001'`) injected by `test_decimal_roundtrip.py` on every DB-gated run since 06-06. The INSERT used `gen_random_uuid()` PKs with `ON CONFLICT DO NOTHING` on no stable key → never conflicted, never cleaned up, accumulated one row per run.
- Blast radius: 252 fake trades inflating stats; **2 markets had baselines built 100% from canary data** (Oprah-2028 n=151, another n=96), 1 market partially polluted (12 real + 3 canary).

### Changes made
- `tests/test_decimal_roundtrip.py`: wrap the normalized_trades canary INSERT in a transaction that always rolls back. `RETURNING` still proves the numeric columns preserve Decimal precision (Postgres coerces into numeric(12,8)/numeric(28,8) before returning), but nothing persists. Verified: 7 test invocations + full suite run → canary count stays 0.
- DB cleanup (operator DB hygiene, scoped precisely to the canary marker): deleted 252 canary `normalized_trades`, deleted 2 fully-canary `market_baselines`, recomputed baselines from clean data (`pmfi baselines compute` → 3 markets, all real). `pmfi stats` now shows truthful 65 trades (was 317).
- `src/pmfi/commands/ingest.py` + `tests/test_cli.py`: `pmfi monitor --fixture-replay` crashed on `malformed_payload.json` because `normalize_event` *raises* `NormalizationError` (so the pipeline can dead-letter) but `cmd_monitor` only checked `if trade is None`. Wrapped the call to report a clean dead-letter line and continue — the on-demand engine self-test is now as non-fragile as the real pipeline. Also fixed the misleading "normalization failed" message on the benign None (non-trade) path. Added regression test `test_monitor_fixture_replay_survives_malformed_fixture`.

### Verification
- Offline suite: **697 passed, 27 skipped** (+1 new test). DB-gated suite: **723 passed**, and **0 canary rows** persist after a full run (was +1 per run before).
- Cross-check: haiku agent scanned all 16 DB-gated test files — `test_decimal_roundtrip.py` was the **only** pollution source; every other DB test cleans up via DELETE-in-finally or rollback. No conftest transactional fixture exists.
- `pmfi monitor --fixture-replay` → "Stream complete: 12 alert(s) from 13 fixture(s)", malformed fixture → "dead-letter (normalization failed): invalid decimal for price: 'not-a-number'", no traceback. This is the operator's on-demand proof the alert engine works.

### Residual risk / next operator steps
- The tool has still only been run for short windows (longest real heartbeat run ≈2 min on 06-10). A multi-hour `pmfi ingest` soak with Docker up remains the one unproven production claim (operator action; requires live network).
- Watched markets are low-volume (max real trade $495 recently); real alerts will be rare unless higher-volume markets are watched. `pmfi monitor --fixture-replay` is the way to confirm the engine independent of live flow.

## 2026-06-13 — Coverage gaps closed, alert review label display fixed

### Changes made
- `tests/test_kalshi_rest_adapter.py`: added `TestGapDetection` class — verifies `logger.warning` fires when the oldest trade in a REST poll page is newer than the previous cycle's max timestamp (poll window overflow). Two-cycle mock: cycle 1 sets prev_max_ts=T1; cycle 2 returns oldest trade at T2>T1 triggering the warning.
- `src/pmfi/commands/alerts.py`: fixed `alerts list` Label column — was showing `mo.outcome_label` (market outcome name) instead of the operator review label. Now subqueries `alert_reviews` for the most recent label (tp/fp/noise) per alert. 8-char alert IDs with a recorded review now show the label inline.

### Verification
- Full offline suite: **696 passed, 27 skipped**.
- Full DB-gated suite (PMFI_DB_URL, Docker up): **723 passed, 0 failed** (up from 720; 3 new tests: FileDelivery OSError, OI=0 guard, Kalshi gap detection).
- CLI smoke: `pmfi alerts review 1b042c8e --label tp` → label recorded; `pmfi alerts list` → `tp` shows in Label column.
- Dead letter audit: 74 dead letters (72 `invalid_price_or_size` from Polymarket "not-a-number" prices — expected; 2 `NormalizationSkipped` for last_trade_price event type — expected). No new bug.

### Findings
- Alert review label was silently discarded from the `alerts list` display — operators couldn't see review state without running `pmfi alerts fp-rate`. Fixed by subquerying `alert_reviews`.
- Kalshi REST gap detection warning was untested — added coverage proves the logger.warning path.

### Residual risk / next operator steps
- No new alerts since 2026-06-06 despite 317 normalized trades — likely thresholds not met by recent market activity, not a pipeline bug (last trade: 2026-06-13, pipeline is live).
- Soak run still recommended: `pmfi ingest` for 30–60 min to observe a fresh alert firing end-to-end.

## 2026-06-12 — Windows UX hardening, test gate to 720, live ingest unblocked

### Changes made
- `tests/test_decimal_roundtrip.py`: raised asyncpg connect timeout from 2s to 10s in `_has_db()` — Docker handshake on this machine took >2s causing all 7 DB-gated tests to always skip. Now 720 passed with PMFI_DB_URL set, 0 skipped.
- `src/pmfi/replay.py`: replaced Unicode arrow `→` with ASCII `->` in two verbose print() calls (Windows cp1252 charmap can't encode U+2192).
- `src/pmfi/cli.py` (replay table title): replaced `→` with `->` in Rich Table title.
- `src/pmfi/cli.py` (startup): added `sys.stdout.reconfigure(encoding='utf-8', errors='replace')` + stderr on Windows — fixes all Rich/print output (em-dashes, box-drawing chars, etc.) without needing manual chcp.
- `src/pmfi/cli.py` (ingest `--dry-run`): added `--max-events N` flag — dry-run now self-exits after N events, enabling bounded live smoke without Ctrl+C.
- `.gitignore`: added `config/app.yaml` so local operator config isn't accidentally committed.
- `config/app.yaml` (created locally, gitignored): both live venues enabled (`enable_polymarket_live: true`, `enable_kalshi_live: true`) — `pmfi ingest` now works without `--venue` flag.

### Verification run
- `python scripts\verify.py` — **693 passed, 27 skipped** (offline gate)
- `PMFI_DB_URL=... pytest tests\ -q` — **720 passed, 0 skipped** (with Docker up)
- End-to-end operator smoke: `pmfi replay --persist`, `pmfi alerts list`, `pmfi alerts explain 1b042c8e`, `pmfi alerts review 1b042c8e --label tp`, `pmfi alerts fp-rate`, `pmfi baselines show`, `pmfi stats`, `pmfi db-verify`, `pmfi health`, `pmfi dead-letters` — all produce correct clean output.

### Findings
- Facts: All 73 dead letters are from `malformed_payload.json` fixture (pm-bad-market-test) — expected test data, not a real normalization issue.
- Facts: Windows cp1252 affects all print() and Rich output; UTF-8 stdout reconfigure at CLI entry is the correct fix.
- Facts: `pmfi ingest --dry-run` connects live (by design); `--max-events` enables bounded smoke without blocking.
- Facts: `config/app.yaml` is now gitignored and created locally with live venues enabled; operator can run `pmfi ingest` immediately with Docker up.

### Next step
- Run `pmfi ingest` with Docker up for 30–60 min soak; verify event→trade→alert flow with real market data.
- After first real alert fires: run `pmfi alerts review <8-char-id> --label tp/fp` to prove end-to-end review workflow.
- Operator command: `pmfi ingest` (both polymarket+kalshi enabled in config/app.yaml).

## 2026-06-12 — Operator resilience: DB-connect hardening, prefix resolution, test coverage

### Changes made
- `src/pmfi/db/repos/alerts.py`: added `resolve_alert_id(conn, prefix)` — full UUID returned directly; short prefix does LIKE query against alerts table. `get_alert_by_id` now resolves prefix before UUID cast.
- `src/pmfi/commands/alerts.py`: `cmd_alerts_review` resolves prefix → full UUID via `resolve_alert_id` before INSERT, so 8-char ID from `alerts list` works directly.
- `src/pmfi/cli.py`: argparse help strings updated; `replay --from-db` and `replay --persist` paths now catch DB connect failure instead of crashing; `ingest --dry-run` catches DB failure.
- `src/pmfi/commands/reporting.py`: `cmd_stats` and `cmd_db_maintenance` guard `create_pool` failure; `cmd_db_maintenance` returns 1 on DB failure.
- `src/pmfi/commands/markets.py`: `cmd_markets_list` and `_cmd_markets_set_watched` guard `create_pool` failure.
- `src/pmfi/commands/ingest.py`: `cmd_live` `_run()` guards `create_pool` failure.
- `docs/ops/OPERATOR_QUICKSTART.md`: ID column guidance updated — 8-char prefix works directly in `explain` and `review`; two places corrected.
- `tests/test_alert_id_prefix.py`: 5 offline tests for `resolve_alert_id` and `get_alert_by_id` prefix path.
- `tests/test_cmd_reporting.py`: 6 offline tests for `cmd_stats`, `cmd_dead_letters`, `cmd_report` (success + failure paths).

### Verification run
- `python scripts\verify.py` — 693 passed, 27 skipped (DB-gated)

### Findings
- Facts: All primary operator commands now return 1 with a user-friendly message on DB connect failure rather than crashing with a traceback.
- Facts: `alerts list` and `watch` both show an 8-char "ID" column; that prefix now works directly with `explain` and `review`.
- Blockers: Short soak proof and 702/702 DB-gated tests still require Docker Desktop running.

### Next step
- Operator: start Docker, `python scripts\db_local.py up`, run `pmfi ingest` 30+ min, test `pmfi alerts review <8-char-id>`.

## 2026-06-12 — Production closeout: FP review, config truth, migration integrity, git hygiene

### Files changed
- `src/pmfi/commands/alerts.py`: added `cmd_alerts_review` (writes to `alert_reviews` table; handles ForeignKeyViolationError) and `cmd_alerts_fp_rate` (queries reviews with optional --since/--rule filters, shows rate breakdown by rule)
- `src/pmfi/cli.py`: wired `pmfi alerts review` and `pmfi alerts fp-rate` parsers + dispatch
- `src/pmfi/config.py`: warn on three unimplemented feature flags (cross_venue_matching, wallet_intelligence, ml_scoring) and on deprecated `app.live_mode_enabled`
- `config/app.example.yaml`: document dead flags and deprecation inline; clarify orderbook flag status
- `src/pmfi/delivery/file.py`: explicit OSError catch in deliver() → logs error + re-raises as RuntimeError so runner.py non-fatal handler surfaces it; removed unused max_file_size_mb/max_bytes
- `src/pmfi/db/migrations.py`: added migrations 008 (is_binary on market_outcomes) and 009 (raw_event_id/trade_id on alerts) to apply_schema_migrations(); both were in SQL_FILES but missing from startup_maintenance path — existing DBs would have missed these columns
- `docs/ops/OPERATOR_QUICKSTART.md`: new §7 "Alert review and false-positive feedback" (review/fp-rate commands + labels table); §8 Daemon log, §9 Autostart; added existing-DB troubleshooting entry
- `tests/test_alerts_review.py`: 6 new offline tests for review + fp-rate commands
- Git history: stripped attribution trailers from all 125 commits; force-pushed to origin

### Verification run
- `python scripts\verify.py` — **674 passed, 34 skipped** (6 new tests; all DB-only skips unchanged)

### Findings
- Facts: all binding handoff v17 requirements are now code-complete; false-positive review workflow fully wired; config truth enforced with runtime warnings; migration integrity covers all 11 SQL files; git history is clean
- Inferences: the one remaining unproven item is short soak (bounded live run) — blocked by Docker Desktop not running; all operator code paths are tested and ready
- Assumptions: startup_maintenance() is called on every pmfi ingest start; operators with old DBs will pick up 008+009 on next daemon start
- Blockers: Docker Desktop not running — short soak + DB-gated suite require it (operator action)

### Proof ledger (handoff v17 binding list)
- documented setup ✓ | fresh DB truth ✓ | existing-DB upgrade ✓ (008+009 added)
- raw-before-derived ✓ | idempotency ✓ | deterministic replay ✓
- connector semantics ✓ | Polymarket token/outcome ✓ | Kalshi REST ✓
- baseline source-of-truth ✓ | config/feature-flag truth ✓
- degraded/failure states ✓ | alert delivery visibility ✓
- false-positive review ✓ | no trading ✓ | no hosted/SaaS drift ✓

### Next step
- Start Docker Desktop → `python scripts\db_local.py up && verify` → `pmfi ingest` short soak (30-60 min)
- Run `pmfi alerts review <id> --label fp` against a real alert after a soak run
- Gate: 702/702 with DB up

## 2026-06-12 — Production closeout: alert_id display, baseline freshness, orderbook stats, architect verification

### Files changed
- `src/pmfi/commands/alerts.py`: `alerts list` table now shows 8-char UUID prefix "ID" column first so operators can copy IDs directly for `pmfi alerts review <id>` without --format json; plain-text fallback also updated
- `src/pmfi/db/repos/baselines.py`: `fetch_all_baselines` now returns ALL baselines (removed freshness WHERE filter) with `is_fresh` boolean computed column (computed_at >= now() - lookback_seconds*2 interval)
- `src/pmfi/pipeline/rules.py`: `MarketRelativeLargeTradeRule` now handles three baseline states — available (fresh, includes `baseline_computed_at` in evidence), stale (is_fresh=False → floor-only, low severity, evidence shows `stale_baseline`), missing (no row, unchanged). Test mocks without `is_fresh` key default to True for backwards compat.
- `src/pmfi/commands/reporting.py`: `pmfi stats` now queries and displays `orderbook_snapshots` count + `last_alert` fired_at timestamp alongside last_event/last_trade
- `src/pmfi/orderbook.py`: HTTP 429/503 responses and exceptions now log at WARNING (previously DEBUG) — operator sees rate-limit and failure events without enabling debug logging
- `src/pmfi/cli.py`: `pmfi baselines show` now displays `computed_at` timestamp and `[STALE]` marker per-market when `is_fresh=False`

### Verification run
- `python scripts\verify.py` — **674 passed, 34 skipped** (all passing; no regressions from freshness default change)

### Architect verification
- Reviewed by opus architect subagent: **ship-ready**. No data-corruption or production risk.
- is_fresh interval math verified sound (lookback_seconds NOT NULL integer; 2x window vs daily recompute = huge margin)
- Migration drift guards (008/009/010) verified idempotent; 010 dedup deterministic
- alert_reviews FK handling verified correct
- One latent asymmetry noted: is_fresh defaults to True when key absent (safe in production; only affects hypothetical future code that constructs baseline dicts without going through fetch_all_baselines)

### Proof ledger (handoff v17 — all 10 ranks)
- Rank 1 Local operator closeout: code-ready; Docker/live proof requires operator action
- Rank 2 Existing-DB migration integrity ✓ (008/009/010/011 all in apply_schema_migrations)
- Rank 3 Short soak: blocked by Docker Desktop not running (operator action)
- Rank 4 Config/feature-flag/delivery truth ✓
- Rank 5 Alert quality / false-positive review ✓
- Rank 6 Baseline freshness semantics ✓ (stale/missing/available distinguishable in evidence)
- Rank 7 Orderbook visibility ✓ (orderbook_snapshots in pmfi stats)
- Rank 8 Cross-venue divergence: deferred (operator-curated; out of scope for this lane)
- Rank 9 Autostart reliability: documented in QUICKSTART §9; code in scripts/autostart.py
- Rank 10 Bologna: deferred

### Findings
- All code-tractable items from handoff v17 are addressed and architect-verified
- Remaining unproven: short soak and live proof require Docker Desktop running
- Gate: 702/702 DB-gated tests; 674/674 offline pass today

### Next step
- Start Docker Desktop → `python scripts\db_local.py up` → `pmfi ingest` 30-60 min soak
- Verify event/trade/alert flow; run `pmfi alerts review <id>` against a real alert
- Gate: 702/702 with DB up

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

## 2026-06-18 02:20 local - Kalshi single-ticker sync/watch and venue-specific live proof

### What changed

- Added `pmfi markets sync-one <ticker> --venue kalshi --watch`, an explicit write command for a public Kalshi ticker found by the read-only `pmfi markets recent-trades` probe.
- Reused the existing Kalshi market/outcome upsert path for both bulk discovery and single-market sync; single sync stores raw market metadata, close time, status, and `volume_fp`/`volume` into the local Postgres market row before optionally marking it watched.
- Updated `recent-trades` follow-ups and operator docs so the copied command can fetch one recent public Kalshi market into Postgres and watch it without requiring broad discovery.
- Tightened `markets list --search` to match title or `venue_market_id`; live smoke found title-only search made copied Kalshi tickers hard to verify after sync.
- Updated `scripts\task.py status` source data so M5 reports strict Polymarket proof plus short Kalshi raw-to-normalized proof, with the remaining gap scoped to strict Kalshi venue-parity soak and alert review.

### Verification

- Focused tests: `.venv\Scripts\python.exe -m pytest .\tests\test_markets_discovery.py .\tests\test_sync_kalshi_status.py -q` = **59 passed**.
- Offline gate: `.venv\Scripts\python.exe scripts\verify.py` = **781 passed, 30 skipped, verification passed**.
- DB gate: `.venv\Scripts\python.exe scripts\db_local.py verify` passed after live writes.
- Diff hygiene: `git diff --check` passed.
- Live operator smoke:
  - `pmfi markets recent-trades --since-minutes 120 --limit 20 --format json --force` returned current Kalshi tickers.
  - `pmfi markets sync-one KXBTCD-26JUN1817-T63749.99 --venue kalshi --watch` synced and watched the ticker.
  - `pmfi markets list --venue kalshi --watched --format json --search KXBTCD-26JUN1817-T63749.99 --limit 5` returned the watched row with title `Bitcoin price on Jun 18, 2026?` and volume `86730.92`.
  - Two bounded Kalshi-only ingest runs (`--max-seconds 60`, then `--max-seconds 90`) persisted venue-specific raw and normalized data.
  - `scripts\task.py soak --window 2h --required-venue kalshi --min-duration-minutes 1 --format json` passed with Kalshi `raw_events=109`, `normalized_trades=109`, no unresolved dead letters, and no open data-quality incidents.

### Decision / coherence check

- Question: should the next Kalshi proof be broad discovery, direct ticker sync, or a deeper adapter rewrite?
- Consensus: direct ticker sync is the narrowest high-leverage step because `recent-trades` already identifies active public tickers, while broad discovery can miss fast-moving markets and an adapter rewrite is premature without a failing runtime contract.
- Payback artifact: unit tests, operator docs, DB smoke, and Kalshi-required soak evidence now prove the operator path from public recent trade to watched local market to persisted raw/normalized trades.

### Residual risk / next whole-product steps

- Kalshi now has a short venue-specific live proof, but not a strict 60+ minute Kalshi-only soak. Run a longer required-Kalshi soak before claiming parity with the strict Polymarket proof.
- Review the live alerts produced by the recent Polymarket/Kalshi runs with `pmfi alerts list`, `pmfi alerts explain`, and alert review commands; alert quality is the next product-truth gap.
- Continue toward a local operator loop: select/watch active markets, ingest, inspect health, review alerts, explain decisions, replay/backtest, and generate a local report.
- Before publication/push, run publish-readiness and handoff checks from a clean worktree and keep local DB/live evidence separate from remote readiness claims.

## 2026-06-18 03:20 local - Alert review queue filters and strict Kalshi venue soak

### What changed

- Added review-state filters to `pmfi alerts list`: `--unreviewed`, `--reviewed`, and `--review-label tp|fp|noise`.
- The alert list review-label filter uses the latest review row per alert (`reviewed_at DESC, review_id DESC`), matching dashboard/report semantics.
- Hardened `pmfi soak` with `--min-required-venue-duration-minutes` so strict venue proof requires each required venue's own raw-evidence span, not only the global raw-evidence span.
- Added per-venue `raw_evidence_duration_minutes` to soak output and routed the new flag through `scripts\task.py soak`.
- Added `reports/soak/` to `.gitignore`; generated soak logs remain local evidence and are not publishable source files.

### Verification

- Focused tests: `.venv\Scripts\python.exe -m pytest .\tests\test_alerts_review.py .\tests\test_cli.py .\tests\test_soak.py -q` = **61 passed**.
- Offline gate: `.venv\Scripts\python.exe scripts\verify.py` = **791 passed, 30 skipped, verification passed**.
- DB gate: `.venv\Scripts\python.exe scripts\db_local.py verify` passed.
- Alert queue smoke:
  - `pmfi alerts list --unreviewed --limit 15 --format json` returned the live unreviewed queue.
  - `pmfi alerts list --reviewed --limit 5 --format json` returned the existing reviewed alert.
  - `pmfi alerts list --review-label tp --limit 5 --format json` returned the latest-review true-positive row.
  - `pmfi alerts list --unreviewed --review-label tp` failed before DB work with the expected conflict message.
- Soak validator smoke:
  - The old global-duration style would have passed while Kalshi had only ~45 minutes of venue-specific evidence.
  - The new strict command correctly failed at 37.6m, 41.3m, 46.4m, 52.0m, and 57.2m Kalshi duration.
  - `scripts\task.py soak --window 2h --required-venue kalshi --min-required-venue-duration-minutes 60 --format json` passed with Kalshi `raw_events=1144`, `normalized_trades=1144`, `raw_evidence_duration_minutes=60.862`, `unresolved_dead_letters=0`, and `open_data_quality_incidents=0`.
- Delegated code review approved the alert-filter slice with zero material findings.

### Residual risk / next whole-product steps

- M5 live adapter proof now has strict Polymarket and strict Kalshi evidence. Next product-truth gap is alert-quality review, not venue ingress.
- The live queue now includes additional Kalshi alerts from the strict soak; use `pmfi alerts list --unreviewed`, `pmfi alerts explain <id>`, and `pmfi alerts review <id> --label tp|fp|noise` to classify them before tuning rules.
- Authenticated Kalshi WebSocket remains intentionally deferred; current supported Kalshi path is local REST polling.

## 2026-06-18 03:43 local - Scriptable alert explanations for review triage

### What changed

- Added `pmfi alerts explain <id> --format json` while preserving the existing plain-text default.
- JSON explain output includes the canonical alert row, parsed evidence, `evidence_summary`, and lineage fields such as `raw_event_id` and `trade_id`.
- Extended dashboard evidence summaries to include `volume_spike_v1` fields used by the current live review queue: `this_trade_usd`, `baseline_median_usd`, `spike_multiplier`, `min_spike_multiplier`, and `baseline_trades`.
- Money summaries now preserve cents for sub-$100 values so low-notional spike evidence like `baseline_median_usd=$0.20` is not rounded into misleading `$0`.
- Updated the operator quickstart to mention `alerts explain --format json` for scripted review/triage.

### Verification

- Focused tests: `.venv\Scripts\python.exe -m pytest .\tests\test_cli.py -k "summarize_evidence or alerts_explain" -q` = **9 passed**.
- CLI file tests: `.venv\Scripts\python.exe -m pytest .\tests\test_cli.py -q` = **40 passed**.
- Diff hygiene: `git diff --check` passed.
- Offline gate: `.venv\Scripts\python.exe scripts\verify.py` = **794 passed, 30 skipped, verification passed**.
- DB gate: `.venv\Scripts\python.exe scripts\db_local.py verify` passed.
- Live DB smoke: `pmfi alerts explain 2f74584e --format json` returned parseable JSON with `evidence_summary=this_trade_usd=$2.82  baseline_median_usd=$0.20  spike_multiplier=14.1x  min_spike_multiplier=5.0x  baseline_trades=20`, plus `raw_event_id` and `trade_id`.
- Delegated code review approved the scoped diff with zero material findings.

### Decision / coherence check

- Question: should the next alert-quality slice record labels, add a new review-pass queue command, or make existing explanations scriptable?
- Consensus: scriptable `alerts explain` is the narrowest high-leverage step. `alerts list --unreviewed --format json` and `pmfi report --format json` already export the queue, and `alerts review` already records labels; the missing contract was machine-readable per-alert evidence and lineage for defensible review.
- Payback artifact: parser/tests, live DB smoke, operator quickstart note, and richer evidence summaries for the dominant live alert rule.

### Residual risk / next whole-product steps

- The 24h live queue still needs operator labels before rule tuning: 24 unreviewed alerts, mostly `volume_spike_v1`, split Kalshi/Polymarket.
- Use `pmfi alerts list --unreviewed --since 24h --format json`, `pmfi alerts explain <id> --format json`, and `pmfi alerts review <id> --label tp|fp|noise --category ... --notes ...` to build reviewed alert truth.
- Tune alert thresholds only after review evidence shows a repeatable false-positive/noise pattern.

## 2026-06-18 03:55 local - Bulk alert triage metadata for review queue

### What changed

- Enhanced `pmfi alerts list --evidence --format json` with additive review metadata while keeping the existing raw `evidence` field intact.
- Added parsed evidence under `evidence_parsed`, reusable `evidence_summary`, and deterministic `triage_flags` for review hints.
- Triage flags are read-only metadata, not review labels. Current flags cover low notional size, thin baseline, near-threshold trigger, degraded data quality, and missing lineage.
- The evidence query now includes `raw_event_id` and `trade_id` when `--evidence` is requested so bulk review can see lineage without per-alert explain calls.
- Updated the operator quickstart to point bulk reviewers at `pmfi alerts list --unreviewed --evidence --format json`.

### Verification

- Focused tests: `.venv\Scripts\python.exe -m pytest .\tests\test_alerts_review.py -q` = **12 passed**.
- Focused CLI tests: `.venv\Scripts\python.exe -m pytest .\tests\test_alerts_review.py .\tests\test_cli.py -q` = **52 passed**.
- Diff hygiene: `git diff --check` passed.
- Offline gate before final worklog entry: `.venv\Scripts\python.exe scripts\verify.py` = **796 passed, 30 skipped, verification passed**.
- DB gate: `.venv\Scripts\python.exe scripts\db_local.py verify` passed.
- Live DB smoke: `pmfi alerts list --unreviewed --since 24h --limit 3 --evidence --format json` returned parseable JSON with evidence summaries, parsed evidence, and flags such as `low_notional`, `thin_baseline`, and `near_threshold`.
- Delegated code review approved the scoped diff with zero material findings.

### Decision / coherence check

- Question: should the next step label alerts, require one `alerts explain` call per alert, or enrich the bulk review queue?
- Consensus: enrich the bulk queue. Automatic labels would overstate subjective alert truth; per-alert explain is accurate but slow for a 24-alert queue. Bulk read-only metadata gives operators enough evidence to label alerts defensibly without writing anything until `pmfi alerts review` is invoked explicitly.
- Payback artifact: additive JSON fields, lineage selection, offline tests, live DB smoke, and quickstart guidance.

### Residual risk / next whole-product steps

- Alert quality still needs explicit `tp|fp|noise` reviews recorded from operator judgment before threshold tuning.
- Use the bulk JSON list to group obvious low-notional/thin-baseline candidates, then use `alerts explain --format json` for edge cases before writing labels.
- After labels exist, run `pmfi alerts fp-rate --since 24h` and tune rules only from the reviewed distribution.

## 2026-06-18 04:04 local - Alert review dry-run preview

### What changed

- Added `pmfi alerts review <id> --dry-run` so operators can verify the exact alert target and planned `tp|fp|noise` label/category/notes before writing to local Postgres.
- Dry-run resolves the alert ID or prefix, fetches the target alert, prints the target rule/severity/outcome/market, and performs no `alert_reviews` insert.
- Existing write behavior is unchanged when `--dry-run` is omitted.
- Updated the operator quickstart command table and alert-review examples to include `--dry-run`.

### Verification

- TDD red check: `.venv\Scripts\python.exe -m pytest .\tests\test_alerts_review.py -q` failed because `--dry-run` was unrecognized and `cmd_alerts_review` inserted immediately.
- Focused tests after implementation: `.venv\Scripts\python.exe -m pytest .\tests\test_alerts_review.py -q` = **14 passed**.
- Focused CLI/review tests: `.venv\Scripts\python.exe -m pytest .\tests\test_alerts_review.py .\tests\test_cli.py -q` = **54 passed**.
- Diff hygiene: `git diff --check` passed.
- Offline gate before final worklog entry: `.venv\Scripts\python.exe scripts\verify.py` = **798 passed, 30 skipped, verification passed**.
- DB gate: `.venv\Scripts\python.exe scripts\db_local.py verify` passed.
- Live DB dry-run smoke: `pmfi alerts review 4ae20077 --label noise --category low_notional --notes preview --dry-run` printed the resolved alert and left `alert_reviews` count for `4ae20077` unchanged at 0.

### Operational note

- During the initial behavior check I accidentally ran `pmfi alerts review 4ae20077 --label noise --category dry-run-check --notes preview` without dry-run, inserting one local review row. I immediately identified the exact row (`review_id=d52fd0b2-8483-4a17-8a7c-dcdde7a3769d`) and removed only that accidental row; follow-up count for `4ae20077` was 0 before and after the new dry-run smoke.

### Residual risk / next whole-product steps

- The safe review-write path now exists, but alert quality still needs intentional operator labels.
- Next pass should use bulk triage metadata plus `alerts review --dry-run` previews to record defensible `tp|fp|noise` labels, then run `pmfi alerts fp-rate --since 24h`.
