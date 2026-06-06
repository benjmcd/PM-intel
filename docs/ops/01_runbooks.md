# 01 — Runbooks

## Feed stale

Symptoms:

- no messages for a venue/channel beyond threshold;
- heartbeat stale;
- REST reconciliation shows missing events.

Actions:

1. Mark feed as degraded.
2. Suppress high-confidence alerts from that feed.
3. Reconnect with backoff.
4. Run REST reconciliation if available.
5. Record incident.

## Parser failure spike

Symptoms:

- parser exceptions increase;
- unknown field formats;
- dead-letter count rises.

Actions:

1. Preserve raw payloads.
2. Add fixture from dead-letter sample.
3. Update parser/normalizer narrowly.
4. Add regression test.
5. Re-run replay.

## Alert storm

Symptoms:

- too many alerts from correlated markets or duplicate events.

Actions:

1. Check dedupe keys.
2. Check suppression windows.
3. Check data-quality degradation.
4. Downgrade severity until fixed.
5. Backtest revised thresholds.
