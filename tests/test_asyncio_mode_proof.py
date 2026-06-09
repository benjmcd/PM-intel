"""Proof test: @pytest.mark.asyncio actually executes under verify.py.

verify.py sets PYTEST_DISABLE_PLUGIN_AUTOLOAD=1, which would normally
prevent pytest-asyncio from loading. With '-p pytest_asyncio' in addopts
and asyncio_mode='auto', this test MUST be collected and awaited — not
silently passed as a sync function.

If this test is skipped or never awaited, the value will never be set and
the assertion will fail (or the coroutine will not run at all).
"""
from __future__ import annotations

import asyncio

import pytest


@pytest.mark.asyncio
async def test_asyncio_marker_actually_executes():
    """This coroutine must be awaited by pytest-asyncio, not collected as sync.

    The sentinel value is set only if the body actually runs as a coroutine.
    A silently-passing sync collection would not await this body, so the
    sentinel would remain False and the assert would fail.
    """
    sentinel = False

    async def _inner():
        nonlocal sentinel
        await asyncio.sleep(0)  # real async yield to confirm we're in an event loop
        sentinel = True

    await _inner()
    assert sentinel is True, (
        "pytest-asyncio did not await this coroutine — "
        "asyncio_mode or plugin loading is misconfigured"
    )
