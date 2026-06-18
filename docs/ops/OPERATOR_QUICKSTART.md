# PMFI Operator Quick-Start

PMFI is a Windows-native, local-only prediction-market flow-intelligence tool. It captures public trade events from Polymarket and Kalshi, normalizes them into a local Postgres database, and emits explainable anomaly alerts. No trading, no SaaS, no external accounts required.

---

## 1. One-time setup

**Requirements:** Python 3.11+, Docker Desktop (for local Postgres).

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

Start and initialize the database (Docker Desktop must be running):

```powershell
python scripts\db_local.py up
python scripts\db_local.py init
python scripts\db_local.py verify
```

`verify` is read-only: it checks container readiness, required PMFI tables/views/indexes, and seeded venues without applying migrations or writing rows.

**Config file.** The tool loads `config\app.yaml` if it exists; otherwise it falls back to `config\app.example.yaml`. Copy the example and edit it:

```powershell
Copy-Item config\app.example.yaml config\app.yaml
```

Enable the venues you want in `config\app.yaml`:

```yaml
features:
  enable_polymarket_live: true   # Polymarket WebSocket
  enable_kalshi_live: true       # Kalshi public REST polling (no API key needed)
```

Verify everything looks correct:

```powershell
pmfi status
pmfi db-verify
```

---

## 2. End-to-end operating loop

### a. Discover markets

Pull active markets from each venue into the local DB. Discovery prints a ranked
top-10-by-volume preview with a copy-paste watch command per market:

```powershell
pmfi markets discover --venue polymarket
pmfi markets discover --venue kalshi
```

Both commands use public REST APIs — no credentials required. Add `--limit N` to cap the fetch (default 100), `--min-volume USD` to filter by minimum volume.

**Watch the highest-volume markets in one command** (this is what you usually want — high-volume markets are the ones that produce alerts):

```powershell
pmfi markets discover --venue polymarket --watch-top 10
```

### b. Find and watch markets

When Kalshi market discovery misses an active ticker, use the read-only
all-market recent-trades probe to find evidence-backed candidates from public
trade activity:

```powershell
$env:PMFI_ENABLE_LIVE = '1'
pmfi markets recent-trades
pmfi markets recent-trades --since-minutes 120 --limit 50 --format json
```

The output groups trades by ticker and prints
`pmfi markets sync-one <ticker> --venue kalshi --watch` follow-ups. The
`recent-trades` command is read-only and does not write to Postgres; `sync-one`
fetches that single public Kalshi market into local Postgres and can mark it
watched in the same local DB operation.

List markets ranked by volume (the default sort) so the most active markets are on top:

```powershell
pmfi markets list                      # ranked by volume, shows a Volume column
pmfi markets list --venue kalshi       # venue-scoped ranking and exact ticker display
pmfi markets list --venue kalshi --format json  # scriptable exact market IDs
pmfi markets list --search "bitcoin"
pmfi markets list --min-volume 100000  # only markets with volume >= 100k
pmfi markets list --watched
```

Sync and watch one Kalshi ticker that you already found from recent public trade
activity:

```powershell
pmfi markets sync-one <ticker> --venue kalshi --watch
```

Watch without copy-pasting the long `venue_market_id` — by title/id search or by top-N volume (both stateless, resolved from a fresh DB query):

```powershell
pmfi markets watch --search "world cup" --venue polymarket
pmfi markets watch --top 10 --venue polymarket
pmfi markets watch <market_id> --venue polymarket   # still works by exact id
```

Remove from the watch list (by id or by search):

```powershell
pmfi markets unwatch <market_id> --venue polymarket
pmfi markets unwatch --search "expired" --venue polymarket
```

> Volume is venue-relative: Polymarket is USD notional, Kalshi is contract count. Rank within a venue (discover/list per `--venue`); the figures are not cross-venue comparable.

### c. Run the ingest daemon

Start persistent ingest (Ctrl+C to stop). It reads all enabled venues from config and auto-reconnects on WebSocket close or Postgres restart:

```powershell
pmfi ingest
pmfi ingest --max-seconds 3600       # bounded persisted run for soak evidence
```

**Alert delivery.** On startup the daemon prints a delivery banner showing where alerts will land. The default delivery mode is `file`: each alert is appended to a dated JSONL file at `reports/alerts/alerts_YYYY-MM-DD.jsonl`. To switch to console-only (ephemeral), set `alerts.default_delivery: console` in `config\app.yaml`.

