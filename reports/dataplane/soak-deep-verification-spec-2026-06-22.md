# M2-SOAK-DEEP - Deeper Soak Measurement Verification Spec (2026-06-22)

Pre-committed before the M2-SOAK-DEEP implementation. Operator chose to pursue
deeper measurement first: produce alarm-grade local soak numbers before any guard
is wired. This remains measure-only and recommend-only: no guard, config value,
or live operational threshold changes are authorized by this spec.

## Goal

1. Leak-slope profile: run a longer bounded local scratch-DB soak and report
   late-window memory growth separately from early warm-up growth. The late
   window is the leak signal; multi-day and multi-host proof remain accepted debt.
2. Contention profile: run with workload concurrency materially above pool size,
   require at least 20 pool-acquire samples, and report a contended p95 rather
   than an idle-path p95.

## Fence

- Local-only, no live venue calls.
- Scratch DB only; primary DB must remain unchanged.
- Bounded by count and/or duration; no unbounded loop.
- Recommend-only; do not wire `operational_health`, config defaults, guard
  thresholds, or daemon behavior.
- Keep the existing fast soak manifest and baseline manifest intact.

## Verification Target

Merge is admissible only if the implementation proves:

- windowed trend logic has plateau and sustained-growth red controls;
- contention evidence is genuinely saturated, not idle-path;
- both profiles are bounded local scratch-DB runs;
- recommendations are emitted as candidates only;
- scratch DBs are cleaned up and the primary DB fingerprint is unchanged.

## RESOLUTION - post-hoc backfill (2026-07-02)

This section was backfilled after the M2-SOAK-DEEP merge because the report was
left without a resolution section during context compaction.

Merged evidence source: commit `b700509` (`M2-SOAK-DEEP deeper soak
measurement`) and its `WORKLOG.md` entry.

- Leak-slope profile: PASS. The merged run processed 30,000 events in 413.651s
  with 64 samples, pool p95 0.031ms from 30,064 samples, total memory growth
  7.572MB, late-window growth 0.059MB per 1,000 events, early-window growth
  0.965MB per 1,000 events, late/early ratio 0.061, verdict
  `warmup_plateau`, dead letters 0, and successful recovery.
- Contention profile: PASS. The merged run processed 8,000 events in 36.345s
  with 36 samples, pool p95 105.612ms from 8,036 samples, p95-to-idle ratio
  3520.4, `material_contention=true`, total memory growth 6.259MB, late-window
  growth 0.803MB per 1,000 events, dead letters 0, and successful recovery.
- Recommendation posture: still recommend-only. The leak-slope candidate and
  contended-pool p95 candidate were surfaced for later operator sign-off; no
  config default, daemon guard, `operational_health` wiring, or alert behavior
  changed in this milestone.
- DB/scope evidence: scratch cleanup reported no remaining `pmfi_soak_%`
  databases, and the primary counts remained unchanged at
  raw_events=661377, normalized_trades=492623, dead_letters=108, alerts=318.

Resolution verdict: M2-SOAK-DEEP met the bounded local measurement target and
remained a measurement artifact, not an applied operational-threshold change.
