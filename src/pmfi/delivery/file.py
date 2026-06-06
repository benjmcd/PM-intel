from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path
from pmfi.domain import AlertDecision

class FileDelivery:
    def __init__(self, output_dir: Path, *, max_file_size_mb: float = 100.0):
        self._output_dir = Path(output_dir)
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._max_bytes = int(max_file_size_mb * 1024 * 1024)

    def _current_path(self) -> Path:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return self._output_dir / f"alerts_{today}.jsonl"

    async def deliver(self, decision: AlertDecision, *, venue_code: str, market_id: str | None = None) -> None:
        path = self._current_path()
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "rule_id": decision.rule_id,
            "severity": decision.severity,
            "confidence": decision.confidence,
            "score": str(decision.score),
            "venue_code": venue_code,
            "market_id": market_id,
            "reason_codes": list(decision.reason_codes),
            "evidence": decision.evidence,
        }
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