**Automatic partition maintenance.** The daemon auto-creates Postgres partitions (current month + 3 months ahead) on the first telemetry cycle and then once per day, so a run that crosses the 3-month horizon never lacks a current partition. No operator action is needed for partition creation.

**Retention warning.** If partitions older than `raw_retention_days` (default: 90 days) are found, the daemon prints a `WARNING` naming them. To reclaim disk space, run the manual prune command:

```powershell
pmfi db-maintenance --prune-old-partitions
```

The daemon **never** auto-drops old partitions — the destructive prune is always a manual step.

**Periodic baseline recompute.** The daemon recomputes market baselines in-process on a configurable interval (default: daily). No separate cron or manual command is needed during normal operation. To tune or disable, add a `baselines:` section to `config\app.yaml`:

```yaml
baselines:
  recompute_enabled: true          # set false to disable in-daemon recompute
  recompute_interval_minutes: 1440 # how often to recompute (default: daily)
  window_days: 30                  # lookback window in days
  min_samples: 10                  # minimum trade samples required per market
```

To trigger an immediate recompute outside the daemon, use the manual command (see §5 Baselines).

**Daemon health check.** The daemon writes a heartbeat file (`reports/health/heartbeat.json`) on startup and every 60 seconds. Check freshness from a second terminal:

```powershell
pmfi health                        # exit 0 = fresh, 1 = stale/missing
pmfi health --json                 # machine-readable JSON output
pmfi health --max-age-seconds 300  # custom staleness threshold (default: 120s)
pmfi health --heartbeat-path <path>  # override heartbeat file location
```

After a supervised run, validate the persisted soak evidence from Postgres:

```powershell
pmfi soak --window 2h
pmfi soak --window 2h --required-venue polymarket --format json
pmfi soak --window 2h --required-venue kalshi --min-required-venue-duration-minutes 60 --format json
pmfi soak --since 2026-06-18T12:45:04Z --until 2026-06-18T13:55:04Z --required-venue polymarket --required-venue kalshi --min-required-venue-duration-minutes 60 --format json
```

This is read-only. It fails closed when the window lacks enough raw events,
normalized trades, global raw-evidence duration, required venue coverage, an
explicit required-venue raw-evidence duration threshold, or has
unresolved dead letters / open data-quality incidents beyond the configured
thresholds. Use `--since` with the heartbeat `started_at` timestamp and `--until`
with the completed run's end timestamp when you need to prove one exact bounded
ingest run instead of any evidence inside a lookback window.

To target a specific venue only:

```powershell
pmfi ingest --venue polymarket
pmfi ingest --venue kalshi
```

**Validate config without writing to the DB** (connects to live feeds, prints events, no DB writes):

```powershell
pmfi ingest --dry-run
```

If ingest exits immediately with "No live venues enabled" — set `enable_polymarket_live` or `enable_kalshi_live` to `true` in `config\app.yaml`, or pass `--venue` explicitly.

If ingest exits with "No watched markets" — run `markets discover` then `markets watch` first (step a/b above).

> **Kalshi note:** Kalshi ingest runs via public REST polling (default interval: 5 seconds, configurable via `ingestion.kalshi_poll_interval_seconds` in `config\app.yaml`). No API key is required. Kalshi WebSocket ingest is not supported.

### d. View output (open a second terminal)

| Purpose | Command |
|---|---|
| Live auto-refreshing alert display | `pmfi watch` |
| Filtered alert drill-down | `pmfi alerts list` |
| Explain a single alert | `pmfi alerts explain <alert_id> [--format json]` |
| Summary report | `pmfi report` |
| Completed-run soak evidence | `pmfi soak --window 2h` or `pmfi soak --since <started_at> --until <ended_at>` |
| Strict venue soak evidence | `pmfi soak --window 2h --required-venue kalshi --min-required-venue-duration-minutes 60` |
| DB row counts per table | `pmfi stats` |
| Normalization failures | `pmfi dead-letters` |

`pmfi alerts explain <id>` prints a plain-English explanation of the stored evidence for a single alert. The **ID** column in `pmfi alerts list` and `pmfi watch` shows an 8-char prefix — paste it directly into `explain` or `review`; the full UUID is not required.

