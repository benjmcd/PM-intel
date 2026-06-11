"""Tests for hardened FileDelivery and HttpDelivery behavior."""
from __future__ import annotations
import asyncio
import logging
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pmfi.domain import AlertDecision
from pmfi.delivery.file import FileDelivery
from pmfi.delivery.http import HttpDelivery, _MAX_ATTEMPTS


def _make_alert() -> AlertDecision:
    return AlertDecision(
        emit_alert=True,
        rule_id="test_rule_v1",
        rule_version="alert_rules.v1",
        severity="high",
        confidence="medium",
        score=Decimal("1.0"),
        reason_codes=("some_code",),
        evidence={"k": "v"},
        data_quality="unverified",
    )


# ---------------------------------------------------------------------------
# FileDelivery – OSError is swallowed and logged, not raised
# ---------------------------------------------------------------------------

def test_file_delivery_oserror_is_swallowed(tmp_path, caplog):
    fd = FileDelivery(tmp_path)
    decision = _make_alert()
    with patch("builtins.open", side_effect=OSError("disk full")):
        with caplog.at_level(logging.WARNING, logger="pmfi.delivery.file"):
            asyncio.run(fd.deliver(decision, venue_code="polymarket", market_id="m1"))
    assert "non-fatal" in caplog.text.lower() or "write failed" in caplog.text.lower()


def test_file_delivery_oserror_does_not_raise(tmp_path):
    fd = FileDelivery(tmp_path)
    decision = _make_alert()
    with patch("builtins.open", side_effect=OSError("permission denied")):
        # Must not raise
        asyncio.run(fd.deliver(decision, venue_code="polymarket"))


# ---------------------------------------------------------------------------
# FileDelivery – rotation at size threshold
# ---------------------------------------------------------------------------

def test_file_delivery_rotation_creates_new_file(tmp_path):
    """When the base file is at or above max_file_size_mb, deliver writes to .1 file."""
    fd = FileDelivery(tmp_path, max_file_size_mb=0.0)  # 0 bytes cap -> always rotate
    decision = _make_alert()

    # First write – base file does not exist yet, so it is created normally.
    asyncio.run(fd.deliver(decision, venue_code="polymarket", market_id="m1"))
    base_files = list(tmp_path.glob("alerts_*.jsonl"))
    # After first write the base file exists but cap is 0, so next call rotates.

    # Second write should go to the .1 file because base file now >= 0 bytes.
    asyncio.run(fd.deliver(decision, venue_code="polymarket", market_id="m1"))

    rollover_files = [f for f in tmp_path.glob("alerts_*.jsonl") if ".1." in f.name]
    assert len(rollover_files) == 1, (
        f"Expected one .1 rollover file, got: {[f.name for f in tmp_path.glob('alerts_*.jsonl')]}"
    )


def test_file_delivery_rotation_continues_incrementing(tmp_path):
    """Rotation increments: base -> .1 -> .2 when size cap is zero."""
    fd = FileDelivery(tmp_path, max_file_size_mb=0.0)
    decision = _make_alert()

    asyncio.run(fd.deliver(decision, venue_code="polymarket"))
    asyncio.run(fd.deliver(decision, venue_code="polymarket"))
    asyncio.run(fd.deliver(decision, venue_code="polymarket"))

    all_files = sorted(tmp_path.glob("alerts_*.jsonl"), key=lambda p: p.name)
    names = [f.name for f in all_files]
    # Expect base, .1, .2
    assert len(all_files) == 3, f"Expected 3 files, got: {names}"


def test_file_delivery_no_rotation_under_cap(tmp_path):
    """Under the cap everything goes into the single base file."""
    fd = FileDelivery(tmp_path, max_file_size_mb=100.0)
    decision = _make_alert()

    for _ in range(5):
        asyncio.run(fd.deliver(decision, venue_code="polymarket"))

    all_files = list(tmp_path.glob("alerts_*.jsonl"))
    assert len(all_files) == 1
    lines = all_files[0].read_text(encoding="utf-8").splitlines()
    assert len(lines) == 5


# ---------------------------------------------------------------------------
# HttpDelivery – bounded retry
# ---------------------------------------------------------------------------

def _make_mock_session(status: int = 200, raise_exc: Exception | None = None):
    """Return a mock aiohttp.ClientSession context manager."""
    resp = MagicMock()
    resp.status = status
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)

    session = MagicMock()
    if raise_exc is not None:
        session.post = MagicMock(side_effect=raise_exc)
    else:
        post_ctx = MagicMock()
        post_ctx.__aenter__ = AsyncMock(return_value=resp)
        post_ctx.__aexit__ = AsyncMock(return_value=False)
        session.post = MagicMock(return_value=post_ctx)

    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    return session


def test_http_delivery_retries_on_exception(caplog):
    """HttpDelivery retries up to _MAX_ATTEMPTS on connection error then logs warning."""
    import aiohttp

    call_count = 0

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        def post(self, *args, **kwargs):
            nonlocal call_count
            call_count += 1

            class _Cm:
                async def __aenter__(self_inner):
                    raise aiohttp.ClientError("conn refused")

                async def __aexit__(self_inner, *_):
                    pass

            return _Cm()

    with patch("aiohttp.ClientSession", return_value=_FakeSession()):
        with patch("asyncio.sleep", new_callable=AsyncMock):
            with caplog.at_level(logging.WARNING, logger="pmfi.delivery.http"):
                asyncio.run(
                    HttpDelivery().deliver(_make_alert(), venue_code="polymarket")
                )

    assert call_count == _MAX_ATTEMPTS, f"Expected {_MAX_ATTEMPTS} attempts, got {call_count}"
    assert "non-fatal" in caplog.text.lower() or "failed after" in caplog.text.lower()


def test_http_delivery_retries_on_4xx(caplog):
    """HttpDelivery retries on 4xx status codes and eventually logs a warning."""
    import aiohttp

    call_count = 0

    class _FakeResp:
        status = 503

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        def post(self, *args, **kwargs):
            nonlocal call_count
            call_count += 1
            return _FakeResp()

    with patch("aiohttp.ClientSession", return_value=_FakeSession()):
        with patch("asyncio.sleep", new_callable=AsyncMock):
            with caplog.at_level(logging.WARNING, logger="pmfi.delivery.http"):
                asyncio.run(
                    HttpDelivery().deliver(_make_alert(), venue_code="polymarket")
                )

    assert call_count == _MAX_ATTEMPTS, f"Expected {_MAX_ATTEMPTS} attempts, got {call_count}"


def test_http_delivery_succeeds_first_try_no_retry():
    """HttpDelivery does not retry on 2xx success."""
    call_count = 0

    class _FakeResp:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        def post(self, *args, **kwargs):
            nonlocal call_count
            call_count += 1
            return _FakeResp()

    with patch("aiohttp.ClientSession", return_value=_FakeSession()):
        asyncio.run(HttpDelivery().deliver(_make_alert(), venue_code="polymarket"))

    assert call_count == 1


def test_http_delivery_non_fatal_does_not_raise():
    """HttpDelivery must never propagate exceptions to callers."""
    import aiohttp

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        def post(self, *args, **kwargs):
            class _Cm:
                async def __aenter__(self_inner):
                    raise aiohttp.ClientError("boom")

                async def __aexit__(self_inner, *_):
                    pass

            return _Cm()

    with patch("aiohttp.ClientSession", return_value=_FakeSession()):
        with patch("asyncio.sleep", new_callable=AsyncMock):
            asyncio.run(HttpDelivery().deliver(_make_alert(), venue_code="polymarket"))
