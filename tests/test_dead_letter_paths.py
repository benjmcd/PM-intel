"""Dead-letter path assertion tests (US-13 Part B).

Asserts that process_event calls insert_dead_letter with the correct
error_class for each dead-letter-producing code path.

All tests are offline — no asyncpg, no DB, no network.
Mocking pattern mirrors test_unknown_market_guard.py.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from pmfi.domain import RawEvent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _poly_event(
    *,
    asset_id: str | None = None,
    market: str | None = None,
    outcome: str | None = None,
    price: str = "0.55",
    size: str = "100",
    side: str = "BUY",
) -> RawEvent:
    payload: dict = {"price": price, "size": size, "side": side}
    if asset_id is not None:
        payload["asset_id"] = asset_id
    if market is not None:
        payload["market"] = market
    if outcome is not None:
        payload["outcome"] = outcome
    return RawEvent(
        venue_code="polymarket",
        source_channel="ws_clob",
        source_event_type="last_trade_price",
        payload=payload,
        venue_market_id=market,
    )


def _make_pool(conn: AsyncMock) -> MagicMock:
    pool = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return pool


def _run_process_event(raw: RawEvent, *, asset_id_map=None) -> tuple[list[dict], MagicMock]:
    """Run process_event offline and return (dead_letter_call_kwargs_list, mock_insert_trade)."""
    from pmfi.pipeline.runner import process_event

    mock_conn = AsyncMock()
    mock_pool = _make_pool(mock_conn)
    mock_engine = MagicMock()
    mock_engine.evaluate.return_value = []

    dead_letter_calls: list[dict] = []

    async def _capture_dead_letter(conn, **kwargs):
        dead_letter_calls.append(kwargs)

    with (
        patch("pmfi.pipeline.runner.insert_raw_event", new=AsyncMock(return_value=("raw-dl-1", False))),
        patch("pmfi.pipeline.runner.insert_dead_letter", side_effect=_capture_dead_letter),
        patch("pmfi.pipeline.runner.insert_trade") as mock_trade,
        patch("pmfi.pipeline.runner.upsert_market", new=AsyncMock(return_value="mkt-dl-1")),
        patch("pmfi.pipeline.runner.upsert_metric_window", new=AsyncMock()),
    ):
        asyncio.run(process_event(raw, mock_pool, mock_engine, AsyncMock(), asset_id_map=asset_id_map))

    return dead_letter_calls, mock_trade


# ---------------------------------------------------------------------------
# 1. asset_map_not_loaded
# ---------------------------------------------------------------------------

def test_dead_letter_asset_map_not_loaded_when_map_is_none():
    """Polymarket event with asset_id + no market + map=None -> error_class='asset_map_not_loaded'."""
    raw = _poly_event(asset_id="token_xyz")
    dl_calls, mock_trade = _run_process_event(raw, asset_id_map=None)

    assert len(dl_calls) == 1, f"expected 1 dead_letter, got {len(dl_calls)}"
    assert dl_calls[0]["error_class"] == "asset_map_not_loaded"
    assert dl_calls[0]["failure_stage"] == "normalization"
    assert dl_calls[0]["venue_code"] == "polymarket"
    assert "token_xyz" in dl_calls[0]["error_message"]
    assert mock_trade.call_count == 0, "insert_trade must NOT be called"


def test_dead_letter_asset_map_not_loaded_when_map_is_empty():
    """Same guard fires when asset_id_map={} (empty dict, falsy)."""
    raw = _poly_event(asset_id="token_abc")
    dl_calls, mock_trade = _run_process_event(raw, asset_id_map={})

    assert len(dl_calls) == 1
    assert dl_calls[0]["error_class"] == "asset_map_not_loaded"
    assert mock_trade.call_count == 0


def test_no_dead_letter_when_event_has_market_field_and_no_map():
    """Event with a 'market' field already set bypasses asset_map guard entirely."""
    raw = _poly_event(asset_id="token_xyz", market="condition_known", outcome="yes")
    dl_calls, _ = _run_process_event(raw, asset_id_map=None)

    # Guard must NOT fire for this event (it has a market field)
    asset_map_errors = [d for d in dl_calls if d.get("error_class") == "asset_map_not_loaded"]
    assert len(asset_map_errors) == 0


# ---------------------------------------------------------------------------
# 2. missing_asset_mapping
# ---------------------------------------------------------------------------

def test_dead_letter_missing_asset_mapping_when_asset_id_not_in_map():
    """asset_id present but not in a non-empty map -> error_class='missing_asset_mapping'."""
    asset_map = {
        "token_known": {
            "venue_market_id": "condition_xyz",
            "venue_code": "polymarket",
            "market_id": "00000000-0000-0000-0000-000000000001",
            "outcome_key": "yes",
            "outcome_label": "Yes",
            "is_binary": True,
        }
    }
    raw = _poly_event(asset_id="token_not_in_map")
    dl_calls, mock_trade = _run_process_event(raw, asset_id_map=asset_map)

    assert len(dl_calls) == 1
    assert dl_calls[0]["error_class"] == "missing_asset_mapping"
    assert dl_calls[0]["failure_stage"] == "normalization"
    assert dl_calls[0]["venue_code"] == "polymarket"
    assert "token_not_in_map" in dl_calls[0]["error_message"]
    assert mock_trade.call_count == 0, "insert_trade must NOT be called for missing mapping"


def test_no_missing_mapping_when_asset_id_resolved():
    """When asset_id IS in the map, no missing_asset_mapping dead_letter is written."""
    asset_map = {
        "token_yes_abc": {
            "venue_market_id": "condition_xyz",
            "venue_code": "polymarket",
            "market_id": "00000000-0000-0000-0000-000000000001",
            "outcome_key": "yes",
            "outcome_label": "Yes",
            "is_binary": True,
        }
    }
    raw = _poly_event(asset_id="token_yes_abc")

    from pmfi.pipeline.runner import process_event

    mock_conn = AsyncMock()
    mock_pool = _make_pool(mock_conn)
    mock_engine = MagicMock()
    mock_trade_obj = MagicMock()
    mock_trade_obj.venue_code = "polymarket"
    mock_trade_obj.venue_market_id = "condition_xyz"
    mock_trade_obj.outcome_key = "yes"
    mock_trade_obj.capital_at_risk_usd = Decimal("500")
    mock_engine.evaluate.return_value = []

    dl_calls: list[dict] = []

    async def _capture_dead_letter(conn, **kwargs):
        dl_calls.append(kwargs)

    with (
        patch("pmfi.pipeline.runner.insert_raw_event", new=AsyncMock(return_value=("raw-dl-2", False))),
        patch("pmfi.pipeline.runner.insert_dead_letter", side_effect=_capture_dead_letter),
        patch("pmfi.pipeline.runner.normalize_event", return_value=mock_trade_obj),
        patch("pmfi.pipeline.runner.upsert_market", new=AsyncMock(return_value="mkt-dl-2")),
        patch("pmfi.pipeline.runner.insert_trade", new=AsyncMock(return_value="trade-dl-2")),
        patch("pmfi.pipeline.runner.upsert_metric_window", new=AsyncMock()),
    ):
        asyncio.run(process_event(raw, mock_pool, mock_engine, AsyncMock(), asset_id_map=asset_map))

    missing_errors = [d for d in dl_calls if d.get("error_class") == "missing_asset_mapping"]
    assert len(missing_errors) == 0, f"no missing_asset_mapping expected, got {missing_errors}"


# ---------------------------------------------------------------------------
# 3. NormalizationError -> error_class routing
# ---------------------------------------------------------------------------

def _run_with_norm_error(error_message: str) -> tuple[list[dict], MagicMock]:
    """Run process_event with normalize_event raising NormalizationError(error_message)."""
    from pmfi.pipeline.runner import process_event
    from pmfi.normalization import NormalizationError

    # Use an event that has a 'market' field so it bypasses asset_map guards
    raw = RawEvent(
        venue_code="polymarket",
        source_channel="ws_clob",
        source_event_type="last_trade_price",
        payload={"market": "cond_norm_err", "outcome": "yes", "price": "bad", "size": "10", "side": "BUY"},
        venue_market_id="cond_norm_err",
    )

    mock_conn = AsyncMock()
    mock_pool = _make_pool(mock_conn)
    mock_engine = MagicMock()
    mock_engine.evaluate.return_value = []

    dl_calls: list[dict] = []

    async def _capture_dead_letter(conn, **kwargs):
        dl_calls.append(kwargs)

    with (
        patch("pmfi.pipeline.runner.insert_raw_event", new=AsyncMock(return_value=("raw-dl-3", False))),
        patch("pmfi.pipeline.runner.insert_dead_letter", side_effect=_capture_dead_letter),
        patch("pmfi.pipeline.runner.normalize_event", side_effect=NormalizationError(error_message)),
        patch("pmfi.pipeline.runner.insert_trade") as mock_trade,
        patch("pmfi.pipeline.runner.upsert_market", new=AsyncMock(return_value="mkt-dl-3")),
        patch("pmfi.pipeline.runner.upsert_metric_window", new=AsyncMock()),
    ):
        asyncio.run(process_event(raw, mock_pool, mock_engine, AsyncMock(), asset_id_map=None))

    return dl_calls, mock_trade


def test_dead_letter_invalid_price_or_size_for_price_error():
    """NormalizationError containing 'price' -> error_class='invalid_price_or_size'."""
    dl_calls, mock_trade = _run_with_norm_error("price must be between 0 and 1, got 99")

    assert len(dl_calls) == 1
    assert dl_calls[0]["error_class"] == "invalid_price_or_size"
    assert dl_calls[0]["failure_stage"] == "normalization"
    assert mock_trade.call_count == 0


def test_dead_letter_invalid_price_or_size_for_size_error():
    """NormalizationError containing 'size' -> error_class='invalid_price_or_size'."""
    dl_calls, mock_trade = _run_with_norm_error("invalid size value: -5")

    assert len(dl_calls) == 1
    assert dl_calls[0]["error_class"] == "invalid_price_or_size"
    assert mock_trade.call_count == 0


def test_dead_letter_payload_schema_mismatch_for_timestamp_error():
    """NormalizationError containing 'timestamp' -> error_class='payload_schema_mismatch'."""
    dl_calls, mock_trade = _run_with_norm_error("invalid timestamp: 'not-a-ts'")

    assert len(dl_calls) == 1
    assert dl_calls[0]["error_class"] == "payload_schema_mismatch"
    assert mock_trade.call_count == 0


def test_dead_letter_payload_schema_mismatch_for_decimal_error():
    """NormalizationError containing 'decimal' -> error_class='payload_schema_mismatch'."""
    dl_calls, mock_trade = _run_with_norm_error("invalid decimal for field_x: 'abc'")

    assert len(dl_calls) == 1
    assert dl_calls[0]["error_class"] == "payload_schema_mismatch"
    assert mock_trade.call_count == 0


def test_dead_letter_normalization_error_fallback():
    """NormalizationError with unrecognized message -> error_class='normalization_error'."""
    dl_calls, mock_trade = _run_with_norm_error("something completely unexpected went wrong")

    assert len(dl_calls) == 1
    assert dl_calls[0]["error_class"] == "normalization_error"
    assert mock_trade.call_count == 0


def test_dead_letter_normalization_error_for_count():
    """NormalizationError containing 'count' -> error_class='invalid_count'."""
    dl_calls, mock_trade = _run_with_norm_error("invalid count value: xyz")

    assert len(dl_calls) == 1
    assert dl_calls[0]["error_class"] == "invalid_count"


def test_dead_letter_normalization_error_for_contracts():
    """NormalizationError containing 'contracts' -> error_class='invalid_count'."""
    dl_calls, mock_trade = _run_with_norm_error("contracts must be positive, got -1")

    assert len(dl_calls) == 1
    assert dl_calls[0]["error_class"] == "invalid_count"


def test_dead_letter_normalization_error_for_invalid_keyword():
    """NormalizationError containing 'invalid' -> error_class='payload_schema_mismatch'."""
    dl_calls, mock_trade = _run_with_norm_error("invalid field format")

    assert len(dl_calls) == 1
    assert dl_calls[0]["error_class"] == "payload_schema_mismatch"


# ---------------------------------------------------------------------------
# 4. Verify error_message is preserved in dead_letter payload
# ---------------------------------------------------------------------------

def test_dead_letter_error_message_preserved():
    """The exact NormalizationError message is stored in the dead_letter."""
    specific_msg = "price must be between 0 and 1, got 1.5 for field price"
    dl_calls, _ = _run_with_norm_error(specific_msg)

    assert len(dl_calls) == 1
    assert dl_calls[0]["error_message"] == specific_msg


# ---------------------------------------------------------------------------
# 5. Payload is included in dead_letter call
# ---------------------------------------------------------------------------

def test_dead_letter_includes_payload():
    """insert_dead_letter must be called with the original event payload."""
    raw = _poly_event(asset_id="token_xyz")
    dl_calls, _ = _run_process_event(raw, asset_id_map=None)

    assert len(dl_calls) == 1
    assert "payload" in dl_calls[0]
    assert isinstance(dl_calls[0]["payload"], dict)
    assert dl_calls[0]["payload"].get("asset_id") == "token_xyz"


def test_trade_event_returning_none_writes_dead_letter():
    """A trade-type frame must not vanish if a venue normalizer returns None."""
    from pmfi.pipeline.runner import process_event

    raw = RawEvent(
        venue_code="kalshi",
        source_channel="rest_trades",
        source_event_type="trade",
        source_event_id="fractional-count-trade",
        venue_market_id="KXWCGAME-26JUN18MEXKOR-MEX",
        payload={
            "ticker": "KXWCGAME-26JUN18MEXKOR-MEX",
            "trade_id": "fractional-count-trade",
            "count_fp": "201.01",
            "taker_side": "yes",
            "yes_price_dollars": "0.4800",
            "no_price_dollars": "0.5200",
        },
    )
    mock_conn = AsyncMock()
    mock_pool = _make_pool(mock_conn)
    mock_engine = MagicMock()
    mock_engine.evaluate.return_value = []
    dl_calls: list[dict] = []

    async def _capture_dead_letter(conn, **kwargs):
        dl_calls.append(kwargs)

    with (
        patch("pmfi.pipeline.runner.insert_raw_event", new=AsyncMock(return_value=("raw-kalshi-none", False))),
        patch("pmfi.pipeline.runner.normalize_event", return_value=None),
        patch("pmfi.pipeline.runner.insert_dead_letter", side_effect=_capture_dead_letter),
        patch("pmfi.pipeline.runner.insert_trade") as mock_trade,
        patch("pmfi.pipeline.runner.upsert_market", new=AsyncMock(return_value="mkt-kalshi-none")),
        patch("pmfi.pipeline.runner.upsert_metric_window", new=AsyncMock()),
    ):
        asyncio.run(process_event(raw, mock_pool, mock_engine, AsyncMock()))

    assert len(dl_calls) == 1
    assert dl_calls[0]["raw_event_id"] == "raw-kalshi-none"
    assert dl_calls[0]["venue_code"] == "kalshi"
    assert dl_calls[0]["failure_stage"] == "normalization"
    assert dl_calls[0]["error_class"] == "trade_normalization_failed"
    assert "trade-type" in dl_calls[0]["error_message"]
    assert mock_trade.call_count == 0


def test_kalshi_non_numeric_count_fp_writes_invalid_count_dead_letter():
    """Unparseable Kalshi count_fp trades are accounted as invalid-count dead letters."""
    from pmfi.pipeline.runner import process_event

    raw = RawEvent(
        venue_code="kalshi",
        source_channel="rest_trades",
        source_event_type="trade",
        source_event_id="bad-count-trade",
        venue_market_id="KXWCGAME-26JUN18MEXKOR-MEX",
        payload={
            "ticker": "KXWCGAME-26JUN18MEXKOR-MEX",
            "trade_id": "bad-count-trade",
            "count_fp": "not-a-count",
            "taker_side": "yes",
            "yes_price_dollars": "0.4800",
            "no_price_dollars": "0.5200",
        },
    )
    mock_conn = AsyncMock()
    mock_pool = _make_pool(mock_conn)
    mock_engine = MagicMock()
    mock_engine.evaluate.return_value = []
    dl_calls: list[dict] = []

    async def _capture_dead_letter(conn, **kwargs):
        dl_calls.append(kwargs)

    with (
        patch("pmfi.pipeline.runner.insert_raw_event", new=AsyncMock(return_value=("raw-kalshi-bad-count", False))),
        patch("pmfi.pipeline.runner.insert_dead_letter", side_effect=_capture_dead_letter),
        patch("pmfi.pipeline.runner.insert_trade") as mock_trade,
        patch("pmfi.pipeline.runner.upsert_market", new=AsyncMock(return_value="mkt-kalshi-bad-count")),
        patch("pmfi.pipeline.runner.upsert_metric_window", new=AsyncMock()),
    ):
        asyncio.run(process_event(raw, mock_pool, mock_engine, AsyncMock()))

    assert len(dl_calls) == 1
    assert dl_calls[0]["raw_event_id"] == "raw-kalshi-bad-count"
    assert dl_calls[0]["venue_code"] == "kalshi"
    assert dl_calls[0]["failure_stage"] == "normalization"
    assert dl_calls[0]["error_class"] == "invalid_count"
    assert "not-a-count" in dl_calls[0]["error_message"]
    assert mock_trade.call_count == 0


def test_non_trade_event_returning_none_remains_silent_skip():
    """Non-trade frames may still return None without a dead letter."""
    from pmfi.pipeline.runner import process_event

    raw = RawEvent(
        venue_code="polymarket",
        source_channel="market_ws",
        source_event_type="price_change",
        source_event_id="pm-price-change",
        venue_market_id="condition-real",
        payload={"market": "condition-real", "price": "0.52"},
    )
    mock_conn = AsyncMock()
    mock_pool = _make_pool(mock_conn)
    mock_engine = MagicMock()
    mock_engine.evaluate.return_value = []

    with (
        patch("pmfi.pipeline.runner.insert_raw_event", new=AsyncMock(return_value=("raw-nontrade", False))),
        patch("pmfi.pipeline.runner.normalize_event", return_value=None),
        patch("pmfi.pipeline.runner.insert_dead_letter") as mock_dead_letter,
        patch("pmfi.pipeline.runner.insert_trade") as mock_trade,
    ):
        asyncio.run(process_event(raw, mock_pool, mock_engine, AsyncMock()))

    mock_dead_letter.assert_not_called()
    mock_trade.assert_not_called()
