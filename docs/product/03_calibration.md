# 03 - Alert Calibration Decisions

This file records packet-backed alert calibration decisions. It is not a trading
policy, not a predictive-performance claim, and not remote publication proof.

## Packet-backed calibration decision - 2026-06-18

### Evidence

- Source packet: local ignored `reports\review-packets\smoke.json`.
- reviewed alerts: 24.
- volume_spike_v1: 23 noise reviews, all category `low_notional_thin_baseline`.
- market_relative_large_trade_v1: 1 true positive review, category `market_relative_outlier_sparse_baseline`.
- Packet context: `raw_events=30529`, `normalized_trades=2948`, `unresolved_dead_letters=0`, `open_data_quality_incidents=0`.
- Existing control: volume_spike_v1.min_trade_usd: 500.
- Replay proof already recorded in `WORKLOG.md`: zero volume_spike_v1 alerts below the configured 500 USD floor.

### Decision

Decision: do not change alert thresholds in this slice.

The reviewed `volume_spike_v1` noise cohort supports the existing 500 USD floor
that was already fixture-tested and replay-validated. The packet does not add
new post-floor reviewed noise evidence that would justify raising the floor
again. The lone `market_relative_large_trade_v1` alert is reviewed true positive,
so market_relative_large_trade_v1 remains unchanged.

### Next Proof Target (Completed Below)

Next proof target: fresh post-calibration live or soak proof.

Future threshold changes still need new reviewed packet evidence plus replay or
fresh-soak proof. A generated review packet is an audit input; it is not enough
by itself to justify a rule change.

## Post-calibration sample review - 2026-06-18

### Evidence

- Source window: exact live sample from `2026-06-18T15:59:55.4707173Z` through `2026-06-18T16:09:57.9192278Z`.
- Exact soak result: `raw_events=4075`, `normalized_trades=206`, `alerts=5`, `unresolved_dead_letters=0`, `open_data_quality_incidents=0`, `raw_evidence_duration_minutes=9.985`.
- Venue evidence: Kalshi `raw_events=178`, Kalshi `normalized_trades=178`, Polymarket `raw_events=3897`, Polymarket `normalized_trades=28`.
- Reviewed labels: 3 true positives, 1 false positive, 1 noise.
- True positives: 2 clean `directional_cluster_v1` no-side cluster alerts and 1 `large_trade_absolute_v1` payout-notional alert with low-capital caveat.
- False positive: 1 `directional_cluster_v1` row with category `directional_outcome_mismatch`, where the stored alert outcome was `yes` but evidence `dominant_side` was `no`.
- Noise: 1 `volume_spike_v1` row with category `low_notional_thin_near_threshold`, where the trade was exactly at the 5.0x spike threshold with `this_trade_usd=940`.
- Packet artifact: ignored local `reports\review-packets\post-calibration-batch-091456.json`.

### Decision

Decision: do not change alert thresholds in this slice.

The sample contains actionable post-calibration true positives, one near-threshold
spike-noise row, and one directional false positive caused by persistence
attribution rather than scoring. One noise row is not enough to raise
`volume_spike_v1.min_trade_usd`, and the directional false positive was handled
as an implementation fix: future directional and momentum alerts persist under
the detected `dominant_side` when available.

### Next Proof Target

Next proof target: run another bounded post-fix sample or wait for the next
natural directional-cluster alert batch, then verify new rows persist under the
detected side before using the sample for threshold decisions.

## Post-fix non-directional sample review - 2026-06-18

### Evidence

- Source window: exact live sample from `2026-06-18T16:43:21.1993165Z` through `2026-06-18T16:58:23.6777118Z`.
- Exact soak result: `raw_events=4717`, `normalized_trades=90`, `alerts=3`, `unresolved_dead_letters=0`, `open_data_quality_incidents=0`, `raw_evidence_duration_minutes=14.983`.
- Venue evidence: Kalshi `raw_events=57`, Kalshi `normalized_trades=57`, Polymarket `raw_events=4660`, Polymarket `normalized_trades=33`.
- Directional outcome audit: exact `pmfi alerts outcome-audit` returned `checked=0`, so the sample does not prove new directional or momentum rows persist under `dominant_side`.
- Reviewed labels: 3 true positives, 0 false positives, 0 noise.
- True positives: 2 Kalshi `volume_spike_v1` rows above the configured 500 USD floor and far above the 5x spike threshold, with low-notional/thin-baseline caveats; 1 Polymarket `large_trade_absolute_v1` payout-notional row with low-capital caveat.
- Packet artifact: ignored local `reports\review-packets\post-fix-audit-20260618-100023.json`.

### Decision

Decision: do not change alert thresholds in this slice.

This reviewed post-fix batch supports keeping the current post-calibration
rules: the `volume_spike_v1` rows were above the active floor and not near the
spike threshold, and the `large_trade_absolute_v1` row matched payout-notional
rule intent with the low-capital caveat retained. The sample contains no new
directional or momentum rows, so it cannot close the dominant-side persistence
proof gap.

### Next Proof Target

Next proof target: use `pmfi alerts outcome-audit --since <run-start> --until
<run-end> --strict` on a future bounded run that actually emits
`directional_cluster_v1` or `momentum_v1`.

## Second post-fix non-directional sample review - 2026-06-18

### Evidence

- Source window: exact live sample from `2026-06-18T17:08:08.8821609Z` through `2026-06-18T17:38:11.3953775Z`.
- Exact soak result: `raw_events=10328`, `normalized_trades=144`, `alerts=2`, `unresolved_dead_letters=0`, `open_data_quality_incidents=0`, `raw_evidence_duration_minutes=29.978`.
- Venue evidence: Kalshi `raw_events=66`, Kalshi `normalized_trades=66`, Polymarket `raw_events=10262`, Polymarket `normalized_trades=78`.
- Directional outcome audit: exact `python scripts\task.py outcome-audit` returned `checked=0`; strict mode returned `ok=false` with `exit_code=1` because the sample had no directional or momentum rows.
- Reviewed labels: 2 true positives, 0 false positives, 0 noise.
- True positives: 1 Kalshi `market_relative_large_trade_v1` row with category `post_fix_market_relative_large_trade`, and 1 Kalshi `volume_spike_v1` row with category `post_fix_volume_spike`. Both came from the same no-side Bitcoin trade with `capital_at_risk_usd=6207.60`; the market-relative row exceeded `min_capital_threshold_usd=5000` and p99.5 baseline `1056.90`, while the spike row was 244.01x a 25.44 USD baseline median.
- Packet artifact: ignored local `reports\review-packets\post-fix-30m-20260618-104021.json`.

### Decision

Decision: do not change alert thresholds in this slice.

This reviewed post-fix batch adds two true positives and no new noise. It
supports keeping the current post-calibration rules, but it contains no new
`directional_cluster_v1` or `momentum_v1` rows and therefore cannot close the
dominant-side persistence proof gap.

### Next Proof Target

Next proof target: use `python scripts\task.py outcome-audit --since
<run-start> --until <run-end> --strict` on a future bounded run that actually
emits `directional_cluster_v1` or `momentum_v1`.

## Refreshed-Kalshi strict live sample review - 2026-06-18

### Evidence

- Source window: exact live sample from `2026-06-18T23:02:27Z` through `2026-06-18T23:12:27Z`.
- Watchlist refresh: recent Kalshi trade probes identified current tickers; `pmfi markets sync-one ... --venue kalshi --watch` added fresh Kalshi markets before the run.
- Exact strict soak result: `raw_events=6047`, `normalized_trades=1698`, `alerts=10`, `unresolved_dead_letters=0`, `open_data_quality_incidents=0`, `raw_evidence_duration_minutes=9.982`.
- Venue evidence: Kalshi `raw_events=1644`, Kalshi `normalized_trades=1644`, Kalshi `raw_evidence_duration_minutes=9.89`; Polymarket `raw_events=4403`, Polymarket `normalized_trades=54`, Polymarket `raw_evidence_duration_minutes=9.982`.
- Directional outcome audit: exact `python scripts\task.py outcome-audit --strict` returned `checked=1`, `matched=1`, `mismatches=0`, `missing_dominant_side=0`; alert `e793a2f4` stored `outcome_key=yes` matching evidence `dominant_side=yes`.
- Reviewed labels: 1 true positive, 0 false positives, 9 noise.
- True positive: 1 Kalshi `directional_cluster_v1` row with category `fresh_kalshi_directional_cluster`, `net_capital_usd=19060.357400`, `cluster_trade_count=165`, `price_impact_cents=2.0000`, and medium side confidence.
- Noise: 9 Kalshi `volume_spike_v1` rows with category `live_low_notional_thin_baseline`; each cleared the configured 500 USD floor but carried `low_notional` and `thin_baseline` triage flags.
- Packet artifact: ignored local `reports\review-packets\live-proof-20260618-160224-reviewed.json`.

### Decision

Decision: do not change alert thresholds in this slice.

This sample closes the natural live-observation gap for the dominant-side
persistence fix: a fresh directional row persisted under the audited dominant
side. It also adds a concentrated batch of low-notional/thin-baseline
`volume_spike_v1` noise. That noise supports continued calibration review, but
one short refreshed-watchlist run is not enough to raise the 500 USD floor or
change spike logic without replay or another fresh-soak sample.

### Next Proof Target

Next proof target: accumulate another reviewed packet after the refreshed
Kalshi watchlist has been active longer, then use replay or fresh-soak proof
before changing thresholds.

## Wrapper-backed refreshed-Kalshi strict live sample review - 2026-06-18

### Evidence

- Source window: exact live sample from `2026-06-18T23:38:56.533631+00:00` through `2026-06-18T23:47:56.705874+00:00`.
- Watchlist refresh: `python scripts\task.py refresh-watchlist --since-minutes 30 --limit 50 --top 5 --format json --sync --watch` selected and watched 5 active Kalshi tickers before the run.
- Exact strict soak result: `raw_events=9703`, `normalized_trades=6699`, `alerts=14`, `unresolved_dead_letters=0`, `open_data_quality_incidents=0`, `raw_evidence_duration_minutes=8.987`.
- Venue evidence: Kalshi `raw_events=6685`, Kalshi `normalized_trades=6685`, Kalshi `raw_evidence_duration_minutes=8.904`; Polymarket `raw_events=3018`, Polymarket `normalized_trades=14`, Polymarket `raw_evidence_duration_minutes=8.987`.
- Directional outcome audit: exact `python scripts\task.py outcome-audit --strict` returned `checked=4`, `matched=4`, `mismatches=0`, `missing_dominant_side=0` across 3 `directional_cluster_v1` rows and 1 `momentum_v1` row.
- Reviewed labels: 5 true positives, 0 false positives, 9 noise.
- True positives: 3 Kalshi `directional_cluster_v1` rows with category `fresh_kalshi_directional_cluster`; 1 Kalshi `momentum_v1` row with category `fresh_kalshi_momentum`; 1 Kalshi `market_relative_large_trade_v1` row with category `refreshed_kalshi_market_relative_baseline_pending`.
- Noise: 8 Kalshi `volume_spike_v1` rows with category `live_low_notional_thin_baseline`; 1 Kalshi `market_relative_large_trade_v1` row with category `baseline_missing_near_threshold`.
- Packet artifact: ignored local `reports\review-packets\strict-refresh-20260618-163854-reviewed.json`.
- Runtime caveat: the run logged repeated Kalshi REST poll-window overflow warnings for hot ticker `KXBTC15M-26JUN181945-45`; poll limit/page-count knobs are now configurable, but a tuned no-overflow live proof is still needed before treating hot-ticker capture as complete.

### Decision

Decision: do not change alert thresholds in this slice.

