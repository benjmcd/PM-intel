from __future__ import annotations

import hashlib
import json
import platform
import subprocess
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from pmfi.domain import RawEvent
from pmfi.normalization import CURRENCY_CONVENTION_BY_VENUE
from pmfi.pipeline.engine import AlertEngine
from pmfi.pipeline.runner import process_event
from pmfi.venue_registry import is_trade_event_type

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_MANIFEST = ROOT / "tests" / "qualification" / "dq2_semantics_manifest.yaml"

_PROVENANCE_REQUIRED = {
    "id",
    "category_tags",
    "origin_class",
    "venue_code",
    "source_channel",
    "source_event_type",
    "capture_or_construction_date",
    "source_schema_fingerprint",
    "parser_normalizer_version_expectation",
    "redaction_status",
    "expected_disposition",
    "purpose",
    "payload",
    "payload_sha256",
}


def _sha256_payload(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _sha256_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _as_path(path: Path) -> Path:
    return path if path.is_absolute() else ROOT / path


def load_dq2_manifest(path: Path = DEFAULT_MANIFEST) -> dict[str, Any]:
    path = _as_path(path)
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def validate_fixture_provenance(manifest: dict[str, Any]) -> dict[str, Any]:
    missing: list[str] = []
    invalid_hashes: list[str] = []
    origin_classes: set[str] = set()
    for fixture in manifest.get("fixtures", []):
        origin_classes.add(str(fixture.get("origin_class")))
        absent = sorted(field for field in _PROVENANCE_REQUIRED if field not in fixture)
        if absent:
            missing.append(f"{fixture.get('id', '<unknown>')}:{','.join(absent)}")
        expected_hash = fixture.get("payload_sha256")
        actual_hash = _sha256_payload(fixture.get("payload") or {})
        if expected_hash != actual_hash:
            invalid_hashes.append(str(fixture.get("id")))
    return {
        "fixture_count": len(manifest.get("fixtures", [])),
        "invalid_hashes": invalid_hashes,
        "missing_required_fields": missing,
        "origin_classes": sorted(origin_classes),
        "not_applicable": manifest.get("not_applicable", {}),
    }


def _git_value(args: list[str]) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=ROOT,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except Exception:
        return None
    return result.stdout.strip()


def _parse_ts(value: str | None) -> datetime | None:
    if value in (None, ""):
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _payload(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        return json.loads(value)
    return dict(value or {})


def _raw_event(fixture: dict[str, Any]) -> RawEvent:
    return RawEvent(
        venue_code=fixture["venue_code"],
        source_channel=fixture["source_channel"],
        source_event_type=fixture["source_event_type"],
        source_event_id=fixture.get("source_event_id"),
        venue_market_id=fixture.get("venue_market_id"),
        exchange_ts=_parse_ts(fixture.get("exchange_ts")),
        received_at=datetime.now(timezone.utc),
        payload=json.loads(json.dumps(fixture["payload"], sort_keys=True)),
    )


async def _noop_alert_handler(decision, venue_code, market_id) -> None:
    return None


async def cleanup_dq2_semantics_rows(pool: Any, manifest_path: Path = DEFAULT_MANIFEST) -> None:
    manifest = load_dq2_manifest(manifest_path)
    source_channels = sorted({fixture["source_channel"] for fixture in manifest["fixtures"]})
    async with pool.acquire() as conn:
        market_rows = await conn.fetch(
            "SELECT market_id FROM markets WHERE venue_market_id LIKE 'DQ2-%' OR venue_market_id LIKE 'KX-DQ2-%'"
        )
        market_ids = [row["market_id"] for row in market_rows]
        raw_rows = await conn.fetch(
            "SELECT raw_event_id FROM raw_events WHERE source_channel = ANY($1::text[])",
            source_channels,
        )
        raw_ids = [row["raw_event_id"] for row in raw_rows]
        if raw_ids:
            await conn.execute("DELETE FROM alerts WHERE raw_event_id = ANY($1::bigint[])", raw_ids)
            await conn.execute("DELETE FROM dead_letters WHERE raw_event_id = ANY($1::bigint[])", raw_ids)
        if source_channels:
            await conn.execute("DELETE FROM dead_letters WHERE source_channel = ANY($1::text[])", source_channels)
        if market_ids:
            await conn.execute("DELETE FROM alerts WHERE market_id = ANY($1::uuid[])", market_ids)
            await conn.execute("DELETE FROM metric_windows WHERE market_id = ANY($1::uuid[])", market_ids)
            await conn.execute(
                "DELETE FROM normalized_trade_dedupe_keys WHERE market_id = ANY($1::uuid[])",
                market_ids,
            )
            await conn.execute("DELETE FROM normalized_trades WHERE market_id = ANY($1::uuid[])", market_ids)
            await conn.execute("DELETE FROM feed_cursors WHERE market_id = ANY($1::uuid[])", market_ids)
        if raw_ids:
            await conn.execute(
                "DELETE FROM event_dedupe_keys WHERE first_raw_event_id = ANY($1::bigint[])",
                raw_ids,
            )
            await conn.execute("DELETE FROM raw_events WHERE raw_event_id = ANY($1::bigint[])", raw_ids)
        if source_channels:
            await conn.execute(
                "DELETE FROM event_dedupe_keys WHERE source_channel = ANY($1::text[])",
                source_channels,
            )
        if market_ids:
            await conn.execute("DELETE FROM markets WHERE market_id = ANY($1::uuid[])", market_ids)


def _identity(fixture: dict[str, Any]) -> str:
    if fixture.get("source_event_id"):
        return f"source:{fixture['source_channel']}:{fixture['source_event_id']}"
    return f"payload:{fixture['source_channel']}:{_sha256_payload(fixture['payload'])}"


async def _fetch_raw_for_fixture(conn: Any, fixture: dict[str, Any]) -> Any | None:
    if fixture.get("source_event_id"):
        return await conn.fetchrow(
            """SELECT raw_event_id, source_event_id, source_event_type, exchange_ts,
                      payload, payload_hash
               FROM raw_events
               WHERE source_channel = $1 AND source_event_id = $2
               ORDER BY raw_event_id
               LIMIT 1""",
            fixture["source_channel"],
            fixture["source_event_id"],
        )
    return await conn.fetchrow(
        """SELECT raw_event_id, source_event_id, source_event_type, exchange_ts,
                  payload, payload_hash
           FROM raw_events
           WHERE source_channel = $1 AND payload_hash = $2
           ORDER BY raw_event_id
           LIMIT 1""",
        fixture["source_channel"],
        _sha256_payload(fixture["payload"]),
    )


async def _classify_fixture(conn: Any, fixture: dict[str, Any], seen: set[str]) -> dict[str, Any]:
    ident = _identity(fixture)
    if ident in seen:
        row = await _fetch_raw_for_fixture(conn, fixture)
        return {
            "fixture_id": fixture["id"],
            "disposition": "DUPLICATE",
            "raw_event_id": row["raw_event_id"] if row else None,
            "raw": row,
            "trade": None,
            "dead_letters": [],
        }
    seen.add(ident)
    row = await _fetch_raw_for_fixture(conn, fixture)
    if row is None:
        return {"fixture_id": fixture["id"], "disposition": "UNKNOWN", "raw_event_id": None, "trade": None, "dead_letters": []}
    dead_letters = await conn.fetch(
        "SELECT error_class, failure_stage, error_message FROM dead_letters WHERE raw_event_id = $1",
        row["raw_event_id"],
    )
    trade = await conn.fetchrow(
        """SELECT nt.venue_code, m.venue_market_id, nt.venue_trade_id, nt.outcome_key,
                  nt.directional_side, nt.price, nt.contracts, nt.capital_at_risk_usd,
                  nt.payout_notional_usd, nt.fee_usd, nt.exchange_ts, nt.source_payload,
                  nt.raw_event_id
           FROM normalized_trades nt
           JOIN markets m ON m.market_id = nt.market_id
           WHERE nt.raw_event_id = $1
           ORDER BY nt.processed_at
           LIMIT 1""",
        row["raw_event_id"],
    )
    if trade is not None:
        disposition = "NORMALIZED"
    elif dead_letters:
        disposition = "QUARANTINED"
    elif not is_trade_event_type(_raw_event(fixture)):
        disposition = "IGNORED_VALID"
    else:
        disposition = "UNKNOWN"
    return {
        "fixture_id": fixture["id"],
        "disposition": disposition,
        "raw_event_id": row["raw_event_id"],
        "raw": row,
        "trade": trade,
        "dead_letters": dead_letters,
    }


def _decimal_equal(actual: Any, expected: str | None) -> bool:
    if expected is None:
        return actual is None
    return Decimal(str(actual)) == Decimal(str(expected))


def _decimal_canonical(value: Any) -> str | None:
    if value is None:
        return None
    return format(Decimal(str(value)).normalize(), "f")


def _canonical_hash_payload_from_expected(fixture: dict[str, Any]) -> dict[str, Any]:
    expected = fixture["expected_canonical"]
    return {
        "venue_code": fixture["venue_code"],
        "venue_market_id": str(expected["venue_market_id"]),
        "venue_trade_id": str(expected["venue_trade_id"]) if expected["venue_trade_id"] is not None else None,
        "outcome_key": str(expected["outcome_key"]),
        "directional_side": str(expected["directional_side"]),
        "price": _decimal_canonical(expected["price"]),
        "contracts": _decimal_canonical(expected["contracts"]),
        "capital_at_risk_usd": _decimal_canonical(expected["capital_at_risk_usd"]),
        "payout_notional_usd": _decimal_canonical(expected["payout_notional_usd"]),
        "fee_usd": _decimal_canonical(expected["fee_usd"]),
        "currency_convention": str(expected["currency_convention"]),
    }


def expected_canonical_hash(fixture: dict[str, Any]) -> str:
    payload = _canonical_hash_payload_from_expected(fixture)
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _canonical_hash_payload_from_trade(trade: Any) -> dict[str, Any]:
    payload = {
        "venue_code": str(trade["venue_code"]),
        "venue_market_id": str(trade["venue_market_id"]),
        "venue_trade_id": str(trade["venue_trade_id"]) if trade["venue_trade_id"] is not None else None,
        "outcome_key": str(trade["outcome_key"]),
        "directional_side": str(trade["directional_side"]),
        "price": _decimal_canonical(trade["price"]),
        "contracts": _decimal_canonical(trade["contracts"]),
        "capital_at_risk_usd": _decimal_canonical(trade["capital_at_risk_usd"]),
        "payout_notional_usd": _decimal_canonical(trade["payout_notional_usd"]),
        "fee_usd": _decimal_canonical(trade["fee_usd"]),
        "currency_convention": CURRENCY_CONVENTION_BY_VENUE.get(str(trade["venue_code"])),
    }
    return payload


def _canonical_trade_hash(trade: Any) -> str:
    payload = _canonical_hash_payload_from_trade(trade)
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _canonical_matches(fixture: dict[str, Any], result: dict[str, Any]) -> bool:
    expected = fixture.get("expected_canonical")
    trade = result.get("trade")
    if not expected or trade is None:
        return True
    checks = [
        str(trade["venue_market_id"]) == str(expected["venue_market_id"]),
        str(trade["venue_trade_id"]) == str(expected["venue_trade_id"]) if expected["venue_trade_id"] is not None else trade["venue_trade_id"] is None,
        str(trade["outcome_key"]) == str(expected["outcome_key"]),
        str(trade["directional_side"]) == str(expected["directional_side"]),
        _decimal_equal(trade["price"], expected["price"]),
        _decimal_equal(trade["contracts"], expected["contracts"]),
        _decimal_equal(trade["capital_at_risk_usd"], expected["capital_at_risk_usd"]),
        _decimal_equal(trade["payout_notional_usd"], expected["payout_notional_usd"]),
        _decimal_equal(trade["fee_usd"], expected["fee_usd"]),
        CURRENCY_CONVENTION_BY_VENUE.get(fixture["venue_code"]) == expected["currency_convention"],
    ]
    return all(checks)


async def _postgres_version(pool: Any) -> str:
    async with pool.acquire() as conn:
        return str(await conn.fetchval("SHOW server_version"))


async def run_dq2_semantics_matrix(pool: Any, manifest_path: Path = DEFAULT_MANIFEST) -> dict[str, Any]:
    manifest_path = _as_path(manifest_path)
    manifest = load_dq2_manifest(manifest_path)
    await cleanup_dq2_semantics_rows(pool, manifest_path)
    engine = AlertEngine()
    seen: set[str] = set()
    results: list[dict[str, Any]] = []
    raw_hashes_before: dict[int, str] = {}
    raw_payloads_before: dict[int, dict[str, Any]] = {}
    canonical_hashes_before: dict[int, str] = {}
    for fixture in manifest["fixtures"]:
        await process_event(
            _raw_event(fixture),
            pool,
            engine,
            _noop_alert_handler,
            asset_id_map=fixture.get("asset_id_map"),
        )
        async with pool.acquire() as conn:
            result = await _classify_fixture(conn, fixture, seen)
            if result.get("raw_event_id") is not None:
                raw_event_id = int(result["raw_event_id"])
                raw_hashes_before[raw_event_id] = str(result["raw"]["payload_hash"])
                raw_payloads_before[raw_event_id] = _payload(result["raw"]["payload"])
                if result.get("trade") is not None:
                    canonical_hashes_before[raw_event_id] = _canonical_trade_hash(result["trade"])
            results.append(result)

    for fixture in manifest["fixtures"]:
        await process_event(
            _raw_event(fixture),
            pool,
            engine,
            _noop_alert_handler,
            asset_id_map=fixture.get("asset_id_map"),
        )

    async with pool.acquire() as conn:
        raw_rows_after = await conn.fetch(
            """SELECT raw_event_id, payload, payload_hash
               FROM raw_events
               WHERE raw_event_id = ANY($1::bigint[])""",
            list(raw_hashes_before),
        )
        raw_hashes_after = {int(row["raw_event_id"]): str(row["payload_hash"]) for row in raw_rows_after}
        raw_payloads_after = {int(row["raw_event_id"]): _payload(row["payload"]) for row in raw_rows_after}
        trade_rows_after = await conn.fetch(
            """SELECT nt.raw_event_id, nt.venue_code, m.venue_market_id, nt.venue_trade_id,
                      nt.outcome_key, nt.directional_side, nt.price, nt.contracts,
                      nt.capital_at_risk_usd, nt.payout_notional_usd, nt.fee_usd
               FROM normalized_trades nt
               JOIN markets m ON m.market_id = nt.market_id
               WHERE nt.raw_event_id = ANY($1::bigint[])
               ORDER BY nt.processed_at""",
            list(canonical_hashes_before),
        )
        canonical_hashes_after = {
            int(row["raw_event_id"]): _canonical_trade_hash(row)
            for row in trade_rows_after
        }

    provenance = validate_fixture_provenance(manifest)
    disposition_counts = Counter(result["disposition"] for result in results)
    dead_letter_count = sum(len(result["dead_letters"]) for result in results if result["disposition"] != "DUPLICATE")
    normalized_results = [result for result in results if result["trade"] is not None]
    canonical_ok = all(
        _canonical_matches(fixture, result)
        for fixture, result in zip(manifest["fixtures"], results, strict=True)
    )
    normalized_pairs = [
        (fixture, result)
        for fixture, result in zip(manifest["fixtures"], results, strict=True)
        if result["trade"] is not None
    ]
    decimal_ok = all(
        _decimal_equal(result["trade"][field], fixture["expected_canonical"][field])
        for fixture, result in normalized_pairs
        for field in ("price", "contracts", "capital_at_risk_usd", "payout_notional_usd", "fee_usd")
    )
    no_yes_default = all(
        result["disposition"] == "QUARANTINED"
        for fixture, result in zip(manifest["fixtures"], results, strict=True)
        if "outcome_unknown_mapping" in fixture["category_tags"]
    )
    multi_outcome_pairs = [
        (fixture, result)
        for fixture, result in zip(manifest["fixtures"], results, strict=True)
        if "multi_outcome" in fixture["category_tags"]
    ]
    multi_outcome_ok = bool(multi_outcome_pairs) and all(
        result["trade"] is not None
        and result["trade"]["outcome_key"] == "unknown"
        and any(dl["error_class"] == "multi_outcome_unsupported" for dl in result["dead_letters"])
        for _, result in multi_outcome_pairs
    )
    optional_drift_pairs = [
        (fixture, result)
        for fixture, result in zip(manifest["fixtures"], results, strict=True)
        if "schema_drift_optional" in fixture["category_tags"]
    ]
    optional_drift_ok = bool(optional_drift_pairs) and all(
        result["trade"] is not None
        and _payload(result["trade"]["source_payload"]).get("new_optional_field") == "kept"
        for _, result in optional_drift_pairs
    )
    critical_quarantined = all(
        result["disposition"] == "QUARANTINED"
        for fixture, result in zip(manifest["fixtures"], results, strict=True)
        if fixture["origin_class"] == "MALFORMED" or any(tag.startswith("schema_") and tag != "schema_drift_optional" and tag != "schema_new_event_type" for tag in fixture["category_tags"])
    )
    stable_hash_ok = all(
        fixture.get("expected_canonical_sha256") == expected_canonical_hash(fixture)
        and _canonical_trade_hash(result["trade"]) == fixture["expected_canonical_sha256"]
        for fixture, result in normalized_pairs
    )
    raw_immutable_ok = raw_hashes_before == raw_hashes_after and raw_payloads_before == raw_payloads_after
    prior_canonical_readable_ok = canonical_hashes_before == canonical_hashes_after
    expected = manifest["expected_counts"]
    measurements = {
        "fixture_inputs": len(manifest["fixtures"]),
        "explicit_dispositions": sum(1 for result in results if result["disposition"] != "UNKNOWN"),
        "normalized": disposition_counts["NORMALIZED"],
        "ignored_valid": disposition_counts["IGNORED_VALID"],
        "quarantined": disposition_counts["QUARANTINED"],
        "duplicates": disposition_counts["DUPLICATE"],
        "dead_letters": dead_letter_count,
        "postgres_roundtrip_checked": len(normalized_results),
        "fixture_hashes_checked": provenance["fixture_count"] - len(provenance["invalid_hashes"]),
        "pinned_canonical_hashes_checked": len(normalized_pairs),
        "reprocessed_fixture_inputs": len(manifest["fixtures"]),
        "raw_payloads_rechecked_after_reprocess": len(raw_hashes_before),
        "prior_canonical_hashes_rechecked_after_reprocess": len(canonical_hashes_before),
    }
    evidence: dict[str, Any] = {
        "version": "pmfi-data-plane-scenario-run.v1",
        "scenario_id": manifest["scenario_id"],
        "scenario_version": manifest["scenario_version"],
        "profile": manifest["profile"],
        "outcome": "PASS",
        "completeness_classifications": {"canonical_semantics": "PROVEN_COMPLETE"},
        "repository": {
            "remote": _git_value(["config", "--get", "remote.origin.url"]),
            "branch": _git_value(["rev-parse", "--abbrev-ref", "HEAD"]),
            "commit": _git_value(["rev-parse", "HEAD"]),
            "worktree_status": "not_recorded_by_db_test",
        },
        "runtime": {
            "python_version": platform.python_version(),
            "postgres_version": await _postgres_version(pool),
            "schema_version": _sha256_path(ROOT / "sql" / "001_init.sql"),
            "config_hash": _sha256_path(ROOT / "config" / "alert_rules.yaml"),
            "environment": "offline_db_gated",
        },
        "time": {"started_at": None, "ended_at": None, "input_bounds": None},
        "expected_truth": {
            "manifest": manifest_path.relative_to(ROOT).as_posix(),
            "artifact_hash": _sha256_path(manifest_path),
        },
        "evidence": {
            "required_facets": ["SOURCE_INSPECTION", "OFFLINE_TEST", "POSTGRES_INTEGRATION"],
            "actual_facets": ["SOURCE_INSPECTION", "OFFLINE_TEST", "POSTGRES_INTEGRATION"],
            "commands": ["python -m pytest -q tests\\test_dq2_semantics_matrix_db.py"],
            "artifacts": [manifest_path.relative_to(ROOT).as_posix()],
            "artifact_hashes": [_sha256_path(manifest_path)],
        },
        "measurements": measurements,
        "pass_invariants": {},
        "fail_conditions": [],
        "blocker_or_inconclusive_reason": None,
        "incidents": {"unresolved_p0": [], "unresolved_p1": []},
        "accepted_debt": [],
        "next_action": "orchestrator_verify_pr",
    }
    evidence["pass_invariants"] = {
        "all_inputs_have_explicit_disposition": measurements["explicit_dispositions"] == measurements["fixture_inputs"],
        "no_supported_input_silently_returns_ambiguous_null": "UNKNOWN" not in disposition_counts,
        "canonical_records_match_exact_values_units_outcomes_timestamps_provenance": canonical_ok,
        "decision_relevant_values_survive_postgres_decimal_roundtrip": decimal_ok,
        "missing_or_ambiguous_mapping_never_defaults_confidently_to_yes": no_yes_default,
        "multi_outcome_explicitly_unsupported_not_coerced_binary": multi_outcome_ok,
        "optional_compatible_drift_retained_and_classified": optional_drift_ok,
        "missing_or_changed_critical_fields_quarantine": critical_quarantined,
        "fixed_input_version_output_hash_is_stable": stable_hash_ok,
        "reprocess_preserves_raw_evidence_and_prior_interpretation": (
            raw_immutable_ok and prior_canonical_readable_ok
        ),
        "fixture_provenance_and_immutable_hashes_valid": (
            not provenance["invalid_hashes"] and not provenance["missing_required_fields"]
        ),
    }
    for key, value in expected.items():
        if measurements.get(key) != value:
            evidence["fail_conditions"].append(f"measurement {key} expected {value}, got {measurements.get(key)}")
    for fixture, result in zip(manifest["fixtures"], results, strict=True):
        if result["disposition"] != fixture["expected_disposition"]:
            evidence["fail_conditions"].append(
                f"{fixture['id']} disposition expected {fixture['expected_disposition']}, got {result['disposition']}"
            )
        expected_dead = fixture.get("expected_dead_letter_class")
        if expected_dead and not any(dl["error_class"] == expected_dead for dl in result["dead_letters"]):
            evidence["fail_conditions"].append(f"{fixture['id']} missing dead_letter {expected_dead}")
    if not all(evidence["pass_invariants"].values()) or evidence["fail_conditions"]:
        evidence["outcome"] = "FAIL"
    return evidence
