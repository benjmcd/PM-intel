# ADR 0003 — Executed Trades First

## Status
Accepted.

## Decision
Prioritize executed trade monitoring before full order-book reconstruction.

## Rationale
Executed trades are real flow. Resting orders can be cancelled and require more fragile state handling. Trade-based anomaly detection provides the best MVP signal-to-complexity ratio.

## Consequences
- Full book reconstruction is deferred.
- Periodic snapshots are acceptable before deltas.
- Alert rules should not over-weight non-executed liquidity until book state is proven.
