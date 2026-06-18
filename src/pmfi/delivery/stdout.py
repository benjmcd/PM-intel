from __future__ import annotations
import json
from pmfi.domain import AlertDecision

async def deliver_stdout(decision: AlertDecision, *, venue_code: str, market_id: str | None = None) -> None:
    payload = {
        "alert": True,
        "rule_id": decision.rule_id,
        "rule_version": decision.rule_version,
        "severity": decision.severity,
        "confidence": decision.confidence,
        "score": str(decision.score),
        "venue_code": venue_code,
        "market_id": market_id,
        "reason_codes": list(decision.reason_codes),
        "data_quality": decision.data_quality,
        "evidence": decision.evidence,
    }
    print(json.dumps(payload))
