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

Pull active markets from each venue into the local DB:

```powershell
pmfi markets discover --venue polymarket
pmfi markets discover --venue kalshi
```

Both commands use public REST APIs — no credentials required. Add `--limit N` to cap the fetch (default 100). Add `--min-volume USD` to filter by minimum volume.

### b. Find and watch markets

Search what was discovered:

```powershell
pmfi markets list --search "bitcoin"
pmfi markets list --watched
```

Add a market to the watch list (replace with an actual `venue_market_id` from the list):

```powershell
pmfi markets watch <market_id> --venue polymarket
pmfi markets watch <ticker>   --venue kalshi
```

Remove from the watch list:

```powershell
pmfi markets unwatch <market_id> --venue polymarket
```

### c. Run the ingest daemon

Start persistent ingest (Ctrl+C to stop). It reads all enabled venues from config and auto-reconnects on WebSocket close or Postgres restart:

```powershell
pmfi ingest
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
| Explain a single alert | `pmfi alerts explain <alert_id>` |
| Summary report | `pmfi report` |
| DB row counts per table | `pmfi stats` |
| Normalization failures | `pmfi dead-letters` |

`pmfi alerts explain <alert_id>` prints a plain-English explanation of the stored evidence for a single alert. Get the UUID from `pmfi alerts list`.

### e. Localhost dashboard (optional)

```powershell
pmfi dashboard          # default port 8766
pmfi dashboard --port 9000
```

Opens a browser-friendly dashboard at `http://localhost:8766` with auto-polling panels for ingest rate, volume, feed health, and **alerts** (backed by the `/api/alerts` endpoint). The dashboard is read-only and requires the DB to be running; it does not require `pmfi ingest` to be running simultaneously.

### f. Compute baselines

After enough trade data has accumulated, sharpen alert thresholds:

```powershell
pmfi baselines compute --days 7
```

This reads `normalized_trades`, computes p99/p99.5 percentiles per market, and **writes directly to the DB** (`market_baselines` table). The updated baselines are picked up automatically by `pmfi ingest`, `pmfi live`, and `pmfi replay` — no restart needed.

`--save` additionally writes a portable `config\baselines.json` file (optional — the DB is the canonical source).

---

## 3. Command cheat sheet

| Command | What it does | Key flags |
|---|---|---|
| `pmfi status` | Show config and feature-flag state | — |
| `pmfi db-verify` | Check Postgres connectivity | — |
| `pmfi markets discover` | Fetch active markets from venue REST API | `--venue`, `--limit`, `--min-volume` |
| `pmfi markets list` | List markets in DB | `--search TEXT`, `--watched`, `--limit` |
| `pmfi markets watch` | Add a market to the watch list | `market_id`, `--venue` |
| `pmfi markets unwatch` | Remove a market from the watch list | `market_id`, `--venue` |
| `pmfi markets fetch-trades` | Fetch recent trades for one Kalshi ticker | `ticker`, `--limit`, `--save-fixtures`, `--force` |
| `pmfi ingest` | Persistent multi-venue ingest daemon | `--venue`, `--dry-run` |
| `pmfi watch` | Live auto-refreshing alert display | `--interval`, `--limit`, `--rule`, `--venue`, `--severity` |
| `pmfi alerts list` | Query alerts from DB | `--limit`, `--evidence`, `--since`, `--severity`, `--venue`, `--market`, `--rule`, `--format` |
| `pmfi alerts explain <id>` | Plain-English explanation of one alert | `alert_id` |
| `pmfi alerts serve` | Local HTTP receiver for alert delivery | `--host`, `--port` |
| `pmfi report` | Summary of recent alert activity | `--since`, `--format` |
| `pmfi stats` | Aggregate DB row counts | — |
| `pmfi dead-letters` | Recent normalization failures | `--limit` |
| `pmfi baselines compute` | Compute baselines from normalized trades | `--days`, `--min-samples`, `--save` |
| `pmfi baselines show` | Show current baselines (from the DB; falls back to the JSON file) | — |
| `pmfi replay` | Replay fixture files or DB events through the alert pipeline | `--fixture-dir`, `--persist`, `--from-db`, `--limit`, `--from TS`, `--to TS`, `--venue`, `--market`, `--verbose` |
| `pmfi dashboard` | Localhost read-only dashboard (ingest rate, volume, alerts panels) | `--port`, `--db-url` |
| `pmfi db-maintenance` | Partition creation and data retention cleanup | `--create-partitions`, `--months-ahead`, `--prune-old-partitions`, `--before-days` |
| `pmfi health` | Check daemon heartbeat freshness (exit 0=fresh, 1=stale/missing) | `--max-age-seconds`, `--json`, `--heartbeat-path` |

---

## 4. Which command when

**Capture path:**

- `pmfi ingest` — recommended for continuous operation. Handles both Polymarket (WebSocket) and Kalshi (REST polling) in one process.
- `pmfi live` — Polymarket-only continuous capture. Use if you need Polymarket in isolation. Flags: `--venue`, `--markets`, `--orderbook`, `--refresh-map-minutes`.
- `pmfi live-smoke` — bounded smoke test (stops after N events or N seconds). Requires `PMFI_ENABLE_LIVE=1` environment variable. Flags: `--venue`, `--max-events`, `--max-seconds`, `--asset-ids`, `--save-fixtures`, `--persist-raw`, `--force`.

**Alert views:**

- `pmfi watch` — live auto-refreshing terminal dashboard; good for monitoring while ingest is running.
- `pmfi alerts list` — filtered drill-down; supports `--since 24h`, `--severity high`, `--venue`, `--market`, `--rule`, `--evidence`, `--format json`.
- `pmfi alerts explain <id>` — plain-English explanation of one alert's stored evidence. Get the ID from `pmfi alerts list`.
- `pmfi report` — narrative summary of activity over a time window (default: last 24h).
- `pmfi dashboard` — browser dashboard at `http://localhost:8766`; includes live alerts panel (via `/api/alerts`). Read-only; no ingest required.

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
Run `pmfi markets discover --venue polymarket` and/or `--venue kalshi`, then `pmfi markets watch <market_id>`.

**Alerts not appearing**
Check `pmfi stats` to confirm trades are being written. Check `pmfi dead-letters` for normalization failures. Verify `pmfi status` shows live venues enabled.

**Kalshi live ingest not working**
Kalshi WebSocket ingest requires RSA authentication and is not supported. Kalshi ingest runs automatically via public REST polling when `enable_kalshi_live: true` and Kalshi markets are watched.

**DB connectivity errors**
Run `pmfi db-verify`. Ensure Docker Desktop is running and the container is up (`python scripts\db_local.py up`).

**PowerShell script execution blocked**
Use `pmfi.cmd <command>` (Command Prompt) or call `python -m pmfi.cli <command>` directly.
