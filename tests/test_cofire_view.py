from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _make_pool(fetch_return=None):
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=fetch_return or [])
    pool.close = AsyncMock()
    return pool


async def _create_pool(pool):
    return pool


def _list_args(
    *,
    group_cofire: bool = False,
    expand: bool = False,
    fmt: str = "json",
) -> argparse.Namespace:
    return argparse.Namespace(
        limit=20,
        evidence=False,
        rule=None,
        venue=None,
        severity=None,
        market=None,
        since=None,
        format=fmt,
        unreviewed=False,
        reviewed=False,
        review_label=None,
        triage_flag=[],
        group_cofire=group_cofire,
        expand=expand,
    )


def _packet_args(
    output: Path,
    *,
    group_cofire: bool = False,
    expand: bool = False,
) -> argparse.Namespace:
    return argparse.Namespace(
        since="24h",
        rule=None,
        review_state="reviewed",
        review_label=None,
        category=None,
        limit=50,
        output=str(output),
        format="json",
        group_cofire=group_cofire,
        expand=expand,
    )


def _alert_row(
    alert_id: str,
    venue_market_id: str,
    fired_at: str,
    *,
    venue_code: str = "kalshi",
    rule_key: str = "volume_spike_v1",
    severity: str = "medium",
    market_title: str = "Germany vs Ivory Coast",
    review_label: str | None = None,
) -> dict[str, object]:
    return {
        "alert_id": alert_id,
        "fired_at": fired_at,
        "rule_key": rule_key,
        "rule_version": "alert_rules.v1",
        "severity": severity,
        "confidence": "medium",
        "score": 0.82,
        "venue_code": venue_code,
        "outcome_key": "yes",
        "data_quality": "live",
        "market_title": market_title,
        "venue_market_id": venue_market_id,
        "outcome_label": "Yes",
        "review_label": review_label,
    }


def _packet_alert(
    alert_id: str,
    venue_market_id: str,
    fired_at: str,
    *,
    venue_code: str = "kalshi",
    rule_key: str = "volume_spike_v1",
    severity: str = "medium",
    latest_label: str | None = None,
) -> dict[str, object]:
    return {
        "alert_id": alert_id,
        "short_id": alert_id[:8],
        "fired_at": fired_at,
        "created_at": fired_at,
        "rule_key": rule_key,
        "rule_version": "alert_rules.v1",
        "severity": severity,
        "confidence": "medium",
        "score": 0.82,
        "venue_code": venue_code,
        "outcome_key": "yes",
        "outcome_label": "Yes",
        "data_quality": "live",
        "title": "Germany vs Ivory Coast",
        "venue_market_id": venue_market_id,
        "raw_event_id": 123,
        "trade_id": "bbbbbbbb-cccc-dddd-eeee-ffffffffffff",
        "evidence_summary": "summary",
        "evidence": {"this_trade_usd": 1200},
        "triage_flags": [],
        "latest_review": {
            "review_id": None,
            "label": latest_label,
            "category": None,
            "notes": None,
            "reviewed_by": None,
            "reviewed_at": None,
        },
    }


def _cofire_rows() -> list[dict[str, object]]:
    return [
        _alert_row(
            "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            "KXWCGAME-26JUN20GERCIV-GER",
            "2026-06-20T20:48:01+00:00",
            rule_key="momentum_v1",
            severity="medium",
            review_label="fp",
        ),
        _alert_row(
            "bbbbbbbb-cccc-dddd-eeee-ffffffffffff",
            "KXWCGAME-26JUN20GERCIV-CIV",
            "2026-06-20T20:35:55+00:00",
            rule_key="volume_spike_v1",
            severity="high",
        ),
        _alert_row(
            "cccccccc-dddd-eeee-ffff-000000000000",
            "KXT20MATCH-26JUN202030NEWWAS-WAS",
            "2026-06-21T00:12:36+00:00",
            rule_key="directional_cluster_v1",
            severity="low",
            review_label="tp",
        ),
        _alert_row(
            "dddddddd-eeee-ffff-0000-111111111111",
            "0xabc",
            "2026-06-22T01:20:06+00:00",
            venue_code="polymarket",
            market_title="Polymarket event",
        ),
    ]