This sample adds a second strict refreshed-Kalshi reviewed batch and proves the
Windows task wrapper can feed live watchlist refresh into a persisted exact
soak. It strengthens evidence that low-notional/thin-baseline spike alerts are
often not actionable, but a blunt threshold raise could suppress existing
post-calibration true positives. The next threshold step should be a replayed
candidate-rule comparison, not an immediate production rule change.

### Next Proof Target

Next proof target: design and replay a candidate suppression/refinement for
low-notional thin-baseline spike alerts, while separately rerunning Kalshi REST
with tuned poll-window config for hot tickers.

## Replayed volume-spike candidate comparison - 2026-06-18

### Evidence

- Command: `python scripts\task.py volume-spike-calibration --from 2026-06-18T23:38:56.533631+00:00 --to 2026-06-18T23:47:56.705874+00:00 --limit 0 --venue kalshi --min-trade-usd 1000 --format json`.
- Command behavior: validate-only local DB replay comparison; no alert persistence and no `config\alert_rules.yaml` change.
- Candidate: `volume_spike_v1.min_trade_usd=1000`.
- Current replay: `normalized_trades=5897`, `markets=10`, `alerts=3053`, `volume_spike_v1=60`; spike triage flags included `low_notional=58` and `thin_baseline=60`.
- Candidate replay: `normalized_trades=5897`, `markets=10`, `alerts=3015`, `volume_spike_v1=22`; spike triage flags included `low_notional=20` and `thin_baseline=22`.
- Delta: `normalized_trades_delta=0`, `alerts_delta=-38`, `volume_spike_delta=-38`, `removed_low_notional_thin_baseline=38`, `added_volume_spike_alerts=0`.

### Decision

Decision: do not change production alert thresholds in this slice.

The replay comparison shows that a 1000 USD `volume_spike_v1` floor would remove
a substantial number of low-notional/thin-baseline spike emissions in the latest
strict refreshed-Kalshi window without changing normalized-trade coverage.
However, this is one candidate on one recent window and does not by itself
settle the false-negative risk against earlier reviewed true-positive spike
rows. The tool is now available for replay-backed threshold work; config remains
unchanged until additional candidate/window comparisons justify a rule change.

### Next Proof Target

Next proof target: compare additional candidate knobs and windows with
`python scripts\task.py volume-spike-calibration`, then either record a
no-change decision or update `config\alert_rules.yaml` with focused replay proof.
Kalshi REST poll-window overflow remains a separate ingestion-hardening target;
the poll limit/page-count controls now exist, but require tuned live proof.

## Per-ticker no-overflow Kalshi proof and spike review - 2026-06-18

### Evidence

- Command: `python -m pmfi.cli ingest --max-seconds 180 --kalshi-poll-interval-seconds 1 --kalshi-trade-poll-limit 10000 --kalshi-trade-poll-max-pages 10 --log-file reports\logs\kalshi-per-ticker-proof-20260618-185219.daemon.log`.
- Code path: per-ticker Kalshi REST polling with one-second `min_ts` overlap and the documented 1000-trade page size.
- Log check: `reports\logs\kalshi-per-ticker-proof-20260618-185219.daemon.log` contained zero `Kalshi REST poll window may have overflowed` warnings.
- Exact strict soak: `raw_events=10201`, `normalized_trades=8951`, `alerts=3`, `unresolved_dead_letters=0`, `open_data_quality_incidents=0`, `raw_evidence_duration_minutes=2.952`; Kalshi and Polymarket were both present for more than 2 minutes.
- Exact outcome audit: `checked=1`, `matched=1`, `mismatches=0`, `missing_dominant_side=0` for the new `momentum_v1` row.
- Reviewed labels for the proof window: 2 true positives, 0 false positives, 1 noise.
- True positives: 1 Kalshi `market_relative_large_trade_v1` row with `capital_at_risk_usd=8942` above p99.5 `486`, and 1 Kalshi `momentum_v1` row with `net_capital_usd=75067` above threshold.
- Noise: 1 Kalshi `volume_spike_v1` row with category `live_low_notional_thin_baseline`, `this_trade_usd=659`, `low_notional`, and `thin_baseline`.
- Packet artifact: ignored local `reports\review-packets\per-ticker-proof-20260618-185219-reviewed.json`.

### Candidate Replay

- Candidate `volume_spike_v1.min_trade_usd=1000` over this proof window removed 3 low-notional/thin-baseline replayed spike emissions with `normalized_trades_delta=0`.
- Historical reviewed true-positive spike rows still include `this_trade_usd=$870` and `$970`, so a blunt 1000 USD floor would suppress known useful live-review evidence.
- Candidate `volume_spike_v1.min_trade_usd=800` removed 28 low-notional/thin-baseline replayed spike emissions in the previous strict refreshed-Kalshi window. A later cross-window replay pass with trade-USD buckets superseded the earlier mixed note for this proof window and is recorded below.

### Decision

Decision: do not change production `volume_spike_v1` thresholds in this slice.

The new proof improves Kalshi capture confidence and adds another reviewed
low-notional spike-noise row, but the candidate threshold evidence is mixed. A
1000 USD floor removes noise but would also cross known true-positive spike
amounts; an 800 USD floor is less risky for those true positives but did not
remove the new proof-window spike noise. Keep `min_trade_usd=500` until a more
selective refinement, such as combining notional, baseline thickness, and
reviewed category evidence, is replayed across additional windows.

### Next Proof Target

Run the documented 600-second diagnostic window, then rerun review-packet and
candidate replay comparisons before any production alert-threshold change.

## 600-Second Per-Ticker Kalshi Proof - 2026-06-18

### Evidence

- Command: `python -m pmfi.cli ingest --max-seconds 600 --kalshi-poll-interval-seconds 1 --kalshi-trade-poll-limit 10000 --kalshi-trade-poll-max-pages 10 --log-file reports\logs\kalshi-per-ticker-proof-600-20260618-190101.daemon.log`.
- Log check: daemon and redirected stderr logs contained zero `Kalshi REST poll window may have overflowed` or `overflowed` matches.
- Exact strict soak: `raw_events=35542`, `normalized_trades=31189`, `alerts=18`, `unresolved_dead_letters=0`, `open_data_quality_incidents=0`, and `raw_evidence_duration_minutes=9.987`.
- Venue coverage: Kalshi had `raw_events=31087`, `normalized_trades=31087`, and `raw_evidence_duration_minutes=9.489`; Polymarket had `raw_events=4455`, `normalized_trades=102`, and `raw_evidence_duration_minutes=9.984`.
- Exact outcome audit: `checked=6`, `matched=6`, `mismatches=0`, `missing_dominant_side=0` across `directional_cluster_v1` and `momentum_v1`.
- Review closeout: 18 proof-window Kalshi alerts were reviewed as 15 true positives, 0 false positives, and 3 noise rows. The review queue returned to zero.
- Packet artifact: ignored local `reports\review-packets\per-ticker-proof-600-20260618-190101-reviewed.json`.

### Candidate Replay

- Before the 2026-06-18 accumulator scalability fix, full-window validate-only calibration with `--limit 0` timed out twice, including a 300-second retry on this hot window.
- After the accumulator fix, full-window candidate `volume_spike_v1.min_trade_usd=1000` completed in about 12 seconds over this proof window, replayed `normalized_trades=18819`, reduced `volume_spike_v1` emissions from 232 to 102, and removed 130 low-notional/thin-baseline spike emissions with `normalized_trades_delta=0`.
- Full-window candidate `volume_spike_v1.min_trade_usd=800` also completed in about 12 seconds, reduced `volume_spike_v1` emissions from 232 to 144, and removed 88 low-notional/thin-baseline spike emissions with `normalized_trades_delta=0`.
- The earlier bounded 5000-trade comparison remains useful as a quick diagnostic, but it is no longer the best proof for this hot window.

### Decision

Decision: keep production `volume_spike_v1.min_trade_usd=500`.

The 600-second proof is strong capture evidence for the public REST path, and
the full-window replay comparisons show that higher notional floors would remove
many low-notional/thin-baseline emissions. They do not yet justify a production
threshold change because earlier reviewed true-positive spike rows include
values below 1000 USD. The next calibration slice should replay candidate
thresholds across multiple reviewed windows and preserve known true-positive
spike rows before changing production rules.

## Cross-window volume-spike floor decision - 2026-06-18

### Evidence

- Command shape: `python -m pmfi.cli volume-spike-calibration --from <window-start> --to <window-end> --limit 0 --venue kalshi --min-trade-usd <candidate> --format json`.
- Command behavior: validate-only local DB replay comparison; no alert persistence and no database writes.
- Calibration output now includes `volume_spike_trade_usd_buckets` for current/candidate replay and `removed_trade_usd_buckets` for removed spike alerts.

| Reviewed window | Candidate floor | Current -> candidate volume spikes | Removed low-notional/thin-baseline | Removed trade-USD buckets |
|---|---:|---:|---:|---|
| `2026-06-18T23:02:27+00:00` to `2026-06-18T23:12:27+00:00` | 1000 | 21 -> 3 | 18 | `500_to_799=11`, `800_to_999=7` |
| same | 800 | 21 -> 10 | 11 | `500_to_799=11`, `800_to_999=0` |
| `2026-06-18T23:38:56.533631+00:00` to `2026-06-18T23:47:56.705874+00:00` | 1000 | 62 -> 22 | 40 | `500_to_799=28`, `800_to_999=12` |
| same | 800 | 62 -> 34 | 28 | `500_to_799=28`, `800_to_999=0` |
| `2026-06-19T01:52:19+00:00` to `2026-06-19T01:55:20+00:00` | 1000 | 43 -> 17 | 26 | `500_to_799=15`, `800_to_999=11` |
| same | 800 | 43 -> 28 | 15 | `500_to_799=15`, `800_to_999=0` |
| `2026-06-19T02:01:01+00:00` to `2026-06-19T02:11:05+00:00` | 1000 | 232 -> 102 | 130 | `500_to_799=88`, `800_to_999=42` |
| same | 800 | 232 -> 144 | 88 | `500_to_799=88`, `800_to_999=0` |

All eight comparisons had `normalized_trades_delta=0`. Across these reviewed
windows, a 1000 USD floor removed 214 low-notional/thin-baseline spike emissions
but also removed 72 replayed spike emissions in the 800-999 USD band. That band
overlaps the documented prior true-positive spike evidence at 870 USD and
970 USD. An 800 USD floor removed 142 low-notional/thin-baseline spike emissions
and removed zero replayed spike emissions in the 800-999 USD band.

### Decision

Decision: set production `volume_spike_v1.min_trade_usd=800`.

The 800 USD floor is the narrowest supported threshold change from the current
evidence. It removes the repeated 500-799 USD low-notional/thin-baseline spike
cohort while preserving the reviewed true-positive risk band that makes a 1000
USD floor too blunt. This remains a local alert-quality decision, not a
predictive-performance or trading claim.

### Next Proof Target

Run post-change replay/live-soak evidence and review any new spike alerts under
the 800 USD floor. Row-level reviewed-TP matching is still not claimed because
read-only replay results do not carry persisted `trade_id` values; this decision
uses aggregate bucket evidence plus the documented reviewed true-positive
amounts.

## Post-800 floor replay audit - 2026-06-18

### Evidence

- Command: `python -m pmfi.cli volume-spike-floor-audit --from 2026-06-19T02:01:01+00:00 --to 2026-06-19T02:11:05+00:00 --limit 0 --venue kalshi --format json`.
- Command behavior: validate-only local DB replay with the current configured `config\alert_rules.yaml`; no alert persistence, no database writes, no config changes, and no live API calls.
- Configured rule: `volume_spike_v1.min_trade_usd=800`.
- Replay window: the documented 600-second Kalshi no-overflow proof window.
- Current replay: `normalized_trades=18819`, `markets=4`, `alerts=16338`, `volume_spike_v1=144`.
- Current volume-spike trade-USD buckets: `unknown=0`, `lt_500=0`, `500_to_799=0`, `800_to_999=42`, `gte_1000=102`.
- Floor check: `below_floor_volume_spike_alerts=0`, `unknown_trade_usd_volume_spike_alerts=0`, `passed=true`, `evidence_status=current_floor_clean`.

