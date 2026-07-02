"""Alert command handlers: alerts list and alerts serve.

Note: cmd_alerts_explain stays in pmfi.cli because tests patch pmfi.cli.asyncio.run
when testing it.  cmd_alerts also stays in pmfi.cli because it dispatches to
cmd_alerts_explain which must resolve in cli.py's namespace.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from pmfi.alert_triage import parse_evidence as _parse_evidence
from pmfi.alert_triage import triage_flags as _triage_flags
from pmfi.data_reports import (
    DEFAULT_FP_RATE_MIN_REVIEWED,
    apply_floor_gated_governance_headlines,
    build_fp_rate_governance_rows,
    build_volume_spike_current_floor_governance,
)


def _parse_since_window(raw: str | None, *, command: str):
    """Parse a relative or ISO since value; return (datetime, error_message)."""
    if not raw:
        return datetime.now(timezone.utc) - timedelta(hours=24), None
    import re
    match = re.match(r"^(\d+)([hdm])$", raw)
    if match:
        n, unit = int(match.group(1)), match.group(2)
        delta = {"h": 3600, "d": 86400, "m": 60}[unit] * n
        return datetime.now(timezone.utc) - timedelta(seconds=delta), None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None, f"[{command}] Invalid --since value: {raw!r}"
    if dt.tzinfo is None:
        return None, f"[{command}] Invalid --since value: {raw!r}; timestamp must include timezone."
    return dt.astimezone(timezone.utc), None


def _parse_until_window(raw: str | None, *, command: str):
    if not raw:
        return None, None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None, f"[{command}] Invalid --until value: {raw!r}"
    if dt.tzinfo is None:
        return None, f"[{command}] Invalid --until value: {raw!r}; timestamp must include timezone."
    return dt.astimezone(timezone.utc), None


def _default_review_packet_path() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S%fZ")
    return _review_packet_output_root() / f"review-packet-{stamp}.json"


def _review_packet_output_root() -> Path:
    from pmfi.commands._shared import ROOT

    return ROOT / "reports" / "review-packets"


def _calibration_packet_output_root() -> Path:
    from pmfi.commands._shared import ROOT

    return ROOT / "reports" / "calibration-packets"


def _calibration_decision_output_root() -> Path:
    from pmfi.commands._shared import ROOT

    return ROOT / "reports" / "calibration-decisions"


def _calibration_cluster_review_output_root() -> Path:
    from pmfi.commands._shared import ROOT

    return ROOT / "reports" / "calibration-cluster-reviews"


def _default_volume_spike_calibration_packet_path() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%SZ")
    return _calibration_packet_output_root() / f"volume-spike-calibration-{stamp}.json"


def _default_calibration_decision_path() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%SZ")
    return _calibration_decision_output_root() / f"calibration-decision-{stamp}.json"


def _default_calibration_cluster_review_path() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%SZ")
    return _calibration_cluster_review_output_root() / f"cluster-review-{stamp}.json"


def _resolve_review_packet_output(output_raw: str | None) -> tuple[Path | None, str | None]:
    output_root = _review_packet_output_root().resolve()
    if not output_raw:
        output_path = _default_review_packet_path()
    else:
        raw_path = Path(output_raw)
        if raw_path.is_absolute():
            output_path = raw_path
        elif raw_path.parent == Path("."):
            output_path = output_root / raw_path.name
        else:
            from pmfi.commands._shared import ROOT

            output_path = ROOT / raw_path
    resolved = output_path.resolve()
    try:
        resolved.relative_to(output_root)
    except ValueError:
        return None, (
            "[alerts review-packet] --output must be inside "
            f"{output_root}"
        )
    if resolved.exists():
        return None, f"[alerts review-packet] output already exists: {resolved}"
    return resolved, None


def _resolve_volume_spike_calibration_packet_output(
    output_raw: str | None,
) -> tuple[Path | None, str | None]:
    output_root = _calibration_packet_output_root().resolve()
    if not output_raw:
        output_path = _default_volume_spike_calibration_packet_path()
    else:
        raw_path = Path(output_raw)
        if raw_path.is_absolute():
            output_path = raw_path
        elif raw_path.parent == Path("."):
            output_path = output_root / raw_path.name
        else:
            from pmfi.commands._shared import ROOT

            output_path = ROOT / raw_path
    resolved = output_path.resolve()
    try:
        resolved.relative_to(output_root)
    except ValueError:
        return None, (
            "[alerts volume-spike-calibration] --packet-output must be inside "
            f"{output_root}"
        )
    if resolved.exists():
        return None, (
            "[alerts volume-spike-calibration] packet output already exists: "
            f"{resolved}"
        )
    return resolved, None


def _resolve_calibration_decision_output(
    output_raw: str | None,
) -> tuple[Path | None, str | None]:
    output_root = _calibration_decision_output_root().resolve()
    if not output_raw:
        output_path = _default_calibration_decision_path()
    else:
        raw_path = Path(output_raw)
        if raw_path.is_absolute():
            output_path = raw_path
        elif raw_path.parent == Path("."):
            output_path = output_root / raw_path.name
        else:
            from pmfi.commands._shared import ROOT

            output_path = ROOT / raw_path
    resolved = output_path.resolve()
    try:
        resolved.relative_to(output_root)
    except ValueError:
        return None, (
            "[calibration-decision] --output must be inside "
            f"{output_root}"
        )
    if resolved.exists():
        return None, f"[calibration-decision] output already exists: {resolved}"
    return resolved, None


def _resolve_calibration_cluster_review_output(
    output_raw: str | None,
) -> tuple[Path | None, str | None]:
    output_root = _calibration_cluster_review_output_root().resolve()
    if not output_raw:
        output_path = _default_calibration_cluster_review_path()
    else:
        raw_path = Path(output_raw)
        if raw_path.is_absolute():
            output_path = raw_path
        elif raw_path.parent == Path("."):
            output_path = output_root / raw_path.name
        else:
            from pmfi.commands._shared import ROOT

            output_path = ROOT / raw_path
    resolved = output_path.resolve()
    try:
        resolved.relative_to(output_root)
    except ValueError:
        return None, (
            "[calibration-cluster-review] --output must be inside "
            f"{output_root}"
        )
    if resolved.exists():
        return None, (
            f"[calibration-cluster-review] output already exists: {resolved}"
        )
    return resolved, None


def _safe_calibration_packet_slug(raw: str, *, label: str) -> tuple[str | None, str | None]:
    import re

    value = str(raw or "").strip()
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,78}[a-z0-9])?", value):
        return None, (
            f"[calibration-packet-batch] {label} must be lowercase kebab-case "
            "without path separators."
        )
    return value, None


def _parse_explicit_calibration_window_ts(
    raw: str,
    *,
    label: str,
) -> tuple[datetime | None, str | None]:
    value = str(raw or "").strip()
    if not value:
        return None, f"{label} is empty"
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None, f"{label} must be a timezone-aware ISO timestamp"
    if parsed.tzinfo is None:
        return None, f"{label} must include timezone"
    return parsed.astimezone(timezone.utc), None


def _parse_calibration_packet_batch_window(
    raw: str,
) -> tuple[dict[str, str] | None, str | None]:
    if ":" not in raw:
        return None, (
            "[calibration-packet-batch] invalid --window; expected "
            "NAME:SINCE:UNTIL with timezone-aware ISO timestamps."
        )
    raw_name, payload = raw.split(":", 1)
    name, name_err = _safe_calibration_packet_slug(raw_name, label="window NAME")
    if name_err:
        return None, name_err

    matches: list[tuple[datetime, datetime]] = []
    for idx, char in enumerate(payload):
        if char != ":":
            continue
        since_raw = payload[:idx]
        until_raw = payload[idx + 1:]
        since_dt, since_err = _parse_explicit_calibration_window_ts(
            since_raw,
            label="SINCE",
        )
        until_dt, until_err = _parse_explicit_calibration_window_ts(
            until_raw,
            label="UNTIL",
        )
        if since_err or until_err or since_dt is None or until_dt is None:
            continue
        matches.append((since_dt, until_dt))

    if len(matches) != 1:
        return None, (
            "[calibration-packet-batch] invalid --window; expected "
            "NAME:SINCE:UNTIL with timezone-aware ISO timestamps."
        )
    since_dt, until_dt = matches[0]
    if since_dt >= until_dt:
        return None, "[calibration-packet-batch] --window SINCE must be before UNTIL."
    return {
        "name": name or "",
        "since": since_dt.isoformat(),
        "until": until_dt.isoformat(),
    }, None


def _parse_calibration_sweep_window(
    raw: str,
) -> tuple[dict[str, str] | None, str | None]:
    parsed, err = _parse_calibration_packet_batch_window(raw)
    if err:
        return None, err.replace(
            "[calibration-packet-batch]",
            "[volume-spike-calibration-sweep]",
        )
    return parsed, None


def _decimal_label(value: Decimal | None) -> str:
    if value is None:
        return "default"
    normalized = value.normalize()
    text = format(normalized, "f")
    return text.replace(".", "p")


def _volume_spike_sweep_recommendation(row: dict[str, int]) -> str:
    if row["removed_reviewed_tp"] > 0:
        return "blocked-by-true-positive-risk"
    if (
        row["removed_reviewed_noise_or_fp"] > 0
        and row["removed_reviewed_tp"] == 0
        and row["removed_review_unmatched"] == 0
        and row["added"] == 0
    ):
        return "change-ready-candidate"
    if row["removed"] > 0 and (
        row["removed_reviewed_noise_or_fp"] == 0
        or row["removed_review_unmatched"] > 0
    ):
        return "needs-persisted-review-evidence"
    if row["removed"] == 0 and row["added"] == 0:
        return "no-candidate-effect"
    return "inspect-required"


def _int_count_map(value: object) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    counts: dict[str, int] = {}
    for key, raw_count in value.items():
        try:
            counts[str(key)] = int(raw_count or 0)
        except (TypeError, ValueError):
            counts[str(key)] = 0
    return dict(sorted(counts.items()))


def _merge_int_count_map(target: dict[str, int], source: dict[str, int]) -> None:
    for key, count in source.items():
        target[key] = target.get(key, 0) + int(count)


def _int_value(value: object, *, default: int = 0) -> int:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _volume_spike_shape_profile(
    value: object,
    *,
    total: int = 0,
    trade_buckets: dict[str, int] | None = None,
    low_notional_thin_baseline_count: int = 0,
) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    return {
        "total": _int_value(raw.get("total"), default=total),
        "trade_usd_buckets": _int_count_map(
            raw.get("trade_usd_buckets") or trade_buckets or {}
        ),
        "spike_multiplier_buckets": _int_count_map(
            raw.get("spike_multiplier_buckets")
        ),
        "triage_flag_counts": _int_count_map(raw.get("triage_flag_counts")),
        "near_threshold_count": _int_value(raw.get("near_threshold_count")),
        "low_notional_thin_baseline_count": _int_value(
            raw.get("low_notional_thin_baseline_count"),
            default=low_notional_thin_baseline_count,
        ),
    }


def _empty_volume_spike_shape_profile() -> dict[str, Any]:
    return _volume_spike_shape_profile({})


def _merge_volume_spike_shape_profile(
    target: dict[str, Any],
    source: dict[str, Any],
) -> None:
    target["total"] = _int_value(target.get("total")) + _int_value(source.get("total"))
    target["near_threshold_count"] = _int_value(
        target.get("near_threshold_count")
    ) + _int_value(source.get("near_threshold_count"))
    target["low_notional_thin_baseline_count"] = _int_value(
        target.get("low_notional_thin_baseline_count")
    ) + _int_value(source.get("low_notional_thin_baseline_count"))
    _merge_int_count_map(
        target["trade_usd_buckets"],
        _int_count_map(source.get("trade_usd_buckets")),
    )
    _merge_int_count_map(
        target["spike_multiplier_buckets"],
        _int_count_map(source.get("spike_multiplier_buckets")),
    )
    _merge_int_count_map(
        target["triage_flag_counts"],
        _int_count_map(source.get("triage_flag_counts")),
    )


def _json_serial(obj):  # noqa: ANN001
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    return str(obj)


def explain_operator_evidence_lines(evidence: dict[str, Any]) -> tuple[list[str], set[str]]:
    lines: list[str] = []
    shown: set[str] = set()

    margin = evidence.get("margin_to_threshold")
    if margin is not None:
        unit = str(evidence.get("margin_to_threshold_unit") or "relative_ratio")
        shown.update({"margin_to_threshold", "margin_to_threshold_unit"})
        try:
            margin_value = float(margin)
        except (TypeError, ValueError):
            lines.append(f"  margin_to_threshold={margin}")
        else:
            if unit == "relative_ratio":
                direction = "above" if margin_value >= 0 else "below"
                lines.append(
                    "  "
                    f"margin_to_threshold={abs(margin_value) * 100:.1f}% "
                    f"{direction} binding threshold"
                )
            else:
                lines.append(f"  margin_to_threshold={margin_value:.4f} {unit}")

    quality = evidence.get("baseline_sample_quality")
    if quality is not None:
        shown.add("baseline_sample_quality")
        lines.append(f"  baseline_sample_quality={quality}")

    computed_at = evidence.get("baseline_computed_at")
    if computed_at:
        shown.add("baseline_computed_at")
        lines.append(f"  baseline_computed_at={computed_at}")

    return lines, shown


def cmd_alerts_list(args: argparse.Namespace) -> int:
    from pmfi.config import load_config
    import asyncpg

    limit = getattr(args, "limit", None) or 20
    show_evidence = getattr(args, "evidence", False)
    rule_filter = getattr(args, "rule", None)
    venue_filter = getattr(args, "venue", None)
    severity_filter = getattr(args, "severity", None)
    market_filter = getattr(args, "market", None)
    unreviewed_filter = getattr(args, "unreviewed", False)
    reviewed_filter = getattr(args, "reviewed", False)
    review_label_filter = getattr(args, "review_label", None)
    triage_filters = list(getattr(args, "triage_flag", None) or [])
    needs_triage = bool(triage_filters)
    needs_evidence_fields = show_evidence or needs_triage
    fmt = getattr(args, "format", "table")
    has_result_filters = any([
        rule_filter,
        venue_filter,
        severity_filter,
        market_filter,
        unreviewed_filter,
        reviewed_filter,
        review_label_filter,
        getattr(args, "since", None),
    ])

    if unreviewed_filter and (reviewed_filter or review_label_filter):
        print("[alerts list] --unreviewed cannot be combined with --reviewed or --review-label.")
        return 1
    if review_label_filter and review_label_filter not in {"tp", "fp", "noise"}:
        print("[alerts list] --review-label must be one of: tp, fp, noise.")
        return 1

    # Parse --since: accepts relative ("1h", "24h", "7d") or ISO datetime string
    since_dt = None
    since_raw = getattr(args, "since", None)
    if since_raw:
        import re
        _m = re.match(r"^(\d+)([hdm])$", since_raw)
        if _m:
            n, unit = int(_m.group(1)), _m.group(2)
            delta = {"h": 3600, "d": 86400, "m": 60}[unit] * n
            from datetime import datetime, timezone, timedelta
            since_dt = datetime.now(timezone.utc) - timedelta(seconds=delta)
        else:
            from datetime import datetime
            try:
                since_dt = datetime.fromisoformat(since_raw)
            except ValueError:
                print(f"[alerts list] Invalid --since value: {since_raw!r}")
                return 1

    async def _query():
        cfg = load_config()
        try:
            pool = await asyncpg.create_pool(
                cfg.database.url, min_size=1, max_size=1,
                server_settings={"search_path": "pmfi,public"},
            )
        except Exception as exc:
            return None, str(exc)
        try:
            ev_col = ", a.evidence, a.raw_event_id, a.trade_id::text AS trade_id" if needs_evidence_fields else ""
            conditions: list[str] = []
            params: list = []
            idx = 1
            if rule_filter:
                conditions.append(f"a.rule_key = ${idx}")
                params.append(rule_filter)
                idx += 1
            if venue_filter:
                conditions.append(f"a.venue_code = ${idx}")
                params.append(venue_filter)
                idx += 1
            if severity_filter:
                conditions.append(f"a.severity = ${idx}")
                params.append(severity_filter)
                idx += 1
            if market_filter:
                conditions.append(
                    f"(m.title ILIKE ${idx} OR m.venue_market_id ILIKE ${idx} OR a.market_id::text ILIKE ${idx})"
                )
                params.append(f"%{market_filter}%")
                idx += 1
            if since_dt is not None:
                conditions.append(f"a.fired_at >= ${idx}")
                params.append(since_dt)
                idx += 1
            if unreviewed_filter:
                conditions.append("lr.alert_id IS NULL")
            if reviewed_filter:
                conditions.append("lr.alert_id IS NOT NULL")
            if review_label_filter:
                conditions.append(f"lr.review_label = ${idx}")
                params.append(review_label_filter)
                idx += 1
            where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
            limit_clause = ""
            if not needs_triage:
                params.append(limit)
                limit_clause = f" LIMIT ${idx}"
            rows = await pool.fetch(
                f"WITH latest_reviews AS ("
                f"SELECT DISTINCT ON (ar.alert_id) ar.alert_id, ar.label AS review_label "
                f"FROM alert_reviews ar "
                f"ORDER BY ar.alert_id, ar.reviewed_at DESC, ar.review_id DESC"
                f") "
                f"SELECT a.alert_id, a.fired_at, a.rule_key, a.rule_version, a.severity, a.confidence, a.score, "
                f"a.venue_code, a.outcome_key, a.data_quality, LEFT(m.title, 60) AS market_title, "
                f"mo.outcome_label, "
                f"lr.review_label AS review_label"
                f"{ev_col} "
                f"FROM alerts a "
                f"LEFT JOIN markets m ON m.market_id = a.market_id "
                f"LEFT JOIN market_outcomes mo ON mo.market_id = a.market_id AND mo.outcome_key = a.outcome_key "
                f"LEFT JOIN latest_reviews lr ON lr.alert_id = a.alert_id "
                f"{where} ORDER BY a.fired_at DESC{limit_clause}",
                *params,
            )
            return rows, None
        finally:
            await pool.close()

    rows, err = asyncio.run(_query())
    if err:
        print(f"DB query failed: {err}\nRun 'pmfi db-verify' to check connectivity.")
        return 1
    if not rows:
        if needs_triage:
            print(f"No alerts match triage flags: {', '.join(triage_filters)}.")
            return 0
        if has_result_filters:
            print("No alerts match the selected filters.")
            return 0
        print("No alerts in DB. Run 'pmfi replay --persist' to populate.")
        return 0

    if needs_evidence_fields:
        enriched_rows = []
        required_flags = set(triage_filters)
        for row in rows:
            item = dict(row)
            evidence = _parse_evidence(item.get("evidence"))
            flags = _triage_flags(item, evidence)
            item["_evidence_parsed"] = evidence
            item["triage_flags"] = flags
            if required_flags and not required_flags.issubset(set(flags)):
                continue
            enriched_rows.append(item)
        rows = enriched_rows[:limit] if needs_triage else enriched_rows
        if needs_triage and not rows:
            print(f"No alerts match triage flags: {', '.join(triage_filters)}.")
            return 0

    # JSON output mode
    if fmt == "json":
        import json as _json
        from pmfi.dashboard.queries import _summarize_evidence
        def _serial(obj):
            if hasattr(obj, "isoformat"):
                return obj.isoformat()
            return str(obj)
        payload = []
        for row in rows:
            item = dict(row)
            if show_evidence:
                evidence = item.pop("_evidence_parsed", None) or _parse_evidence(item.get("evidence"))
                item["evidence_parsed"] = evidence
                item["evidence_summary"] = _summarize_evidence(evidence)
                item["triage_flags"] = item.get("triage_flags") or _triage_flags(item, evidence)
            else:
                item.pop("_evidence_parsed", None)
                if needs_triage:
                    item.pop("evidence", None)
                    item.pop("raw_event_id", None)
                    item.pop("trade_id", None)
            payload.append(item)
        print(_json.dumps(payload, indent=2, default=_serial))
        return 0

    count = len(rows)
    try:
        from rich.console import Console
        from rich.table import Table
        # Force wide output so rule names, timestamps, and optional flags stay visible.
        console = Console(width=240 if needs_triage else 140)
        table = Table(title=f"Recent Alerts (DB, last {count})", show_lines=show_evidence)
        table.add_column("ID", style="dim", no_wrap=True, min_width=8)
        table.add_column("When", style="cyan", no_wrap=True, min_width=11)
        table.add_column("Rule", style="yellow", min_width=32)
        table.add_column("Ver", min_width=8)
        table.add_column("Sev", style="red", min_width=4)
        table.add_column("Conf", min_width=6)
        table.add_column("DQ", min_width=10)
        table.add_column("Venue", style="green", min_width=10)
        table.add_column("Outcome", min_width=3)
        table.add_column("Label", min_width=8)
        table.add_column("Score", min_width=6)
        table.add_column("Market", style="dim", min_width=20)
        if needs_triage:
            table.add_column("Flags", min_width=12, no_wrap=True)
        if show_evidence:
            table.add_column("Evidence")
        for row in rows:
            when = str(row["fired_at"])[5:16]  # "MM-DD HH:MM"
            ev_cell = ""
            if show_evidence:
                import json as _json
                ev = row["evidence"] or {}
                if isinstance(ev, str):
                    try:
                        ev = _json.loads(ev)
                    except Exception:
                        pass
                # Include rule_version in evidence view
                ev_lines = [f"rule_version={row.get('rule_version') or '—'}"]
                ev_lines += [f"{k}={v}" for k, v in ev.items()] if isinstance(ev, dict) else [str(ev)]
                ev_cell = "\n".join(ev_lines)
            title = row["market_title"] or "—"
            cells = [
                str(row["alert_id"])[:8],
                when,
                row["rule_key"],
                row.get("rule_version") or "—",
                row["severity"],
                row["confidence"],
                row.get("data_quality") or "—",
                row["venue_code"],
                row["outcome_key"] or "—",
                row.get("review_label") or "—",
                str(row["score"])[:6],
                title,
            ]
            if needs_triage:
                cells.append(",".join(row.get("triage_flags") or []) or "-")
            if show_evidence:
                cells.append(ev_cell)
            table.add_row(*cells)
        console.print(table)
    except ImportError:
        for row in rows:
            flags = ""
            if needs_triage:
                flags = f"  flags={','.join(row.get('triage_flags') or []) or '-'}"
            print(f"{str(row['alert_id'])[:8]}  {str(row['fired_at'])[5:16]}  {row['rule_key']}  {row['severity']}  {row['venue_code']}  {row['outcome_key']}{flags}")
    return 0


def cmd_alerts_serve(args: argparse.Namespace) -> int:
    """Run a local HTTP receiver for alert delivery testing."""
    port = getattr(args, "port", 8765)
    host = getattr(args, "host", "127.0.0.1")
    from pmfi.commands._shared import require_loopback_host
    try:
        host = require_loopback_host(host, label="--host")
    except ValueError as exc:
        print(f"[alerts serve] {exc}")
        return 1
    from pmfi.delivery.server import run_alert_receiver
    try:
        asyncio.run(run_alert_receiver(host=host, port=port))
    except KeyboardInterrupt:
        print("\n[alerts serve] stopped.")
    return 0


def cmd_alerts_review(args: argparse.Namespace) -> int:
    """Write a review record to the alert_reviews table."""
    from pmfi.config import load_config
    from pmfi.review_metadata import normalize_reviewed_by

    alert_id = args.alert_id
    label = args.label
    category = getattr(args, "category", None)
    notes = getattr(args, "notes", None)
    try:
        reviewed_by = normalize_reviewed_by(getattr(args, "reviewed_by", None))
    except ValueError as exc:
        print(f"[alerts review] Invalid --reviewed-by: {exc}")
        return 1
    dry_run = getattr(args, "dry_run", False)

    async def _insert():
        import asyncpg
        from pmfi.db.repos.alerts import get_alert_by_id, insert_alert_review
        cfg = load_config()
        try:
            pool = await asyncpg.create_pool(
                cfg.database.url, min_size=1, max_size=1,
                server_settings={"search_path": "pmfi,public"},
            )
        except Exception as exc:
            return str(exc)
        try:
            if dry_run:
                async with pool.acquire() as _conn:
                    row = await get_alert_by_id(_conn, alert_id)
                if not row:
                    return f"__fk__{alert_id}"
                return {"dry_run": True, "alert": row, "alert_id": row["alert_id"]}

            async with pool.acquire() as _conn:
                review = await insert_alert_review(
                    _conn,
                    alert_id,
                    label=label,
                    category=category,
                    notes=notes,
                    reviewed_by=reviewed_by,
                )
            if review is None:
                return f"__fk__{alert_id}"
            return {"review": review}
        except asyncpg.ForeignKeyViolationError:
            return f"__fk__{alert_id}"
        except Exception as exc:
            return str(exc)
        finally:
            await pool.close()

    err = asyncio.run(_insert())
    if isinstance(err, dict) and err.get("dry_run"):
        row = err.get("alert") or {}
        print("[review dry-run] no database write performed.")
        print(f"  alert_id={err.get('alert_id')}")
        print(f"  label={label}")
        if category:
            print(f"  category={category}")
        if notes:
            print(f"  notes={notes}")
        if reviewed_by:
            print(f"  reviewed_by={reviewed_by}")
        print(
            "  target="
            f"{row.get('rule_key', 'unknown')} "
            f"severity={row.get('severity', 'unknown')} "
            f"outcome={row.get('outcome_key') or '-'}"
        )
        title = row.get("market_title") or row.get("venue_market_id")
        if title:
            print(f"  market={title}")
        return 0
    if isinstance(err, dict) and err.get("review"):
        review = err["review"]
        print(f"[review] alert_id={review.get('alert_id', alert_id)} label={label} recorded.")
        return 0
    if isinstance(err, str) and err.startswith("__fk__"):
        aid = err[len("__fk__"):]
        print(f"Alert {aid} not found.")
        return 1
    print(f"DB error: {err}\nRun 'pmfi db-verify' to check connectivity.")
    return 1


def cmd_alerts_review_packet(args: argparse.Namespace) -> int:
    """Export a read-only local JSON review packet for reviewed or unreviewed cohorts."""
    from pmfi.config import load_config

    since_dt, since_err = _parse_since_window(
        getattr(args, "since", None),
        command="alerts review-packet",
    )
    if since_err:
        print(since_err)
        return 1
    limit = getattr(args, "limit", 50)
    if limit <= 0:
        print("[alerts review-packet] --limit must be a positive integer.")
        return 1
    review_label = getattr(args, "review_label", None)
    if review_label and review_label not in {"tp", "fp", "noise"}:
        print("[alerts review-packet] --review-label must be one of: tp, fp, noise.")
        return 1
    review_state = getattr(args, "review_state", "reviewed") or "reviewed"
    if review_state not in {"reviewed", "unreviewed"}:
        print("[alerts review-packet] --review-state must be one of: reviewed, unreviewed.")
        return 1
    if review_state == "unreviewed" and (review_label or getattr(args, "category", None)):
        print("[alerts review-packet] --review-state unreviewed cannot be combined with --review-label or --category.")
        return 1
    fmt = getattr(args, "format", "json")
    if fmt != "json":
        print("[alerts review-packet] only JSON output is supported.")
        return 1

    output_path, output_err = _resolve_review_packet_output(getattr(args, "output", None))
    if output_err:
        print(output_err)
        return 1
    assert output_path is not None

    async def _export():
        import asyncpg
        from pmfi.db.repos.alerts import get_review_packet

        cfg = load_config()
        try:
            pool = await asyncpg.create_pool(
                cfg.database.url, min_size=1, max_size=1,
                server_settings={"search_path": "pmfi,public"},
            )
        except Exception as exc:
            return None, str(exc)
        try:
            async with pool.acquire() as conn:
                packet = await get_review_packet(
                    conn,
                    since=since_dt,
                    rule=getattr(args, "rule", None),
                    review_state=review_state,
                    review_label=review_label,
                    category=getattr(args, "category", None),
                    limit=limit,
                )
            return packet, None
        except Exception as exc:
            return None, str(exc)
        finally:
            await pool.close()

    packet, err = asyncio.run(_export())
    if err:
        print(f"DB query failed: {err}\nRun 'pmfi db-verify' to check connectivity.")
        return 1
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(packet, indent=2, default=_json_serial) + "\n",
        encoding="utf-8",
    )
    totals = (packet or {}).get("cohort_totals") or (packet or {}).get("reviewed_cohort_totals") or {}
    print(
        f"[review-packet] wrote {output_path} "
        f"alerts={totals.get('alerts', 0)}"
    )
    return 0


def cmd_alerts_lineage_check(args: argparse.Namespace) -> int:
    """Read-only check for alerts with dangling raw/trade lineage references."""
    from pmfi.config import load_config

    raw_since = getattr(args, "since", None)
    if raw_since:
        since_dt, since_err = _parse_since_window(
            raw_since,
            command="alerts lineage-check",
        )
        if since_err:
            print(since_err)
            return 1
    else:
        since_dt = None

    limit = int(getattr(args, "limit", 50) or 50)
    if limit <= 0:
        print("[alerts lineage-check] --limit must be a positive integer.")
        return 1

    fmt = getattr(args, "format", "table")
    strict = bool(getattr(args, "strict", False))

    async def _check():
        import asyncpg
        from pmfi.db.repos.alerts import get_alert_lineage_integrity

        cfg = load_config()
        try:
            pool = await asyncpg.create_pool(
                cfg.database.url,
                min_size=1,
                max_size=1,
                server_settings={"search_path": "pmfi,public"},
            )
        except Exception as exc:
            return None, str(exc)
        try:
            async with pool.acquire() as conn:
                check = await get_alert_lineage_integrity(
                    conn,
                    since=since_dt,
                    limit=limit,
                )
            return check, None
        except Exception as exc:
            return None, str(exc)
        finally:
            await pool.close()

    check, err = asyncio.run(_check())
    if err:
        print(f"DB query failed: {err}\nRun 'pmfi db-verify' to check connectivity.")
        return 1
    assert check is not None

    if fmt == "json":
        print(json.dumps(check, indent=2, default=_json_serial))
    else:
        totals = check["totals"]
        print(
            "[lineage-check] "
            f"alerts_with_lineage={totals['alerts_with_lineage']} "
            f"alerts_with_orphans={totals['alerts_with_orphans']} "
            f"raw_event_orphans={totals['raw_event_orphans']} "
            f"trade_orphans={totals['trade_orphans']}"
        )
        rows = check.get("rows") or []
        if rows:
            for row in rows:
                missing = []
                if row.get("raw_event_missing"):
                    missing.append(f"raw_event_id={row.get('raw_event_id')}")
                if row.get("trade_missing"):
                    missing.append(f"trade_id={row.get('trade_id')}")
                print(
                    f"  {row['short_id']} {row.get('rule_key')} {row.get('venue_code')} "
                    f"missing={', '.join(missing)}"
                )
        else:
            print("  No dangling alert lineage references found.")

    return 1 if strict and not check["ok"] else 0


def _parse_decimal_option(
    raw: object,
    *,
    name: str,
    allow_zero: bool,
    command: str = "alerts volume-spike-calibration",
) -> tuple[Decimal | None, str | None]:
    if raw is None:
        return None, None
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError):
        return None, f"[{command}] {name} must be numeric."
    if allow_zero:
        if value < 0:
            return None, f"[{command}] {name} must be >= 0."
    elif value <= 0:
        return None, f"[{command}] {name} must be > 0."
    return value, None


def _first_present(*values: object) -> object:
    for value in values:
        if value is not None:
            return value
    return None


def _volume_spike_min_trade_usd(rules_config: dict[str, Any]) -> tuple[Decimal | None, str | None]:
    from pmfi.calibration import VOLUME_SPIKE_RULE

    spike_config = ((rules_config.get("rules") or {}).get(VOLUME_SPIKE_RULE) or {})
    floor, err = _parse_decimal_option(
        spike_config.get("min_trade_usd"),
        name=f"{VOLUME_SPIKE_RULE}.min_trade_usd",
        allow_zero=True,
        command="volume-spike-floor-audit",
    )
    if err:
        return None, err
    if floor is None:
        return None, (
            f"[volume-spike-floor-audit] config\\alert_rules.yaml missing "
            f"{VOLUME_SPIKE_RULE}.min_trade_usd."
        )
    return floor, None


def cmd_alerts_volume_spike_calibration(args: argparse.Namespace) -> int:
    """Compare current vs candidate volume_spike_v1 rules through read-only DB replay."""
    import yaml
    from pmfi.calibration import VolumeSpikeCandidate
    from pmfi.commands._shared import ROOT
    from pmfi.config import load_config
    from pmfi.volume_spike_calibration import (
        build_volume_spike_calibration_packet,
        insufficient_volume_spike_evidence_reason,
        run_volume_spike_calibration_replay,
    )

    raw_since = _first_present(getattr(args, "since", None), getattr(args, "calibration_from", None))
    raw_until = _first_present(getattr(args, "until", None), getattr(args, "calibration_to", None))
    since_dt, since_err = _parse_since_window(
        raw_since,
        command="alerts volume-spike-calibration",
    )
    if since_err:
        print(since_err)
        return 1
    if raw_until is None:
        until_dt, until_err = None, None
    else:
        until_dt, until_err = _parse_since_window(
            str(raw_until),
            command="alerts volume-spike-calibration",
        )
    if until_err:
        print(until_err)
        return 1
    assert since_dt is not None
    if until_dt is not None and since_dt >= until_dt:
        print("[alerts volume-spike-calibration] --since must be before --until.")
        return 1

    limit = int(getattr(args, "limit", 0))
    if limit < 0:
        print("[alerts volume-spike-calibration] --limit must be >= 0.")
        return 1

    min_trade, err = _parse_decimal_option(
        _first_present(getattr(args, "candidate_min_trade_usd", None), getattr(args, "min_trade_usd", None)),
        name="--candidate-min-trade-usd",
        allow_zero=True,
    )
    if err:
        print(err)
        return 1
    min_multiplier, err = _parse_decimal_option(
        _first_present(
            getattr(args, "candidate_min_spike_multiplier", None),
            getattr(args, "min_spike_multiplier", None),
        ),
        name="--candidate-min-spike-multiplier",
        allow_zero=False,
    )
    if err:
        print(err)
        return 1
    min_baseline_raw = (
        getattr(args, "candidate_min_baseline_trades", None)
        if getattr(args, "candidate_min_baseline_trades", None) is not None
        else getattr(args, "min_baseline_trades", None)
    )
    min_baseline = int(min_baseline_raw) if min_baseline_raw is not None else None
    if min_baseline is not None and min_baseline <= 0:
        print("[alerts volume-spike-calibration] --candidate-min-baseline-trades must be > 0.")
        return 1
    low_notional_min_baseline_raw = getattr(args, "low_notional_min_baseline_trades", None)
    low_notional_min_baseline = (
        int(low_notional_min_baseline_raw)
        if low_notional_min_baseline_raw is not None
        else None
    )
    if low_notional_min_baseline is not None and low_notional_min_baseline <= 0:
        print("[alerts volume-spike-calibration] --low-notional-min-baseline-trades must be > 0.")
        return 1
    low_notional_min_median, err = _parse_decimal_option(
        getattr(args, "low_notional_min_baseline_median_usd", None),
        name="--low-notional-min-baseline-median-usd",
        allow_zero=False,
    )
    if err:
        print(err)
        return 1
    low_notional_max_multiplier, err = _parse_decimal_option(
        getattr(args, "low_notional_max_spike_multiplier", None),
        name="--low-notional-max-spike-multiplier",
        allow_zero=False,
    )
    if err:
        print(err)
        return 1
    low_notional_threshold, err = _parse_decimal_option(
        getattr(args, "low_notional_threshold_usd", None),
        name="--low-notional-threshold-usd",
        allow_zero=False,
    )
    if err:
        print(err)
        return 1
    history_max_raw = getattr(args, "history_max", None)
    history_max = int(history_max_raw) if history_max_raw is not None else None
    if history_max is not None and history_max <= 0:
        print("[alerts volume-spike-calibration] --history-max must be > 0.")
        return 1
    packet_limit = int(getattr(args, "packet_limit", 0) or 0)
    if packet_limit < 0:
        print("[alerts volume-spike-calibration] --packet-limit must be >= 0.")
        return 1

    if (
        min_trade is None
        and min_multiplier is None
        and min_baseline is None
        and low_notional_min_baseline is None
        and low_notional_min_median is None
        and low_notional_max_multiplier is None
        and low_notional_threshold is None
        and history_max is None
    ):
        print("[alerts volume-spike-calibration] provide at least one candidate volume_spike_v1 knob.")
        return 1

    candidate = VolumeSpikeCandidate(
        min_trade_usd=min_trade,
        min_spike_multiplier=min_multiplier,
        min_baseline_trades=min_baseline,
        low_notional_min_baseline_trades=low_notional_min_baseline,
        low_notional_min_baseline_median_usd=low_notional_min_median,
        low_notional_max_spike_multiplier=low_notional_max_multiplier,
        low_notional_threshold_usd=low_notional_threshold,
        history_max=history_max,
    )
    base_rules = yaml.safe_load((ROOT / "config" / "alert_rules.yaml").read_text(encoding="utf-8")) or {}
    fmt = getattr(args, "format", "table")
    if fmt == "text":
        fmt = "table"
    venue = _first_present(getattr(args, "venue", None), getattr(args, "calibration_venue", None))
    market = _first_present(getattr(args, "market", None), getattr(args, "calibration_market", None))
    export_packet = bool(
        getattr(args, "export_packet", False)
        or getattr(args, "packet_output", None)
    )
    packet_output_path: Path | None = None
    if export_packet:
        packet_output_path, output_err = _resolve_volume_spike_calibration_packet_output(
            getattr(args, "packet_output", None)
        )
        if output_err:
            print(output_err)
            return 1
        assert packet_output_path is not None

    async def _compare():
        import asyncpg

        cfg = load_config()
        try:
            pool = await asyncpg.create_pool(
                cfg.database.url,
                min_size=1,
                max_size=1,
                server_settings={"search_path": "pmfi,public"},
            )
        except Exception as exc:
            return None, str(exc)
        try:
            return (
                await run_volume_spike_calibration_replay(
                    pool,
                    base_rules_config=base_rules,
                    since_dt=since_dt,
                    until_dt=until_dt,
                    limit=limit,
                    venue=venue,
                    market=market,
                    candidate=candidate,
                    cold_start=bool(getattr(args, "cold_start", False)),
                    delta_records_limit=packet_limit if export_packet else None,
                ),
                None,
            )
        except Exception as exc:
            return None, str(exc)
        finally:
            await pool.close()

    summary, db_err = asyncio.run(_compare())
    if db_err:
        print(f"DB query failed: {db_err}\nRun 'pmfi db-verify' to check connectivity.")
        return 1
    assert summary is not None
    evidence_reason = insufficient_volume_spike_evidence_reason(summary)
    if evidence_reason == "no normalized trades":
        print("[alerts volume-spike-calibration] no normalized trades in replay window; widen --since/--until or ingest first.")
        return 1
    if evidence_reason == "no current volume_spike_v1 alerts":
        print("[alerts volume-spike-calibration] no current volume_spike_v1 alerts in replay window; insufficient spike evidence.")
        return 1

    packet_message = None
    if packet_output_path is not None:
        summary["packet_output"] = str(packet_output_path)
        packet = build_volume_spike_calibration_packet(summary)
        packet_output_path.parent.mkdir(parents=True, exist_ok=True)
        packet_output_path.write_text(
            json.dumps(packet, indent=2, default=_json_serial) + "\n",
            encoding="utf-8",
        )
        counts = packet["export_metadata"]["record_counts"]
        packet_message = (
            f"[volume-spike-calibration-packet] wrote {packet_output_path} "
            f"removed_records={counts['removed_volume_spike_records']} "
            f"added_records={counts['added_volume_spike_records']}"
        )

    if fmt == "json":
        print(json.dumps(summary, indent=2, default=str))
        return 0

    current = summary["current"]
    proposed = summary["candidate_replay"]
    comparison = summary["comparison"]
    print("[volume-spike-calibration] validate-only local DB replay comparison")
    print(f"  window: since={summary['filters']['since']} until={summary['filters']['until'] or 'now'} limit={limit}")
    print(
        "  current: "
        f"trades={current['normalized_trades']} alerts={current['alerts']} "
        f"volume_spike={current['volume_spike_alerts']}"
    )
    print(
        "  candidate: "
        f"alerts={proposed['alerts']} volume_spike={proposed['volume_spike_alerts']}"
    )
    print(
        "  delta: "
        f"alerts={comparison['alerts_delta']} "
        f"volume_spike={comparison['volume_spike_delta']} "
        f"removed_low_notional_thin_baseline={comparison['removed_low_notional_thin_baseline']}"
    )
    print(
        "  removed_trade_usd_buckets: "
        f"{json.dumps(comparison['removed_trade_usd_buckets'], sort_keys=True)}"
    )
    print(
        "  review_matches: "
        f"removed={comparison.get('removed_review_matches', 0)} "
        f"added={comparison.get('added_review_matches', 0)}"
    )
    if packet_message is not None:
        print(f"  packet: {packet_message}")
    print("  no DB writes, no config changes")
    return 0


def cmd_volume_spike_calibration_sweep(args: argparse.Namespace) -> int:
    """Run validate-only volume-spike calibration candidates over explicit windows."""
    import yaml
    from pmfi.calibration import VolumeSpikeCandidate
    from pmfi.commands._shared import ROOT
    from pmfi.config import load_config
    from pmfi.volume_spike_calibration import (
        insufficient_volume_spike_evidence_reason,
        run_volume_spike_calibration_replay,
    )

    raw_windows = list(getattr(args, "window", None) or [])
    if not raw_windows:
        print("[volume-spike-calibration-sweep] at least one --window is required.")
        return 1

    parsed_windows: list[dict[str, str]] = []
    for raw_window in raw_windows:
        parsed, parse_err = _parse_calibration_sweep_window(raw_window)
        if parse_err:
            print(parse_err)
            return 1
        assert parsed is not None
        parsed_windows.append(parsed)

    limit = int(getattr(args, "limit", 0) or 0)
    if limit < 0:
        print("[volume-spike-calibration-sweep] --limit must be >= 0.")
        return 1

    def _option_list(raw: object) -> list[object]:
        if raw is None:
            return []
        if isinstance(raw, (list, tuple)):
            return list(raw)
        return [raw]

    baseline_values = _option_list(
        getattr(args, "low_notional_min_baseline_trades", None)
    )

    baselines: list[int | None] = []
    for raw_baseline in baseline_values:
        try:
            baseline = int(raw_baseline)
        except (TypeError, ValueError):
            print("[volume-spike-calibration-sweep] --low-notional-min-baseline-trades must be numeric.")
            return 1
        if baseline <= 0:
            print("[volume-spike-calibration-sweep] --low-notional-min-baseline-trades must be > 0.")
            return 1
        baselines.append(baseline)

    raw_thresholds = _option_list(getattr(args, "low_notional_threshold_usd", None))
    thresholds: list[Decimal | None] = []
    if raw_thresholds:
        for raw_threshold in raw_thresholds:
            threshold, threshold_err = _parse_decimal_option(
                raw_threshold,
                name="--low-notional-threshold-usd",
                allow_zero=False,
                command="volume-spike-calibration-sweep",
            )
            if threshold_err:
                print(threshold_err)
                return 1
            thresholds.append(threshold)
    else:
        thresholds.append(None)

    raw_medians = _option_list(
        getattr(args, "low_notional_min_baseline_median_usd", None)
    )
    min_medians: list[Decimal | None] = []
    if raw_medians:
        for raw_median in raw_medians:
            median, median_err = _parse_decimal_option(
                raw_median,
                name="--low-notional-min-baseline-median-usd",
                allow_zero=False,
                command="volume-spike-calibration-sweep",
            )
            if median_err:
                print(median_err)
                return 1
            min_medians.append(median)

    raw_max_multipliers = _option_list(
        getattr(args, "low_notional_max_spike_multiplier", None)
    )
    max_multipliers: list[Decimal | None] = []
    if raw_max_multipliers:
        for raw_multiplier in raw_max_multipliers:
            multiplier, multiplier_err = _parse_decimal_option(
                raw_multiplier,
                name="--low-notional-max-spike-multiplier",
                allow_zero=False,
                command="volume-spike-calibration-sweep",
            )
            if multiplier_err:
                print(multiplier_err)
                return 1
            max_multipliers.append(multiplier)
    else:
        max_multipliers.append(None)

    if not baselines and not min_medians:
        print(
            "[volume-spike-calibration-sweep] provide at least one "
            "--low-notional-min-baseline-trades or "
            "--low-notional-min-baseline-median-usd candidate."
        )
        return 1
    if not baselines:
        baselines.append(None)
    if not min_medians:
        min_medians.append(None)

    candidates: list[dict[str, Any]] = []
    seen_labels: set[str] = set()
    for baseline in baselines:
        for threshold in thresholds:
            for min_median in min_medians:
                for max_multiplier in max_multipliers:
                    baseline_label = str(baseline) if baseline is not None else "default"
                    label = (
                        f"baseline-{baseline_label}"
                        f"-threshold-{_decimal_label(threshold)}"
                        f"-median-{_decimal_label(min_median)}"
                        f"-maxmult-{_decimal_label(max_multiplier)}"
                    )
                    if label in seen_labels:
                        print(f"[volume-spike-calibration-sweep] duplicate candidate label: {label}")
                        return 1
                    seen_labels.add(label)
                    candidates.append({
                        "label": label,
                        "low_notional_min_baseline_trades": baseline,
                        "low_notional_threshold_usd": (
                            float(threshold) if threshold is not None else None
                        ),
                        "low_notional_min_baseline_median_usd": (
                            float(min_median) if min_median is not None else None
                        ),
                        "low_notional_max_spike_multiplier": (
                            float(max_multiplier) if max_multiplier is not None else None
                        ),
                        "candidate": VolumeSpikeCandidate(
                            low_notional_min_baseline_trades=baseline,
                            low_notional_threshold_usd=threshold,
                            low_notional_min_baseline_median_usd=min_median,
                            low_notional_max_spike_multiplier=max_multiplier,
                        ),
                    })

    venue = _first_present(getattr(args, "venue", None), getattr(args, "calibration_venue", None))
    market = _first_present(getattr(args, "market", None), getattr(args, "calibration_market", None))
    cold_start = bool(getattr(args, "cold_start", False))
    fmt = getattr(args, "format", "text")
    base_rules = yaml.safe_load((ROOT / "config" / "alert_rules.yaml").read_text(encoding="utf-8")) or {}

    async def _sweep():
        import asyncpg

        cfg = load_config()
        try:
            pool = await asyncpg.create_pool(
                cfg.database.url,
                min_size=1,
                max_size=1,
                server_settings={"search_path": "pmfi,public"},
            )
        except Exception as exc:
            return None, str(exc)
        try:
            rows: list[dict[str, Any]] = []
            aggregate: dict[str, dict[str, Any]] = {}
            for window in parsed_windows:
                since_dt, since_err = _parse_explicit_calibration_window_ts(
                    window["since"],
                    label="SINCE",
                )
                until_dt, until_err = _parse_explicit_calibration_window_ts(
                    window["until"],
                    label="UNTIL",
                )
                if since_err or until_err or since_dt is None or until_dt is None:
                    raise ValueError("validated window failed timestamp reparse")
                for candidate_info in candidates:
                    summary = await run_volume_spike_calibration_replay(
                        pool,
                        base_rules_config=base_rules,
                        since_dt=since_dt,
                        until_dt=until_dt,
                        limit=limit,
                        venue=venue,
                        market=market,
                        candidate=candidate_info["candidate"],
                        cold_start=cold_start,
                    )
                    current = summary.get("current") or {}
                    candidate_replay = summary.get("candidate_replay") or {}
                    comparison = summary.get("comparison") or {}
                    labels = comparison.get("removed_review_labels") or {}
                    removed_trade_buckets = _int_count_map(
                        comparison.get("removed_trade_usd_buckets")
                    )
                    added_trade_buckets = _int_count_map(
                        comparison.get("added_trade_usd_buckets")
                    )
                    removed = int(comparison.get("removed_volume_spike_alerts") or 0)
                    added = int(comparison.get("added_volume_spike_alerts") or 0)
                    removed_low_thin = int(
                        comparison.get("removed_low_notional_thin_baseline") or 0
                    )
                    added_low_thin = int(
                        comparison.get("added_low_notional_thin_baseline") or 0
                    )
                    removed_shape_profile = _volume_spike_shape_profile(
                        comparison.get("removed_shape_profile"),
                        total=removed,
                        trade_buckets=removed_trade_buckets,
                        low_notional_thin_baseline_count=removed_low_thin,
                    )
                    added_shape_profile = _volume_spike_shape_profile(
                        comparison.get("added_shape_profile"),
                        total=added,
                        trade_buckets=added_trade_buckets,
                        low_notional_thin_baseline_count=added_low_thin,
                    )
                    row = {
                        "window_name": window["name"],
                        "window_since": window["since"],
                        "window_until": window["until"],
                        "candidate_label": candidate_info["label"],
                        "candidate_config": candidate_info["candidate"].as_dict(),
                        "current_spikes": int(current.get("volume_spike_alerts") or 0),
                        "candidate_spikes": int(candidate_replay.get("volume_spike_alerts") or 0),
                        "removed": removed,
                        "added": added,
                        "removed_low_notional_thin_baseline": removed_low_thin,
                        "added_low_notional_thin_baseline": added_low_thin,
                        "removed_trade_usd_buckets": removed_trade_buckets,
                        "added_trade_usd_buckets": added_trade_buckets,
                        "removed_shape_profile": removed_shape_profile,
                        "added_shape_profile": added_shape_profile,
                        "removed_review_matches": int(comparison.get("removed_review_matches") or 0),
                        "removed_review_unmatched": int(comparison.get("removed_review_unmatched") or 0),
                        "removed_review_labels": dict(labels),
                        "removed_review_categories": dict(
                            comparison.get("removed_review_categories") or {}
                        ),
                        "added_review_matches": int(comparison.get("added_review_matches") or 0),
                        "added_review_labels": dict(comparison.get("added_review_labels") or {}),
                        "evidence_reason": insufficient_volume_spike_evidence_reason(summary),
                    }
                    rows.append(row)

                    aggregate_row = aggregate.setdefault(
                        candidate_info["label"],
                        {
                            "windows": 0,
                            "current_spikes": 0,
                            "candidate_spikes": 0,
                            "removed": 0,
                            "added": 0,
                            "removed_reviewed_noise_or_fp": 0,
                            "removed_reviewed_tp": 0,
                            "removed_review_unmatched": 0,
                            "removed_trade_usd_buckets": {},
                            "added_trade_usd_buckets": {},
                            "removed_shape_profile": _empty_volume_spike_shape_profile(),
                            "added_shape_profile": _empty_volume_spike_shape_profile(),
                            "recommendation": "inspect-required",
                        },
                    )
                    aggregate_row["windows"] = int(aggregate_row["windows"]) + 1
                    aggregate_row["current_spikes"] = int(aggregate_row["current_spikes"]) + row["current_spikes"]
                    aggregate_row["candidate_spikes"] = int(aggregate_row["candidate_spikes"]) + row["candidate_spikes"]
                    aggregate_row["removed"] = int(aggregate_row["removed"]) + removed
                    aggregate_row["added"] = int(aggregate_row["added"]) + added
                    aggregate_row["removed_reviewed_noise_or_fp"] = (
                        int(aggregate_row["removed_reviewed_noise_or_fp"])
                        + int(labels.get("noise") or 0)
                        + int(labels.get("fp") or 0)
                    )
                    aggregate_row["removed_reviewed_tp"] = (
                        int(aggregate_row["removed_reviewed_tp"]) + int(labels.get("tp") or 0)
                    )
                    aggregate_row["removed_review_unmatched"] = (
                        int(aggregate_row["removed_review_unmatched"])
                        + row["removed_review_unmatched"]
                    )
                    _merge_int_count_map(
                        aggregate_row["removed_trade_usd_buckets"],
                        removed_trade_buckets,
                    )
                    _merge_int_count_map(
                        aggregate_row["added_trade_usd_buckets"],
                        added_trade_buckets,
                    )
                    _merge_volume_spike_shape_profile(
                        aggregate_row["removed_shape_profile"],
                        removed_shape_profile,
                    )
                    _merge_volume_spike_shape_profile(
                        aggregate_row["added_shape_profile"],
                        added_shape_profile,
                    )

            for aggregate_row in aggregate.values():
                typed_row = {
                    "removed_reviewed_tp": int(aggregate_row["removed_reviewed_tp"]),
                    "removed_reviewed_noise_or_fp": int(
                        aggregate_row["removed_reviewed_noise_or_fp"]
                    ),
                    "removed_review_unmatched": int(
                        aggregate_row["removed_review_unmatched"]
                    ),
                    "removed": int(aggregate_row["removed"]),
                    "added": int(aggregate_row["added"]),
                }
                aggregate_row["recommendation"] = _volume_spike_sweep_recommendation(typed_row)
            return (rows, aggregate), None
        except Exception as exc:
            return None, str(exc)
        finally:
            await pool.close()

    result, db_err = asyncio.run(_sweep())
    if db_err:
        print(f"DB query failed: {db_err}\nRun 'pmfi db-verify' to check connectivity.")
        return 1
    assert result is not None
    rows, aggregate = result
    payload = {
        "schema_version": "volume_spike_calibration_sweep.v1",
        "local_only": True,
        "validate_only": True,
        "config_mutation": False,
        "db_mutation": False,
        "live_calls": False,
        "filters": {
            "limit": limit,
            "venue": venue,
            "market": market,
            "cold_start": cold_start,
        },
        "candidates": [
            {
                "label": item["label"],
                "low_notional_min_baseline_trades": item[
                    "low_notional_min_baseline_trades"
                ],
                "low_notional_threshold_usd": item["low_notional_threshold_usd"],
                "low_notional_min_baseline_median_usd": item[
                    "low_notional_min_baseline_median_usd"
                ],
                "low_notional_max_spike_multiplier": item[
                    "low_notional_max_spike_multiplier"
                ],
            }
            for item in candidates
        ],
        "rows": rows,
        "aggregate": aggregate,
    }
    if fmt == "json":
        print(json.dumps(payload, indent=2, default=_json_serial))
        return 0

    print("[volume-spike-calibration-sweep] validate-only local DB replay sweep")
    print(
        f"  windows={len(parsed_windows)} candidates={len(candidates)} "
        f"rows={len(rows)} venue={venue or '-'} market={market or '-'}"
    )
    for row in rows:
        print(
            f"  {row['window_name']} {row['candidate_label']}: "
            f"current={row['current_spikes']} candidate={row['candidate_spikes']} "
            f"removed={row['removed']} added={row['added']} "
            f"removed_buckets={json.dumps(row['removed_trade_usd_buckets'], sort_keys=True)} "
            f"removed_spike_buckets={json.dumps(row['removed_shape_profile']['spike_multiplier_buckets'], sort_keys=True)} "
            f"evidence={row['evidence_reason'] or 'ok'}"
        )
    print("  aggregate:")
    for label, row in aggregate.items():
        print(
            f"    {label}: windows={row['windows']} removed={row['removed']} "
            f"added={row['added']} "
            f"removed_buckets={json.dumps(row['removed_trade_usd_buckets'], sort_keys=True)} "
            f"removed_spike_buckets={json.dumps(row['removed_shape_profile']['spike_multiplier_buckets'], sort_keys=True)} "
            f"recommendation={row['recommendation']}"
        )
    print("  local_only=true validate_only=true config_mutation=false db_mutation=false live_calls=false")
    return 0


def cmd_calibration_packet_batch(args: argparse.Namespace) -> int:
    """Export volume-spike calibration packets for explicit independent windows."""
    import contextlib
    import io

    prefix, prefix_err = _safe_calibration_packet_slug(
        getattr(args, "packet_output_prefix", None) or "independent",
        label="--packet-output-prefix",
    )
    if prefix_err:
        print(prefix_err)
        return 1
    assert prefix is not None

    parsed_windows: list[dict[str, str]] = []
    output_names: list[str] = []
    resolved_outputs: list[Path] = []
    seen_outputs: set[Path] = set()
    for raw_window in getattr(args, "window", None) or []:
        parsed, parse_err = _parse_calibration_packet_batch_window(raw_window)
        if parse_err:
            print(parse_err)
            return 1
        assert parsed is not None
        output_name = f"{prefix}-{parsed['name']}.json"
        output_path, output_err = _resolve_volume_spike_calibration_packet_output(
            output_name
        )
        if output_err:
            print(output_err)
            return 1
        assert output_path is not None
        if output_path in seen_outputs:
            print(
                "[calibration-packet-batch] duplicate packet output: "
                f"{output_path}"
            )
            return 1
        seen_outputs.add(output_path)
        parsed_windows.append(parsed)
        output_names.append(output_name)
        resolved_outputs.append(output_path)

    if not parsed_windows:
        print("[calibration-packet-batch] at least one --window is required.")
        return 1

    fmt = getattr(args, "format", "text")
    results: list[dict[str, Any]] = []
    for parsed, output_name, output_path in zip(
        parsed_windows,
        output_names,
        resolved_outputs,
    ):
        child_values = vars(args).copy()
        child_values.update({
            "calibration_from": parsed["since"],
            "calibration_to": parsed["until"],
            "since": None,
            "until": None,
            "export_packet": True,
            "packet_output": output_name,
            "format": "text",
        })
        child_args = argparse.Namespace(**child_values)

        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            rc = cmd_alerts_volume_spike_calibration(child_args)
        child_output = captured.getvalue()
        if fmt != "json" and child_output:
            print(child_output, end="")

        row = {
            "name": parsed["name"],
            "since": parsed["since"],
            "until": parsed["until"],
            "packet_output": str(output_path),
            "exit_code": rc,
        }
        results.append(row)
        if rc != 0:
            if fmt == "json" and child_output:
                print(child_output, end="")
            print(
                "[calibration-packet-batch] window failed: "
                f"{parsed['name']} exit_code={rc}"
            )
            return rc or 1

    payload = {
        "schema_version": "calibration_packet_batch.v1",
        "local_only": True,
        "validate_only": True,
        "config_mutation": False,
        "db_mutation": False,
        "live_calls": False,
        "packet_output_prefix": prefix,
        "windows": results,
    }
    if fmt == "json":
        print(json.dumps(payload, indent=2, default=_json_serial))
        return 0

    print(
        f"[calibration-packet-batch] exported {len(results)} packet(s) "
        f"prefix={prefix}"
    )
    for row in results:
        print(
            f"  {row['name']}: since={row['since']} until={row['until']} "
            f"packet={row['packet_output']}"
        )
    print("  local_only=true validate_only=true config_mutation=false db_mutation=false")
    return 0


def cmd_calibration_decision(args: argparse.Namespace) -> int:
    """Write a local decision handoff from calibration packet comparison evidence."""
    from pmfi.calibration_decisions import build_calibration_decision_record
    from pmfi.calibration_cluster_reviews import (
        calibration_cluster_review_coverage,
        list_calibration_cluster_review_files,
        load_calibration_cluster_review,
    )
    from pmfi.calibration_packets import (
        calibration_packet_comparison,
        calibration_packet_review_summary,
        list_calibration_packet_files,
        load_calibration_packet,
    )

    decision = str(getattr(args, "decision", "") or "").strip()
    rationale = str(getattr(args, "rationale", "") or "").strip()
    if not rationale:
        print("[calibration-decision] --rationale is required and must not be empty.")
        return 1

    output_path, output_err = _resolve_calibration_decision_output(
        getattr(args, "output", None)
    )
    if output_err:
        print(output_err)
        return 1
    assert output_path is not None

    selected_names = list(getattr(args, "packet", None) or [])
    if not selected_names:
        selected_names = [packet["name"] for packet in list_calibration_packet_files()]
    if not selected_names:
        print("[calibration-decision] no calibration packet JSON files found.")
        return 1

    named_packets: list[tuple[str, dict[str, Any]]] = []
    for name in selected_names:
        try:
            named_packets.append((name, load_calibration_packet(name)))
        except ValueError:
            print(f"[calibration-decision] invalid packet name: {name}")
            return 1
        except FileNotFoundError:
            print(f"[calibration-decision] packet not found: {name}")
            return 1
        except json.JSONDecodeError:
            print(f"[calibration-decision] invalid packet JSON: {name}")
            return 1
        except TypeError as exc:
            print(f"[calibration-decision] invalid packet: {name}: {exc}")
            return 1

    comparison = calibration_packet_comparison(named_packets)
    review_summary = (
        calibration_packet_review_summary(named_packets)
        if getattr(args, "include_review_summary", False)
        else None
    )
    cluster_review_coverage = None
    if getattr(args, "include_cluster_review_summary", False):
        selected_reviews = list(getattr(args, "review", None) or [])
        if not selected_reviews:
            selected_reviews = [
                review["name"] for review in list_calibration_cluster_review_files()
            ]
        review_records: list[tuple[str, dict[str, Any]]] = []
        for name in selected_reviews:
            try:
                review_records.append((name, load_calibration_cluster_review(name)))
            except ValueError:
                print(f"[calibration-decision] invalid cluster review name: {name}")
                return 1
            except FileNotFoundError:
                print(f"[calibration-decision] cluster review not found: {name}")
                return 1
            except json.JSONDecodeError:
                print(f"[calibration-decision] invalid cluster review JSON: {name}")
                return 1
            except TypeError as exc:
                print(f"[calibration-decision] invalid cluster review: {name}: {exc}")
                return 1
        cluster_review_coverage = calibration_cluster_review_coverage(
            named_packets,
            review_records,
            state="removed",
            review_group="unmatched_replay_only",
        )
    record = build_calibration_decision_record(
        comparison=comparison,
        selected_packet_names=selected_names,
        decision=decision,
        rationale=rationale,
        review_summary=review_summary,
        cluster_review_coverage=cluster_review_coverage,
        generated_at=datetime.now(timezone.utc),
        output_artifact_path=str(output_path),
        output_artifact_name=output_path.name,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(record, indent=2, default=_json_serial) + "\n",
        encoding="utf-8",
    )

    fmt = getattr(args, "format", "text")
    if fmt == "json":
        print(json.dumps(record, indent=2, default=_json_serial))
        return 0

    aggregate = comparison.get("aggregate") or {}
    print(
        f"[calibration-decision] wrote {output_path} "
        f"decision={decision} packets={comparison.get('packet_count', 0)} "
        f"removed_records={aggregate.get('removed_records', 0)} "
        f"added_records={aggregate.get('added_records', 0)}"
    )
    if review_summary is not None:
        print(
            "  review_summary: "
            f"recommendation={review_summary.get('recommendation')} "
            f"schema={review_summary.get('schema_version')}"
        )
    if cluster_review_coverage is not None:
        totals = cluster_review_coverage.get("totals") or {}
        print(
            "  cluster_review_coverage: "
            f"covered={totals.get('covered_market_cluster_count', 0)} "
            f"uncovered={totals.get('uncovered_market_cluster_count', 0)} "
            f"clusters={totals.get('market_cluster_count', 0)}"
        )
    print("  local_only=true validate_only=true config_mutation=false db_mutation=false")
    return 0


def _queue_group_text(groups: dict[str, Any]) -> str:
    parts: list[str] = []
    for state in ("removed", "added"):
        counts = groups.get(state) or {}
        active = [
            f"{name}={count}"
            for name, count in sorted(counts.items())
            if int(count or 0) > 0
        ]
        parts.append(f"{state}: " + (", ".join(active) if active else "none"))
    return " | ".join(parts)


def _flat_count_text(counts: dict[str, Any]) -> str:
    active = [
        f"{name}={count}"
        for name, count in sorted((counts or {}).items())
        if int(count or 0) > 0
    ]
    return ", ".join(active) if active else "none"


def _queue_cluster_range(cluster: dict[str, Any], key: str) -> str:
    minimum = cluster.get(f"{key}_min")
    maximum = cluster.get(f"{key}_max")
    if minimum is None and maximum is None:
        return "-"
    if minimum == maximum:
        return str(minimum)
    return f"{minimum}-{maximum}"


def cmd_calibration_review_queue(args: argparse.Namespace) -> int:
    """Print a read-only operator queue from local calibration packet deltas."""
    from pmfi.calibration_packets import (
        calibration_packet_review_queue,
        list_calibration_packet_files,
        load_calibration_packet,
    )

    selected_names = list(getattr(args, "packet", None) or [])
    if not selected_names:
        selected_names = [packet["name"] for packet in list_calibration_packet_files()]
    if not selected_names:
        print("[calibration-review-queue] no calibration packet JSON files found.")
        return 1

    named_packets: list[tuple[str, dict[str, Any]]] = []
    for name in selected_names:
        try:
            named_packets.append((name, load_calibration_packet(name)))
        except ValueError:
            print(f"[calibration-review-queue] invalid packet name: {name}")
            return 1
        except FileNotFoundError:
            print(f"[calibration-review-queue] packet not found: {name}")
            return 1
        except json.JSONDecodeError:
            print(f"[calibration-review-queue] invalid packet JSON: {name}")
            return 1
        except TypeError as exc:
            print(f"[calibration-review-queue] invalid packet: {name}: {exc}")
            return 1

    try:
        queue = calibration_packet_review_queue(
            named_packets,
            state=getattr(args, "state", "all"),
            review_group=getattr(args, "review_group", "all"),
            market_cluster=getattr(args, "market_cluster", None),
            limit=getattr(args, "limit", 0),
        )
    except ValueError as exc:
        print(f"[calibration-review-queue] {exc}")
        return 1

    if getattr(args, "format", "text") == "json":
        print(json.dumps(queue, indent=2, default=_json_serial))
        return 0

    totals = queue.get("totals") or {}
    print("[calibration-review-queue] local-only validate-only packet review queue")
    print(
        f"  packets={queue.get('packet_count', 0)} "
        f"candidate_groups={queue.get('candidate_groups', 0)} "
        f"available_rows={totals.get('available_rows', 0)} "
        f"filtered_rows={totals.get('filtered_rows', 0)} "
        f"returned_rows={totals.get('returned_rows', 0)} "
        f"truncated={str(bool(totals.get('truncated'))).lower()}"
    )
    filters = queue.get("filters") or {}
    print(
        "  filters: "
        f"state={filters.get('state')} "
        f"review_group={filters.get('review_group')} "
        f"market_cluster={filters.get('market_cluster') or '-'} "
        f"limit={filters.get('limit')}"
    )
    print(f"  groups: {_queue_group_text(queue.get('groups') or {})}")
    clusters = queue.get("market_clusters") or []
    if clusters:
        print("  market clusters:")
        for cluster in clusters[:5]:
            print(
                "   - "
                f"{cluster.get('market_key')}: "
                f"rows={cluster.get('row_count', 0)} "
                f"packets={','.join(cluster.get('packet_names') or []) or '-'} "
                f"trade_usd={_queue_cluster_range(cluster, 'this_trade_usd')} "
                f"baseline={_queue_cluster_range(cluster, 'baseline_median_usd')} "
                f"replay_only={cluster.get('replay_only_count', 0)}"
            )
        if len(clusters) > 5:
            print(f"   ... {len(clusters) - 5} more market cluster(s)")
    else:
        print("  market clusters: none")
    rows = queue.get("rows") or []
    preview = rows[:10]
    if not preview:
        print("  preview: no rows")
    else:
        print("  preview:")
        for row in preview:
            trade_usd = row.get("this_trade_usd")
            if trade_usd is None:
                trade_usd = row.get("trade_usd")
            print(
                "   - "
                f"{row.get('state')} {row.get('review_group')} "
                f"packet={row.get('packet_name')} "
                f"raw_event_id={row.get('raw_event_id')} "
                f"market_cluster={row.get('market_cluster') or '-'} "
                f"venue={row.get('venue') or '-'} "
                f"trade_usd={trade_usd if trade_usd is not None else '-'} "
                f"persisted_alert_reviewable="
                f"{str(bool(row.get('persisted_alert_reviewable'))).lower()}"
            )
        if len(rows) > len(preview):
            print(f"   ... {len(rows) - len(preview)} more returned row(s)")
    print("  local_only=true validate_only=true config_mutation=false db_mutation=false live_calls=false")
    return 0


def cmd_calibration_cluster_review(args: argparse.Namespace) -> int:
    """Write a local packet-level review artifact for one market cluster."""
    from pmfi.calibration_cluster_reviews import (
        build_calibration_cluster_review_record,
    )
    from pmfi.calibration_packets import (
        list_calibration_packet_files,
        load_calibration_packet,
    )

    output_path, output_err = _resolve_calibration_cluster_review_output(
        getattr(args, "output", None)
    )
    if output_err:
        print(output_err)
        return 1
    assert output_path is not None

    selected_names = list(getattr(args, "packet", None) or [])
    if not selected_names:
        selected_names = [packet["name"] for packet in list_calibration_packet_files()]
    if not selected_names:
        print("[calibration-cluster-review] no calibration packet JSON files found.")
        return 1

    named_packets: list[tuple[str, dict[str, Any]]] = []
    for name in selected_names:
        try:
            named_packets.append((name, load_calibration_packet(name)))
        except ValueError:
            print(f"[calibration-cluster-review] invalid packet name: {name}")
            return 1
        except FileNotFoundError:
            print(f"[calibration-cluster-review] packet not found: {name}")
            return 1
        except json.JSONDecodeError:
            print(f"[calibration-cluster-review] invalid packet JSON: {name}")
            return 1
        except TypeError as exc:
            print(f"[calibration-cluster-review] invalid packet: {name}: {exc}")
            return 1

    try:
        record = build_calibration_cluster_review_record(
            named_packets,
            market_cluster=getattr(args, "market_cluster", None),
            assessment=getattr(args, "assessment", None),
            rationale=getattr(args, "rationale", None),
            state=getattr(args, "state", "removed"),
            review_group=getattr(args, "review_group", "unmatched_replay_only"),
            reviewed_by=getattr(args, "reviewed_by", None),
            generated_at=datetime.now(timezone.utc),
            output_artifact_path=str(output_path),
            output_artifact_name=output_path.name,
        )
    except ValueError as exc:
        print(f"[calibration-cluster-review] {exc}")
        return 1

    include_raw_events = bool(getattr(args, "include_raw_events", False))
    include_raw_payload = bool(getattr(args, "include_raw_payload", False))
    if include_raw_payload:
        include_raw_events = True
    if include_raw_events:
        from pmfi.config import load_config
        from pmfi.raw_event_lookup import query_raw_event_lookup

        try:
            raw_event_ids = [int(value) for value in record.get("raw_event_ids") or []]
        except (TypeError, ValueError):
            print("[calibration-cluster-review] raw_event_ids must be integers.")
            return 1
        if not raw_event_ids:
            print("[calibration-cluster-review] no raw_event_ids available for lookup.")
            return 1
        try:
            lookup = asyncio.run(
                query_raw_event_lookup(
                    load_config().database.url,
                    raw_event_ids,
                    include_payload=include_raw_payload,
                )
            )
        except Exception as exc:
            print(f"[calibration-cluster-review] raw event lookup failed: {exc}")
            return 1
        missing_ids = lookup.get("missing_raw_event_ids") or []
        if missing_ids:
            print(
                "[calibration-cluster-review] missing raw_event_ids: "
                + ", ".join(str(value) for value in missing_ids)
            )
            return 1
        lookup["artifact_scope"] = "calibration_cluster_review"
        lookup["required_for_artifact"] = True
        record["raw_event_lookup"] = lookup

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(record, indent=2, default=_json_serial) + "\n",
        encoding="utf-8",
    )

    if getattr(args, "format", "text") == "json":
        print(json.dumps(record, indent=2, default=_json_serial))
        return 0

    cluster = record.get("cluster") or {}
    print(
        f"[calibration-cluster-review] wrote {output_path} "
        f"assessment={record['assessment']['label']} "
        f"market_cluster={record.get('market_cluster')} "
        f"rows={cluster.get('row_count', len(record.get('rows') or []))} "
        f"packets={record['packet_selection']['count']}"
    )
    if record.get("raw_event_lookup"):
        lookup = record["raw_event_lookup"]
        print(
            "  raw_event_lookup=embedded "
            f"found={lookup.get('found_count', 0)} "
            f"include_payload={str(bool(lookup.get('include_payload'))).lower()}"
        )
    print(
        "  local_only=true validate_only=true "
        "config_mutation=false db_mutation=false live_calls=false "
        "persisted_alert_review=false"
    )
    return 0


def cmd_calibration_cluster_review_summary(args: argparse.Namespace) -> int:
    """Summarize local cluster-review artifact coverage over queue clusters."""
    from pmfi.calibration_cluster_reviews import (
        calibration_cluster_review_coverage,
        list_calibration_cluster_review_files,
        load_calibration_cluster_review,
    )
    from pmfi.calibration_packets import (
        list_calibration_packet_files,
        load_calibration_packet,
    )

    selected_names = list(getattr(args, "packet", None) or [])
    if not selected_names:
        selected_names = [packet["name"] for packet in list_calibration_packet_files()]
    if not selected_names:
        print("[calibration-cluster-review-summary] no calibration packet JSON files found.")
        return 1

    named_packets: list[tuple[str, dict[str, Any]]] = []
    for name in selected_names:
        try:
            named_packets.append((name, load_calibration_packet(name)))
        except ValueError:
            print(f"[calibration-cluster-review-summary] invalid packet name: {name}")
            return 1
        except FileNotFoundError:
            print(f"[calibration-cluster-review-summary] packet not found: {name}")
            return 1
        except json.JSONDecodeError:
            print(f"[calibration-cluster-review-summary] invalid packet JSON: {name}")
            return 1
        except TypeError as exc:
            print(f"[calibration-cluster-review-summary] invalid packet: {name}: {exc}")
            return 1

    review_names = list(getattr(args, "review", None) or [])
    if not review_names:
        review_names = [
            review["name"] for review in list_calibration_cluster_review_files()
        ]
    review_records: list[tuple[str, dict[str, Any]]] = []
    for name in review_names:
        try:
            review_records.append((name, load_calibration_cluster_review(name)))
        except ValueError:
            print(f"[calibration-cluster-review-summary] invalid review name: {name}")
            return 1
        except FileNotFoundError:
            print(f"[calibration-cluster-review-summary] review not found: {name}")
            return 1
        except json.JSONDecodeError:
            print(f"[calibration-cluster-review-summary] invalid review JSON: {name}")
            return 1
        except TypeError as exc:
            print(f"[calibration-cluster-review-summary] invalid review: {name}: {exc}")
            return 1

    try:
        coverage = calibration_cluster_review_coverage(
            named_packets,
            review_records,
            state=getattr(args, "state", "removed"),
            review_group=getattr(args, "review_group", "unmatched_replay_only"),
            market_cluster=getattr(args, "market_cluster", None),
        )
    except ValueError as exc:
        print(f"[calibration-cluster-review-summary] {exc}")
        return 1

    if getattr(args, "format", "text") == "json":
        print(json.dumps(coverage, indent=2, default=_json_serial))
        return 0

    totals = coverage.get("totals") or {}
    queue_totals = coverage.get("queue_totals") or {}
    print("[calibration-cluster-review-summary] local-only validate-only coverage")
    print(
        f"  packets={coverage.get('packet_count', 0)} "
        f"review_artifacts={coverage.get('review_artifact_count', 0)} "
        f"considered_reviews={coverage.get('considered_review_artifact_count', 0)} "
        f"queue_clusters={totals.get('market_cluster_count', 0)} "
        f"covered={totals.get('covered_market_cluster_count', 0)} "
        f"uncovered={totals.get('uncovered_market_cluster_count', 0)} "
        f"queue_rows={queue_totals.get('filtered_rows', 0)}"
    )
    filters = coverage.get("filters") or {}
    print(
        "  filters: "
        f"state={filters.get('state')} "
        f"review_group={filters.get('review_group')} "
        f"market_cluster={filters.get('market_cluster') or '-'}"
    )
    print(
        "  candidate_readiness="
        f"{_flat_count_text(totals.get('candidate_readiness_counts') or {})} "
        "candidate_signals="
        f"{_flat_count_text(totals.get('candidate_signal_counts') or {})}"
    )
    print(
        "  raw_lookup_payload_status="
        f"{_flat_count_text(totals.get('raw_event_lookup_payload_status_counts') or {})}"
    )
    print(
        "  candidate_next_action="
        f"{_flat_count_text(totals.get('candidate_next_action_counts') or {})}"
    )
    clusters = coverage.get("market_clusters") or []
    if not clusters:
        print("  clusters: none")
    else:
        print("  clusters:")
        for cluster in clusters[:10]:
            latest = cluster.get("latest_review") or {}
            print(
                "   - "
                f"{cluster.get('market_key')}: "
                f"rows={cluster.get('row_count', 0)} "
                f"covered={str(bool(cluster.get('covered'))).lower()} "
                f"assessment={latest.get('assessment') or '-'} "
                f"readiness={latest.get('calibration_candidate_readiness') or '-'} "
                f"next_action={latest.get('calibration_candidate_next_action') or '-'} "
                f"signals={','.join(latest.get('calibration_candidate_signals') or []) or '-'} "
                f"raw_lookup={latest.get('raw_event_lookup_payload_status') or '-'} "
                f"review={latest.get('name') or '-'} "
                f"missing_raw_events={cluster.get('missing_raw_event_id_count', 0)}"
            )
        if len(clusters) > 10:
            print(f"   ... {len(clusters) - 10} more cluster(s)")
    print(
        "  local_only=true validate_only=true "
        "config_mutation=false db_mutation=false live_calls=false "
        "persisted_alert_review=false"
    )
    return 0


def cmd_alerts_volume_spike_floor_audit(args: argparse.Namespace) -> int:
    """Replay current volume_spike_v1 rules once and audit configured floor adherence."""
    import yaml
    from pmfi.calibration import summarize_volume_spike_floor_audit
    from pmfi.commands._shared import ROOT
    from pmfi.config import load_config
    from pmfi.replay import replay_from_db

    raw_since = _first_present(getattr(args, "since", None), getattr(args, "audit_from", None))
    raw_until = _first_present(getattr(args, "until", None), getattr(args, "audit_to", None))
    since_dt, since_err = _parse_since_window(
        raw_since,
        command="volume-spike-floor-audit",
    )
    if since_err:
        print(since_err)
        return 1
    until_dt, until_err = _parse_until_window(
        raw_until,
        command="volume-spike-floor-audit",
    )
    if until_err:
        print(until_err)
        return 1
    assert since_dt is not None
    if until_dt is not None and since_dt >= until_dt:
        print("[volume-spike-floor-audit] --since must be before --until.")
        return 1

    limit = int(getattr(args, "limit", 0))
    if limit < 0:
        print("[volume-spike-floor-audit] --limit must be >= 0.")
        return 1

    rules_path = ROOT / "config" / "alert_rules.yaml"
    rules_config = yaml.safe_load(rules_path.read_text(encoding="utf-8")) or {}
    configured_floor, floor_err = _volume_spike_min_trade_usd(rules_config)
    if floor_err:
        print(floor_err)
        return 1
    assert configured_floor is not None

    fmt = getattr(args, "format", "text")
    if fmt == "table":
        fmt = "text"
    venue = _first_present(getattr(args, "venue", None), getattr(args, "audit_venue", None))
    market = _first_present(getattr(args, "market", None), getattr(args, "audit_market", None))

    async def _audit():
        import asyncpg

        cfg = load_config()
        try:
            pool = await asyncpg.create_pool(
                cfg.database.url,
                min_size=1,
                max_size=1,
                server_settings={"search_path": "pmfi,public"},
            )
        except Exception as exc:
            return None, str(exc)
        try:
            replay_results = await replay_from_db(
                pool,
                rules_config=rules_config,
                limit=limit,
                start_ts=since_dt,
                end_ts=until_dt,
                venue=venue,
                market=market,
                persist=False,
                seed=not getattr(args, "cold_start", False),
                print_summary=False,
            )
            return (
                summarize_volume_spike_floor_audit(
                    replay_results,
                    configured_min_trade_usd=configured_floor,
                ),
                None,
            )
        except Exception as exc:
            return None, str(exc)
        finally:
            await pool.close()

    summary, db_err = asyncio.run(_audit())
    if db_err:
        print(f"DB query failed: {db_err}\nRun 'pmfi db-verify' to check connectivity.")
        return 1
    assert summary is not None
    summary["filters"] = {
        "since": since_dt.isoformat(),
        "until": until_dt.isoformat() if until_dt else None,
        "limit": limit,
        "venue": venue,
        "market": market,
        "cold_start": bool(getattr(args, "cold_start", False)),
    }

    if summary["current"]["normalized_trades"] == 0:
        print("[volume-spike-floor-audit] no normalized trades in replay window; widen --since/--until or ingest first.")
        return 1
    if summary["current"]["volume_spike_alerts"] == 0:
        print("[volume-spike-floor-audit] no current volume_spike_v1 alerts in replay window; insufficient floor evidence.")
        return 1

    passed = bool(summary["floor_check"]["passed"])
    if fmt == "json":
        print(json.dumps(summary, indent=2, default=str))
        return 0 if passed else 1

    current = summary["current"]
    floor_check = summary["floor_check"]
    print("[volume-spike-floor-audit] validate-only local DB replay floor audit")
    print(f"  window: since={summary['filters']['since']} until={summary['filters']['until'] or 'now'} limit={limit}")
    print(
        "  current: "
        f"trades={current['normalized_trades']} alerts={current['alerts']} "
        f"volume_spike={current['volume_spike_alerts']}"
    )
    print(
        "  configured_rule: "
        f"volume_spike_v1.min_trade_usd={floor_check['configured_min_trade_usd']}"
    )
    print(
        "  floor_check: "
        f"below_floor={floor_check['below_floor_volume_spike_alerts']} "
        f"unknown_trade_usd={floor_check['unknown_trade_usd_volume_spike_alerts']} "
        f"passed={passed}"
    )
    print(
        "  volume_spike_trade_usd_buckets: "
        f"{json.dumps(current['volume_spike_trade_usd_buckets'], sort_keys=True)}"
    )
    print(f"  evidence_status: {summary['evidence_status']}")
    print("  no DB writes, no config changes")
    return 0 if passed else 1


def cmd_alerts_outcome_audit(args: argparse.Namespace) -> int:
    """Read-only audit for directional alert outcome_key vs dominant_side evidence."""
    from pmfi.config import load_config
    from pmfi.db.repos.alerts import DIRECTIONAL_OUTCOME_RULES

    since_dt, since_err = _parse_since_window(
        getattr(args, "since", None),
        command="alerts outcome-audit",
    )
    if since_err:
        print(since_err)
        return 1
    until_dt, until_err = _parse_until_window(
        getattr(args, "until", None),
        command="alerts outcome-audit",
    )
    if until_err:
        print(until_err)
        return 1
    assert since_dt is not None
    if until_dt is not None and since_dt >= until_dt:
        print("[alerts outcome-audit] --since must be before --until.")
        return 1
    limit = getattr(args, "limit", 50)
    if limit <= 0:
        print("[alerts outcome-audit] --limit must be a positive integer.")
        return 1
    rules = list(getattr(args, "rule", None) or DIRECTIONAL_OUTCOME_RULES)
    invalid_rules = sorted(set(rules) - set(DIRECTIONAL_OUTCOME_RULES))
    if invalid_rules:
        print(f"[alerts outcome-audit] unsupported rule(s): {', '.join(invalid_rules)}")
        return 1
    fmt = getattr(args, "format", "table")
    strict = getattr(args, "strict", False)

    async def _query():
        import asyncpg
        from pmfi.db.repos.alerts import get_directional_outcome_audit

        cfg = load_config()
        try:
            pool = await asyncpg.create_pool(
                cfg.database.url, min_size=1, max_size=1,
                server_settings={"search_path": "pmfi,public"},
            )
        except Exception as exc:
            return None, str(exc)
        try:
            async with pool.acquire() as conn:
                audit = await get_directional_outcome_audit(
                    conn,
                    since=since_dt,
                    until=until_dt,
                    rules=rules,
                    limit=limit,
                )
            return audit, None
        except Exception as exc:
            return None, str(exc)
        finally:
            await pool.close()

    audit, err = asyncio.run(_query())
    if err:
        print(f"DB query failed: {err}\nRun 'pmfi db-verify' to check connectivity.")
        return 1
    assert audit is not None
    totals = audit.get("totals") or {}
    checked = int(totals.get("checked") or 0)
    has_outcome_gap = bool(
        int(totals.get("mismatches") or 0) > 0
        or int(totals.get("missing_dominant_side") or 0) > 0
    )
    strict_failed = bool(strict and (checked == 0 or has_outcome_gap))
    audit["ok"] = not has_outcome_gap and (not strict or checked > 0)

    if fmt == "json":
        print(json.dumps(audit, indent=2, default=_json_serial))
    else:
        summary = (
            "[outcome-audit] "
            f"checked={totals.get('checked', 0)} "
            f"matched={totals.get('matched', 0)} "
            f"mismatches={totals.get('mismatches', 0)} "
            f"missing_dominant_side={totals.get('missing_dominant_side', 0)}"
        )
        print(summary)
        for row in audit.get("rows") or []:
            print(
                f"{row['short_id']} {row['fired_at']} {row['rule_key']} "
                f"stored={row.get('stored_outcome_key') or '-'} "
                f"dominant={row.get('dominant_side') or '-'} "
                f"status={row['status']} "
                f"{row.get('title') or ''}"
            )

    if strict_failed:
        return 1
    return 0


def _load_rule_fp_rate_targets() -> tuple[dict[str, float], str | None]:
    import yaml
    from pmfi.commands._shared import ROOT

    rules_path = ROOT / "config" / "alert_rules.yaml"
    try:
        rules_config = yaml.safe_load(rules_path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        return {}, f"failed to read config\\alert_rules.yaml: {exc}"

    targets: dict[str, float] = {}
    for rule_key, cfg in (rules_config.get("rules") or {}).items():
        if not isinstance(cfg, dict) or "acceptable_fp_rate_percent" not in cfg:
            continue
        raw_target = cfg.get("acceptable_fp_rate_percent")
        try:
            target = float(raw_target)
        except (TypeError, ValueError):
            return {}, (
                f"invalid {rule_key}.acceptable_fp_rate_percent={raw_target!r}; "
                "expected a percentage from 0 to 100"
            )
        if target < 0 or target > 100:
            return {}, (
                f"invalid {rule_key}.acceptable_fp_rate_percent={raw_target!r}; "
                "expected a percentage from 0 to 100"
            )
        targets[str(rule_key)] = target
    return targets, None


def _load_rule_fp_rate_min_reviewed() -> tuple[dict[str, int], str | None]:
    import yaml
    from pmfi.commands._shared import ROOT

    rules_path = ROOT / "config" / "alert_rules.yaml"
    try:
        rules_config = yaml.safe_load(rules_path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        return {}, f"failed to read config\\alert_rules.yaml: {exc}"

    min_reviewed: dict[str, int] = {}
    for rule_key, cfg in (rules_config.get("rules") or {}).items():
        if not isinstance(cfg, dict) or "acceptable_fp_rate_percent" not in cfg:
            continue
        raw_value = cfg.get(
            "min_reviewed_for_fp_rate_breach",
            DEFAULT_FP_RATE_MIN_REVIEWED,
        )
        try:
            parsed = int(raw_value)
        except (TypeError, ValueError):
            return {}, (
                f"invalid {rule_key}.min_reviewed_for_fp_rate_breach={raw_value!r}; "
                "expected a positive integer"
            )
        if parsed <= 0:
            return {}, (
                f"invalid {rule_key}.min_reviewed_for_fp_rate_breach={raw_value!r}; "
                "expected a positive integer"
            )
        min_reviewed[str(rule_key)] = parsed
    return min_reviewed, None


def _load_volume_spike_min_trade_usd() -> tuple[float | None, str | None]:
    import yaml
    from pmfi.commands._shared import ROOT

    rules_path = ROOT / "config" / "alert_rules.yaml"
    try:
        rules_config = yaml.safe_load(rules_path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        return None, f"failed to read config\\alert_rules.yaml: {exc}"

    rule_cfg = (rules_config.get("rules") or {}).get("volume_spike_v1") or {}
    raw_value = rule_cfg.get("min_trade_usd")
    try:
        parsed = float(raw_value)
    except (TypeError, ValueError):
        return None, (
            f"invalid volume_spike_v1.min_trade_usd={raw_value!r}; "
            "expected a number >= 0"
        )
    if parsed < 0:
        return None, (
            f"invalid volume_spike_v1.min_trade_usd={raw_value!r}; "
            "expected a number >= 0"
        )
    return parsed, None


def _row_get(row: Any, key: str, default: Any = None) -> Any:
    try:
        return row[key]
    except (KeyError, TypeError):
        return default


def _extend_volume_spike_review_rows(target: list[dict[str, Any]], value: Any) -> None:
    if not value:
        return
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return
        value = parsed
    if isinstance(value, dict):
        target.append(dict(value))
        return
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                target.append(dict(item))


def cmd_alerts_fp_rate(args: argparse.Namespace) -> int:
    """Show false-positive statistics from alert_reviews."""
    from pmfi.config import load_config

    since_raw = getattr(args, "since", None)
    rule_filter = getattr(args, "rule", None)

    # Parse --since
    since_dt = None
    if since_raw:
        import re
        _m = re.match(r"^(\d+)([hdm])$", since_raw)
        if _m:
            n, unit = int(_m.group(1)), _m.group(2)
            delta = {"h": 3600, "d": 86400, "m": 60}[unit] * n
            from datetime import datetime, timezone, timedelta
            since_dt = datetime.now(timezone.utc) - timedelta(seconds=delta)
        else:
            from datetime import datetime
            try:
                since_dt = datetime.fromisoformat(since_raw)
            except ValueError:
                print(f"[alerts fp-rate] Invalid --since value: {since_raw!r}")
                return 1

    async def _query():
        import asyncpg
        cfg = load_config()
        try:
            pool = await asyncpg.create_pool(
                cfg.database.url, min_size=1, max_size=1,
                server_settings={"search_path": "pmfi,public"},
            )
        except Exception as exc:
            return None, str(exc)
        try:
            conditions: list[str] = []
            params: list = []
            idx = 1
            if since_dt is not None:
                conditions.append(f"a.fired_at >= ${idx}")
                params.append(since_dt)
                idx += 1
            if rule_filter:
                conditions.append(f"a.rule_key = ${idx}")
                params.append(rule_filter)
                idx += 1
            where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
            rows = await pool.fetch(
                f"WITH latest_reviews AS ("
                f"SELECT DISTINCT ON (ar.alert_id) ar.alert_id, ar.label, ar.reviewed_at "
                f"FROM alert_reviews ar "
                f"ORDER BY ar.alert_id, ar.reviewed_at DESC, ar.review_id DESC"
                f") "
                f"SELECT lr.label, a.rule_key, COUNT(*) AS cnt, "
                f"COALESCE("
                f"jsonb_agg(jsonb_build_object('label', lr.label, 'rule_key', a.rule_key, 'evidence', a.evidence)) "
                f"FILTER (WHERE a.rule_key = 'volume_spike_v1'), "
                f"'[]'::jsonb"
                f") AS volume_spike_review_rows "
                f"FROM latest_reviews lr "
                f"JOIN alerts a ON a.alert_id = lr.alert_id "
                f"{where} "
                f"GROUP BY lr.label, a.rule_key "
                f"ORDER BY a.rule_key, lr.label",
                *params,
            )
            return rows, None
        except Exception as exc:
            return None, str(exc)
        finally:
            await pool.close()

    rows, err = asyncio.run(_query())
    if err:
        print(f"DB query failed: {err}\nRun 'pmfi db-verify' to check connectivity.")
        return 1

    targets, target_err = _load_rule_fp_rate_targets()
    if target_err:
        print(f"[alerts fp-rate] {target_err}")
        return 1
    min_reviewed_by_rule, min_reviewed_err = _load_rule_fp_rate_min_reviewed()
    if min_reviewed_err:
        print(f"[alerts fp-rate] {min_reviewed_err}")
        return 1

    if not rows:
        print("No reviews recorded yet. Use 'pmfi alerts review <alert_id> --label fp|tp|noise' to add one.")
        return 0

    total_reviewed = sum(r["cnt"] for r in rows)
    fp_count = sum(r["cnt"] for r in rows if r["label"] == "fp")
    tp_count = sum(r["cnt"] for r in rows if r["label"] == "tp")
    noise_count = sum(r["cnt"] for r in rows if r["label"] == "noise")
    fp_rate = fp_count / total_reviewed * 100 if total_reviewed > 0 else 0.0

    since_label = since_raw if since_raw else "all time"
    rule_label = f"rule={rule_filter}" if rule_filter else "all rules"
    header = f"Alert Review Summary ({rule_label} / since {since_label})"
    summary = (
        f"Reviewed: {total_reviewed} | FP-only: {fp_count} ({fp_rate:.1f}% of reviewed) | "
        f"TP: {tp_count} | Noise: {noise_count}"
    )
    rule_totals: dict[str, dict[str, int]] = {}
    volume_spike_review_rows: list[dict[str, Any]] = []
    for row in rows:
        rule_key = str(row["rule_key"])
        label = str(row["label"])
        count = int(row["cnt"])
        stats = rule_totals.setdefault(
            rule_key,
            {"reviewed": 0, "tp": 0, "fp": 0, "noise": 0},
        )
        stats["reviewed"] += count
        if label in {"tp", "fp", "noise"}:
            stats[label] += count
        _extend_volume_spike_review_rows(
            volume_spike_review_rows,
            _row_get(row, "volume_spike_review_rows"),
        )

    all_time_governance_rows = build_fp_rate_governance_rows(
        rule_totals,
        fp_rate_targets=targets,
        min_reviewed_by_rule=min_reviewed_by_rule,
    )
    volume_spike_current_floor_row = None
    if volume_spike_review_rows:
        current_floor, floor_err = _load_volume_spike_min_trade_usd()
        if floor_err:
            print(f"[alerts fp-rate] {floor_err}")
            return 1
        assert current_floor is not None
        volume_spike_current_floor_row = build_volume_spike_current_floor_governance(
            volume_spike_review_rows,
            current_min_trade_usd=current_floor,
            target=targets.get("volume_spike_v1"),
            min_reviewed=min_reviewed_by_rule.get(
                "volume_spike_v1",
                DEFAULT_FP_RATE_MIN_REVIEWED,
            ),
        )
    governance_rows = apply_floor_gated_governance_headlines(
        all_time_governance_rows,
        current_floor_rows=(
            {"volume_spike_v1": volume_spike_current_floor_row}
            if volume_spike_current_floor_row is not None
            else {}
        ),
    )
    breach_rows = [row for row in governance_rows if row["status"] == "BREACH"]
    volume_spike_headline_row = next(
        (
            row
            for row in governance_rows
            if row.get("rule_key") == "volume_spike_v1"
            and row.get("cohort") == "current_floor"
        ),
        None,
    )

    try:
        from rich.console import Console
        from rich.table import Table
        console = Console()
        table = Table(title=header)
        table.add_column("Rule", style="yellow")
        table.add_column("Label", style="cyan")
        table.add_column("Count", justify="right")
        for row in rows:
            table.add_row(str(row["rule_key"]), str(row["label"]), str(row["cnt"]))
        console.print(table)
        console.print(summary)
        governance = Table(title="Per-rule FP+Noise / Reviewed Governance")
        governance.add_column("Rule", style="yellow")
        governance.add_column("Cohort", justify="right")
        governance.add_column("Reviewed", justify="right")
        governance.add_column("Min Reviewed", justify="right")
        governance.add_column("TP", justify="right")
        governance.add_column("FP", justify="right")
        governance.add_column("Noise", justify="right")
        governance.add_column("FP+Noise / Reviewed", justify="right")
        governance.add_column("Target", justify="right")
        governance.add_column("Status", justify="right")
        for row in governance_rows:
            target = row["target"]
            governance.add_row(
                str(row["rule_key"]),
                str(row.get("cohort") or "all_time"),
                str(row["reviewed"]),
                str(row["min_reviewed"]) if target is not None else "-",
                str(row["tp"]),
                str(row["fp"]),
                str(row["noise"]),
                f"{float(row['not_actionable_rate']):.1f}%",
                f"<={float(target):.1f}%" if target is not None else "-",
                str(row["status"]),
            )
        console.print(governance)
        if volume_spike_headline_row is not None:
            floor = volume_spike_headline_row
            secondary = floor.get("secondary_all_time")
            if isinstance(secondary, dict):
                all_time_table = Table(title="volume_spike_v1 all-time cohort")
                all_time_table.add_column("Reviewed", justify="right")
                all_time_table.add_column("Min Reviewed", justify="right")
                all_time_table.add_column("TP", justify="right")
                all_time_table.add_column("FP", justify="right")
                all_time_table.add_column("Noise", justify="right")
                all_time_table.add_column("FP+Noise / Reviewed", justify="right")
                all_time_table.add_column("Target", justify="right")
                all_time_table.add_column("Status", justify="right")
                target = secondary["target"]
                all_time_table.add_row(
                    str(secondary["reviewed"]),
                    str(secondary["min_reviewed"]) if target is not None else "-",
                    str(secondary["tp"]),
                    str(secondary["fp"]),
                    str(secondary["noise"]),
                    f"{float(secondary['not_actionable_rate']):.1f}%",
                    f"<={float(target):.1f}%" if target is not None else "-",
                    str(secondary["status"]),
                )
                console.print(all_time_table)
            console.print(
                "volume_spike_v1 current-floor exclusions: "
                f"below_current_floor_reviewed={floor['below_current_floor_reviewed']} "
                f"unknown_trade_usd_reviewed={floor['unknown_trade_usd_reviewed']} "
                f"excluded_reviewed={floor['excluded_reviewed']}"
            )
    except ImportError:
        print(header)
        print(summary)
        for row in rows:
            print(f"  {row['rule_key']}  {row['label']}  {row['cnt']}")
        print("Per-rule FP+Noise / Reviewed Governance:")
        for row in governance_rows:
            target = row["target"]
            target_text = (
                f"target<={float(target):.1f}%"
                if target is not None
                else "target=none"
            )
            min_reviewed_text = (
                f"min_reviewed={row['min_reviewed']}"
                if target is not None
                else "min_reviewed=-"
            )
            print(
                f"  {row['rule_key']} reviewed={row['reviewed']} "
                f"tp={row['tp']} fp={row['fp']} noise={row['noise']} "
                f"fp_noise_rate={float(row['not_actionable_rate']):.1f}% "
                f"{target_text} {min_reviewed_text} status={row['status']} "
                f"cohort={row.get('cohort') or 'all_time'}"
            )
        if volume_spike_headline_row is not None:
            floor = volume_spike_headline_row
            secondary = floor.get("secondary_all_time")
            if isinstance(secondary, dict):
                target = secondary["target"]
                target_text = (
                    f"target<={float(target):.1f}%"
                    if target is not None
                    else "target=none"
                )
                min_reviewed_text = (
                    f"min_reviewed={secondary['min_reviewed']}"
                    if target is not None
                    else "min_reviewed=-"
                )
                print("volume_spike_v1 all-time cohort:")
                print(
                    f"  reviewed={secondary['reviewed']} "
                    f"tp={secondary['tp']} fp={secondary['fp']} "
                    f"noise={secondary['noise']} "
                    f"fp_noise_rate={float(secondary['not_actionable_rate']):.1f}% "
                    f"{target_text} {min_reviewed_text} "
                    f"status={secondary['status']} cohort=all_time"
                )
            print(
                "volume_spike_v1 current-floor exclusions: "
                f"current_min_trade_usd={float(floor['current_min_trade_usd']):.1f} "
                f"below_current_floor_reviewed={floor['below_current_floor_reviewed']} "
                f"unknown_trade_usd_reviewed={floor['unknown_trade_usd_reviewed']} "
                f"excluded_reviewed={floor['excluded_reviewed']}"
            )

    return 1 if breach_rows else 0