def _cofire_packet() -> dict[str, object]:
    alerts = [
        _packet_alert(
            "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            "KXWCGAME-26JUN20GERCIV-GER",
            "2026-06-20T20:48:01+00:00",
            rule_key="momentum_v1",
            severity="medium",
            latest_label="fp",
        ),
        _packet_alert(
            "bbbbbbbb-cccc-dddd-eeee-ffffffffffff",
            "KXWCGAME-26JUN20GERCIV-CIV",
            "2026-06-20T20:35:55+00:00",
            rule_key="volume_spike_v1",
            severity="high",
        ),
        _packet_alert(
            "cccccccc-dddd-eeee-ffff-000000000000",
            "KXT20MATCH-26JUN202030NEWWAS-WAS",
            "2026-06-21T00:12:36+00:00",
            rule_key="directional_cluster_v1",
            severity="low",
            latest_label="tp",
        ),
        _packet_alert(
            "dddddddd-eeee-ffff-0000-111111111111",
            "0xabc",
            "2026-06-22T01:20:06+00:00",
            venue_code="polymarket",
        ),
    ]
    return {
        "export_metadata": {
            "schema_version": "review_packet.v1",
            "local_only": True,
            "generated_at": "2026-07-06T12:00:00+00:00",
            "filters": {"limit": 50},
        },
        "cohort_totals": {"alerts": len(alerts)},
        "reviewed_cohort_totals": {"alerts": len(alerts)},
        "alerts": alerts,
    }


def test_alerts_cofire_view_cli_flags_parse(tmp_path):
    from pmfi.cli import _build_parser

    parser = _build_parser()
    list_args = parser.parse_args(["alerts", "list", "--group-cofire", "--expand"])
    packet_args = parser.parse_args([
        "alerts",
        "review-packet",
        "--group-cofire",
        "--expand",
        "--output",
        str(tmp_path / "packet.json"),
    ])

    assert list_args.group_cofire is True
    assert list_args.expand is True
    assert packet_args.group_cofire is True
    assert packet_args.expand is True


def test_alerts_list_group_cofire_off_keeps_json_output_byte_identical(capsys):
    import asyncpg
    from pmfi.commands.alerts import cmd_alerts_list

    row = dict(_cofire_rows()[0])
    row.pop("venue_market_id")
    pool = _make_pool(fetch_return=[row])

    with patch("pmfi.commands.alerts.asyncio.run", side_effect=_run), \
            patch.object(asyncpg, "create_pool", side_effect=lambda *a, **kw: _create_pool(pool)), \
            patch("pmfi.config.load_config") as mock_cfg:
        mock_cfg.return_value = MagicMock(database=MagicMock(url="postgresql://localhost/test"))
        rc = cmd_alerts_list(_list_args(group_cofire=False))

    assert rc == 0
    sql = pool.fetch.call_args[0][0]
    assert "m.venue_market_id" not in sql
    assert capsys.readouterr().out == json.dumps([row], indent=2, default=str) + "\n"


def test_alerts_list_group_cofire_on_collapses_event_and_expand_lists_legs(capsys):
    import asyncpg
    from pmfi.commands.alerts import cmd_alerts_list

    pool = _make_pool(fetch_return=_cofire_rows())

    with patch("pmfi.commands.alerts.asyncio.run", side_effect=_run), \
            patch.object(asyncpg, "create_pool", side_effect=lambda *a, **kw: _create_pool(pool)), \
            patch("pmfi.config.load_config") as mock_cfg:
        mock_cfg.return_value = MagicMock(database=MagicMock(url="postgresql://localhost/test"))
        rc = cmd_alerts_list(_list_args(group_cofire=True, expand=True))

    assert rc == 0
    sql = pool.fetch.call_args[0][0]
    assert "m.venue_market_id" in sql
    payload = json.loads(capsys.readouterr().out)
    assert [item["leg_count"] for item in payload] == [1, 1, 2]

    poly = payload[0]
    assert poly["event_ticker"] is None
    assert poly["venue_codes"] == ["polymarket"]

    singleton = payload[1]
    assert singleton["event_ticker"] == "KXT20MATCH-26JUN202030NEWWAS"
    assert singleton["leg_count"] == 1
    assert singleton["legs"][0]["alert_id"].startswith("cccccccc")

    group = payload[2]
    assert group["event_ticker"] == "KXWCGAME-26JUN20GERCIV"
    assert group["is_cofire"] is True
    assert group["rules"] == ["momentum_v1", "volume_spike_v1"]
    assert group["worst_severity"] == "high"
    assert group["leg_ids"] == [
        "bbbbbbbb-cccc-dddd-eeee-ffffffffffff",
        "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
    ]
    assert [leg["review_label"] for leg in group["legs"]] == [None, "fp"]


