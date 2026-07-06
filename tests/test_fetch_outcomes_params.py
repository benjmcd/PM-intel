from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def test_fetch_outcomes_accepts_custom_packet_and_outputs(tmp_path, monkeypatch) -> None:
    module = _load_fetch_outcomes()
    packet = tmp_path / "packet.json"
    out_json = tmp_path / "outcomes.json"
    out_md = tmp_path / "outcomes.md"
    packet.write_text(
        json.dumps(
            {
                "alerts": [
                    {
                        "short_id": "abc12345",
                        "rule_key": "momentum_v1",
                        "venue_code": "kalshi",
                        "venue_market_id": "KXTEST-26JUL06-YES",
                        "title": "Test market",
                        "fired_at": "2026-07-06T00:00:00+00:00",
                        "outcome_key": "yes",
                        "evidence": {"dominant_side": "yes"},
                        "triage_flags": [],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    def fake_analyze_kalshi(_alert):
        return {
            "venue": "kalshi",
            "ticker": "KXTEST-26JUL06-YES",
            "status": "finalized",
            "result": "yes",
            "close_time": "2026-07-06T01:00:00+00:00",
            "event_ticker": "KXTEST-26JUL06",
            "settled_within_7d": True,
            "candles": 0,
        }

    monkeypatch.setattr(module, "analyze_kalshi", fake_analyze_kalshi)
    module.main(
        [
            "--packet",
            str(packet),
            "--out",
            str(out_json),
            "--out-md",
            str(out_md),
            "--generated",
            "2026-07-06",
            "--expected-real-count",
            "1",
        ]
    )

    data = json.loads(out_json.read_text(encoding="utf-8"))
    assert data["generated"] == "2026-07-06"
    assert data["alerts"][0]["short_id"] == "abc12345"
    assert data["alerts"][0]["proposed_label"] == "tp"
    assert out_md.exists()


def _load_fetch_outcomes():
    root = Path(__file__).resolve().parents[1]
    path = root / "reports" / "alert-quality" / "fetch_outcomes.py"
    spec = importlib.util.spec_from_file_location("pmfi_fetch_outcomes_test", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module