### Decision

Decision: the exact replay audit supports the configured 800 USD floor on the
600-second hot Kalshi window.

This closes the immediate replay-proof gap for the post-800 configuration. It
does not claim fresh persisted post-change alert review, row-level reviewed-TP
matching, predictive performance, or trading utility.

### Next Proof Target

Run a fresh bounded persisted live/soak sample under the 800 USD floor, then
review any new persisted `volume_spike_v1` rows. Keep authenticated
WebSocket/backfill deferred unless the public REST path regresses.

## Fresh post-800 live review - 2026-06-18

### Evidence

- Source window: exact live sample from `2026-06-19T04:00:45.3850667Z` through `2026-06-19T04:10:51.8437266Z`.
- Watchlist refresh: `python scripts\task.py refresh-watchlist --since-minutes 30 --limit 50 --top 5 --sync --watch --replace-watch --format json` failed closed until `PMFI_ENABLE_LIVE=1` was set, then watched 5 active Kalshi tickers before ingest.
- Ingest command: `python -m pmfi.cli ingest --max-seconds 600 --kalshi-poll-interval-seconds 1 --kalshi-trade-poll-limit 10000 --kalshi-trade-poll-max-pages 10 --log-file reports\logs\post-800-live-20260619-040045.daemon.log`.
- Overflow scan: no `Kalshi REST poll window may have overflowed` matches in the ignored local daemon log.
- Exact strict soak result: `raw_events=32884`, `normalized_trades=30737`, `alerts=18`, `unresolved_dead_letters=0`, `open_data_quality_incidents=0`, `raw_evidence_duration_minutes=9.964`.
- Venue evidence: Kalshi `raw_events=30724`, Kalshi `normalized_trades=30724`, Kalshi `duration_minutes=9.913`; Polymarket `raw_events=2160`, Polymarket `normalized_trades=13`, Polymarket `duration_minutes=9.955`.
- Directional outcome audit: exact `python scripts\task.py outcome-audit --strict` returned `checked=6`, `matched=6`, `mismatches=0`, `missing_dominant_side=0` across `directional_cluster_v1` and `momentum_v1` rows.
- Current-floor audit: exact `python scripts\task.py volume-spike-floor-audit` returned configured `volume_spike_v1.min_trade_usd=800`, `volume_spike_v1=33`, buckets `unknown=0`, `lt_500=0`, `500_to_799=0`, `800_to_999=15`, `gte_1000=18`, and `below_floor_volume_spike_alerts=0`.
- Reviewed labels: 8 true positives, 0 false positives, 10 noise.
- True positives: 3 Kalshi `directional_cluster_v1` rows with category `fresh_kalshi_directional_cluster`; 3 Kalshi `momentum_v1` rows with category `fresh_kalshi_momentum`; 1 Kalshi `large_trade_absolute_v1` row with category `capital_threshold_low_payout_notional` because `capital_at_risk_usd=27900` cleared `min_capital_at_risk_usd=25000` while `payout_notional_usd=45000` stayed below `min_payout_notional_usd=100000`; 1 Kalshi `market_relative_large_trade_v1` row with category `refreshed_kalshi_market_relative_baseline_pending`.
- Noise: 7 Kalshi `volume_spike_v1` rows with category `live_low_notional_thin_baseline`; 3 Kalshi `market_relative_large_trade_v1` rows with category `baseline_missing_near_threshold`.
- Packet artifact: ignored local `reports\review-packets\post-800-live-20260619-040045-reviewed-v2.json`, generated after the corrected large-trade latest review row.

### Decision

Decision: keep `volume_spike_v1.min_trade_usd=800` for now and pursue a
selective spike refinement next.

The fresh sample verifies the floor invariant in persisted traffic: no
post-change spike alert fell below 800 USD or lacked trade-USD evidence. It also
shows the floor is not sufficient as the final noise control: every persisted
`volume_spike_v1` row in this fresh sample still carried `low_notional` and
`thin_baseline` and was reviewed as noise. A blunt 1000 USD floor remains too
coarse because cross-window replay showed it cuts into the 800-999 USD band that
overlaps documented reviewed true-positive spike evidence.

### Next Proof Target

Design a validate-only candidate that targets the combined
low-notional/thin-baseline shape, replay it across the reviewed Kalshi windows,
and only then update `config\alert_rules.yaml` if it preserves the documented
800-999 USD true-positive risk band.

## Conditional baseline-maturity spike candidate - 2026-06-18

### Evidence

- Implemented a validate-only candidate knob for `volume_spike_v1`: `low_notional_min_baseline_trades`.
- The candidate requires extra pre-trade history only for trades below the low-notional threshold while still appending every trade to rolling history. It leaves the existing 20-trade median window unchanged, so it cannot create new alerts by changing the baseline median.
- Routed the knob through `python -m pmfi.cli volume-spike-calibration` and `python scripts\task.py volume-spike-calibration`; optional `--low-notional-threshold-usd` is available for threshold diagnostics.
- Focused tests covered engine behavior, parser support, task-wrapper forwarding, and candidate rule construction.

Seeded replay over the fresh post-800 window was neutral for `low_notional_min_baseline_trades` values from 30 through 200: `normalized_trades_delta=0`, `added_volume_spike_alerts=0`, and `removed_volume_spike_alerts=0`. This means the candidate is specifically a cold-start maturity control, not a mature DB replay noise control.

Cold-start replay used the same reviewed Kalshi windows:

| Reviewed window | Candidate low-notional baseline trades | Current -> candidate volume spikes | Removed low-notional/thin-baseline | Removed trade-USD buckets |
|---|---:|---:|---:|---|
| `2026-06-18T23:02:27+00:00` to `2026-06-18T23:12:27+00:00` | 50 | 9 -> 9 | 0 | `800_to_999=0`, `gte_1000=0` |
| `2026-06-18T23:38:56.533631+00:00` to `2026-06-18T23:47:56.705874+00:00` | 50 | 33 -> 33 | 0 | `800_to_999=0`, `gte_1000=0` |
| `2026-06-19T01:52:19+00:00` to `2026-06-19T01:55:20+00:00` | 50 | 27 -> 27 | 0 | `800_to_999=0`, `gte_1000=0` |
| `2026-06-19T02:01:01+00:00` to `2026-06-19T02:11:05+00:00` | 50 | 144 -> 144 | 0 | `800_to_999=0`, `gte_1000=0` |
| `2026-06-19T04:00:45.385066+00:00` to `2026-06-19T04:10:51.843726+00:00` | 50 | 33 -> 31 | 2 | `800_to_999=1`, `gte_1000=1` |
| `2026-06-19T04:00:45.385066+00:00` to `2026-06-19T04:10:51.843726+00:00` | 30 | 33 -> 32 | 1 | `800_to_999=1`, `gte_1000=0` |
| `2026-06-19T04:00:45.385066+00:00` to `2026-06-19T04:10:51.843726+00:00` | 100 | 33 -> 31 | 2 | `800_to_999=1`, `gte_1000=1` |
| `2026-06-19T04:00:45.385066+00:00` to `2026-06-19T04:10:51.843726+00:00` | 200 | 33 -> 29 | 4 | `800_to_999=2`, `gte_1000=2` |

All listed comparisons had `normalized_trades_delta=0` and `added_volume_spike_alerts=0`. Broader cross-window cold-start checks showed candidate `100` and `200` remove more low-notional/thin-baseline spike emissions, but they also remove replayed 800-999 USD spike alerts in historical windows. That is too close to the documented 870 and 970 USD true-positive risk band without row-level reviewed-TP matching.

### Decision

Decision: do not change `config\alert_rules.yaml` in this slice.

The candidate mechanism is coherent and now executable, but the evidence is not
strong enough for a production default. Candidate `50` is the only value tested
that preserves all historical 800-999 buckets, but it only removes two replayed
spikes in the fresh post-800 window and still removes one 800-999 replayed
spike there. Because this slice's replay comparison was aggregate, not row-level
latest-review matching, enabling it would have overclaimed preservation of the
documented true-positive risk band.

### Next Proof Target

Add row-level replay-to-review matching or a reviewed-packet comparison that can
identify whether candidate-removed replayed spikes correspond to reviewed true
positives, reviewed noise, or unpersisted in-memory-only emissions. Until that
exists, keep `volume_spike_v1.min_trade_usd=800` as the active production noise
control.

## Row-level baseline-maturity replay matching - 2026-06-18

### Evidence

- `replay_from_db` now carries `raw_event_id` into replay results so candidate deltas can be compared with persisted local alert lineage.
- `volume-spike-calibration` now loads latest persisted `volume_spike_v1` review metadata by `raw_event_id` for the same replay window and optional venue/market filters.
- Calibration output now reports removed/added persisted review matches, unmatched replay-only emissions, review labels, and review categories. Persisted alerts with no latest review are counted as `unreviewed`.
- Exact smoke: `python scripts\task.py volume-spike-calibration --from 2026-06-19T04:00:45.385066+00:00 --to 2026-06-19T04:10:51.843726+00:00 --limit 0 --venue kalshi --low-notional-min-baseline-trades 50 --cold-start --format json`.
- Smoke result: `normalized_trades=15620`, current `volume_spike_v1=33`, candidate `volume_spike_v1=31`, `removed_volume_spike_alerts=2`, `removed_low_notional_thin_baseline=2`, removed buckets `800_to_999=1` and `gte_1000=1`, `review_data_provided=true`, `removed_review_matches=0`, and `removed_review_unmatched=2`.

### Decision

Decision: do not change `config\alert_rules.yaml` in this slice.

The row-level pass resolves the previous aggregate-only ambiguity for the fresh
post-800 candidate-50 smoke: the two removed replay spikes do not correspond to
persisted reviewed alerts in that window. That lowers the specific concern that
candidate `50` would have removed a known reviewed true positive there.
However, it also means the checked candidate did not prove removal of persisted
reviewed noise. Its observed benefit remains small and cold-start-specific, so
the production config stays at the current 800 USD floor.

### Next Proof Target

Move the next pass to operator UX and review workflow quality. The backend now
has enough local replay, review, and calibration evidence to support better
analyst-facing flows; the dashboard should make reviewed-vs-unreviewed state,
triage flags, evidence, and calibration context easier to compare without
requiring CLI-only packet inspection.

## Dashboard aggregate calibration context - 2026-06-18

### Evidence

- The CLI and dashboard now share the same read-only `pmfi.volume_spike_calibration` service for current/candidate DB replay and latest-review matching.
- Dashboard route: `GET /api/volume-spike-calibration`.
- Route behavior: explicit ISO timestamp and candidate parsing; HTTP 400 for invalid inputs; HTTP 422 for insufficient replay evidence; no DB writes, config changes, report artifacts, or live API calls.
- Dashboard UI: click-only volume-spike calibration panel; no replay runs on page load.
- Exact smoke through both CLI and dashboard over `2026-06-19T04:00:45.385066+00:00` through `2026-06-19T04:10:51.843726+00:00` with `--venue kalshi --low-notional-min-baseline-trades 50 --cold-start` returned `normalized_trades=15620`, current `volume_spike_v1=33`, candidate `volume_spike_v1=31`, `volume_spike_delta=-2`, `removed_low_notional_thin_baseline=2`, `removed_review_matches=0`, and `removed_review_unmatched=2`.
- Headed and headless browser checks rendered the aggregate result without console errors, horizontal overflow, or detected table-cell overlaps.