def test_alerts_review_packet_group_cofire_off_writes_identical_packet(tmp_path, capsys):
    import asyncpg
    from pmfi.commands.alerts import cmd_alerts_review_packet

    packet_root = tmp_path / "reports" / "review-packets"
    out_path = packet_root / "packet.json"
    packet = _cofire_packet()
    pool = _make_pool()
    conn = AsyncMock()
    pool.acquire = MagicMock()
    pool.acquire.return_value.__aenter__.return_value = conn

    async def _fake_packet(_conn, **_kwargs):
        assert _conn is conn
        return packet

    with patch("pmfi.commands.alerts.asyncio.run", side_effect=_run), \
            patch("pmfi.commands.alerts._review_packet_output_root", return_value=packet_root), \
            patch.object(asyncpg, "create_pool", side_effect=lambda *a, **kw: _create_pool(pool)), \
            patch("pmfi.db.repos.alerts.get_review_packet", side_effect=_fake_packet), \
            patch("pmfi.config.load_config") as mock_cfg:
        mock_cfg.return_value = MagicMock(database=MagicMock(url="postgresql://localhost/test"))
        rc = cmd_alerts_review_packet(_packet_args(out_path, group_cofire=False))

    assert rc == 0
    assert json.loads(out_path.read_text(encoding="utf-8")) == packet
    assert "co_fire_groups" not in out_path.read_text(encoding="utf-8")
    assert "[review-packet]" in capsys.readouterr().out


def test_alerts_review_packet_group_cofire_adds_summary_and_retains_full_legs(
    tmp_path,
    capsys,
):
    import asyncpg
    from pmfi.commands.alerts import cmd_alerts_review_packet

    packet_root = tmp_path / "reports" / "review-packets"
    out_path = packet_root / "packet.json"
    packet = _cofire_packet()
    pool = _make_pool()
    conn = AsyncMock()
    pool.acquire = MagicMock()
    pool.acquire.return_value.__aenter__.return_value = conn

    async def _fake_packet(_conn, **_kwargs):
        assert _conn is conn
        return packet

    with patch("pmfi.commands.alerts.asyncio.run", side_effect=_run), \
            patch("pmfi.commands.alerts._review_packet_output_root", return_value=packet_root), \
            patch.object(asyncpg, "create_pool", side_effect=lambda *a, **kw: _create_pool(pool)), \
            patch("pmfi.db.repos.alerts.get_review_packet", side_effect=_fake_packet), \
            patch("pmfi.config.load_config") as mock_cfg:
        mock_cfg.return_value = MagicMock(database=MagicMock(url="postgresql://localhost/test"))
        rc = cmd_alerts_review_packet(_packet_args(out_path, group_cofire=True, expand=True))

    assert rc == 0
    saved = json.loads(out_path.read_text(encoding="utf-8"))
    assert saved["alerts"] == packet["alerts"]
    assert saved["co_fire_groups"]["schema_version"] == "co_fire_groups.v1"
    assert saved["co_fire_groups"]["window_s"] == 900
    assert [item["leg_count"] for item in saved["co_fire_groups"]["groups"]] == [1, 1, 2]
    group = saved["co_fire_groups"]["groups"][2]
    assert group["event_ticker"] == "KXWCGAME-26JUN20GERCIV"
    assert group["worst_severity"] == "high"
    assert [leg["alert_id"] for leg in group["legs"]] == [
        "bbbbbbbb-cccc-dddd-eeee-ffffffffffff",
        "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
    ]
    assert "alerts=4" in capsys.readouterr().out


def test_cofire_view_does_not_touch_emission_paths():
    root = Path(__file__).resolve().parents[1]
    for rel_path in [
        "src/pmfi/pipeline/runner.py",
        "src/pmfi/pipeline/engine.py",
        "src/pmfi/pipeline/rules.py",
    ]:
        text = (root / rel_path).read_text(encoding="utf-8").lower()
        assert "cofire" not in text
