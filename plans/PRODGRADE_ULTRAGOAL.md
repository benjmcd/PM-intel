# PRODGRADE ULTRAGOAL — Production-grade advancement ledger

Durable multi-goal ledger for advancing PMFI to a production-grade, fully-usable local
prediction-market flow-intelligence tool. Survives session restarts. Append events; do not
rewrite history. (`omc ultragoal` CLI unavailable in this environment → native tracking here.)

- Worktree: `C:\Users\benny\OneDrive\Desktop\PM-intel-prodgrade`
- Branch: `prodgrade-advance` (off `origin/main` @ `1e49fd6`)
- Baseline (verified): `668 passed, 34 DB-gated skipped` offline.
- Constraints (binding): local-only scope, no trading, raw-before-derived lineage, Postgres-first,
  Windows-native, no live API in default tests, no co-author/generated notes on commits/files.

---

## Current production-readiness (gap-analysis workflow + opus validation)

Per-subsystem readiness (0–10), from a 9-investigator read-only gap workflow, validated firsthand:

| Subsystem | Score | Headline |
|---|---|---|
| alert-rules | 5 | 6 rules; MVP #5 (price-impact) partial, MVP #6 (data-quality alert) missing; "later" types dead-flagged |
| feature-flags | 6 | 3 dead flags (cross_venue, wallet, ml_scoring) parsed but unconsumed |
| adapters-feeds | 6 | No subscription ack-check, no 429 handling, no transient/permanent split, WS total=None |
| db-storage | 6 | No migration ledger; manual retention (by design); ON CONFLICT unnamed; data_quality_incidents unused |
| daemon-observability | 7 | Strong: supervisor, heartbeat, recompute, partition tick. No self-emitted went-silent alert |
| cli-ux | 6 | 18 cmds wired; review-pass stub; AssertionError fallback; minor stderr/stdout inconsistency |
| dashboard-delivery | 6 | FileDelivery no I/O guard + unused size cap; HttpDelivery no retry; server endpoints untested |
| tests | 6 | 697 funcs; gaps: delivery failure, dashboard HTTP server, disabled-rule, concurrency |
| docs-drift | 8 | Well-aligned; minor example-config completeness nits |

### Validation corrections (haiku findings opus rejected/downgraded)
- **REJECTED** "no automatic baseline recompute" — daemon DOES recompute + hot-reload (`daemon.py:96-123`, `engine.update_baselines`).
- **DOWNGRADED** "allowed_delivery_modes broken" — YAML overrides default; `localhost_http_receiver` honored (`cli.py:1611`). Minor config-validation nit only.

---

## Plan — tiered, modular, validated