### Decision

Decision: do not change `config\alert_rules.yaml` in this slice.

This pass improves calibration usability by moving aggregate validate-only replay
context into the operator dashboard. It does not add new quality evidence beyond
the row-level matching result above: the candidate removed two replay-only
unmatched emissions and no persisted reviewed noise in the checked window.

### Next Proof Target

Add row-level dashboard drilldown or a local calibration packet surface for
removed/added candidate emissions before considering a production rule change.

## Dashboard calibration delta samples - 2026-06-18

### Evidence

- The shared volume-spike calibration summary now includes bounded removed/added replay sample rows.
- Sample fields include `raw_event_id`, venue trade ID, venue, market, trade USD, baseline median, spike multiplier, triage flags, and matched review metadata when present.
- Dashboard query/UI now supports `details_limit`, capped at 50 and defaulting to 10.
- Exact dashboard API smoke with `details_limit=2` over `2026-06-19T04:00:45.385066+00:00` through `2026-06-19T04:10:51.843726+00:00` returned two removed sample rows, raw event IDs `247767` and `247241`.
- Both removed rows were unmatched replay emissions, not persisted reviewed alerts.
- Headed and headless browser checks rendered the two sample rows without console errors, horizontal overflow, or detected alert-table cell overlaps.

### Decision

Decision: do not change `config\alert_rules.yaml` in this slice.

The new row-level dashboard view makes the candidate effect inspectable in the
operator surface. It confirms the same policy-relevant result as the prior
row-level matching pass: candidate `low_notional_min_baseline_trades=50` removed
two replay-only unmatched emissions in this window and did not prove removal of
persisted reviewed noise.

### Next Proof Target

Add a full local calibration packet/export or full drilldown handoff path for
candidate deltas before using this dashboard workflow to justify a production
rule change.

## Local calibration packet export - 2026-06-18

### Evidence

- `volume-spike-calibration` now supports opt-in local packet export with `--export-packet`, `--packet-output`, and `--packet-limit`.
- Packet artifacts are constrained to ignored `reports\calibration-packets\` paths and refuse overwrites.
- Packet schema: `volume_spike_calibration_packet.v1`.
- Packet behavior: validate-only local DB replay; `persist=false`; no config writes; no live API calls.
- The packet wraps the existing `volume_spike_calibration.v1` summary and includes full removed/added delta records when requested with `--packet-limit 0`.
- Focused tests: `python -m pytest tests\test_alerts_review.py -k "volume_spike_calibration or calibration_packet" tests\test_replay_cli_offline.py::test_volume_spike_calibration_accepts_candidate_knobs tests\test_replay_cli_offline.py::test_volume_spike_calibration_defaults_validate_only_and_rejects_persist tests\test_task_operator_routes.py::test_task_volume_spike_calibration_forwards_supported_cli_flags -q` = 13 passed.
- Exact smoke: `python -m pmfi.cli volume-spike-calibration --from 2026-06-19T04:00:45.385066+00:00 --to 2026-06-19T04:10:51.843726+00:00 --limit 0 --venue kalshi --low-notional-min-baseline-trades 50 --cold-start --export-packet --packet-output packet-smoke-20260619-0410-v2.json --format json`.
- Smoke packet: ignored local `reports\calibration-packets\packet-smoke-20260619-0410-v2.json`, with removed raw event IDs `247767` and `247241`, `added_records=0`, no truncation, and `volume_spike_delta=-2`.

### Decision

Decision: do not change `config\alert_rules.yaml` in this slice.

The local packet closes the prior export/handoff gap for full candidate delta
rows, but it does not add new label truth. The checked candidate still removed
two replay-only unmatched emissions in this window and did not prove removal of
persisted reviewed noise. Packet export is now a review artifact surface, not a
threshold-change justification by itself.

### Next Proof Target

Use local calibration packets to compare candidate deltas across multiple
reviewed windows, then add a dense operator review workflow that can inspect
packet rows and convert them into a threshold decision record without mutating
DB/config state.

## Dashboard calibration packet browser - 2026-06-18

### Evidence

- Dashboard endpoints:
  - `GET /api/calibration-packets` lists direct `.json` packets under ignored `reports\calibration-packets\`, newest first.
  - `GET /api/calibration-packets/{name}` loads one parsed packet.
- Endpoint guardrails: unsafe names/path traversal/non-json names return 400, missing packets return 404, invalid JSON returns 422.
- Dashboard UI now includes a calibration packet browser inside the calibration panel. It refreshes local packet artifacts, loads a selected packet, and renders removed/added packet rows with raw event ID, venue trade ID, market, trade USD, spike multiplier, triage flags, and review metadata.
- Tests: `python -m pytest tests\test_dashboard_static.py -q` = 12 passed.
- Fresh local dashboard: `http://127.0.0.1:8770/`.
- API smoke listed `packet-smoke-20260619-0410-v2.json` and `packet-smoke-20260619-0410.json`.
- API smoke loaded `packet-smoke-20260619-0410-v2.json` with `schema_version=volume_spike_calibration_packet.v1`, removed raw event IDs `247767` and `247241`, and `added_records=0`.
- Browser smoke: headed desktop, headless desktop, and headless mobile loaded the packet rows with no console/page errors and no horizontal overflow.

### Decision

Decision: do not change `config\alert_rules.yaml` in this slice.

The dashboard can now inspect full packeted candidate deltas, but the viewed
packet still carries replay-only unmatched removals rather than persisted
reviewed noise. This improves operator review ergonomics; it does not by itself
prove a better production threshold.

### Next Proof Target

Add cross-window packet comparison and a local decision-record handoff so
operators can evaluate whether candidate removals consistently target reviewed
noise without cutting reviewed true positives.

## Dashboard calibration packet comparison - 2026-06-18

### Evidence

- Dashboard endpoint: `GET /api/calibration-packets/compare`.
- Query behavior: no `name` parameters compares all direct local packet JSON files; repeated `name=<packet.json>` parameters compare selected packets.
- Endpoint output: `schema_version=calibration_packet_comparison.v1`, `local_only=true`, `validate_only=true`, packet count, candidate groups, per-packet windows/candidates, removed/added record totals, review match/unmatched totals, review label/category counters, unique raw event IDs, and repeated raw event IDs across packets.
- Dashboard UI now includes **Compare all** in the calibration packet browser and renders aggregate comparison metrics plus a per-packet comparison table.
- Tests: `python -m pytest tests\test_dashboard_static.py -q` = 14 passed.
- Fresh local dashboard: `http://127.0.0.1:8771/`.
- API smoke comparing all current packets returned `packet_count=2`, `candidate_groups=1`, `removed_records=4`, `added_records=0`, `removed_review_labels={"unmatched": 4}`, and repeated removed raw event IDs `247241` and `247767`.
- API smoke comparing `packet-smoke-20260619-0410-v2.json` alone returned `packet_count=1`, `removed_records=2`, and `unique_removed_raw_event_ids=2`.
- Browser smoke: headed desktop, headless desktop, and headless mobile rendered the two-packet comparison with no console/page errors and no horizontal overflow.

### Decision

Decision: do not change `config\alert_rules.yaml` in this slice.

The comparison shows the current local packets are repeated smokes over the same
two unmatched replay removals. This proves the comparison surface, but it does
not prove durable reviewed-noise reduction or true-positive safety across
independent windows.

### Next Proof Target (Completed Below)

Add a local calibration decision-record handoff that consumes packet comparison
evidence and explicitly records a no-change or change-ready decision without
mutating DB/config state.

## Local calibration decision record handoff - 2026-06-19 UTC

### Evidence

- Command: `python -m pmfi.cli calibration-decision --packet packet-smoke-20260619-0410-v2.json --packet packet-smoke-20260619-0410.json --decision needs-more-evidence --rationale "Comparison removes repeated unmatched replay emissions only; no config mutation is justified." --output smoke-decision-20260619.json --format json`.
- Task wrapper route: `python scripts\task.py calibration-decision`.
- Decision artifacts are constrained to ignored `reports\calibration-decisions\` paths; bare `--output` filenames resolve inside that directory and existing files are not overwritten.
- Decision schema: `calibration_decision_record.v1`.
- Record safeguards: `local_only=true`, `validate_only=true`, `config_mutation=false`, `db_mutation=false`, and `live_calls=false`.
- Smoke artifact: ignored local `reports\calibration-decisions\smoke-decision-20260619.json`.
- Smoke comparison embedded the same two local calibration packets, `packet_count=2`, `candidate_groups=1`, `removed_records=4`, `added_records=0`, `removed_review_labels={"unmatched": 4}`, and repeated removed raw event IDs `247241` and `247767`.
- Focused tests: `python -m pytest .\tests\test_calibration_decisions.py .\tests\test_replay_cli_offline.py::test_calibration_decision_accepts_explicit_packet_record_flags .\tests\test_task_operator_routes.py::test_task_calibration_decision_forwards_supported_cli_flags -q` = 7 passed.

### Decision

Decision: record `needs-more-evidence`; do not change `config\alert_rules.yaml`
in this slice.

The record makes the packet-comparison decision explicit without treating
smoke-repeat evidence as durable alert-quality proof. The compared packets
repeat the same two unmatched replay-only removals, so they are useful for
handoff and UX verification but not enough to prove persisted reviewed noise
reduction or true-positive safety.

### Next Proof Target

Use the decision-record command after comparing independent packet windows. A
future `change-ready` decision still needs reviewed persisted-noise evidence,
cross-window replay support, and a focused config patch plus replay/fresh-runtime
proof before any threshold change is treated as complete.

## Dashboard calibration decision history - 2026-06-19 UTC

### Evidence

- Dashboard endpoints:
  - `GET /api/calibration-decisions` lists ignored local decision JSON artifacts under `reports\calibration-decisions\`, newest first, with parsed decision summaries when valid.
  - `GET /api/calibration-decisions/{name}` loads one decision record and attaches a compact dashboard summary.
- Dashboard UI now includes a read-only `Calibration decisions` section inside the calibration panel. It refreshes local decision records, loads a selected decision, displays rationale, packet selection, removed/added counts, review-label summaries, repeated raw event IDs, and no-mutation safeguards.
- Helper/static/dashboard route tests: `python -m pytest .\tests\test_calibration_decisions.py .\tests\test_dashboard_static.py -q` = 30 passed.
- Ruff check for the touched helper/server/test files passed.
- Fresh dashboard smoke on `http://127.0.0.1:8772/` returned 3 local decision artifacts; the latest `refactor-decision-20260619.json` was `decision=needs-more-evidence`, `removed_records=4`, `added_records=0`, and all safeguards true.
- Loaded decision smoke returned `schema_version=calibration_decision_record.v1`, `packet_count=2`, repeated removed raw event IDs `247241` and `247767`, and `config_mutation=false`, `db_mutation=false`, `live_calls=false`.
- Browser smoke: headed desktop, headless desktop, and headless mobile rendered the decision-history panel, loaded `needs-more-evidence`, showed `no mutation`, and had no console/page errors or horizontal overflow.

### Decision

Decision: keep decision creation in the explicit CLI/task command; keep the
dashboard decision-history surface read-only.

The browser is now adequate for inspecting local decision history, but it should
not become an implicit config or artifact writer. The CLI/task command remains
the intentional handoff boundary for local decision records.

### Next Proof Target

Build a denser cross-window calibration review workflow that makes reviewed
persisted noise, unmatched replay-only removals, true-positive risk bands, and
candidate readiness easy to compare before any future `change-ready` record or
config patch.

## Dashboard calibration packet review summary - 2026-06-19 UTC

### Evidence

