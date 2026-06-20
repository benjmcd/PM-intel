"""Localhost-only HTTP dashboard for live ingest rate/volume and alert review.

The dashboard serves read endpoints for feed-health, volume, and alerts, plus a
single local append-only alert review POST. It binds 127.0.0.1 only (never
public) and does not add auth, SaaS, external services, or trading surfaces.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from pmfi.calibration_decisions import (
    list_calibration_decision_files as _list_calibration_decision_files,
    load_calibration_decision as _load_calibration_decision,
    summarize_calibration_decision_record as _summarize_calibration_decision_record,
)
from pmfi.calibration_cluster_reviews import (
    calibration_cluster_review_coverage as _calibration_cluster_review_coverage,
    list_calibration_cluster_review_files as _list_calibration_cluster_review_files,
    load_calibration_cluster_review as _load_calibration_cluster_review,
    summarize_calibration_cluster_review_record as _summarize_calibration_cluster_review_record,
)
from pmfi.calibration_packets import (
    calibration_packet_comparison as _calibration_packet_comparison,
    calibration_packet_review_queue as _build_calibration_packet_review_queue,
    calibration_packet_review_summary as _build_calibration_packet_review_summary,
    list_calibration_packet_files as _list_calibration_packet_files,
    load_calibration_packet as _load_calibration_packet,
)

_STATIC_DIR = Path(__file__).resolve().parent / "static"

logger = logging.getLogger(__name__)


def _parse_alerts_query(query: Any) -> dict[str, Any]:
    """Parse dashboard /api/alerts query params and return typed kwargs for recent_alerts()."""
    from pmfi.dashboard.queries import (
        ALLOWED_ALERT_RULE_KEYS,
        ALLOWED_REVIEW_LABELS,
        ALLOWED_REVIEW_STATES,
        ALLOWED_TRIAGE_FLAGS,
    )

    def _int(name: str, default: int, lo: int, hi: int) -> int:
        raw = query.get(name, None)
        if raw is None or raw == "":
            return default
        try:
            value = int(raw)
        except (TypeError, ValueError):
            raise ValueError(f"{name} must be an integer")
        if value < lo or value > hi:
            raise ValueError(f"{name} must be between {lo} and {hi}")
        return value

    review_state = query.get("review_state")
    review_state = review_state if review_state not in ("", None) else None
    review_label = query.get("review_label")
    review_label = review_label if review_label not in ("", None) else None
    rule_key = query.get("rule_key")
    rule_key = rule_key if rule_key not in ("", None) else None

    triage_flag_inputs: list[str] = []
    if hasattr(query, "getall"):
        raw_flags = query.getall("triage_flag", [])
    else:
        value = query.get("triage_flag", None)
        raw_flags = value if isinstance(value, list) else ([value] if value else [])
    for raw_flag in raw_flags:
        if raw_flag is None:
            continue
        triage_flag_inputs.extend(
            [part.strip() for part in str(raw_flag).split(",") if part.strip()]
        )

    if review_state is not None and review_state not in ALLOWED_REVIEW_STATES:
        raise ValueError(f"invalid review_state {review_state!r}")
    if review_label is not None and review_label not in ALLOWED_REVIEW_LABELS:
        raise ValueError(f"invalid review_label {review_label!r}")
    if review_state == "unreviewed" and review_label is not None:
        raise ValueError("review_state=unreviewed cannot be combined with review_label")
    if rule_key is not None and rule_key not in ALLOWED_ALERT_RULE_KEYS:
        raise ValueError(f"invalid rule_key {rule_key!r}")

    unknown_flags = [f for f in triage_flag_inputs if f not in ALLOWED_TRIAGE_FLAGS]
    if unknown_flags:
        raise ValueError(f"invalid triage_flag {', '.join(sorted(set(unknown_flags)))}")

    limit = _int("limit", 20, 1, 200)
    return {
        "limit": limit,
        "review_state": review_state,
        "review_label": review_label,
        "rule_key": rule_key,
        "triage_flags_filter": triage_flag_inputs,
    }


def _parse_alert_review_history_query(query: Any) -> dict[str, int]:
    raw_limit = query.get("limit", "20")
    try:
        limit = int(raw_limit)
    except (TypeError, ValueError) as exc:
        raise ValueError("limit must be an integer") from exc
    if limit < 1 or limit > 100:
        raise ValueError("limit must be between 1 and 100")
    return {"limit": limit}


def _parse_volume_spike_calibration_query(query: Any) -> dict[str, Any]:
    from pmfi.calibration import VolumeSpikeCandidate

    def _text(name: str) -> str | None:
        raw = query.get(name, None)
        if raw is None:
            return None
        value = str(raw).strip()
        return value or None

    def _datetime(name: str, *, required: bool = False) -> datetime | None:
        raw = _text(name)
        if raw is None:
            if required:
                raise ValueError(f"{name} is required")
            return None
        try:
            value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"{name} must be an ISO timestamp") from exc
        if value.tzinfo is None:
            raise ValueError(f"{name} timestamp must include timezone")
        return value.astimezone(timezone.utc)

    def _int(name: str, *, default: int | None = None) -> int | None:
        raw = _text(name)
        if raw is None:
            return default
        try:
            value = int(raw)
        except ValueError as exc:
            raise ValueError(f"{name} must be an integer") from exc
        return value

    def _positive_int(name: str) -> int | None:
        value = _int(name)
        if value is not None and value <= 0:
            raise ValueError(f"{name} must be > 0")
        return value

    def _positive_decimal(name: str) -> Decimal | None:
        raw = _text(name)
        if raw is None:
            return None
        try:
            value = Decimal(raw)
        except (InvalidOperation, ValueError) as exc:
            raise ValueError(f"{name} must be numeric") from exc
        if not value.is_finite() or value <= 0:
            raise ValueError(f"{name} must be > 0")
        return value

    def _bool(name: str) -> bool:
        raw = _text(name)
        if raw is None:
            return False
        lowered = raw.lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
        raise ValueError(f"{name} must be a boolean")

    since_dt = _datetime("from") or _datetime("since", required=True)
    until_dt = _datetime("to") or _datetime("until")
    assert since_dt is not None
    if until_dt is not None and since_dt >= until_dt:
        raise ValueError("from must be before to")

    limit = _int("limit", default=0)
    assert limit is not None
    if limit < 0:
        raise ValueError("limit must be >= 0")
    details_limit = _int("details_limit", default=10)
    assert details_limit is not None
    if details_limit < 0 or details_limit > 50:
        raise ValueError("details_limit must be between 0 and 50")

    candidate = VolumeSpikeCandidate(
        min_trade_usd=_positive_decimal("min_trade_usd"),
        min_spike_multiplier=_positive_decimal("min_spike_multiplier"),
        min_baseline_trades=_positive_int("min_baseline_trades"),
        low_notional_min_baseline_trades=_positive_int("low_notional_min_baseline_trades"),
        low_notional_min_baseline_median_usd=_positive_decimal(
            "low_notional_min_baseline_median_usd"
        ),
        low_notional_max_spike_multiplier=_positive_decimal(
            "low_notional_max_spike_multiplier"
        ),
        low_notional_threshold_usd=_positive_decimal("low_notional_threshold_usd"),
        history_max=_positive_int("history_max"),
    )
    if not any(value is not None for value in candidate.as_dict().values()):
        raise ValueError("provide at least one candidate volume_spike_v1 knob")

    return {
        "since_dt": since_dt,
        "until_dt": until_dt,
        "limit": limit,
        "venue": _text("venue"),
        "market": _text("market"),
        "candidate": candidate,
        "cold_start": _bool("cold_start"),
        "details_limit": details_limit,
    }


def _parse_alert_review_body(body: Any) -> dict[str, str | None]:
    """Validate dashboard POST /api/alerts/{id}/review JSON."""
    from pmfi.dashboard.queries import ALLOWED_REVIEW_LABELS
    from pmfi.review_metadata import normalize_reviewed_by

    if not isinstance(body, dict):
        raise ValueError("body must be a JSON object")

    allowed = {"label", "category", "notes", "reviewed_by"}
    unknown = sorted(set(body) - allowed)
    if unknown:
        raise ValueError(f"unknown fields: {', '.join(unknown)}")

    label = body.get("label")
    if not isinstance(label, str) or label not in ALLOWED_REVIEW_LABELS:
        raise ValueError("label must be one of: tp, fp, noise")

    parsed: dict[str, str | None] = {"label": label}
    for name in ("category", "notes", "reviewed_by"):
        value = body.get(name)
        if value is None:
            if name in body:
                raise ValueError(f"{name} must be a string")
            parsed[name] = None
            continue
        if not isinstance(value, str):
            raise ValueError(f"{name} must be a string")
        if name == "reviewed_by":
            parsed[name] = normalize_reviewed_by(value)
        else:
            parsed[name] = value
    return parsed


def _request_origin(request: Any) -> str:
    return f"{request.scheme}://{request.host}"


def _url_origin(value: str) -> str | None:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if not parsed.scheme or not parsed.netloc:
        return None
    return f"{parsed.scheme}://{parsed.netloc}"


def _review_write_origin_error(request: Any) -> str | None:
    """Return an error detail when a dashboard write has a foreign origin."""
    expected = _request_origin(request)
    origin = request.headers.get("Origin")
    if origin and _url_origin(origin) != expected:
        return "review writes require same-origin Origin"
    referer = request.headers.get("Referer")
    if referer and _url_origin(referer) != expected:
        return "review writes require same-origin Referer"
    return None


def _load_alert_rules_config() -> dict[str, Any]:
    import yaml
    from pmfi.config import ROOT

    rules_path = ROOT / "config" / "alert_rules.yaml"
    return yaml.safe_load(rules_path.read_text(encoding="utf-8")) or {}


def _create_dashboard_app(pool: Any):
    from aiohttp import web

    from pmfi.dashboard.queries import (
        alert_review_history,
        feed_health,
        recent_alerts,
        volume_timeseries,
    )
    from pmfi.db.repos.alerts import insert_alert_review

    started_at = datetime.now(timezone.utc).isoformat()

    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    async def _dashboard_capabilities(request: web.Request) -> web.Response:
        return web.json_response({
            "schema_version": "dashboard_capabilities.v1",
            "local_only": True,
            "server_started_at": started_at,
            "generated_at": _now_iso(),
            "routes": {
                "feedhealth": True,
                "volume": True,
                "alerts": True,
                "alert_review_history": True,
                "alert_review_write": True,
                "volume_spike_calibration": True,
                "calibration_packets": True,
                "calibration_packet_comparison": True,
                "calibration_packet_review_summary": True,
                "calibration_packet_review_queue": True,
                "calibration_decisions": True,
                "calibration_cluster_reviews": True,
                "calibration_cluster_review_coverage": True,
                "raw_event_lookup": True,
            },
        })

    async def _feedhealth(request: web.Request) -> web.Response:
        try:
            lookback = max(1, min(int(request.query.get("lookback", "10")), 1440))
        except (TypeError, ValueError):
            lookback = 10
        async with pool.acquire() as conn:
            venues = await feed_health(conn, lookback_minutes=lookback)
        return web.json_response({"venues": venues, "lookback_minutes": lookback, "generated_at": _now_iso()})

    async def _volume(request: web.Request) -> web.Response:
        try:
            minutes = max(1, min(int(request.query.get("minutes", "60")), 1440))
        except (TypeError, ValueError):
            minutes = 60
        async with pool.acquire() as conn:
            buckets = await volume_timeseries(conn, lookback_minutes=minutes)
        return web.json_response({"buckets": buckets, "minutes": minutes, "generated_at": _now_iso()})

    async def _alerts(request: web.Request) -> web.Response:
        try:
            params = _parse_alerts_query(request.query)
        except (TypeError, ValueError) as exc:
            return web.json_response({"error": "invalid query", "detail": str(exc)}, status=400)
        async with pool.acquire() as conn:
            try:
                alerts = await recent_alerts(
                    conn,
                    limit=params["limit"],
                    review_state=params["review_state"],
                    review_label=params["review_label"],
                    rule_key=params["rule_key"],
                    triage_flags_filter=params["triage_flags_filter"],
                )
            except ValueError as exc:
                return web.json_response({"error": "invalid query", "detail": str(exc)}, status=400)
        return web.json_response({"alerts": alerts, "generated_at": _now_iso()})

    async def _alert_reviews(request: web.Request) -> web.Response:
        try:
            params = _parse_alert_review_history_query(request.query)
        except (TypeError, ValueError) as exc:
            return web.json_response({"error": "invalid query", "detail": str(exc)}, status=400)
        async with pool.acquire() as conn:
            try:
                history = await alert_review_history(
                    conn,
                    request.match_info["alert_id"],
                    limit=params["limit"],
                )
            except ValueError as exc:
                return web.json_response(
                    {"error": "invalid query", "detail": str(exc)},
                    status=400,
                )
        if history is None:
            return web.json_response(
                {"error": "not found", "alert_id": request.match_info["alert_id"]},
                status=404,
            )
        return web.json_response({
            "alert_id": history["alert_id"],
            "reviews": history["reviews"],
            "limit": params["limit"],
            "generated_at": _now_iso(),
        })

    async def _raw_event_lookup(request: web.Request) -> web.Response:
        raw_id = request.match_info["raw_event_id"]
        try:
            raw_event_id = int(raw_id)
        except (TypeError, ValueError):
            return web.json_response(
                {"error": "invalid query", "detail": "raw_event_id must be an integer"},
                status=400,
            )
        if raw_event_id <= 0:
            return web.json_response(
                {"error": "invalid query", "detail": "raw_event_id must be positive"},
                status=400,
            )
        include_payload = str(request.query.get("include_payload", "")).lower() in {
            "1",
            "true",
            "yes",
        }
        from pmfi.raw_event_lookup import (
            build_raw_event_lookup_result,
            fetch_raw_event_lookup_rows,
        )

        async with pool.acquire() as conn:
            rows = await fetch_raw_event_lookup_rows(
                conn,
                [raw_event_id],
                include_payload=include_payload,
            )
        result = build_raw_event_lookup_result(
            [raw_event_id],
            rows,
            include_payload=include_payload,
        )
        result["generated_at"] = _now_iso()
        if not rows:
            return web.json_response(result, status=404)
        return web.json_response(result)

    async def _calibration_packets(request: web.Request) -> web.Response:
        return web.json_response({
            "packets": _list_calibration_packet_files(),
            "generated_at": _now_iso(),
        })

    def _names_from_query(request: web.Request, key: str) -> list[str]:
        if hasattr(request.query, "getall"):
            raw_names = request.query.getall(key, [])
        else:
            raw_name = request.query.get(key)
            raw_names = [raw_name] if raw_name else []
        names: list[str] = []
        for raw_name in raw_names:
            if raw_name is None:
                continue
            for part in str(raw_name).split(","):
                name = part.strip()
                if name:
                    names.append(name)
        return names

    def _calibration_packet_names_from_query(request: web.Request) -> list[str]:
        names = _names_from_query(request, "name")
        if not names:
            names = [packet["name"] for packet in _list_calibration_packet_files()]
        return names

    def _load_named_calibration_packets(names: list[str]) -> list[tuple[str, dict[str, Any]]]:
        named_packets: list[tuple[str, dict[str, Any]]] = []
        for name in names:
            named_packets.append((name, _load_calibration_packet(name)))
        return named_packets

    def _calibration_cluster_review_names_from_query(request: web.Request) -> list[str]:
        return _names_from_query(request, "review")

    def _load_named_calibration_cluster_reviews(
        names: list[str],
    ) -> list[tuple[str, dict[str, Any]]]:
        review_records: list[tuple[str, dict[str, Any]]] = []
        for name in names:
            review_records.append((name, _load_calibration_cluster_review(name)))
        return review_records

    def _load_default_calibration_cluster_reviews_for_coverage(
    ) -> tuple[list[tuple[str, dict[str, Any]]], list[dict[str, str]]]:
        review_records: list[tuple[str, dict[str, Any]]] = []
        invalid_reviews: list[dict[str, str]] = []
        for review in _list_calibration_cluster_review_files():
            name = str(review.get("name") or "")
            try:
                review_records.append((name, _load_calibration_cluster_review(name)))
            except FileNotFoundError as exc:
                invalid_reviews.append({
                    "name": name,
                    "error": "not found",
                    "detail": str(exc),
                })
            except json.JSONDecodeError as exc:
                invalid_reviews.append({
                    "name": name,
                    "error": "invalid artifact json",
                    "detail": str(exc),
                })
            except (TypeError, ValueError) as exc:
                invalid_reviews.append({
                    "name": name,
                    "error": "invalid artifact",
                    "detail": str(exc),
                })
        return review_records, invalid_reviews

    async def _calibration_packet_load_error(
        request: web.Request,
        exc: Exception,
    ) -> web.Response:
        if isinstance(exc, json.JSONDecodeError):
            return web.json_response(
                {"error": "invalid packet json", "detail": str(exc)},
                status=422,
            )
        if isinstance(exc, ValueError):
            return web.json_response(
                {"error": "invalid packet name", "detail": str(exc)},
                status=400,
            )
        if isinstance(exc, FileNotFoundError):
            return web.json_response(
                {"error": "not found", "name": str(exc) or request.match_info.get("name")},
                status=404,
            )
        if isinstance(exc, TypeError):
            return web.json_response(
                {"error": "invalid packet json", "detail": str(exc)},
                status=422,
            )
        raise exc

    async def _calibration_packet(request: web.Request) -> web.Response:
        try:
            packet = _load_calibration_packet(request.match_info["name"])
        except (json.JSONDecodeError, ValueError, FileNotFoundError, TypeError) as exc:
            return await _calibration_packet_load_error(request, exc)
        body = dict(packet)
        body["generated_at"] = _now_iso()
        return web.json_response(body)

    async def _calibration_packet_compare(request: web.Request) -> web.Response:
        try:
            named_packets = _load_named_calibration_packets(
                _calibration_packet_names_from_query(request)
            )
        except json.JSONDecodeError as exc:
            return web.json_response(
                {"error": "invalid packet json", "detail": str(exc)},
                status=422,
            )
        except ValueError as exc:
            return web.json_response(
                {"error": "invalid packet name", "detail": str(exc)},
                status=400,
            )
        except FileNotFoundError as exc:
            return web.json_response(
                {"error": "not found", "name": str(exc)},
                status=404,
            )
        except TypeError as exc:
            return web.json_response(
                {"error": "invalid packet json", "detail": str(exc)},
                status=422,
            )
        comparison = _calibration_packet_comparison(named_packets)
        comparison["generated_at"] = _now_iso()
        return web.json_response(comparison)

    async def _calibration_packet_review_summary(request: web.Request) -> web.Response:
        try:
            named_packets = _load_named_calibration_packets(
                _calibration_packet_names_from_query(request)
            )
        except (json.JSONDecodeError, ValueError, FileNotFoundError, TypeError) as exc:
            return await _calibration_packet_load_error(request, exc)
        summary = _build_calibration_packet_review_summary(named_packets)
        summary["generated_at"] = _now_iso()
        return web.json_response(summary)

    async def _calibration_packet_review_queue(request: web.Request) -> web.Response:
        raw_limit = request.query.get("limit", "0")
        try:
            limit = int(raw_limit)
        except (TypeError, ValueError):
            return web.json_response(
                {"error": "invalid query", "detail": "limit must be an integer"},
                status=400,
            )
        try:
            named_packets = _load_named_calibration_packets(
                _calibration_packet_names_from_query(request)
            )
        except (json.JSONDecodeError, ValueError, FileNotFoundError, TypeError) as exc:
            return await _calibration_packet_load_error(request, exc)
        try:
            queue = _build_calibration_packet_review_queue(
                named_packets,
                state=request.query.get("state", "all") or "all",
                review_group=request.query.get("review_group", "all") or "all",
                market_cluster=request.query.get("market_cluster") or None,
                limit=limit,
            )
        except ValueError as exc:
            return web.json_response(
                {"error": "invalid query", "detail": str(exc)},
                status=400,
            )
        queue["generated_at"] = _now_iso()
        return web.json_response(queue)

    async def _calibration_decisions(request: web.Request) -> web.Response:
        decisions: list[dict[str, Any]] = []
        for item in _list_calibration_decision_files():
            summary = dict(item)
            try:
                record = _load_calibration_decision(item["name"])
                summary.update(_summarize_calibration_decision_record(item["name"], record))
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                summary["invalid"] = True
                summary["error"] = str(exc)
            decisions.append(summary)
        return web.json_response({
            "decisions": decisions,
            "generated_at": _now_iso(),
        })

    async def _calibration_decision(request: web.Request) -> web.Response:
        try:
            record = _load_calibration_decision(request.match_info["name"])
        except json.JSONDecodeError as exc:
            return web.json_response(
                {"error": "invalid decision json", "detail": str(exc)},
                status=422,
            )
        except ValueError as exc:
            return web.json_response(
                {"error": "invalid decision name", "detail": str(exc)},
                status=400,
            )
        except FileNotFoundError:
            return web.json_response(
                {"error": "not found", "name": request.match_info["name"]},
                status=404,
            )
        except TypeError as exc:
            return web.json_response(
                {"error": "invalid decision json", "detail": str(exc)},
                status=422,
            )
        body = dict(record)
        body["summary"] = _summarize_calibration_decision_record(
            request.match_info["name"],
            record,
        )
        body["generated_at"] = _now_iso()
        return web.json_response(body)

    async def _calibration_cluster_reviews(request: web.Request) -> web.Response:
        reviews: list[dict[str, Any]] = []
        for item in _list_calibration_cluster_review_files():
            summary = dict(item)
            try:
                record = _load_calibration_cluster_review(item["name"])
                summary.update(
                    _summarize_calibration_cluster_review_record(item["name"], record)
                )
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                summary["invalid"] = True
                summary["error"] = str(exc)
            reviews.append(summary)
        return web.json_response({
            "cluster_reviews": reviews,
            "generated_at": _now_iso(),
        })

    async def _calibration_cluster_review(request: web.Request) -> web.Response:
        try:
            record = _load_calibration_cluster_review(request.match_info["name"])
        except json.JSONDecodeError as exc:
            return web.json_response(
                {"error": "invalid cluster review json", "detail": str(exc)},
                status=422,
            )
        except ValueError as exc:
            return web.json_response(
                {"error": "invalid cluster review name", "detail": str(exc)},
                status=400,
            )
        except FileNotFoundError:
            return web.json_response(
                {"error": "not found", "name": request.match_info["name"]},
                status=404,
            )
        except TypeError as exc:
            return web.json_response(
                {"error": "invalid cluster review json", "detail": str(exc)},
                status=422,
            )
        body = dict(record)
        body["summary"] = _summarize_calibration_cluster_review_record(
            request.match_info["name"],
            record,
        )
        body["generated_at"] = _now_iso()
        return web.json_response(body)

    async def _calibration_cluster_review_coverage_route(
        request: web.Request,
    ) -> web.Response:
        try:
            raw_market_cluster = request.query.get("market_cluster")
            market_cluster = (
                str(raw_market_cluster).strip()
                if raw_market_cluster is not None
                else None
            ) or None
            review_names = _calibration_cluster_review_names_from_query(request)
            invalid_reviews: list[dict[str, str]] = []
            if review_names:
                review_records = _load_named_calibration_cluster_reviews(review_names)
            else:
                (
                    review_records,
                    invalid_reviews,
                ) = _load_default_calibration_cluster_reviews_for_coverage()
            coverage = _calibration_cluster_review_coverage(
                _load_named_calibration_packets(
                    _calibration_packet_names_from_query(request)
                ),
                review_records,
                state=request.query.get("state", "removed") or "removed",
                review_group=(
                    request.query.get("review_group", "unmatched_replay_only")
                    or "unmatched_replay_only"
                ),
                market_cluster=market_cluster,
            )
        except json.JSONDecodeError as exc:
            return web.json_response(
                {"error": "invalid artifact json", "detail": str(exc)},
                status=422,
            )
        except FileNotFoundError as exc:
            return web.json_response(
                {"error": "not found", "name": str(exc)},
                status=404,
            )
        except TypeError as exc:
            return web.json_response(
                {"error": "invalid artifact json", "detail": str(exc)},
                status=422,
            )
        except ValueError as exc:
            return web.json_response(
                {"error": "invalid query", "detail": str(exc)},
                status=400,
            )
        coverage["generated_at"] = _now_iso()
        coverage["invalid_review_artifact_count"] = len(invalid_reviews)
        coverage["invalid_review_artifacts"] = invalid_reviews
        coverage["discovered_review_artifact_count"] = (
            int(coverage.get("review_artifact_count") or 0) + len(invalid_reviews)
        )
        return web.json_response(coverage)

    async def _volume_spike_calibration(request: web.Request) -> web.Response:
        try:
            params = _parse_volume_spike_calibration_query(request.query)
        except (TypeError, ValueError) as exc:
            return web.json_response({"error": "invalid query", "detail": str(exc)}, status=400)
        try:
            from pmfi import volume_spike_calibration as calibration_service

            summary = await calibration_service.run_volume_spike_calibration_replay(
                pool,
                base_rules_config=_load_alert_rules_config(),
                since_dt=params["since_dt"],
                until_dt=params["until_dt"],
                limit=params["limit"],
                venue=params["venue"],
                market=params["market"],
                candidate=params["candidate"],
                cold_start=params["cold_start"],
                details_limit=params["details_limit"],
            )
            reason = calibration_service.insufficient_volume_spike_evidence_reason(summary)
        except Exception as exc:
            logger.warning("dashboard volume-spike calibration failed: %s", exc)
            return web.json_response({"error": "replay failed", "detail": str(exc)}, status=500)
        if reason is not None:
            return web.json_response(
                {"error": "insufficient evidence", "detail": reason},
                status=422,
            )
        summary["generated_at"] = _now_iso()
        return web.json_response(summary)

    async def _review_alert(request: web.Request) -> web.Response:
        origin_error = _review_write_origin_error(request)
        if origin_error:
            return web.json_response({"error": "forbidden", "detail": origin_error}, status=403)
        if request.content_type != "application/json":
            return web.json_response(
                {"error": "invalid body", "detail": "Content-Type must be application/json"},
                status=400,
            )
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "invalid body", "detail": "body must be JSON"}, status=400)
        try:
            parsed = _parse_alert_review_body(body)
        except ValueError as exc:
            return web.json_response({"error": "invalid body", "detail": str(exc)}, status=400)
        async with pool.acquire() as conn:
            review = await insert_alert_review(
                conn,
                request.match_info["alert_id"],
                label=parsed["label"] or "",
                category=parsed["category"],
                notes=parsed["notes"],
                reviewed_by=parsed["reviewed_by"],
            )
        if review is None:
            return web.json_response({"error": "not found", "alert_id": request.match_info["alert_id"]}, status=404)
        return web.json_response({
            "ok": True,
            "alert_id": review["alert_id"],
            "review": review,
            "generated_at": _now_iso(),
        })

    async def _healthz(request: web.Request) -> web.Response:
        ok = True
        try:
            async with pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
        except Exception as exc:  # pragma: no cover - defensive
            ok = False
            logger.warning("dashboard healthz DB check failed: %s", exc)
        return web.json_response({"ok": ok, "generated_at": _now_iso()})

    async def _index(request: web.Request) -> web.Response:
        return web.FileResponse(_STATIC_DIR / "index.html")

    app = web.Application()
    app.router.add_get("/", _index)
    app.router.add_get("/api/dashboard-capabilities", _dashboard_capabilities)
    app.router.add_get("/api/feedhealth", _feedhealth)
    app.router.add_get("/api/volume", _volume)
    app.router.add_get("/api/alerts", _alerts)
    app.router.add_get("/api/alerts/{alert_id}/reviews", _alert_reviews)
    app.router.add_get("/api/raw-events/{raw_event_id}", _raw_event_lookup)
    app.router.add_get("/api/calibration-packets", _calibration_packets)
    app.router.add_get("/api/calibration-packets/compare", _calibration_packet_compare)
    app.router.add_get("/api/calibration-packets/review-summary", _calibration_packet_review_summary)
    app.router.add_get("/api/calibration-packets/review-queue", _calibration_packet_review_queue)
    app.router.add_get("/api/calibration-packets/{name:.+}", _calibration_packet)
    app.router.add_get("/api/calibration-decisions", _calibration_decisions)
    app.router.add_get("/api/calibration-decisions/{name:.+}", _calibration_decision)
    app.router.add_get("/api/calibration-cluster-reviews", _calibration_cluster_reviews)
    app.router.add_get(
        "/api/calibration-cluster-reviews/coverage",
        _calibration_cluster_review_coverage_route,
    )
    app.router.add_get("/api/calibration-cluster-reviews/{name:.+}", _calibration_cluster_review)
    app.router.add_get("/api/volume-spike-calibration", _volume_spike_calibration)
    app.router.add_post("/api/alerts/{alert_id}/review", _review_alert)
    app.router.add_get("/healthz", _healthz)
    if _STATIC_DIR.is_dir():
        app.router.add_static("/static/", _STATIC_DIR)
    return app


async def run_dashboard(*, db_url: str, host: str = "127.0.0.1", port: int = 8766) -> None:
    """Serve the dashboard JSON endpoints on localhost until interrupted.

    Endpoints (GET, JSON):
      /api/dashboard-capabilities static UI/server route compatibility manifest
      /api/feedhealth          per-venue last-event age, events_60s/5m, unresolved dead-letters
      /api/volume[?minutes=N]  per-venue per-bucket trade_count + gross capital volume
      /api/alerts              recent alerts with review filters
      /api/alerts/{id}/reviews read-only append-only alert review history
      /api/calibration-packets ignored local calibration packet list/load/compare/review-summary
      /api/calibration-decisions ignored local calibration decision history
      /api/calibration-cluster-reviews ignored local cluster-review artifacts
      /api/alerts/{id}/review  append one local alert review row (POST)
      /healthz                 liveness + DB reachability
    """
    from aiohttp import web

    from pmfi.db import create_pool, close_pool

    # host is forced to loopback below; never honor a public bind.
    if host not in ("127.0.0.1", "localhost", "::1"):
        logger.warning("dashboard: ignoring non-loopback host %r; binding 127.0.0.1", host)
        host = "127.0.0.1"

    pool = await create_pool(db_url)
    app = _create_dashboard_app(pool)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    print(
        f"[dashboard] listening on http://{host}:{port}  "
        f"(/api/feedhealth  /api/volume  /api/alerts  /healthz) — Ctrl+C to stop"
    )
    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        await runner.cleanup()
        await close_pool(pool)