### TIER 1 — Production reliability + MVP completion (offline + DB-gated verifiable)
- **T1.1 Data-quality degradation alert (MVP #6).** New feed-health monitor: venue-silence,
  dead-letter-rate spike, ingest stall → emit first-class `data_quality_degradation_v1` alert via
  the normal sink AND write the currently-unused `data_quality_incidents` table. Leverages
  heartbeat + dead_letters infra. Modular monitor component; config-gated thresholds.
- **T1.2 Adapter feed robustness.** (a) subscription ack/nack detection (surface silent dead
  subscriptions); (b) HTTP 429 + Retry-After handling in REST paths; (c) transient-vs-permanent
  error classification (bounded retry on permanent); (d) WS receive/idle timeout (replace
  `total=None`).
- **T1.3 Delivery robustness.** FileDelivery: I/O error handling + enforce size-based rotation
  (wire the parsed-but-unused `_max_bytes`). HttpDelivery: bounded retry/backoff. Add the missing
  delivery-failure + dashboard-HTTP-server tests.

### TIER 2 — Correctness + hardening
- **T2.1 Price-impact confirmation rule (MVP #5)** — standalone modular rule.
- **T2.2 DB hardening** — schema migration ledger table; explicit ON CONFLICT targets;
  partition-precedes-ingest guard.
- **T2.3 Dead flags** — remove `enable_ml_scoring` (architecture rejects ML) to archive note;
  warn-if-enabled for unimplemented `cross_venue`/`wallet` flags; mark in example config.
- **T2.4 Test hardening** — disabled-rule, delivery failure/rotation, dashboard endpoints, HttpDelivery.

### TIER 3 — Feature expansion ("later" product scope; bigger lifts; needs product decision)
- **T3.1 Liquidity wall/vacuum** (orderbook depth) — uses `enable_orderbook_reconstruction`.
- **T3.2 Cross-venue divergence** (market matching) — `enable_cross_venue_matching`.
- **T3.3 Wallet/holder accumulation** (Polymarket public address flow) — `enable_wallet_intelligence`.
- **T3.4 False-positive feedback loop** — `alert_reviews` table → suppression/threshold tuning.
- **T3.5 Category-specific rules.**

---

## Decisions (RESOLVED via grill)
1. **Scope: Everything (Tier 1-3) now.** Maximum ambition this push.
2. **ML scoring: keep ML OUT.** Add a TRANSPARENT composite scorer (documented weights blending
   existing rule scores); repurpose `enable_ml_scoring` to gate that. No black box (preserves the
   explainable-alerts product principle). Implement cross-venue + wallet + liquidity (light up their flags).
3. **Verification: offline + DB-gated + bounded live-feed smoke** (PMFI_ENABLE_LIVE, read-only public feeds).
4. **Delivery: commit per-slice (no co-author notes), push `prodgrade-advance`, open one PR to main at end.**

## Environment (verified)
- Postgres 16.14 native (winget), service `postgresql-x64-16`, bound to loopback on the local default port.
- App DB: role `pmfi` / db `pmfi` on the local native server (loopback, default port). `PMFI_DB_URL` is set in the shell env only — never committed (the repo reserves the conflicting default port literal).
- Full suite green WITH DB: **701 passed, 1 skipped** (skip = data-dependent). `verify.py` passes.
- CLI smoke OK: `db-verify` (2 venues), `status` (6 rules), `dashboard`/`ingest` wired.
- NOTE: native-DLL load under Windows Application Control was transiently blocked from the OneDrive-synced
  venv on first pytest run (warmed after). Operator advisory: prefer a non-OneDrive path for the prod daemon venv.

## Execution phases
- **A (foundation):** A1 self-registering rule system (dir auto-discovery, `from_config`, behavior-preserving);
  A2 DB migration ledger (`schema_migrations`). → unblocks conflict-free parallel rule additions.
- **B (design/feasibility):** Tier-3 data-dependent rules (wallet address availability in Polymarket feed,
  cross-venue market matching approach, category metadata, liquidity from orderbook_snapshots). Mark blockers.
- **C (parallel slices, disjoint files):** feeds robustness; delivery robustness; unblocked new rules
  (data-quality #6, price-impact #5, composite scorer, liquidity); DB hardening remainder; FP-feedback loop.
- **D (Tier-3 data-dependent):** cross-venue divergence, wallet accumulation, category rules (per B).
- **E:** tests + docs + integration verify (full suite + verify.py).
- **F:** live-feed smoke; commit/push/PR.

## Tier-3 feasibility verdicts (workflow + evidence)
- **wallet-accumulation: BLOCKED.** Public Polymarket WS payload carries NO wallet/maker/taker id
  (fixtures confirm: event_type,id,market,asset_id,outcome,price,size,side,bucket_index only).
  Requires authenticated REST (`/get-trades-for-a-user-or-markets`) → out of local-only scope; ADR-gated.
  DECISION: do not implement; keep `enable_wallet_intelligence` flag with a clear "blocked: needs auth feed" warning + roadmap note. Honesty over a fake feature.
- **cross-venue-divergence: FEASIBLE (conservative).** `market_aliases` table already exists
  (source/target market_id, confidence, alias_type, rationale). Operator-curated matches only (no
  auto-match — respects experiments/03 false-match gate). Divergence = price spread across matched
  markets' latest snapshots. Add `pmfi markets link` CLI + a manual-matching doc.
- **category-specific: FEASIBLE.** `markets.category` populated by discovery (both venues);
  `market_baselines.scope` already supports 'category'. Add optional `category` to NormalizedTrade +
  per-category threshold overrides read from alert_rules.yaml.
- **liquidity-wall/vacuum: PARTIAL.** orderbook_snapshots/levels + Polymarket /book capture exist.
  Wall=top-depth USD vs recent trade median; vacuum=spread vs baseline. Caveats (→ ADR): snapshots are
  trade-coupled (quiet-period blind spots), Polymarket-only, 10-level truncation. Ship v1 with caveats.

## Architectural refinement — monitors vs rules
Per-trade engine rules (sync `evaluate(trade, engine)`): price_impact_confirmation_v1; category overrides.
Periodic/event MONITORS (async DB/snapshot access, emit via insert_alert+delivery): data_quality_degradation_v1
(daemon tick), cross_venue_divergence_v1 (daemon tick), liquidity_wall_v1 (runner orderbook-capture path).
→ Build a small `pmfi/monitoring/` framework (registry + emit helper + one daemon-tick `run_monitors` call)
so monitors are pluggable modules (scalable, conflict-free additions). liquidity uses the runner snapshot path.

## Ledger (append-only)
- [init] Worktree off origin/main; baseline 668/34; gap workflow (9 investigators) complete; plan synthesized.
- [env] Native Postgres 16.14 up; pmfi role/db + schema 001-011 applied; full suite 701/1-skip green; CLI smoke OK.
- [grill] 4 decisions resolved: everything-now · transparent-composite-no-ML · offline+DB+live · commit/push/PR.
- [feasibility] wallet BLOCKED (no public wallet data); cross-venue/category FEASIBLE; liquidity PARTIAL (caveats). Monitor framework added to plan.
- [exec] WF-C1 launched: feeds robustness, delivery robustness, DB hardening+migration ledger, price-impact rule (#5).
- [exec] Tier-1/2 landed + verified + pushed (commits ece71ca..d8381eb); 3 integration breaks fixed; PR #4 opened.
- [exec] MVP #6 data_quality_degradation_v1 monitor + monitoring/ framework landed (034c209).
- [exec] FP-feedback: alert_reviews repo + `pmfi alerts review/reviews/fp-rate` CLI landed (no auto-suppression).
- [exec] Feature-flag warnings for blocked/unimplemented flags (wallet blocked, ml by-design, cross_venue then-pending).
- [exec] Cross-venue: cross_venue_divergence_v1 monitor + `pmfi markets link/links` CLI + market_aliases repo + manual-matching doc.
- [exec] Docs: OPERATOR_QUICKSTART updated for all new commands + alert types.
- [proof] LIVE smoke green: Polymarket REST discover (8 markets) + WS live-smoke (12 events via the new receive()-loop). Feed hardening live-proven.
- [exec] liquidity_wall_v1 (opt-in orderbook path + ADR-0009), transparent composite scorer
  (corroboration annotation, no ML), category-specific overrides (suppress-only) — all landed,
  verified, pushed.
- [done] ALL Tier-1/2/3 scope addressed EXCEPT wallet/holder accumulation (blocked — no wallet
  identity in the public Polymarket feed; would need authenticated REST, out of local-only scope).
  16 commits on `prodgrade-advance`; PR #4 to `main`; live-feed smoke green.
- [future] optional: periodic orderbook poll (remove liquidity's quiet-period blind spot);
  Kalshi orderbook capture; gate composite/cross-venue behind their config flags.
