"""HTTP POST delivery for local receivers (localhost_http_receiver mode)."""
from __future__ import annotations
import asyncio
import json
import logging
from datetime import datetime, timezone

from pmfi.domain import AlertDecision

logger = logging.getLogger(__name__)

_MAX_ATTEMPTS = 3
_RETRY_BASE_DELAY = 0.5  # seconds; doubles each attempt


class HttpDelivery:
    """POST alerts as JSON to a local HTTP endpoint."""

    def __init__(self, endpoint: str = "http://localhost:8765/alerts", *, timeout: float = 5.0):
        self._endpoint = endpoint
        self._timeout = timeout

    async def deliver(self, decision: AlertDecision, *, venue_code: str, market_id: str | None = None) -> None:
        import aiohttp

        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "rule_id": decision.rule_id,
            "rule_version": decision.rule_version,
            "severity": decision.severity,
            "confidence": decision.confidence,
            "score": str(decision.score),
            "venue_code": venue_code,
            "market_id": market_id,
            "reason_codes": list(decision.reason_codes),
            "evidence": decision.evidence,
        }
        last_exc: Exception | None = None
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        self._endpoint,
                        data=json.dumps(payload),
                        headers={"Content-Type": "application/json"},
                        timeout=aiohttp.ClientTimeout(total=self._timeout),
                    ) as resp:
                        if resp.status >= 400:
                            logger.warning(
                                "HTTP delivery got status %d from %s (attempt %d/%d)",
                                resp.status, self._endpoint, attempt, _MAX_ATTEMPTS,
                            )
                            last_exc = Exception(f"HTTP {resp.status}")
                            if attempt < _MAX_ATTEMPTS:
                                await asyncio.sleep(_RETRY_BASE_DELAY * (2 ** (attempt - 1)))
                            continue
                        return  # success
            except Exception as exc:
                last_exc = exc
                logger.debug(
                    "HTTP delivery attempt %d/%d failed: %s", attempt, _MAX_ATTEMPTS, exc
                )
                if attempt < _MAX_ATTEMPTS:
                    await asyncio.sleep(_RETRY_BASE_DELAY * (2 ** (attempt - 1)))

        logger.warning("HTTP delivery failed after %d attempts (non-fatal): %s", _MAX_ATTEMPTS, last_exc)
