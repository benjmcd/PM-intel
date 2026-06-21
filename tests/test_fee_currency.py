from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal

from pmfi.domain import NormalizedTrade, RawEvent
from pmfi.normalization import (
    CURRENCY_CONVENTION_BY_VENUE,
    normalize_kalshi_fixture,
    normalize_polymarket_fixture,
)


def _raw(venue_code: str, payload: dict) -> RawEvent:
    return RawEvent(
        venue_code=venue_code,  # type: ignore[arg-type]
        source_channel="test",
        source_event_type="trade",
        source_event_id=f"{venue_code}-fee-1",
        venue_market_id=f"{venue_code}-market",
        exchange_ts=datetime.now(timezone.utc),
        payload=payload,
    )


def test_fee_usd_only_populates_when_payload_supplies_fee() -> None:
    no_fee = normalize_polymarket_fixture(
        _raw(
            "polymarket",
            {"market": "pm-fee", "price": "0.55", "size": "10", "side": "buy", "outcome": "yes"},
        )
    )
    with_fee = normalize_kalshi_fixture(
        _raw(
            "kalshi",
            {
                "ticker": "KX-FEE",
                "yes_no": "yes",
                "taker_side": "buy",
                "yes_price_dollars": "0.42",
                "count_fp": "100.5",
                "fee_usd": "0.37",
            },
        )
    )

    assert no_fee.fee_usd is None
    assert with_fee.fee_usd == Decimal("0.37")


def test_supported_venue_currency_conventions_are_labeled() -> None:
    assert CURRENCY_CONVENTION_BY_VENUE == {"polymarket": "USD", "kalshi": "USD"}


def test_insert_trade_persists_fee_usd_when_present() -> None:
    from pmfi.db.repos.trades import insert_trade

    class _Tx:
        async def __aenter__(self):
            return None

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class _Conn:
        def __init__(self) -> None:
            self.normalized_trade_query = ""
            self.normalized_trade_args: tuple = ()

        def transaction(self):
            return _Tx()

        async def fetchrow(self, query: str, *args):
            if "normalized_trade_dedupe_keys" in query:
                return {"dedupe_id": "dedupe-fee"}
            if "normalized_trades" in query:
                self.normalized_trade_query = query
                self.normalized_trade_args = args
                return {"trade_id": "trade-fee"}
            raise AssertionError(f"unexpected query: {query}")

        async def execute(self, *_args, **_kwargs):
            return None

    trade = NormalizedTrade(
        venue_code="kalshi",
        venue_market_id="KX-FEE",
        outcome_key="yes",
        price=Decimal("0.42"),
        contracts=Decimal("100.5"),
        capital_at_risk_usd=Decimal("42.21"),
        payout_notional_usd=Decimal("100.5"),
        directional_side="yes",
        aggressor_side="buy",
        side_confidence="medium",
        venue_trade_id="fee-trade-1",
        exchange_ts=datetime.now(timezone.utc),
        fee_usd=Decimal("0.37"),
        source_payload={"fee_usd": "0.37"},
    )
    conn = _Conn()

    asyncio.run(insert_trade(conn, trade, raw_event_id=1, market_id="11111111-1111-1111-1111-111111111111"))  # type: ignore[arg-type]

    assert "fee_usd" in conn.normalized_trade_query
    assert Decimal("0.37") in conn.normalized_trade_args
