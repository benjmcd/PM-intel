# Review Thread Classification Addendum - 2026-07-05

Post-merge closeout for `reports/review-threads/classification-2026-07-02.md`.

## Live Authority

- `origin/main` was fetched and verified at `a487ede` (`Close review burndown defects (#86)`).
- Gauge-lane merge commits checked for adjudication: PR #84 = `7615d7c`, PR #85 = `2725e89`.
- Thread state was re-read from the 57-item review inventory using REST comment mapping plus thread-scoped GraphQL `isResolved` enrichment. GraphQL writes were limited to `resolveReviewThread` for the still-open #86-fixed set.

## Closeout Summary

- The 15 still-unresolved `still_open` threads from the prior pass received post-merge evidence replies and were resolved.
- The 16th `still_open` classification item, idx 7, already had a distinct review thread and was already resolved at closeout start.
- Therefore the 16-fixed-in-PR vs 15-left-open discrepancy is not a missing review thread. It is idx 7 (`PRRT_kwDOSyqVUc6LGM8G`), which mapped to a distinct thread that was already `isResolved=true`.
- All 8 `handoff_to_gauge_lane` threads received post-merge adjudication replies and remain open. PR #84 and PR #85 did not fix those findings.
- Final 57-inventory live state: 31 `fixed_on_main` threads resolved, 16 `still_open` threads resolved, 2 `invalid/wontfix` threads resolved, and 8 `handoff_to_gauge_lane` threads open. Total: 49 resolved, 8 open.

Consensus: resolve only threads with current-main fix evidence. Do not convert a gauge handoff into a resolved thread unless the merged gauge PRs actually touched and fixed the implicated behavior.

## Still-Open Fixed-In-#86 Threads

| idx | source PR | thread | final state | closeout evidence |
| --- | --- | --- | --- | --- |
| 4 | #48 | `PRRT_kwDOSyqVUc6LGo0z` | resolved | Replied with `a487ede` evidence for `SingleActiveIngestLock.reacquire()` / pool recreation coverage. |
| 7 | #46 | `PRRT_kwDOSyqVUc6LGM8G` | already resolved | Re-query showed this distinct DQ1 lineage thread was already resolved before this closeout. |
| 15 | #44 | `PRRT_kwDOSyqVUc6LFx4t` | resolved | Replied with `a487ede` evidence for oldest-first Kalshi REST page ordering. |
| 16 | #44 | `PRRT_kwDOSyqVUc6LFx4w` | resolved | Replied with `a487ede` evidence for non-fatal malformed optional fee warnings. |
| 18 | #42 | `PRRT_kwDOSyqVUc6LEyAC` | resolved | Replied with `a487ede` evidence for ignoring advisory `post_normalize` dead letters as durable dispositions. |
| 19 | #42 | `PRRT_kwDOSyqVUc6LEyAD` | resolved | Replied with `a487ede` evidence for registered-rule-only enabled validation. |
| 24 | #39 | `PRRT_kwDOSyqVUc6LD_vo` | resolved | Replied with `a487ede` evidence for `AlertEngine.reload_rules()` prior-state rollback. |
| 25 | #39 | `PRRT_kwDOSyqVUc6LD_vq` | resolved | Replied with `a487ede` evidence for JSON-mode doctor refusal. |
| 27 | #39 | `PRRT_kwDOSyqVUc6LD_vs` | resolved | Replied with `a487ede` evidence for relative `pmfi backtest --from` windows. |
| 34 | #26 | `PRRT_kwDOSyqVUc6LB_PD` | resolved | Replied with `a487ede` evidence for latest-review filtering by raw-event time. |
| 36 | #26 | `PRRT_kwDOSyqVUc6LB_PL` | resolved | Replied with `a487ede` evidence for rejecting negative backtest limits. |
| 42 | #21 | `PRRT_kwDOSyqVUc6LACjm` | resolved | Replied with `a487ede` evidence for preserving DB-outage failure streaks despite DB-path progress. |
| 43 | #21 | `PRRT_kwDOSyqVUc6LACjo` | resolved | Replied with `a487ede` evidence for keeping failed half-open trials circuit-open. |
| 45 | #18 | `PRRT_kwDOSyqVUc6K_rvD` | resolved | Replied with `a487ede` evidence for explicit 0/1 retention boolean parsing. |
| 50 | #3 | `PRRT_kwDOSyqVUc6Hs9a5` | resolved | Replied with `a487ede` evidence for pruning stale `market_baselines` rows on recompute. |
| 53 | #2 | `PRRT_kwDOSyqVUc6Hp7Rn` | resolved | Replied with `a487ede` evidence for filling Polymarket market IDs while preserving existing outcomes. |

## Gauge-Handoff Adjudication

| idx | source PR | thread | final state | adjudication |
| --- | --- | --- | --- | --- |
| 22 | #40 | `PRRT_kwDOSyqVUc6LEMgj` | open | PR #84 and PR #85 did not touch `src/pmfi/commands/daemon.py`; daemon reload rollback remains unclosed by gauge work. |
| 23 | #40 | `PRRT_kwDOSyqVUc6LEMgk` | open | PR #84 and PR #85 did not touch `src/pmfi/commands/daemon.py`; unchanged-rule retry behavior remains unclosed. |
| 35 | #26 | `PRRT_kwDOSyqVUc6LB_PI` | open | PR #84 touched `src/pmfi/data_reports.py` for FP-rate/current-floor governance only; it did not broaden the data-coverage non-trade skip allowlist. |
| 37 | #26 | `PRRT_kwDOSyqVUc6LB_PN` | open | PR #84 touched `src/pmfi/data_reports.py` for FP-rate/current-floor governance only; it did not account for duplicate trade skips in coverage buckets. |
| 44 | #20 | `PRRT_kwDOSyqVUc6K_1p_` | open | PR #84 and PR #85 did not touch `src/pmfi/commands/_shared.py`; multi-host dashboard DSN rejection remains unclosed. |
| 46 | #18 | `PRRT_kwDOSyqVUc6K_rvH` | open | PR #84 and PR #85 did not touch `src/pmfi/commands/daemon.py`; heartbeat-before-maintenance behavior remains unclosed. |
| 47 | #18 | `PRRT_kwDOSyqVUc6K_rvL` | open | PR #84 and PR #85 did not touch `src/pmfi/commands/daemon.py`; partial prune-result reporting remains unclosed. |
| 48 | #18 | `PRRT_kwDOSyqVUc6K_rvN` | open | PR #84 and PR #85 did not touch `src/pmfi/commands/daemon.py`; pruning remains ungated on successful partition creation by those PRs. |

## Documentation Nits

- The #86 WORKLOG fence note should be read as a narrow check against the named gauge-lane file list, not as a global no-change assertion.
- The #85 dry-run regression proves full venue-code dry-run labels by checking `[dry:stub]` and negative `[dry:poly]`; it does not assert a separate literal gate string.
- The #84 WORKLOG FP-rate numbers were point-in-time PR-authoring evidence. Later 2026-07-02 labels moved the live current-floor `volume_spike_v1` cohort to reviewed=89, FP+Noise=31.5%, status=BREACH; that supersedes the old point-in-time status without changing the gauge-honesty behavior.