Use `pmfi alerts explain <id> --format json` when reviewing or scripting exact evidence, lineage IDs, and evidence summaries.
For bulk review, `pmfi alerts list --unreviewed --evidence --format json` includes parsed evidence, evidence summaries, and deterministic triage flags without writing review labels.
Use `pmfi alerts list --triage-flag FLAG` to drill into deterministic read-only cohorts such as `low_notional`, `thin_baseline`, `near_threshold`, `degraded_data_quality`, and `missing_lineage`. Repeat `--triage-flag` to require every requested flag. JSON output includes `triage_flags` for matching rows; raw evidence and lineage IDs are still omitted unless `--evidence` is also set.
`pmfi report --format json` and the default table report summarize those same deterministic flags for the current unreviewed queue; this is read-only triage metadata, not a recorded review label.

Use review-state filters to work the alert queue:

```powershell
pmfi alerts list --unreviewed
pmfi alerts list --reviewed
pmfi alerts list --review-label tp
pmfi alerts list --reviewed --review-label fp
```

`--review-label` matches the latest review row for each alert, the same review state used by the dashboard and report surfaces. It can be combined with `--reviewed`; `--unreviewed` cannot be combined with either `--reviewed` or `--review-label`.

`pmfi dead-letters` shows an 8-character ID prefix and resolved/unresolved status for each normalization failure. Use `pmfi dead-letters --format json` when you need full UUIDs, resolved timestamps, and scriptable previews without dumping full payloads. Preview a triage action with `pmfi dead-letters resolve <id-prefix> --dry-run`; omit `--dry-run` to mark exactly one unresolved row resolved. This updates `resolved` / `resolved_at` in local Postgres and does not delete rows.

### e. Localhost dashboard (optional)

```powershell
pmfi dashboard          # default port 8766
pmfi dashboard --port 9000
```

Opens a browser-friendly dashboard at `http://localhost:8766` with auto-polling panels for ingest rate, volume, feed health, and **alerts**. Alert rows show the short alert ID, deterministic triage flags, latest review state from Postgres, and a compact append-only review action for unreviewed rows. The alerts panel can filter by review state, latest review label, and deterministic triage flags; review writes use local POST `/api/alerts/{alert_id}/review` and insert one `alert_reviews` row. It requires the DB to be running but does not require `pmfi ingest` to be running simultaneously.

### f. Compute baselines

After enough trade data has accumulated, sharpen alert thresholds:

```powershell
pmfi baselines compute --days 7
```

`volume_spike_v1.min_trade_usd` in `config\alert_rules.yaml` is the configurable minimum trade size for spike-only alerts. The default is `$500` after Tier-1 review marked sub-$500 low-notional/thin-baseline spike alerts as noise.

This reads `normalized_trades`, computes p99/p99.5 percentiles per market, and **writes directly to the DB** (`market_baselines` table). The updated baselines are picked up automatically by `pmfi ingest`, `pmfi live`, and `pmfi replay` — no restart needed.

`--save` additionally writes a portable `config\baselines.json` file (optional — the DB is the canonical source).

---

## 3. Command cheat sheet

