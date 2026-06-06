# Experiment 03 — Cross-Venue Matching Feasibility

## Goal
Estimate whether automatic Polymarket/Kalshi market equivalence matching is reliable enough to support divergence alerts.

## Procedure

1. Create a hand-labeled dataset:
   - 50 clear matches;
   - 50 near-matches;
   - 50 non-matches.
2. Compare title, event, resolution source, cutoff time, timezone, contract terms, and market status.
3. Score candidate matching heuristics.
4. Measure false-match rate.

## Gate

Do not ship automatic cross-venue divergence alerts until false-match rate is low and ambiguous cases are suppressed.
