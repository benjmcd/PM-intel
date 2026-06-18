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

### Next Proof Target

Next proof target: fresh post-calibration live or soak proof.

Future threshold changes still need new reviewed packet evidence plus replay or
fresh-soak proof. A generated review packet is an audit input; it is not enough
by itself to justify a rule change.