| Command | What it does | Key flags |
|---|---|---|
| `pmfi status` | Show config and feature-flag state | — |
| `pmfi db-verify` | Check Postgres connectivity | — |
| `pmfi markets discover` | Fetch markets + print ranked top-10-by-volume preview | `--venue`, `--limit`, `--min-volume`, `--watch-top N` |
| `pmfi markets list` | List markets ranked by volume | `--venue`, `--format table\|json`, `--sort {volume,trades,last-trade}`, `--min-volume USD`, `--search TEXT`, `--watched`, `--limit` |
| `pmfi markets sync-one` | Fetch one public Kalshi market by ticker into local Postgres | `ticker`, `--venue kalshi`, `--watch` |
| `pmfi markets watch` | Watch market(s): by id, `--top N`, or `--search TEXT` | `market_id`, `--top`, `--search`, `--venue` |
| `pmfi markets unwatch` | Unwatch market(s): by id or `--search TEXT` | `market_id`, `--search`, `--venue` |
| `pmfi markets recent-trades` | Read-only Kalshi all-market recent trade ticker probe | `--limit`, `--since-minutes`, `--format table\|json`, `--force` |
| `pmfi markets fetch-trades` | Fetch recent trades for one Kalshi ticker | `ticker`, `--limit`, `--save-fixtures`, `--force` |
| `pmfi ingest` | Persistent multi-venue ingest daemon | `--venue`, `--dry-run`, `--max-events` (dry-run only), `--max-seconds` |
| `pmfi watch` | Live auto-refreshing alert display | `--interval`, `--limit`, `--rule`, `--venue`, `--severity` |
| `pmfi alerts list` | Query alerts from DB | `--limit`, `--evidence`, `--triage-flag`, `--since`, `--severity`, `--venue`, `--market` title/id substring, `--rule`, `--unreviewed`, `--reviewed`, `--review-label tp\|fp\|noise`, `--format` |
| `pmfi alerts explain <id>` | Explain one alert; JSON is available for scripts | `alert_id`, `--format text\|json` |
| `pmfi alerts review <id>` | Record or preview a review label for an alert | `--label tp\|fp\|noise`, `--category`, `--notes`, `--reviewed-by`, `--dry-run` |
| `pmfi alerts fp-rate` | Show false-positive rate from recorded reviews | `--since`, `--rule` |
| `pmfi alerts serve` | Local HTTP receiver for alert delivery | `--host`, `--port` |
| `pmfi report` | Summary of recent alerts, review queue, review outcomes, and data gaps | `--since`, `--format` |
| `pmfi stats` | Aggregate DB row counts | — |
| `pmfi dead-letters` | Recent normalization failures | `--limit`, `--format table\|json` |
| `pmfi baselines compute` | Compute baselines from normalized trades | `--days`, `--min-samples`, `--save` |
| `pmfi baselines show` | Show current baselines (from the DB; falls back to the JSON file) | — |
| `pmfi replay` | Replay fixture files or DB events through the alert pipeline | `--fixture-dir`, `--persist`, `--from-db`, `--limit`, `--from TS`, `--to TS`, `--venue`, `--market`, `--verbose` |
| `pmfi dashboard` | Localhost dashboard (ingest rate, volume, alerts panels, append-only alert reviews) | `--port`, `--db-url` |
| `pmfi db-maintenance` | Partition creation and data retention cleanup | `--create-partitions`, `--months-ahead`, `--prune-old-partitions`, `--before-days` |
| `pmfi health` | Check daemon heartbeat freshness (exit 0=fresh, 1=stale/missing) | `--max-age-seconds`, `--json`, `--heartbeat-path` |

---

## 4. Which command when

**Capture path:**

- `pmfi ingest` — recommended for continuous operation. Handles both Polymarket (WebSocket) and Kalshi (REST polling) in one process.
- `pmfi ingest --max-seconds N` — bounded persisted ingest run for soak evidence; unlike `--dry-run`, this writes raw events/trades/alerts to local Postgres.
- `pmfi live` — Polymarket-only continuous capture. Requires `PMFI_ENABLE_LIVE=1` environment variable. Use if you need Polymarket in isolation. Flags: `--venue`, `--markets`, `--orderbook`, `--refresh-map-minutes`.
- `pmfi live-smoke` — bounded smoke test (stops after N events or N seconds). Requires `PMFI_ENABLE_LIVE=1` environment variable. Flags: `--venue`, `--max-events`, `--max-seconds`, `--asset-ids`, `--save-fixtures`, `--persist-raw`, `--force`.

**Alert views:**

- `pmfi watch` — live auto-refreshing terminal dashboard; good for monitoring while ingest is running.
- `pmfi alerts list` - filtered drill-down; supports `--since 24h`, `--severity high`, `--venue`, `--market`, `--rule`, `--unreviewed`, `--reviewed`, `--review-label tp|fp|noise`, `--triage-flag low_notional`, `--evidence`, `--format json`. `--market` matches market title, venue market ID, and internal market UUID substrings; `--review-label` filters by the latest review row; repeated `--triage-flag` values are ANDed and remain read-only metadata.
- `pmfi alerts explain <id>` — plain-English explanation of one alert's stored evidence. Get the ID from `pmfi alerts list`.
- `pmfi report` — narrative summary of activity over a time window (default: last 24h), including unreviewed alert IDs, deterministic triage flag counts for the review queue, latest review-label totals, false-positive categories, unresolved dead-letter summaries, and open data-quality incident counts.
- `pmfi dashboard` — browser dashboard at `http://localhost:8766`; includes live alerts panel with filters for review state, latest review label, deterministic triage flags, and append-only local review writes for unreviewed rows; no ingest required.

