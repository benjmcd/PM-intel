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
- Candidate `volume_spike_v1.min_trade_usd=800` removed 28 low-notional/thin-baseline replayed spike emissions in the previous strict refreshed-Kalshi window, but removed 0 in the new no-overflow proof window.

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
