# PMFI Release Profile v1

## Status

This document records the local v1 operator-baseline release profile for `pm-flow-intel` at verified main SHA `dbd3a0044156f71424ff27133aa0283bb0a5c5ca`.

M-RELEASE-v1 same-host rehearsal was green from a detached clean checkout under `worktrees/release-v1` with a fresh `.venv` editable install. The rehearsal found no source defect and did not create a tag.

## Required caveats

This proves SAME-HOST clean-checkout reproducibility: repo-materials sufficiency, fresh venv install, gates, and operator commands without depending on the dirty root checkout or ignored root artifacts. It is NOT a true separate-machine proof. Cross-machine proof is deferred until a second operator-provided environment exists.

The intended v1 tag is a LOCAL v1 OPERATOR BASELINE only. It is explicitly NOT a predictive or alert-quality claim. Polymarket alert-label expansion remains reserved as the next operational validation lane.

## Rehearsal proof

- `git fetch origin` then `git worktree add --detach .\worktrees\release-v1 origin/main` created a detached clean worktree at `dbd3a0044156f71424ff27133aa0283bb0a5c5ca`.
- `py -3.11 -m venv .venv` created a fresh virtual environment inside the rehearsal worktree.
- `.\.venv\Scripts\python.exe -m pip install -e ".[dev]"` installed `pm-flow-intel==0.0.1` in editable mode from the clean checkout.
- `.\.venv\Scripts\python.exe scripts\verify.py` passed with `1194 passed, 46 skipped`.
- `.\.venv\Scripts\python.exe scripts\db_local.py verify` passed; Postgres was ready, schema readiness passed, and seeded venues were `kalshi` and `polymarket`.
- Final rehearsal worktree status was clean.

## Operator-command smoke

All commands below were run through installed `.\.venv\Scripts\pmfi.exe` with `DATABASE_URL=postgresql://pmfi:pmfi_local_password_change_me@localhost:5433/pmfi`. No live API calls were made.

- `pmfi init` exited 0 and idempotently applied the local DB schema and seeds.
- `pmfi doctor --json` exited 0 with overall `OK`.
- `pmfi rules list` exited 0.
- `pmfi rules disable volume_spike_v1` exited 0.
- `pmfi rules enable volume_spike_v1` exited 0.
- `pmfi rules set volume_spike_v1 min_trade_usd 850` exited 0 and set the rule to the same current value; `config\alert_rules.yaml` was restored afterward.
- `pmfi backtest --limit 5 --format json` exited 0 with `normalized_trades_replayed=5`, `hypothetical_alerts=6`, and `persisted=false`.
- `pmfi data-coverage --format json` exited 0 with `coverage_percent=100.0`, `normalized=451184`, `skipped_non_trade=162450`, `dead_lettered=0`, `unaccounted=0`, `excluded_synthetic_raw_events=44`, and `has_unaccounted_warning=false`.
- `pmfi dashboard --port 18767 --db-url <local DB>` served `GET /api/dashboard-capabilities` with `schema_version=dashboard_capabilities.v1` and `routes.persistence_health=true`.
- `GET /api/persistence-health` returned `persistence.venues=2` and `unresolved_dead_letters_1h=0`.

## Included workflows

- Setup and diagnostics: `pmfi init`, `pmfi doctor`, `pmfi status`, `pmfi db-verify`, `pmfi review-pass`, and `pmfi health`.
- Capture and market operations: `pmfi ingest`, `pmfi live`, `pmfi live-smoke`, `pmfi monitor`, and `pmfi markets discover`, `sync-one`, `recent-trades`, `refresh-watchlist`, `watch`, and `unwatch`.
- Data, replay, and analytics: `pmfi replay`, `pmfi replay-fixtures`, `pmfi data-coverage`, `pmfi backtest`, `pmfi backtest-analytics`, `pmfi raw-events`, `pmfi dead-letters`, `pmfi stats`, `pmfi db-maintenance`, `pmfi baselines`, `pmfi baseline`, and `pmfi soak`.
- Alert and review operations: `pmfi alerts list`, `explain`, `review`, `review-packet`, `outcome-audit`, `fp-rate`, and `serve`; local `watch`; local `report`; and volume-spike calibration, packet, decision, and cluster-review commands.

Live capture remains opt-in and disabled by default.

## Venues and transports

- Polymarket: public WebSocket for optional live read.
- Kalshi: public REST polling is the supported Kalshi path.
- Persistence: raw-before-derived Postgres storage for both venues.
- Extensibility: venue registry seam is present for future venue adapters.

## Alert-rule families

All listed rules are enabled in `alert_rules.v1`.

- `directional_cluster_v1`: high severity.
- `momentum_v1`: high severity.
- `open_interest_shock_v1`: high severity.
- `large_trade_absolute_v1`: medium severity.
- `market_relative_large_trade_v1`: medium severity.
- `volume_spike_v1`: low severity.

## Dashboard and API capabilities

The verified dashboard capabilities route advertises:

- `feedhealth`
- `persistence_health`
- `volume`
- `alerts`
- `alert_review_history`
- `alert_review_write`
- `volume_spike_calibration`
- `calibration_packets`
- `calibration_packet_comparison`
- `calibration_packet_review_summary`
- `calibration_packet_review_queue`
- `calibration_decisions`
- `calibration_cluster_reviews`
- `calibration_cluster_review_coverage`
- `raw_event_lookup`

## Optional enrichments

- Polymarket orderbook reconstruction exists but is disabled by default.
- `enable_cross_venue_matching`, `enable_wallet_intelligence`, and `enable_ml_scoring` are false and not implemented.
- Local delivery modes are `console`, `file`, and `localhost_http_receiver`; `file` is the default.

## Schema, config, and rule versions

- Package: `pm-flow-intel` version `0.0.1`.
- Rules: `alert_rules.v1`.
- Dashboard capabilities: `dashboard_capabilities.v1`.
- Backtest output: `backtest_summary.v1`.
- SQL migrations: `001_init.sql` through `013_normalized_trade_dedupe_guard.sql`.
- Alert suppression window: 300 seconds.

## Required lane proof states

The verified main line includes:

- M-DUR verified and merged.
- M-TRUTH verified and merged.
- M-OPS-POLISH verified and merged.
- M-SEAM verified and merged.
- M-DATA verified and merged.
- M-RC and M-RC-FIX verified and merged.
- M-PORT verified and merged.
- M-PORT-NITS verified and merged.
- M-RELEASE-v1 same-host rehearsal green.

## Explicit exclusions

This release profile does not include:

- Trading or order placement.
- Hosted or SaaS runtime.
- Billing.
- Tenant or user-account systems.
- RBAC or OIDC.
- External notification SaaS.
- External secret managers.
- Registry publishing, signing, or attestation.
- Automatic key rotation.
- Default live API calls.
- Authenticated Kalshi WebSocket support.
- Separate-machine reproducibility proof.
- Predictive-accuracy or alert-quality certification.

## Known limitations

- The release rehearsal used the same host and the existing local Docker/Postgres service; it did not prove a blank second-machine install.
- Current local DB coverage proof is over the operator DB state at rehearsal time, not a static fixture corpus.
- Local default DB password warnings are expected for the local Docker profile; operators should set `DATABASE_URL` and `POSTGRES_PASSWORD` for non-default local credentials.
- Polymarket alert-label expansion is future operational evidence, not part of this local v1 baseline proof.
- Optional #40 nits remain backlog only: throttle per-event `os.stat` in rules reload polling and default `RulesFileReloader` path from `engine._rules_path`.
