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


def test_fetch_outcomes_rejects_post_close_settlement_tp(tmp_path, monkeypatch) -> None:
    data = _run_single_alert(
        tmp_path,
        monkeypatch,
        fired_at="2026-07-06T02:00:00+00:00",
        close_time="2026-07-06T01:00:00+00:00",
    )

    alert = data["alerts"][0]
    assert alert["facts"]["settled_within_7d"] is False
    assert alert["proposed_label"] == "noise"


def test_fetch_outcomes_accepts_pre_close_settlement_tp(tmp_path, monkeypatch) -> None:
    data = _run_single_alert(
        tmp_path,
        monkeypatch,
        fired_at="2026-07-06T00:00:00+00:00",
        close_time="2026-07-06T01:00:00+00:00",
    )

    alert = data["alerts"][0]
    assert alert["facts"]["settled_within_7d"] is True
    assert alert["proposed_label"] == "tp"


def test_fetch_outcomes_treats_hedge_group_as_outcome_caveat(tmp_path, monkeypatch) -> None:
    module = _load_fetch_outcomes()
    data = _run_multi_alerts(
        module,
        tmp_path,
        monkeypatch,
        [
            _alert(
                short_id="tp000001",
                market="KXHEDGE-26JUL06-AAA",
                side="yes",
                fired_at="2026-07-06T00:00:00+00:00",
                this_trade_usd=1000,
            ),
            _alert(
                short_id="opp00001",
                market="KXHEDGE-26JUL06-BBB",
                side="no",
                fired_at="2026-07-06T00:02:00+00:00",
                this_trade_usd=990,
            ),
            _alert(
                short_id="opp00002",
                market="KXHEDGE-26JUL06-CCC",
                side="no",
                fired_at="2026-07-06T00:04:00+00:00",
                this_trade_usd=1010,
            ),
        ],
        {
            "KXHEDGE-26JUL06-AAA": _facts(
                ticker="KXHEDGE-26JUL06-AAA",
                event="KXHEDGE-26JUL06",
                result="yes",
                close_time="2026-07-06T03:00:00+00:00",
                fav_max_move=0.22,
                fav_end_move=0.14,
            ),
            "KXHEDGE-26JUL06-BBB": _facts(
                ticker="KXHEDGE-26JUL06-BBB",
                event="KXHEDGE-26JUL06",
                result="yes",
                close_time="2026-07-06T03:00:00+00:00",
                fav_max_move=0.0,
                fav_end_move=0.0,
            ),
            "KXHEDGE-26JUL06-CCC": _facts(
                ticker="KXHEDGE-26JUL06-CCC",
                event="KXHEDGE-26JUL06",
                result="yes",
                close_time="2026-07-06T03:00:00+00:00",
                fav_max_move=0.0,
                fav_end_move=0.0,
            ),
        },
    )

    alert = _by_short_id(data, "tp000001")
    assert alert["proposed_label"] == "tp"
    assert alert["proposed_category"] is None
    assert any("cross_market_hedge_caveat" in reason for reason in alert["why"])


def test_fetch_outcomes_does_not_mark_one_sided_directional_cofire_as_hedge(
    tmp_path, monkeypatch
) -> None:
    module = _load_fetch_outcomes()
    data = _run_multi_alerts(
        module,
        tmp_path,
        monkeypatch,
        [
            _alert(
                short_id="dir00001",
                market="KXDIR-26JUL06-AAA",
                side="no",
                fired_at="2026-07-06T00:00:00+00:00",
                this_trade_usd=1200,
            ),
            _alert(
                short_id="dir00002",
                market="KXDIR-26JUL06-BBB",
                side="no",
                fired_at="2026-07-06T00:03:00+00:00",
                this_trade_usd=1180,
            ),
        ],
        {
            "KXDIR-26JUL06-AAA": _facts(
                ticker="KXDIR-26JUL06-AAA",
                event="KXDIR-26JUL06",
                result="no",
                close_time="2026-07-06T03:00:00+00:00",
                fav_max_move=0.18,
                fav_end_move=0.11,
            ),
            "KXDIR-26JUL06-BBB": _facts(
                ticker="KXDIR-26JUL06-BBB",
                event="KXDIR-26JUL06",
                result="no",
                close_time="2026-07-06T03:00:00+00:00",
                fav_max_move=0.16,
                fav_end_move=0.10,
            ),
        },
    )

    alert = _by_short_id(data, "dir00001")
    assert alert["proposed_label"] == "tp"
    assert alert["proposed_category"] != "cross_market_hedge"


