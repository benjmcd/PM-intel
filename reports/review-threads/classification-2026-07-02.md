# Review Thread Classification - 2026-07-02

Target checked: `origin/main` at `b814c7f643bd34222a428272c9eea4404adac576`.

Inputs:
- `reports/review-threads/unresolved-threads-2026-06-21.json` from the root checkout, read-only.
- `reports/review-threads/classification-2026-06-21.md` from the root checkout, read-only.
- Fresh worktree branch `codex/review-burndown` under `worktrees/rvbd`.

Verdict notes:
- `fixed_on_main(<commit>)` means the thread was already addressed before this branch.
- `still_open` means the defect was still real at `b814c7f`; this branch adds the fix and a regression test.
- `handoff_to_gauge_lane` means the affected file is fenced for the concurrent gauge lane and was not edited here.
- Line references below are from this worktree after this branch's fixes unless the verdict is explicitly `fixed_on_main`.

## Per-Thread Verdicts

| idx | verdict | evidence |
| --- | --- | --- |
| 0 | `fixed_on_main(715de78)` | Duplicate recovery is serialized by `_acquire_duplicate_recovery_lock` and the lock spans disposition check plus recovery processing in `src/pmfi/pipeline/runner.py`; DB race coverage exists in `tests/test_dq3_recovery_trial_db.py`. |
| 1 | `fixed_on_main(a98f60c)` | DQ3 evidence records `scrubbed_git_remote(...)` rather than raw remotes in `src/pmfi/qualification/dq3_recovery.py`. |
| 2 | `fixed_on_main(715de78)` | The processing-claim marker is now emitted after raw persistence and recovery locking; `tests/test_dq3_recovery_trial_db.py` asserts `processing_claim_raw_rows == 1` and `processing_claim_accounted_rows == 1`. |
| 3 | `fixed_on_main(0f70dc2)` | DQ3 now detects inflated metric aggregates; `tests/test_dq3_recovery_trial_db.py::test_dq3_metric_window_invariant_detects_inflated_aggregate` plants the inflation and expects `duplicate_metric_windows == 1`. |
| 4 | `still_open` | Fixed in this branch: `SingleActiveIngestLock.reacquire()` plus `PoolManager(on_recreate=...)` reclaims the guard after pool recreation; covered by `tests/test_ingest_single_active.py` and `tests/test_ingest_supervisor.py`. |
| 5 | `fixed_on_main(0f70dc2)` | `SingleActiveIngestLock._connect()` retries before giving up, preserving startup/restart tolerance. |
| 6 | `fixed_on_main(0f70dc2)` | DQ1 now records `buffer_high_water_mark` before chunking and tests oversized page materialization in `tests/test_review_cleanup_a.py`. |
| 7 | `still_open` | Fixed in this branch: DQ1 stores `dq1_observation` page/ordinal metadata and counts verified lineage with `_count_verified_lineages`; covered by `tests/test_review_cleanup_a.py::test_dq1_lineage_verification_requires_stored_observation_metadata`. |
| 8 | `fixed_on_main(0f70dc2)` | DQ1 payload-hash expectations compare persisted payload truth for source-ID rows. |
| 9 | `fixed_on_main(0f70dc2)` | DQ1 checkpoint evidence no longer advances past failed raw durability scope. |
| 10 | `fixed_on_main(251a7c7)` | DQ1 includes an explicit concurrency probe source channel and concurrency measurements. |
| 11 | `fixed_on_main(0f70dc2)` | Schema evidence uses the shared SQL-directory fingerprint rather than hashing only the initial migration. |
| 12 | `fixed_on_main(a98f60c)` | DQ1 evidence records `scrubbed_git_remote(...)`. |
| 13 | `fixed_on_main(0f70dc2)` | DQ1 duplicate-canonical-fact checks use the same venue/trade identity semantics as the DB guard. |
| 14 | `invalid/wontfix(scope-deferred)` | DQ1 fault injection is not the active outage/overflow fault harness; DQ3 carries the restart/fault-injection proof. |
| 15 | `still_open` | Fixed in this branch: Kalshi REST page trades are yielded oldest-first by created time before cursor advancement; covered by `tests/test_kalshi_rest_adapter.py`. |
| 16 | `still_open` | Fixed in this branch: malformed optional fee fields produce `invalid_fee_usd` warnings instead of dropping the trade; covered by `tests/test_normalization.py`. |
| 17 | `fixed_on_main(057cf39)` | Volume-spike seed history now has bounded TTL/LRU controls while preserving count-based seed history. |
| 18 | `still_open` | Fixed in this branch: duplicate recovery ignores advisory `post_normalize` dead letters as final dispositions; covered by `tests/test_runner_integrity_floor.py`. |
| 19 | `still_open` | Fixed in this branch: enabled-rule validation counts only registered rule IDs; covered by `tests/test_us005_rules.py`. |
| 20 | `fixed_on_main(c0d3e95)` | `scripts/verify.py` assigns its environment flag directly and the entrypoint test is caller-env independent. |
| 21 | `fixed_on_main(0f70dc2)` | `pyproject.toml` preserves pytest recursion exclusions. |
| 22 | `handoff_to_gauge_lane` | `src/pmfi/commands/daemon.py` is fenced for the gauge lane. |
| 23 | `handoff_to_gauge_lane` | `src/pmfi/commands/daemon.py` is fenced for the gauge lane. |
| 24 | `still_open` | Fixed in this branch: `AlertEngine.reload_rules()` restores prior state if rebuilding throws on unparseable values; covered by `tests/test_us005_rules.py`. |
| 25 | `still_open` | Fixed in this branch: `pmfi doctor --json` emits JSON for non-loopback refusal; covered by `tests/test_us004_init_doctor.py`. |
| 26 | `fixed_on_main(5781539)` | `pmfi rules` rejects unknown rule fields before the engine can ignore them. |
| 27 | `still_open` | Fixed in this branch: `pmfi backtest --from 24h` accepts relative windows; covered by `tests/test_backtest.py`. |
| 28 | `fixed_on_main(b700509)` | `docs/implementation/02_task_graph.yaml` now records the 60-minute high-capacity live durability proof. |
| 29 | `fixed_on_main(9f26f42)` | `WORKLOG.md` now contains the later alert-review/status queue evidence and current-floor governance notes. |
| 30 | `fixed_on_main(b700509)` | `docs/implementation/02_task_graph.yaml` records the current 60-minute proof command and evidence window. |
| 31 | `fixed_on_main(b700509)` | `docs/implementation/02_task_graph.yaml` records `--kalshi-trade-poll-max-pages 50`. |
| 32 | `invalid/wontfix(point-in-time-index)` | `docs/REPO_INDEX.md` is a dated 2026-06-20 assessment with explicit provenance; broad regeneration is outside this narrow code review-burndown PR. |
| 33 | `fixed_on_main(0bc9c21)` | Kalshi `count_fp` fractional REST trades are accepted by the normalizer. |
| 34 | `still_open` | Fixed in this branch: latest-review indexing filters by raw event time via `JOIN raw_events` and `COALESCE(re.exchange_ts, re.received_at)`; covered by `tests/test_data_reports.py`. |
| 35 | `handoff_to_gauge_lane` | `src/pmfi/data_reports.py` is fenced for the gauge lane. |
| 36 | `still_open` | Fixed in this branch: `pmfi backtest --limit -1` is rejected by CLI parsing; covered by `tests/test_backtest.py`. |
| 37 | `handoff_to_gauge_lane` | `src/pmfi/data_reports.py` is fenced for the gauge lane. |
| 38 | `fixed_on_main(0f70dc2)` | Scoring uses the closest satisfied large-trade threshold rather than a misleading max-margin calculation. |
| 39 | `fixed_on_main(0f70dc2)` | Market-relative large-trade margin logic includes the capital floor. |
| 40 | `fixed_on_main(9f26f42)` | Current status/worklog docs reflect the 850 USD `volume_spike_v1` floor and current-floor governance. |
| 41 | `fixed_on_main(057cf39)` | DB write timeout/connectivity errors are classified as connection-loss failures in the runner. |
| 42 | `still_open` | Fixed in this branch: DB-path progress no longer resets outage streaks; covered by `tests/test_ingest_supervisor.py`. |
| 43 | `still_open` | Fixed in this branch: half-open trials no longer clear failure state before a successful clean run; covered by `tests/test_ingest_supervisor.py`. |
| 44 | `handoff_to_gauge_lane` | `src/pmfi/commands/_shared.py` is fenced for the gauge lane. |
| 45 | `still_open` | Fixed in this branch: numeric retention booleans accept only explicit 0/1 and otherwise fail closed; covered by `tests/test_config.py`. |
| 46 | `handoff_to_gauge_lane` | `src/pmfi/commands/daemon.py` is fenced for the gauge lane. |
| 47 | `handoff_to_gauge_lane` | `src/pmfi/commands/daemon.py` is fenced for the gauge lane. |
| 48 | `handoff_to_gauge_lane` | `src/pmfi/commands/daemon.py` is fenced for the gauge lane. |
| 49 | `fixed_on_main(0fdd2e2)` | Replay/backtest DB baselines are loaded before JSON fallback. |
| 50 | `still_open` | Fixed in this branch: `compute_and_store_baselines()` prunes stale market-scope rows for the recomputed lookback; covered by `tests/test_baseline_recompute.py`. |
| 51 | `fixed_on_main(51638428)` | Ingest preflight failures propagate a non-zero return code. |
| 52 | `fixed_on_main(6e265c7)` | Ingest runner venue variable shadowing was removed. |
| 53 | `still_open` | Fixed in this branch: Polymarket asset resolution fills `market` while preserving an existing outcome; covered by `tests/test_venue_dispatch.py`. |
| 54 | `fixed_on_main(1edf240f)` | Startup maintenance applies new schema migrations before runtime paths depend on them. |
| 55 | `fixed_on_main(4dc97251)` | Live ingest refreshes baselines during the stream. |
| 56 | `fixed_on_main(0f70dc2)` | Polymarket discovery paginates beyond the first 100 markets. |

## Coverage Summary

- Total indices: 57.
- `fixed_on_main`: 31.
- `still_open` on `b814c7f` and fixed in this branch: 16.
- `handoff_to_gauge_lane`: 8.
- `invalid/wontfix`: 2.
- Unaccounted: 0.

## Verification

- Focused red/green examples were captured for the new branch fixes, including DQ1 lineage, runner advisory dispositions, rules reload, single-active re-acquire, backtest parsing, review-time filtering, and stale-baseline pruning.
- Affected file suites: `180 passed`.
- Full repo verifier: `python scripts\verify.py` -> `1345 passed, 94 skipped`, verification passed.
- Local DB verifier: `python scripts\db_local.py verify` -> Postgres ready and schema readiness passed.