---

## 5. Baselines

**In-daemon recompute (automatic).** `pmfi ingest` recomputes baselines on a configurable interval (default: daily). This requires no operator action. To tune or disable, add a `baselines:` section to `config\app.yaml` (see §2c above).

**Manual on-demand recompute.** `pmfi baselines compute` is the canonical command for an immediate recompute outside the daemon. It reads `normalized_trades` (per-trade level), computes p99/p99.5 percentiles per market, and writes them to the `market_baselines` table. All consumers (`pmfi ingest`, `pmfi live`, `pmfi replay`, `pmfi monitor`) read from that table automatically.

```powershell
pmfi baselines compute --days 30
pmfi baselines compute --days 7 --min-samples 5
pmfi baselines show
```

Add `--save` to `baselines compute` only if you want a portable `config\baselines.json` snapshot in addition to the DB write (optional).

> **Note:** `pmfi baseline compute` (singular, no 's') still works but is deprecated. It redirects to `pmfi baselines compute` and prints a deprecation warning. Update any scripts that use the singular form.

---

## 6. Troubleshooting

**"No live venues enabled"**
Set `enable_polymarket_live: true` and/or `enable_kalshi_live: true` in `config\app.yaml`, or pass `--venue polymarket` / `--venue kalshi` to `pmfi ingest`.

**"No watched markets" / "No usable subscriptions"**
Run `pmfi markets discover --venue polymarket` and/or `--venue kalshi`, then `pmfi markets watch <market_id>`. For a Kalshi ticker found by `pmfi markets recent-trades`, run `pmfi markets sync-one <ticker> --venue kalshi --watch`.

**Alerts not appearing**
Check `pmfi stats` to confirm trades are being written. Check `pmfi dead-letters` for normalization failures. Verify `pmfi status` shows live venues enabled.

**Kalshi live ingest not working**
Kalshi WebSocket ingest requires RSA authentication and is not supported. Kalshi ingest runs automatically via public REST polling when `enable_kalshi_live: true` and Kalshi markets are watched.

**DB connectivity errors**
Run `pmfi db-verify`. Ensure Docker Desktop is running and the container is up (`python scripts\db_local.py up`).

**PowerShell script execution blocked**
Use `pmfi.cmd <command>` (Command Prompt) or call `python -m pmfi.cli <command>` directly.

**Existing DB is missing columns / "column does not exist" errors**
The daemon applies all incremental schema migrations automatically on startup (`startup_maintenance`). If you see column-not-found errors, ensure the daemon has started at least once against your DB, or re-run:
```powershell
python scripts\db_local.py init   # idempotent — safe on an existing DB
```
This applies all SQL migration files and is safe to re-run. If the error persists, check `python scripts\db_local.py verify`; it now fails closed when required schema objects are missing.

---

## 7. Alert review and false-positive feedback

After alerts fire, you can mark them as true positives, false positives, or noise. This feedback is stored in Postgres and can be used to assess rule quality over time.

**Mark a single alert:**

```powershell
pmfi alerts review <alert_id> --label fp          # false positive
pmfi alerts review <alert_id> --label tp          # true positive
pmfi alerts review <alert_id> --label noise       # technically correct but not actionable
pmfi alerts review <alert_id> --label fp --category "stale_baseline" --notes "baseline was 45 days old"
pmfi alerts review <alert_id> --label noise --category "low_notional" --dry-run
```

Get `<alert_id>` from the **ID** column in `pmfi alerts list` — the 8-char prefix shown there is accepted directly by both `review` and `explain`. Full UUID also works (`pmfi alerts list --format json` to retrieve it).

The dashboard uses the same local review labels and alert-prefix resolution through POST `/api/alerts/{alert_id}/review`. It validates JSON bodies fail-closed, accepts only `label` plus optional string `category`, `notes`, and `reviewed_by`, and appends a new `alert_reviews` row without updating or deleting prior reviews.

**Work the review queue:**