- Shared helper: `pmfi.calibration_packets.calibration_packet_review_summary`.
- Dashboard endpoint: `GET /api/calibration-packets/review-summary`.
- Query behavior: no `name` parameters summarizes all direct local packet JSON files; repeated `name=<packet.json>` parameters summarize selected packets.
- Endpoint output: `schema_version=calibration_packet_review_summary.v1`, `local_only=true`, `validate_only=true`, `config_mutation=false`, `db_mutation=false`, `live_calls=false`, embedded `calibration_packet_comparison.v1`, conservative `recommendation`, rationale, risk counts, grouped removed/added records, and flattened display-ready sample rows.
- Dashboard UI now includes **Review summary** in the calibration packet browser. It renders readiness, removed reviewed noise/false-positive counts, removed true-positive risk, unmatched replay-only removals, rationale, and sample rows.
- Focused helper/static/dashboard route tests: `python -m pytest .\tests\test_calibration_packets.py .\tests\test_dashboard_static.py -q` = 21 passed.
- Ruff check for the touched helper/server/static-test files passed.

### Decision

Decision: keep the review summary read-only and do not change
`config\alert_rules.yaml` in this slice.

The summary makes the missing evidence explicit. A candidate with only
unmatched replay removals remains `needs-persisted-review-evidence`; a candidate
that removes reviewed true positives is `blocked-by-true-positive-risk`; and
only candidates removing reviewed noise/false positives without unmatched or
true-positive removals become `change-ready-candidate`.

### Next Proof Target

Generate independent calibration packets across fresh windows, then use the
review summary and explicit calibration-decision command to decide whether a
future focused config patch is justified.

## Review-summary-backed calibration decision records - 2026-06-19 UTC

### Evidence

- CLI/task flag: `--include-review-summary` on `pmfi calibration-decision` and `python scripts\task.py calibration-decision`.
- Decision record behavior: existing packet comparison remains embedded; when the flag is present, the record also embeds `review_summary` with `schema_version=calibration_packet_review_summary.v1`, recommendation, rationale, risk counts, grouped records, and display samples.
- Dashboard behavior: decision history can display the embedded review recommendation, unmatched removals, and removed true-positive risk when present.
- Smoke artifact: ignored local `reports\calibration-decisions\review-summary-decision-20260619.json`.
- Smoke result over the two current packet artifacts: `decision=needs-more-evidence`, `review_summary.recommendation=needs-persisted-review-evidence`, `removed_unmatched=4`, `removed_reviewed_noise_or_fp=0`, and `removed_reviewed_tp=0`.
- Focused tests: `python -m pytest .\tests\test_calibration_decisions.py .\tests\test_dashboard_static.py .\tests\test_replay_cli_offline.py::test_calibration_decision_accepts_explicit_packet_record_flags .\tests\test_task_operator_routes.py::test_task_calibration_decision_forwards_supported_cli_flags -q` = 35 passed.

### Decision

Decision: keep `--include-review-summary` opt-in and do not infer or write a
config patch from the decision command.

The flag reduces operator mismatch between the dashboard readiness summary and
the durable decision artifact, while preserving the explicit human decision
boundary. The current evidence still says `needs-persisted-review-evidence`;
there is no reviewed persisted-noise basis for a config mutation.

### Next Proof Target

Use this embedded-review-summary path on independent packet windows. A future
`change-ready` record should still be followed by a narrow config patch, replay
proof, fresh runtime proof, and review evidence before the threshold change is
treated as complete.

## Dashboard selected-packet calibration review - 2026-06-19 UTC

### Evidence

- Dashboard packet browser behavior: `#packet-select` is now a multi-select control.
- All-packet actions remain available through **Compare all** and **Review summary**.
- New selected actions: **Compare selected** and **Review selected** call the existing read-only packet endpoints with repeated `name=<packet.json>` query parameters.
- Endpoint authority remains unchanged: `/api/calibration-packets/compare` and `/api/calibration-packets/review-summary` already resolve selected direct packet filenames under the ignored packet root, reject unsafe names, and write no DB/config/live state.
- Focused static tests: `python -m pytest .\tests\test_dashboard_static.py -q` = 18 passed.
- Browser smoke on `http://127.0.0.1:8774/`: headed Chrome, headless desktop Chrome, and headless mobile Chrome selected two packet artifacts for comparison and one packet artifact for review summary. The observed request URLs included repeated packet names for comparison and one packet name for review summary.
- Browser smoke result: selected two-packet comparison rendered repeated removed raw event IDs `247241:2` and `247767:2`; selected one-packet review summary rendered `needs-persisted-review-evidence` with `unmatched removed=2`; single-packet load rendered packet rows `247767` and `247241`.

### Decision

Decision: keep selected-packet review as a dashboard read-only affordance.

This closes the operator ergonomics gap for choosing independent packet windows
after those packets exist. It does not create packets, reviews, decision records,
or config patches. The canonical write boundary remains the explicit
`calibration-decision` CLI/task command.

### Next Proof Target

Generate genuinely independent calibration packet windows, then use selected
dashboard compare/review summary plus `calibration-decision --include-review-summary`
to record whether evidence still says `needs-more-evidence` or supports a future
focused config patch.

## Independent-window calibration packet batch export - 2026-06-19 UTC

### Evidence