def test_fetch_outcomes_marks_all_outcomes_basket_loser_as_cross_market_hedge(
    tmp_path, monkeypatch
) -> None:
    module = _load_fetch_outcomes()
    data = _run_multi_alerts(
        module,
        tmp_path,
        monkeypatch,
        [
            _alert(
                short_id="loser001",
                market="KXBASK-26JUL06-AAA",
                side="yes",
                fired_at="2026-07-06T00:00:00+00:00",
                this_trade_usd=1000,
            ),
            _alert(
                short_id="opp00003",
                market="KXBASK-26JUL06-BBB",
                side="no",
                fired_at="2026-07-06T00:02:00+00:00",
                this_trade_usd=970,
            ),
            _alert(
                short_id="opp00004",
                market="KXBASK-26JUL06-CCC",
                side="no",
                fired_at="2026-07-06T00:04:00+00:00",
                this_trade_usd=1030,
            ),
        ],
        {
            "KXBASK-26JUL06-AAA": _facts(
                ticker="KXBASK-26JUL06-AAA",
                event="KXBASK-26JUL06",
                result="no",
                close_time="2026-07-06T03:00:00+00:00",
                fav_max_move=0.03,
                fav_end_move=0.0,
            ),
            "KXBASK-26JUL06-BBB": _facts(
                ticker="KXBASK-26JUL06-BBB",
                event="KXBASK-26JUL06",
                result="no",
                close_time="2026-07-06T03:00:00+00:00",
                fav_max_move=0.11,
                fav_end_move=0.06,
            ),
            "KXBASK-26JUL06-CCC": _facts(
                ticker="KXBASK-26JUL06-CCC",
                event="KXBASK-26JUL06",
                result="no",
                close_time="2026-07-06T03:00:00+00:00",
                fav_max_move=0.12,
                fav_end_move=0.07,
            ),
        },
    )

    alert = _by_short_id(data, "loser001")
    assert alert["proposed_label"] == "fp"
    assert alert["proposed_category"] == "cross_market_hedge"


def _run_single_alert(tmp_path, monkeypatch, *, fired_at: str, close_time: str) -> dict:
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
                        "fired_at": fired_at,
                        "outcome_key": "yes",
                        "evidence": {"dominant_side": "yes"},
                        "triage_flags": [],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    def fake_kalshi_market(_ticker):
        return {
            "ticker": "KXTEST-26JUL06-YES",
            "status": "finalized",
            "result": "yes",
            "close_time": close_time,
            "event_ticker": "KXTEST-26JUL06",
        }

    monkeypatch.setattr(module, "kalshi_market", fake_kalshi_market)
    monkeypatch.setattr(module, "kalshi_series", lambda _event: None)
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
    return json.loads(out_json.read_text(encoding="utf-8"))


def _run_multi_alerts(module, tmp_path, monkeypatch, alerts: list[dict], facts_by_ticker: dict) -> dict:
    packet = tmp_path / "packet.json"
    out_json = tmp_path / "outcomes.json"
    out_md = tmp_path / "outcomes.md"
    packet.write_text(json.dumps({"alerts": alerts}), encoding="utf-8")

    def fake_analyze_kalshi(alert):
        return facts_by_ticker[alert["venue_market_id"]]

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
            str(len(alerts)),
        ]
    )
    return json.loads(out_json.read_text(encoding="utf-8"))


def _alert(
    *,
    short_id: str,
    market: str,
    side: str,
    fired_at: str,
    this_trade_usd: float,
) -> dict:
    return {
        "short_id": short_id,
        "rule_key": "momentum_v1",
        "venue_code": "kalshi",
        "venue_market_id": market,
        "title": market,
        "fired_at": fired_at,
        "outcome_key": side,
        "evidence": {
            "dominant_side": side,
            "this_trade_usd": this_trade_usd,
        },
        "triage_flags": [],
    }


def _facts(
    *,
    ticker: str,
    event: str,
    result: str,
    close_time: str,
    fav_max_move: float,
    fav_end_move: float,
) -> dict:
    return {
        "venue": "kalshi",
        "ticker": ticker,
        "status": "finalized",
        "result": result,
        "close_time": close_time,
        "event_ticker": event,
        "settled_within_7d": True,
        "candles": 3,
        "fav_max_move": fav_max_move,
        "fav_end_move": fav_end_move,
    }


def _by_short_id(data: dict, short_id: str) -> dict:
    return next(alert for alert in data["alerts"] if alert["short_id"] == short_id)


def _load_fetch_outcomes():
    root = Path(__file__).resolve().parents[1]
    path = root / "reports" / "alert-quality" / "fetch_outcomes.py"
    spec = importlib.util.spec_from_file_location("pmfi_fetch_outcomes_test", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module