```powershell
pmfi alerts list --unreviewed              # queue items with no review rows
pmfi alerts list --reviewed                # alerts with at least one review row
pmfi alerts list --review-label noise      # latest review label is noise
pmfi alerts list --reviewed --review-label tp
pmfi alerts list --triage-flag low_notional
pmfi alerts list --triage-flag low_notional --triage-flag thin_baseline
```

Review-label filtering uses the latest review per alert, ordered by review timestamp and review id. Older labels on the same alert do not match this filter if a newer review changed the label.
Triage-flag filtering computes deterministic flags from stored alert evidence and does not create `tp`, `fp`, or `noise` review rows.

**View false-positive rate:**

```powershell
pmfi alerts fp-rate                # all time, all rules
pmfi alerts fp-rate --since 7d     # last 7 days
pmfi alerts fp-rate --rule large_trade_absolute_v1
```

Labels:
- `tp` — true positive: alert was correct and actionable
- `fp` — false positive: alert fired incorrectly (wrong threshold, stale baseline, bad data)
- `noise` — alert condition was met but the signal was not useful (e.g. thin market, low-volume period)

---

## 8. Daemon log file

By default, `pmfi ingest` writes telemetry to the console only. To capture logs durably, enable the rotating file handler.

### Option A — config file

Add `log_file` under the `app:` section in `config\app.yaml`:

```yaml
app:
  log_level: INFO
  log_file: reports/logs/pmfi.log
```

### Option B — CLI flag (overrides config)

```powershell
pmfi ingest --log-file reports\logs\pmfi.log
```

The parent directory (`reports\logs\`) is created automatically on first use. The handler rotates at **5 MB** and keeps **3 backup files** (`pmfi.log.1`, `pmfi.log.2`, `pmfi.log.3`).

### Tailing the log in PowerShell

```powershell
Get-Content -Wait -Tail 50 reports\logs\pmfi.log
```

This streams new lines as they are written, similar to `tail -f` on Unix.

---

## 9. Autostart — running the daemon on Windows login

`scripts\autostart.py` manages a Windows Scheduled Task that starts `pmfi ingest` automatically when you log on. No third-party tools required — it uses the built-in `schtasks` command.

### Install

```powershell
python scripts\autostart.py install
```

This registers a task named **"PMFI Ingest"** that runs:

```
.venv\Scripts\pmfi.exe ingest --log-file <repo>\reports\logs\pmfi.log
```

on every user logon. Both paths are absolute so the task is not sensitive to a working directory. The `/F` flag makes repeated installs idempotent (safe to re-run after a config change).

**Preview without registering (dry-run):**

```powershell
python scripts\autostart.py install --dry-run
```

Prints the exact `schtasks` command that *would* be executed. Use this to verify the paths before committing.

### Verify the task is registered

```powershell
python scripts\autostart.py status
```

Or query via the built-in Windows tool directly:

```powershell
schtasks /Query /TN "PMFI Ingest" /FO LIST
```

### Uninstall

```powershell
python scripts\autostart.py uninstall
```

Removes the scheduled task. Safe to run even if the task is not registered — it reports "nothing to remove" instead of failing.

### Where output goes

Daemon output is written to `reports\logs\pmfi.log` (rotating, 5 MB per file, 3 backups). Tail it while the daemon runs:

```powershell
Get-Content -Wait -Tail 50 reports\logs\pmfi.log
```

### Important: Docker Desktop and Postgres must be running

The ingest daemon connects to a local Postgres container. If Docker Desktop is not running at logon, the daemon will fail its DB preflight and exit (or retry/supervise depending on the error). **Recommended mitigation:** configure Docker Desktop to start on login via its own system-tray settings (`Settings > General > Start Docker Desktop when you log in`). The daemon can then connect successfully once Docker Desktop finishes starting.

### Options

| Flag | Default | Description |
|---|---|---|
| `--task-name NAME` | `PMFI Ingest` | Override the scheduled task name |
| `--trigger onlogon\|onstart` | `onlogon` | `onstart` fires at boot (requires elevated prompt) |
| `--log-file PATH` | `<repo>\reports\logs\pmfi.log` | Override the log file path |
| `--pmfi-exe PATH` | `<repo>\.venv\Scripts\pmfi.exe` | Override the executable path |
| `--dry-run` | off | Print the command without running it |
