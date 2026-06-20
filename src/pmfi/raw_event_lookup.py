from __future__ import annotations

import json
from decimal import Decimal
from typing import Any


RAW_EVENT_LOOKUP_SQL = """
SELECT r.raw_event_id,
       r.venue_code,
       r.source_channel,
       r.source_event_type,
       r.source_event_id,
       r.venue_market_id,
       r.exchange_ts,
       r.received_at,
       r.parser_version,
       r.payload_hash,
       LEFT(r.payload::text, 240) AS payload_preview,
       CASE WHEN $2::boolean THEN r.payload ELSE NULL END AS payload,
       m.title AS market_title,
       t.trade_id::text AS trade_id,
       t.venue_trade_id AS venue_trade_id,
       t.outcome_key AS outcome_key,
       t.directional_side AS directional_side,
       t.price AS price,
       t.contracts AS contracts,
       t.capital_at_risk_usd AS capital_at_risk_usd,
       t.payout_notional_usd AS payout_notional_usd,
       t.normalization_version AS normalization_version,
       t.warnings AS warnings
FROM raw_events r
LEFT JOIN markets m ON m.market_id = r.market_id
LEFT JOIN normalized_trades t ON t.raw_event_id = r.raw_event_id
WHERE r.raw_event_id = ANY($1::bigint[])
ORDER BY r.raw_event_id
"""


def row_get(row: Any, key: str, default: Any = None) -> Any:
    try:
        value = row[key]
    except (KeyError, TypeError):
        return default
    return default if value is None else value


def json_value(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def _json_payload_value(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def raw_event_json_row(row: Any, *, include_payload: bool) -> dict[str, Any]:
    payload = {
        "raw_event_id": row_get(row, "raw_event_id"),
        "venue_code": row_get(row, "venue_code"),
        "source_channel": row_get(row, "source_channel"),
        "source_event_type": row_get(row, "source_event_type"),
        "source_event_id": row_get(row, "source_event_id"),
        "venue_market_id": row_get(row, "venue_market_id"),
        "market_title": row_get(row, "market_title"),
        "exchange_ts": json_value(row_get(row, "exchange_ts")),
        "received_at": json_value(row_get(row, "received_at")),
        "parser_version": row_get(row, "parser_version"),
        "payload_hash": row_get(row, "payload_hash"),
        "payload_preview": row_get(row, "payload_preview"),
        "trade": {
            "trade_id": row_get(row, "trade_id"),
            "venue_trade_id": row_get(row, "venue_trade_id"),
            "outcome_key": row_get(row, "outcome_key"),
            "directional_side": row_get(row, "directional_side"),
            "price": json_value(row_get(row, "price")),
            "contracts": json_value(row_get(row, "contracts")),
            "capital_at_risk_usd": json_value(row_get(row, "capital_at_risk_usd")),
            "payout_notional_usd": json_value(row_get(row, "payout_notional_usd")),
            "normalization_version": row_get(row, "normalization_version"),
            "warnings": row_get(row, "warnings", []),
        },
    }
    if include_payload:
        payload["payload"] = _json_payload_value(row_get(row, "payload"))
    return payload


async def fetch_raw_event_lookup_rows(
    pool: Any,
    raw_event_ids: list[int],
    *,
    include_payload: bool,
) -> list[Any]:
    return list(
        await pool.fetch(
            RAW_EVENT_LOOKUP_SQL,
            raw_event_ids,
            include_payload,
        )
    )


def build_raw_event_lookup_result(
    raw_event_ids: list[int],
    rows: list[Any],
    *,
    include_payload: bool,
) -> dict[str, Any]:
    found_ids = {int(row_get(row, "raw_event_id")) for row in rows}
    missing_ids = [value for value in raw_event_ids if value not in found_ids]
    return {
        "schema_version": "raw_event_lookup.v1",
        "local_only": True,
        "read_only": True,
        "config_mutation": False,
        "db_mutation": False,
        "live_calls": False,
        "requested_raw_event_ids": raw_event_ids,
        "found_count": len(rows),
        "missing_raw_event_ids": missing_ids,
        "include_payload": include_payload,
        "rows": [
            raw_event_json_row(row, include_payload=include_payload)
            for row in rows
        ],
    }


async def query_raw_event_lookup(
    database_url: str,
    raw_event_ids: list[int],
    *,
    include_payload: bool,
) -> dict[str, Any]:
    import asyncpg

    pool = await asyncpg.create_pool(
        database_url,
        min_size=1,
        max_size=1,
        server_settings={"search_path": "pmfi,public"},
    )
    try:
        rows = await fetch_raw_event_lookup_rows(
            pool,
            raw_event_ids,
            include_payload=include_payload,
        )
    finally:
        await pool.close()
    return build_raw_event_lookup_result(
        raw_event_ids,
        rows,
        include_payload=include_payload,
    )
