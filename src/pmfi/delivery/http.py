"""HTTP POST delivery for local receivers (localhost_http_receiver mode)."""
from __future__ import annotations
import json
import logging
from datetime import datetime, timezone
from urllib.parse import urlsplit

from pmfi.domain import AlertDecision

logger = logging.getLogger(__name__)

_LOOPBACK_ENDPOINT_HOSTS = {"localhost", "127.0.0.1", "::1"}
_HTTP_ENDPOINT_SCHEMES = {"http", "https"}


def validate_loopback_http_endpoint(endpoint: str) -> str:
    """Validate that alert HTTP delivery targets an operator-owned loopback endpoint."""
    value = endpoint.strip()
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
    except ValueError as exc:
        raise ValueError("HTTP alert delivery endpoint must be a loopback/local endpoint") from exc

    if parsed.scheme.lower() not in _HTTP_ENDPOINT_SCHEMES or not host:
        raise ValueError("HTTP alert delivery endpoint must be an http(s) loopback/local endpoint")
    if host.lower() not in _LOOPBACK_ENDPOINT_HOSTS:
        raise ValueError(
            "HTTP alert delivery endpoint must be a loopback/local endpoint "
            "(localhost, 127.0.0.1, or ::1)"
        )
    return value


class HttpDelivery:
    """POST alerts as JSON to a local HTTP endpoint."""

    def __init__(self, endpoint: str = "http://localhost:8765/alerts", *, timeout: float = 5.0):
        self._endpoint = validate_loopback_http_endpoint(endpoint)
        self._timeout = timeout

    async def deliver(self, decision: AlertDecision, *, venue_code: str, market_id: str | None = None) -> None:
        import aiohttp

        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "rule_id": decision.rule_id,
            "rule_version": decision.rule_version,
            "data_quality": decision.data_quality,
            "severity": decision.severity,
            "confidence": decision.confidence,
            "score": str(decision.score),
            "venue_code": venue_code,
            "market_id": market_id,
            "reason_codes": list(decision.reason_codes),
            "evidence": decision.evidence,
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self._endpoint,
                    data=json.dumps(payload),
                    headers={"Content-Type": "application/json"},
                    timeout=aiohttp.ClientTimeout(total=self._timeout),
                ) as resp:
                    if resp.status >= 400:
                        logger.warning("HTTP delivery got status %d from %s", resp.status, self._endpoint)
        except Exception as exc:
            logger.warning("HTTP delivery failed (non-fatal): %s", exc)