- CLI/task command: `pmfi calibration-packet-batch` and `python scripts\task.py calibration-packet-batch`.
- Input shape: repeated `--window NAME:SINCE:UNTIL` values with explicit timezone-aware ISO timestamps.
- Output behavior: each window writes one ignored local packet under `reports\calibration-packets\` using `--packet-output-prefix` plus the window name.
- Guardrails: lowercase kebab-case names/prefixes, duplicate/existing packet-output preflight, local-only validate-only replay, no DB writes, no config changes, and no live calls.
- Focused tests: `python -m pytest .\tests\test_replay_cli_offline.py .\tests\test_task_operator_routes.py .\tests\test_alerts_review.py -q` = 101 passed.
- DB smoke command: `python .\scripts\task.py calibration-packet-batch --window refreshed-a:2026-06-18T23:02:27+00:00:2026-06-18T23:12:27+00:00 --window refreshed-b:2026-06-18T23:38:56.533631+00:00:2026-06-18T23:47:56.705874+00:00 --limit 0 --venue kalshi --low-notional-min-baseline-trades 50 --cold-start --packet-output-prefix indwin-20260619 --format json`.
- DB smoke artifacts: ignored local `reports\calibration-packets\indwin-20260619-refreshed-a.json` and `reports\calibration-packets\indwin-20260619-refreshed-b.json`.
- DB smoke result: refreshed-a had current `volume_spike_v1=9`, candidate `volume_spike_v1=9`, removed records 0, added records 0; refreshed-b had current `volume_spike_v1=33`, candidate `volume_spike_v1=33`, removed records 0, added records 0.
- Overwrite guard smoke: rerunning the refreshed-a output prefix refused the existing packet before replay.
- Selected dashboard API smoke over the two independent packets returned `packet_count=2`, `removed_records=0`, `added_records=0`, and review-summary `recommendation=no-candidate-effect`.
- Decision artifact smoke: `calibration-decision --include-review-summary` over the two independent packets wrote ignored local `reports\calibration-decisions\indwin-decision-20260619.json` with `decision=needs-more-evidence` and `review_summary.recommendation=no-candidate-effect`.

### Decision

Decision: keep `low_notional_min_baseline_trades=50` validate-only and do not
change `config\alert_rules.yaml` from this batch.

The new command closes the independent-window packet generation gap. The first
two independent refreshed-Kalshi packet windows are useful negative evidence:
they show the candidate had no effect in windows that did contain current
volume-spike alerts. That is not a config-change justification.

### Next Proof Target

Run batch packet export across additional reviewed windows or candidate values
that can plausibly remove reviewed persisted noise. A future `change-ready`
decision still needs removed reviewed noise/false positives, no true-positive
risk, replay proof, fresh runtime proof, and explicit config patch review.

## Volume-spike baseline-median candidate - 2026-06-19 UTC

### Evidence

- Candidate knob: `low_notional_min_baseline_median_usd` for validate-only `volume_spike_v1` replay, packet export, packet batch export, and candidate sweeps.
- Rule shape: suppress only low-notional spikes where `this_trade_usd < low_notional_threshold_usd` and the computed baseline median is below the candidate median floor. History still records the trade after evaluation; no DB rows or config are written by validation.
- Focused tests: `python -m pytest .\tests\test_pipeline_engine.py .\tests\test_alerts_review.py .\tests\test_replay_cli_offline.py .\tests\test_task_operator_routes.py -q` = 143 passed.
- Undefined-name check: `python -m ruff check --select F821,F822,F823 ...` passed over the changed calibration/rule/parser/wrapper/test files.
- Six-window Kalshi sweep: thresholds 850 and 1000 crossed with baseline-median floors 10, 15, 20, and 25 under `--cold-start --format json`.
- Best current candidate: `baseline-default-threshold-1000-median-20` removed 46 spikes, added 0, removed 4 reviewed noise rows, removed 0 reviewed true positives, and removed 42 unmatched replay-only rows.
- Blocked candidate: `baseline-default-threshold-1000-median-25` removed one reviewed true positive.
- Packet artifacts: ignored local `reports\calibration-packets\m20-pct.json`, `m20-pfr.json`, `m20-ra.json`, `m20-rb.json`, `m20-no.json`, and `m20-p800.json`.
- Decision artifact: ignored local `reports\calibration-decisions\m20.json` records `decision=needs-more-evidence`, `removed_reviewed_noise=4`, `removed_reviewed_tp=0`, `removed_unmatched=42`, and `added_unmatched=0`.

### Decision

Decision: keep the median-baseline candidate validate-only and do not mutate
`config\alert_rules.yaml` yet.

The candidate aligns with the reviewed low-notional/thin-baseline noise shape:
the reviewed removals are noise and no reviewed true positives are removed at
median floor 20. It is still not config-ready because most removed rows are
unmatched replay-only evidence, not persisted review truth.

### Next Proof Target

Use the `m20-*` packet set as the review queue for the candidate blast radius.
If the unmatched removals are confirmed noise or false positives, rerun the
same sweep and issue a new decision record. If any are true positives, keep the
knob validate-only and search a narrower rule shape before any config patch.

## Calibration packet review queue - 2026-06-19 UTC

### Evidence

- CLI/task command: `pmfi calibration-review-queue` and `python scripts\task.py calibration-review-queue`.
- Dashboard endpoint/UI: `GET /api/calibration-packets/review-queue`, plus `Queue all` and `Queue selected` controls in the packet browser.
- Output behavior: local-only validate-only queue rows from packet delta records; no DB writes, no config changes, no report writes, and no live calls.
- Filters: `--packet`, `--state removed|added|all`, `--review-group matched_noise|matched_fp|matched_tp|matched_unreviewed|matched_other|unmatched_replay_only|all`, and `--limit`.
- Guardrail: unmatched replay-only rows are marked `persisted_alert_reviewable=false` and require manual packet/raw-event inspection rather than an alert-review write.
- Focused tests before cluster summaries: `python -m pytest .\tests\test_calibration_packets.py .\tests\test_dashboard_static.py .\tests\test_replay_cli_offline.py .\tests\test_task_operator_routes.py -q` = 82 passed.
- Real `m20-*` queue smoke: six packets returned `available_rows=46`, `filtered_rows=42`, `returned_rows=42`, `candidate_groups=1`, and `truncated=false` for `state=removed`, `review_group=unmatched_replay_only`.
- Cluster summary update: the queue now also returns `market_clusters` computed from filtered rows before limit truncation and surfaced in CLI text output plus the dashboard review queue table.
- Focused cluster tests: `python -m pytest .\tests\test_calibration_packets.py .\tests\test_dashboard_static.py .\tests\test_replay_cli_offline.py .\tests\test_task_operator_routes.py -q` = 84 passed.
- Real cluster smoke: the six-packet `m20-*` queue returned 10 market clusters for the 42 unresolved unmatched removals; the top four clusters cover 31 rows.
- First cluster targets: `KXWCGAME-26JUN18MEXKOR-MEX` has 13 rows, `KXWCGAME-26JUN18MEXKOR-TIE` has 9 rows, `KXBTC15M-26JUN181945-45` has 5 rows, and `KXBTC15M-26JUN190015-15` has 4 rows.
- Market-cluster filter update: CLI/task now supports `--market-cluster <cluster-key>`, the API accepts `market_cluster`, and the dashboard queue controls include a `Market cluster` input for `Queue all` and `Queue selected`.
- Filter behavior: exact-match, case-sensitive filtering uses the same canonical key as `market_clusters`, applies after state/review-group filters and before limit truncation, and returns `filters.market_cluster`.
- Dashboard action update: each market-cluster row now has a compact `Use` button that fills the `Market cluster` input with the exact key and reruns `Queue all` through the same read-only endpoint.
- Row key update: returned queue rows now include `market_cluster`, and CLI/dashboard row details print that same canonical key so row inspection cannot drift from the active filter.
- Cluster review artifact update: `python scripts\task.py calibration-cluster-review` now writes ignored local `calibration_cluster_review.v1` JSON artifacts for one exact market cluster, including the full filtered queue rows, raw event IDs, cluster summary, explicit assessment, rationale, and `persisted_alert_review=false`.
- Raw-lineage embedding update: `calibration-cluster-review --include-raw-events` now embeds the same read-only local Postgres `raw_event_lookup.v1` evidence inside the artifact before it is written. `--include-raw-payload` additionally embeds full raw public payloads. The default artifact path remains packet-only and does not require DB access.
- Cluster review coverage update: `python scripts\task.py calibration-cluster-review-summary` now compares current queue clusters with local cluster-review artifacts and reports covered/uncovered clusters, latest assessment, review artifact name, and missing raw-event counts without writing DB/config/live state.
- Dashboard cluster-review browser update: the localhost dashboard now exposes read-only `GET /api/calibration-cluster-reviews`, `GET /api/calibration-cluster-reviews/{name}`, and `GET /api/calibration-cluster-reviews/coverage` plus a **Cluster reviews** panel that lists local artifacts, shows safeguards, renders embedded raw-event lookup rows and compact raw lookup profiles when present, and summarizes all/default or selected-packet coverage with the current market-cluster filter.
- Decision embedding update: `python scripts\task.py calibration-decision --include-cluster-review-summary` now embeds the local cluster-review coverage summary into ignored decision artifacts and dashboard decision summaries. The latest raw-lineage-backed `m20-no.json` decision smoke recorded `covered=3`, `uncovered=0`, `assessment_counts.uncertain=3`, and still `decision=needs-more-evidence`.
- Raw event lookup update: `python scripts\task.py raw-events --id <raw_event_id>` now gives operators a read-only local Postgres lookup for raw event lineage and joined normalized trade facts. It replaces ad hoc SQL during packet/raw-event review, supports repeated IDs, and can include the full raw payload in JSON with `--include-payload`.
- Focused filter tests: `python -m pytest .\tests\test_calibration_packets.py .\tests\test_dashboard_static.py .\tests\test_replay_cli_offline.py .\tests\test_task_operator_routes.py -q` = 90 passed.
- Real filtered smokes: `KXWCGAME-26JUN18MEXKOR-MEX` returned 13 filtered rows from `m20-no.json`; `KXBTC15M-26JUN181945-45` returned 5 filtered rows from the six-packet queue.
- Dashboard `Use` smoke: clicking `Use` for `KXWCGAME-26JUN18MEXKOR-TIE` filtered the dashboard queue to 9 rows with no console errors.

### Decision

Decision: use the review queue as the next calibration proof surface, not as an
automatic labeler.

Replay-only packet rows are not persisted alert rows. The queue therefore
narrows and exposes the unresolved blast radius without pretending that the
repo can append review labels for rows that have no persisted alert target.

### Next Proof Target

Work through independent packet windows and persisted-review evidence, not a
config patch. For `m20-no.json`, the current three queue clusters are covered by
raw-lineage-backed local cluster-review artifacts, but all latest assessments
remain `uncertain`, so the median20/threshold1000 candidate remains
validate-only. If future packet/raw-event review confirms clusters as
noise/false positives, rerun the sweep and write a new decision record. If any
cluster is true-positive risk, keep the candidate validate-only and narrow the
rule shape.

## Volume-spike calibration sweep - 2026-06-19 UTC

### Evidence

- CLI/task command: `pmfi volume-spike-calibration-sweep` and `python scripts\task.py volume-spike-calibration-sweep`.
- Input shape: repeated `--window NAME:SINCE:UNTIL`, repeated `--low-notional-min-baseline-trades`, repeated `--low-notional-threshold-usd`, optional `--venue`, `--market`, `--limit`, `--cold-start`, and `--format text|json`.
- Output behavior: one validate-only result row per window x candidate pair plus candidate-level aggregate recommendations. Rows and aggregates include removed/added shape profiles with trade-USD buckets, spike-multiplier buckets, triage-flag counts, near-threshold counts, and low-notional/thin-baseline counts so the 800-999 USD true-positive-risk band is visible without opening packet artifacts.
- Guardrails: malformed windows and non-positive candidate values fail before DB access; output declares `local_only=true`, `validate_only=true`, `config_mutation=false`, `db_mutation=false`, and `live_calls=false`; the command writes no packets, no decision records, and no config.
- Focused tests: `python -m pytest .\tests\test_replay_cli_offline.py .\tests\test_task_operator_routes.py .\tests\test_alerts_review.py -q` = 110 passed.
- Undefined-name check: `python -m ruff check --select F821,F822,F823 .\src\pmfi\commands\alerts.py .\src\pmfi\cli.py .\scripts\task.py .\tests\test_alerts_review.py .\tests\test_replay_cli_offline.py .\tests\test_task_operator_routes.py` passed.
- DB sweep command: `python -m pmfi.cli volume-spike-calibration-sweep --window post-calibration-tp:2026-06-18T15:25:00+00:00:2026-06-18T15:49:00+00:00 --window post-fix-risk:2026-06-18T16:23:00+00:00:2026-06-18T17:39:00+00:00 --window refreshed-a:2026-06-18T23:02:27+00:00:2026-06-18T23:12:27+00:00 --window refreshed-b:2026-06-18T23:38:56.533631+00:00:2026-06-18T23:47:56.705874+00:00 --window no-overflow:2026-06-19T01:59:00+00:00:2026-06-19T02:07:00+00:00 --window post800:2026-06-19T04:00:45.385066+00:00:2026-06-19T04:10:51.843726+00:00 --limit 0 --venue kalshi --low-notional-min-baseline-trades 100 --low-notional-threshold-usd 850 --low-notional-threshold-usd 1000 --cold-start --format text`.
- DB sweep result: candidate `baseline-100-threshold-850` across 6 windows removed 1 row, added 0, removed reviewed true positives 0, removed reviewed noise/false positives 0, removed unmatched 1, and returned `needs-persisted-review-evidence`.
- DB sweep result: candidate `baseline-100-threshold-1000` across 6 windows removed 5 rows, added 0, removed reviewed true positives 2, removed reviewed noise/false positives 1, removed unmatched 2, and returned `blocked-by-true-positive-risk`.
- Follow-up shape-profile smoke over `no-overflow` plus `post800` with median floor 20 and threshold 1000 removed 33 rows, added 0, placed all 33 removals in `800_to_999` and `gte_25x`, and returned `needs-persisted-review-evidence`.

### Decision

Decision: keep both checked conditional low-notional candidates validate-only and
do not mutate `config\alert_rules.yaml`.

The sweep makes the current tradeoff explicit. A threshold of 1000 USD removes a
persisted reviewed noise row, but it also removes two reviewed true positives in
the post-fix risk window. A threshold of 850 USD avoids those reviewed true
positives in this sweep, but only removes unmatched replay-only evidence. Neither
result is a change-ready candidate.

### Next Proof Target

Search for a different rule shape or candidate band that removes persisted
reviewed noise without touching the 870/970 USD true-positive band. If a future
sweep candidate becomes non-blocked and evidence-bearing, export packet artifacts
and record a `calibration-decision --include-review-summary` before any config
patch.

## Cluster-review candidate readiness signals - 2026-06-19 UTC

### Evidence

- Shared summary output now assigns each latest cluster-review artifact a conservative `calibration_candidate_readiness` value plus machine-readable blockers and side/outcome signals.
- The readiness labels are intentionally non-authoritative: `blocked-true-positive-risk`, `packet-review-only`, `needs-more-evidence`, and `review-supported` summarize review posture, but do not write config, DB, report, or live state.
- Coverage totals aggregate readiness and signal counts for CLI, API, and dashboard consumers.
- Real local smoke over `m20-no.json` returned `candidate_readiness_counts={"needs-more-evidence":3}` and `candidate_signal_counts={"mixed_directional_sides":3,"mixed_outcome_keys":3}`.
- The three current M20 clusters were all covered by raw-lineage-backed local artifacts, but all three latest assessments remain `uncertain` and packet-level only.

### Decision

Decision: expose readiness as an operator triage signal, not a rule-change
decision.

The current M20 evidence is stronger than a packet-only delta because raw lookup
facts are embedded and visible, but it is still not a persisted alert-review
basis. Mixed side/outcome facts reduce the risk of over-simplifying the clusters
as one-sided noise, and `assessment_uncertain` plus `packet_review_only` should
block any automatic config patch.

### Next Proof Target

Use the readiness blockers to drive the next review pass: either classify the
current M20 clusters with stronger raw-payload/operator evidence, or generate an
independent packet window that produces persisted reviewed noise without
true-positive risk before considering a focused `volume_spike_v1` config patch.

## Dashboard raw-payload cluster review - 2026-06-19 UTC

### Evidence

- Single cluster-review artifact loads already return embedded `raw_event_lookup.rows[].payload` when an artifact was written with `--include-raw-payload`.
- The dashboard now renders payload previews for embedded raw lookup rows and exposes full payloads in collapsed, scrollable blocks for artifacts that include them.
- The UI change is frontend-only and read-only; it does not add API routes, DB writes, config mutation, report writes, or live calls.
- Real local smoke over `m20-kor-raw-payload.json` rendered 3 embedded raw rows, 3 payload previews, and 3 full-payload blocks.

### Decision

Decision: keep raw-payload inspection attached to explicit artifact loading
rather than adding another dashboard endpoint.

The artifact is already the local handoff boundary for packet-level cluster
review evidence. Rendering the payload in the loaded artifact view avoids a
second source of truth and keeps full-payload exposure opt-in through the
existing `--include-raw-payload` flag.

### Next Proof Target

Use the payload view to finish packet-level review of the current unresolved M20
clusters or generate a fresh independent packet window. Do not treat payload
visibility itself as evidence for a `volume_spike_v1` config change.

## Dashboard coverage-to-review load action - 2026-06-19 UTC

### Evidence

- Cluster-review coverage rows now render a `Load` action when `latest_review.name` exists.
- The action calls the existing single-artifact loader and `GET /api/calibration-cluster-reviews/{name}` route; it does not add another API, artifact format, DB write, config mutation, report write, or live call.
- Headed dashboard smoke over `m20-no.json` loaded `m20-mex-raw.json` directly from a coverage row, then loaded `m20-kor-raw-payload.json` from its coverage row and rendered 3 payload previews plus 3 full-payload blocks.

### Decision

Decision: keep coverage as the index and artifact load as the detail authority.

Coverage answers whether current packet clusters have local review artifacts and
what their latest assessment/readiness signals are. Detailed raw lookup and
payload inspection stays attached to the artifact view so the dashboard does not
create a second source of truth or make full payloads visible outside explicit
`--include-raw-payload` artifacts.

### Next Proof Target

Use the faster coverage-to-detail path to classify the current M20 packet-level
clusters or generate independent packet evidence. The UI path itself is not a
`volume_spike_v1` rule-change justification.

## Cluster-review next action and full-payload coverage - 2026-06-19 UTC

### Evidence

- Cluster-review summaries now include advisory `calibration_candidate_next_action` tokens and reason lists derived from the existing readiness blockers, mixed raw signals, and raw lookup payload status.
- Cluster-review coverage totals now include `candidate_next_action_counts` and `raw_event_lookup_payload_status_counts`.
- Before regenerating artifacts, the real `m20-no.json` coverage summary reported `full-payload=1`, `preview-only=2`, `classify-cluster=1`, and `rerun-with-full-payload=2`.
- Local ignored artifacts `m20-mex-raw-payload.json` and `m20-tie-raw-payload.json` were written with `--include-raw-events --include-raw-payload`, giving all three current M20 clusters full raw public payload coverage.
- The post-artifact coverage summary reports `raw_lookup_payload_status=full-payload=3` and `candidate_next_action=classify-cluster=3`.
- Local ignored decision artifact `m20-full-payload-review-state.json` records `decision=needs-more-evidence` with cluster coverage `covered=3`, `uncovered=0`, payload status `full-payload=3`, and next action `classify-cluster=3`.

### Decision

Decision: use next-action tokens as operator guidance only.

The tokens make the next local review step explicit without changing candidate
readiness semantics. A cluster can move from `rerun-with-full-payload` to
`classify-cluster`, but it remains packet-review-only until an explicit
operator assessment and later calibration decision justify a rule change.

### Next Proof Target

Classify the three full-payload M20 cluster artifacts. Keep `volume_spike_v1`
unchanged unless later reviewed evidence proves removed persisted noise or false
positives without unresolved replay-only blast radius or true-positive risk.

## M20 true-positive-risk no-change decision - 2026-06-19 UTC

### Evidence

- Full-payload classification artifacts were written for all three current
  `m20-no.json` clusters:
  `m20-mex-true-positive-risk.json`,
  `m20-tie-true-positive-risk.json`, and
  `m20-kor-true-positive-risk.json`.
- Each artifact embeds read-only raw-event lookup with full public payloads and
  remains `local_only=true`, `validate_only=true`,
  `persisted_alert_review=false`, and non-mutating.
- The real post-classification cluster summary reports
  `assessment_counts={"true-positive-risk":3}`,
  `candidate_readiness_counts={"blocked-true-positive-risk":3}`,
  `candidate_next_action_counts={"narrow-rule-before-config-review":3}`,
  `raw_event_lookup_payload_status_counts={"full-payload":3}`, and
  `covered=3`, `uncovered=0`.
- The cluster rows are distinct non-block Kalshi trades clustered within
  minutes, with capital-at-risk ranges MEX `$817-$998`, TIE `$818-$962`, and
  KOR `$878-$917`; all overlap the documented 800-999 USD true-positive risk
  band and have high replay spike multipliers.
- Local ignored decision artifact
  `m20-no-change-true-positive-risk.json` records `decision=no-change`,
  `removed_records=25`, `added_records=0`, `removed_unmatched=25`, and the
  embedded true-positive-risk cluster coverage counts above.

### Decision

Decision: record `no-change` for the median20/threshold1000 candidate shape and
do not mutate `config\alert_rules.yaml`.

The full-payload review did not convert the replay-only rows into reviewed
noise. It showed plausible useful alert risk in the exact notional band already
called out by prior calibration evidence. The candidate should therefore remain
validate-only for this slice.

### Next Proof Target

Search for a narrower low-notional/thin-baseline rule shape or produce a fresh
independent window with persisted reviewed-noise removals that does not touch
the 800-999 USD true-positive risk band. A future config patch still needs a
new review-supported decision artifact, replay proof, and fresh runtime proof.

## Low-notional spike-multiplier ceiling candidate - 2026-06-19 UTC

### Evidence

- Candidate knob: `low_notional_max_spike_multiplier` for validate-only
  `volume_spike_v1` replay, sweep, packet export, and packet batch export.
- CLI/task flag: `--low-notional-max-spike-multiplier`.
- Rule shape: when paired with low-notional median-floor suppression, preserve
  rows whose observed `spike_multiplier` is above the ceiling instead of
  suppressing all low-notional rows that fail the median-floor candidate.
- Scope: calibration-only; no production default is justified until replay and
  packet evidence prove a stable ceiling that removes reviewed noise without
  cutting true-positive-risk rows.

### Decision

Decision: keep the spike-multiplier ceiling candidate validate-only and do not
mutate `config\alert_rules.yaml`.

The knob narrows the prior median-floor shape by protecting high-multiplier
low-notional alerts from suppression, which directly addresses the full-payload
M20 review concern. It is not itself evidence that any ceiling is production
safe.

### Next Proof Target

Run cross-window sweeps and packet exports with explicit
`--low-notional-max-spike-multiplier` values. A future config patch still needs
review-supported packet evidence, no true-positive-risk removals, replay proof,
and fresh runtime proof.

## Capped low-notional multiplier sweep - 2026-06-19 UTC

### Evidence

- Command shape: `python scripts\task.py volume-spike-calibration-sweep` over
  the six reviewed Kalshi windows used for the M20 sweep, with
  `--low-notional-min-baseline-median-usd 20`,
  `--low-notional-threshold-usd 1000`, `--cold-start`, and explicit
  `--low-notional-max-spike-multiplier` values.
- `maxmult-24` removed 0 rows across all six windows and returned
  `no-candidate-effect`.
- `maxmult-50` removed 4 rows, added 0, removed 0 reviewed noise/false-positive
  rows, removed 0 reviewed true-positive rows, and left all 4 removals as
  unmatched replay-only rows.
- `maxmult-100` removed 34 rows, added 0, removed 4 reviewed noise/false-positive
  rows, removed 0 reviewed true-positive rows, and left 30 removals unmatched.
- `maxmult-200` removed 42 rows, added 0, removed 4 reviewed noise/false-positive
  rows, removed 0 reviewed true-positive rows, and left 38 removals unmatched.
- All capped-candidate removals were in the `800_to_999` trade-USD bucket and
  `gte_25x` spike-multiplier bucket.

### Decision

Decision: keep all capped multiplier candidates validate-only and do not mutate
`config\alert_rules.yaml`.

The ceiling axis is a real narrowing tool: it eliminates the reviewed true
positive removals that blocked the uncapped median20/threshold1000 candidate in
this six-window sweep. It still does not create a change-ready rule because the
evidence-bearing candidates leave a large unmatched replay-only blast radius in
the same high-multiplier 800-999 USD band that required packet/raw-event review.

### Next Proof Target

Export and review packets for the most evidence-bearing capped candidate,
currently median20/threshold1000/maxmult100, before considering config. A future
change-ready decision still needs reviewed noise/false-positive removals, zero
true-positive risk after packet review, replay proof, fresh runtime proof, and an
explicit config patch.

## Maxmult100 packet export and decision - 2026-06-19 UTC

### Evidence

- Packet batch command: `python scripts\task.py calibration-packet-batch` over
  the same six reviewed Kalshi windows with
  `--low-notional-min-baseline-median-usd 20`,
  `--low-notional-threshold-usd 1000`,
  `--low-notional-max-spike-multiplier 100`, `--cold-start`, and
  `--packet-output-prefix mx100`.
- Ignored local packet artifacts written:
  `mx100-pct.json`, `mx100-pfr.json`, `mx100-ra.json`, `mx100-rb.json`,
  `mx100-no.json`, and `mx100-p800.json`.
- Review queue over the six packets returned `available_rows=34`,
  `filtered_rows=30`, `returned_rows=30`, `truncated=false` for removed
  unmatched replay-only rows.
- Top unmatched review clusters: `KXWCGAME-26JUN18MEXKOR-MEX` with 13 rows,
  `KXWCGAME-26JUN18MEXKOR-TIE` with 4 rows,
  `KXBTC15M-26JUN190015-15` with 3 rows,
  `KXWCGAME-26JUN18MEXKOR-KOR` with 3 rows, and
  `KXWCGAME-26JUN19USAAUS-USA` with 3 rows.
- Decision artifact: ignored local `reports\calibration-decisions\mx100.json`
  records `decision=needs-more-evidence` and embeds the review summary.
- Decision aggregate: `removed_records=34`, `added_records=0`,
  `removed_review_matches=4`, `removed_review_unmatched=30`,
  `removed_review_labels={"noise": 4, "unmatched": 30}`, and
  `removed_review_categories={"live_low_notional_thin_baseline": 4,
  "unmatched": 30}`.
- Review summary: `removed_reviewed_noise=4`, `removed_reviewed_tp=0`,
  `removed_unmatched=30`, `added_unmatched=0`, recommendation
  `needs-more-evidence`.

### Decision

Decision: keep median20/threshold1000/maxmult100 validate-only and do not mutate
`config\alert_rules.yaml`.

This packet set is a better review target than the uncapped M20 packet because
it retains reviewed noise removal with zero reviewed true-positive removals. It
still has 30 replay-only removals, including World Cup and BTC clusters already
known to require raw-lineage review before treating them as safe noise.

### Next Proof Target

Review the top `mx100-*` unmatched clusters before any config patch. Start with
`mx100-no.json` / `KXWCGAME-26JUN18MEXKOR-MEX`, then TIE/KOR, and the p800 BTC
and USA clusters. Reuse existing raw-event lookup and cluster-review artifact
commands; do not append persisted alert reviews for replay-only rows.

## Maxmult100 MEX cluster true-positive-risk block - 2026-06-19 UTC

### Evidence

- Cluster-review artifact: ignored local
  `reports\calibration-cluster-reviews\mx100-mex-risk.json`.
- Covered packet/cluster: `mx100-no.json` /
  `KXWCGAME-26JUN18MEXKOR-MEX`.
- Covered rows: 13 removed replay-only packet rows, with the same raw-event ID
  set as the earlier M20 MEX true-positive-risk artifact.
- Assessment: `true-positive-risk`.
- Raw lineage: full-payload raw-event lookup embedded, with `found_count=13`
  and `missing_raw_event_ids=[]`.
- Decision artifact: ignored local
  `reports\calibration-decisions\mx100-mex-blocked.json`.
- Decision aggregate: `decision=no-change`, `removed_records=34`,
  `added_records=0`, `removed_review_matches=4`,
  `removed_review_unmatched=30`.
- Embedded coverage totals: `market_cluster_count=8`,
  `covered_market_cluster_count=1`, `uncovered_market_cluster_count=7`,
  `assessment_counts={"true-positive-risk": 1}`,
  `candidate_readiness_counts={"blocked-true-positive-risk": 1}`,
  `candidate_next_action_counts={"narrow-rule-before-config-review": 1}`, and
  `raw_event_lookup_payload_status_counts={"full-payload": 1}`.
- Remaining uncovered clusters: `KXWCGAME-26JUN18MEXKOR-TIE` with 4 rows,
  `KXBTC15M-26JUN190015-15` with 3 rows,
  `KXWCGAME-26JUN18MEXKOR-KOR` with 3 rows,
  `KXWCGAME-26JUN19USAAUS-USA` with 3 rows,
  `KXBTC15M-26JUN181945-45` with 2 rows,
  `KXMLBGAME-26JUN181840NYMPHI-NYM` with 1 row, and
  `KXWCSPREAD-26JUN18CANQAT-CAN5` with 1 row.

### Decision

Decision: keep median20/threshold1000/maxmult100 validate-only and do not mutate
`config\alert_rules.yaml`.

The candidate has stronger persisted-review evidence than the uncapped M20
shape because it removes four reviewed noise rows and zero reviewed true
positives in the six-window comparison. The first direct cluster review still
blocks production use: the largest unmatched removal cluster is raw-lineage
reviewed as true-positive risk, so the candidate would suppress plausible useful
high-multiplier 800-999 USD Kalshi alert evidence.

### Next Proof Target

At this stage, the next proof target was to review the remaining uncovered
mx100 clusters if that could shape a narrower rule, or search directly for a
narrower low-notional/thin-baseline candidate that preserved high-multiplier
true-positive-risk rows. The expanded closeout below resolves the remaining
mx100 clusters; a future config patch still needs a fresh change-ready decision
artifact, replay proof, runtime proof, and explicit config review.

## Simple threshold/multiplier grid after MEX block - 2026-06-19 UTC

### Evidence

- Command shape: validate-only `python scripts\task.py
  volume-spike-calibration-sweep` over the same six Kalshi windows, with
  `--low-notional-min-baseline-median-usd 20`, thresholds 850/900/950/1000,
  max multipliers 45/50/55/75/100, `--limit 0`, `--venue kalshi`, and
  `--cold-start`.
- Scope: local-only DB replay, no config mutation, no DB mutation, no report or
  packet writes, and no live calls.
- Result: no simple threshold/multiplier candidate was change-ready. All 20
  aggregate rows returned `needs-persisted-review-evidence`.
- All removals stayed in the same `800_to_999` trade-USD bucket and `gte_25x`
  multiplier bucket that contains the MEX true-positive-risk block.
- Aggregate removed row counts:
  - Threshold 850: maxmult 45/50/55/75/100 removed 1/1/1/2/3 rows.
  - Threshold 900: maxmult 45/50/55/75/100 removed 1/1/3/8/10 rows.
  - Threshold 950: maxmult 45/50/55/75/100 removed 1/2/7/13/18 rows.
  - Threshold 1000: maxmult 45/50/55/75/100 removed 1/4/16/26/34 rows.
- Decision-summary code now derives `decision_readiness` from packet-review and
  cluster-review evidence. The existing `mx100-mex-blocked.json` summary reports
  `blocked-by-cluster-true-positive-risk`.

### Decision

Decision: keep median20 threshold/multiplier candidates validate-only and do not
mutate `config\alert_rules.yaml`.

Simple global threshold and multiplier narrowing did not separate reviewed noise
from unresolved replay-only or cluster-reviewed true-positive-risk rows. Adding a
new suppression knob would be premature unless a future raw-lineage review finds
a stable non-market-specific feature that distinguishes noise from plausible
true-positive evidence.

### Next Proof Target

Use decision readiness as the operator-facing blocker signal. Continue only with
remaining cluster review if it can reveal a stable data feature, or run a new
candidate search only when a specific separable feature is identified.

## mx100 TIE/KOR cluster coverage - 2026-06-19 UTC

### Evidence

- New ignored full-payload cluster-review artifacts:
  - `reports\calibration-cluster-reviews\mx100-tie-uncertain.json`
  - `reports\calibration-cluster-reviews\mx100-kor-uncertain.json`
- TIE cluster: 4 replay-only rows from `mx100-no.json`, raw event IDs
  `200053`, `208510`, `211088`, and `211048`, capital 855.19-961.63 USD,
  baseline median 8.81-18.86 USD, spike 50.99x-97.02x, all same-side YES/TIE
  clean non-block Kalshi trades.
- KOR cluster: 3 replay-only rows from `mx100-no.json`, raw event IDs `204986`,
  `204968`, and `203569`, capital 877.69-916.74 USD, baseline median
  16.24-16.61 USD, spike 52.89x-55.18x, mixed YES/NO clean non-block Kalshi
  trades.
- Assessment for both: `uncertain`. The rows show a stable low-notional,
  thin-baseline, 800-999 USD, high-multiplier shape, but the raw events are real
  clean public trades and have no persisted alert-review target.
- Expanded decision artifact:
  `reports\calibration-decisions\mx100-expanded-coverage-no-change.json`.
- Embedded coverage in that decision: 8 queue clusters, 3 covered, 5 uncovered,
  `assessment_counts={"true-positive-risk": 1, "uncertain": 2}`,
  `candidate_readiness_counts={"blocked-true-positive-risk": 1,
  "needs-more-evidence": 2}`, `raw_lookup_payload_status_counts={"full-payload": 3}`.
- Decision summary: `decision=no-change`,
  `decision_readiness=blocked-by-cluster-true-positive-risk`,
  `removed_records=34`, `added_records=0`, and review recommendation
  `needs-more-evidence`.

### Decision

Decision: keep mx100 validate-only and do not mutate `config\alert_rules.yaml`.

TIE and KOR strengthen the hypothesis that mx100 is hitting a stable
low-notional/thin-baseline shape. They do not make the candidate safe. The
candidate remained blocked at this stage by MEX true-positive-risk, two
now-covered uncertain real-trade clusters, and five still-uncovered replay-only
clusters; the full closeout below resolves those five as uncertain rather than
safe noise.

### Next Proof Target

Continue with the remaining mx100-rb and mx100-p800 uncovered clusters only to
close the packet-level evidence gap. The current state from this section is
superseded by the full expanded coverage pass below.

## mx100 expanded cluster coverage closeout - 2026-06-19 UTC

### Evidence

- New ignored full-payload cluster-review artifacts:
  - `reports\calibration-cluster-reviews\mx100-btc190015-uncertain.json`
  - `reports\calibration-cluster-reviews\mx100-usa-uncertain.json`
  - `reports\calibration-cluster-reviews\mx100-btc181945-uncertain.json`
  - `reports\calibration-cluster-reviews\mx100-nym-uncertain.json`
  - `reports\calibration-cluster-reviews\mx100-can5-uncertain.json`
- `mx100-p800.json` coverage now includes the BTC `KXBTC15M-26JUN190015-15`
  cluster with three clean non-block Kalshi trades, mixed YES/NO sides,
  806.58-989.94 USD capital at risk, and full raw-event lineage.
- `mx100-p800.json` coverage now includes the USA
  `KXWCGAME-26JUN19USAAUS-USA` cluster with three clean non-block Kalshi
  same-side YES trades, 954.60-974.08 USD capital at risk, and full raw-event
  lineage.
- `mx100-rb.json` coverage now includes `KXBTC15M-26JUN181945-45`,
  `KXMLBGAME-26JUN181840NYMPHI-NYM`, and `KXWCSPREAD-26JUN18CANQAT-CAN5`;
  all are clean non-block Kalshi trades with full raw-event lineage and no
  persisted alert-review target.
- Six-packet coverage smoke:
  `python scripts\task.py calibration-cluster-review-summary --packet mx100-pct.json --packet mx100-pfr.json --packet mx100-ra.json --packet mx100-rb.json --packet mx100-no.json --packet mx100-p800.json --format text`
  returned `queue_clusters=8`, `covered=8`, `uncovered=0`,
  `assessment_counts={"true-positive-risk": 1, "uncertain": 7}`, and
  `raw_lookup_payload_status_counts={"full-payload": 8}`.
- Fresh decision artifact:
  `reports\calibration-decisions\mx100-expanded-covered-no-change.json`.
- Decision summary: `decision=no-change`, `removed_records=34`,
  `added_records=0`, cluster coverage `covered=8`, `uncovered=0`, and embedded
  review recommendation `needs-more-evidence`.

### Decision

Decision: keep mx100 validate-only and do not mutate `config\alert_rules.yaml`.

Complete cluster coverage closes the prior packet-review gap but does not make
the candidate change-ready. The selected six-packet mx100 family resolves to one
true-positive-risk cluster plus seven uncertain clean real-trade clusters. That
is not a safe noise-only basis for production suppression.

### Next Proof Target

Treat median20/threshold1000/maxmult100 as rejected for production config until
a different candidate family or a newly reviewed persisted-noise corpus proves a
separable feature that preserves true-positive-risk and uncertain clean-trade
clusters.

## Superseded below-current-floor persisted spike reviews - 2026-06-19 UTC

### Evidence

- Active configured rule: `volume_spike_v1.min_trade_usd=800`.
- Current 7d unreviewed `volume_spike_v1` audit found 26 rows: 11 historical
  rows below the current 800 USD floor and 15 rows at or above the current
  floor.
- Raw/trade lineage lookup found all 11 below-current-floor raw events, joined
  normalized trades for all 11, no lookup warnings, and
  `capital_at_risk_usd < 800` for each row.
- The 11 below-current-floor rows were dry-run resolved before mutation, then
  appended as local Postgres reviews with `label=noise` and
  `category=superseded_below_current_floor`.
- `python -m pmfi.cli alerts fp-rate --since 7d --rule volume_spike_v1`
  returned `volume_spike_v1 noise=63`, `tp=7`, reviewed `70`, FP `0`, TP `7`,
  and noise `63`.
- `python scripts\task.py report --since 7d --format json` returned
  `review_outcomes.reviewed_total=119` and `review_queue.total=32`.

### Decision

Decision: keep `volume_spike_v1.min_trade_usd=800` unchanged.

The review pass closes a narrow historical inconsistency: rows that were emitted
under an older lower floor but would be suppressed by the current configured
floor are now marked as local superseded noise after raw/trade lineage proof.
This does not justify a new config change, and it does not classify the 15
remaining unreviewed `volume_spike_v1` rows that are at or above the current
800 USD floor.

### Next Proof Target

Review the remaining at-or-above-floor `volume_spike_v1` rows by raw/trade
evidence and bounded market cohorts. Treat floor-only reasoning as exhausted for
this queue; any future rule change still needs reviewed row evidence plus replay
and runtime proof.

## At-or-above-floor unreviewed spike queue packet - 2026-06-19 UTC

### Evidence

- `python scripts\task.py review-packet --since 7d --rule volume_spike_v1
  --review-state unreviewed --limit 50 --output
  volume-spike-unreviewed-queue-wrapper.json --format json` wrote ignored local
  `reports\review-packets\volume-spike-unreviewed-queue-wrapper.json`.
- Packet filters: `rule=volume_spike_v1`, `review_state=unreviewed`,
  `review_label=null`, `category=null`, and `limit=50`.
- Packet totals: `alerts=15`, `by_label={"unreviewed": 15}`,
  `by_category={"unreviewed": 15}`, triage flags `low_notional=15` and
  `thin_baseline=15`.
- Each packet row keeps `latest_review` fields null, preserving that no
  `tp`/`fp`/`noise` review was written for this at-or-above-floor cohort.
- Subagent and main-session read-only review agreed that the 15 rows have clean
  raw/trade lineage but mixed precedent: similar low-notional/thin-baseline
  rows have previously been reviewed both as noise and as caveated true
  positives depending on run and market context.

### Decision

Decision: do not batch-label the 15 at-or-above-floor `volume_spike_v1` rows
and do not mutate `config\alert_rules.yaml`.

The packet makes the unresolved review queue reproducible without weakening
local review truth. All rows are above the active 800 USD floor and carry
`low_notional` plus `thin_baseline`; that shape alone is not decisive because
the same band contains documented true-positive risk. Treat the exported packet
as an operator handoff artifact and calibration input, not as a reviewed-noise
corpus.

### Next Proof Target

Review the packet by bounded market cohort, starting with the concentrated
Mexico vs Korea rows, and record `tp`/`noise` only when market/raw/trade context
resolves the ambiguity. If ambiguity remains, keep the rows unreviewed rather
than collapsing them into a weak bulk label.
