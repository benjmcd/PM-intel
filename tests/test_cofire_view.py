from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


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
    limit: int = 20,
) -> argparse.Namespace:
    return argparse.Namespace(
        limit=limit,
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
    limit: int = 50,
) -> argparse.Namespace:
    return argparse.Namespace(
        since="24h",
        rule=None,
        review_state="reviewed",
        review_label=None,
        category=None,
        limit=limit,
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


def _limit_boundary_rows(count: int) -> list[dict[str, object]]:
    base = datetime(2026, 7, 6, 12, 0, tzinfo=timezone.utc)
    rows = []
    for idx in range(count):
        fired_at = base - timedelta(minutes=idx)
        rows.append(
            _alert_row(
                f"{idx:08d}-0000-0000-0000-000000000000",
                f"KXLIMIT-26JUL06-{idx}",
                fired_at.isoformat(),
            )
        )
    return rows


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


def _invoke_alerts_list(args, rows, capsys):
    import asyncpg
    from pmfi.commands.alerts import cmd_alerts_list

    pool = _make_pool(fetch_return=rows)
    with patch("pmfi.commands.alerts.asyncio.run", side_effect=_run), \
            patch.object(asyncpg, "create_pool", side_effect=lambda *a, **kw: _create_pool(pool)), \
            patch("pmfi.config.load_config") as mock_cfg:
        mock_cfg.return_value = MagicMock(database=MagicMock(url="postgresql://localhost/test"))
        rc = cmd_alerts_list(args)
    return rc, capsys.readouterr().out, pool


def _legacy_args(args: argparse.Namespace) -> argparse.Namespace:
    copied = argparse.Namespace(**vars(args))
    for name in ("group_cofire", "expand"):
        if hasattr(copied, name):
            delattr(copied, name)
    return copied


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


@pytest.mark.parametrize(
    "case_name,mutate",
    [
        ("table", lambda args: setattr(args, "format", "table")),
        ("json-limit", lambda args: setattr(args, "limit", 7)),
        ("since", lambda args: setattr(args, "since", "2026-06-20T20:00:00+00:00")),
        ("rule", lambda args: setattr(args, "rule", "volume_spike_v1")),
        ("venue", lambda args: setattr(args, "venue", "kalshi")),
        ("severity", lambda args: setattr(args, "severity", "medium")),
        ("market", lambda args: setattr(args, "market", "GERCIV")),
        ("reviewed", lambda args: setattr(args, "reviewed", True)),
        ("unreviewed", lambda args: setattr(args, "unreviewed", True)),
        ("review-label", lambda args: setattr(args, "review_label", "fp")),
        ("invalid-review-label", lambda args: setattr(args, "review_label", "bad")),
    ],
)
def test_alerts_list_group_cofire_off_matches_legacy_filters_and_errors(
    case_name,
    mutate,
    capsys,
):
    row = dict(_cofire_rows()[0])
    row.pop("venue_market_id")
    flagged_args = _list_args(group_cofire=False)
    mutate(flagged_args)
    legacy_args = _legacy_args(flagged_args)

    flagged_rc, flagged_out, flagged_pool = _invoke_alerts_list(
        flagged_args,
        [row],
        capsys,
    )
    legacy_rc, legacy_out, legacy_pool = _invoke_alerts_list(
        legacy_args,
        [row],
        capsys,
    )

    assert case_name
    assert (flagged_rc, flagged_out) == (legacy_rc, legacy_out)
    if flagged_pool.fetch.call_args:
        select_clause = flagged_pool.fetch.call_args[0][0].split(" FROM alerts a ", 1)[0]
        assert "m.venue_market_id" not in select_clause
    if legacy_pool.fetch.call_args:
        select_clause = legacy_pool.fetch.call_args[0][0].split(" FROM alerts a ", 1)[0]
        assert "m.venue_market_id" not in select_clause


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


def test_alerts_list_group_cofire_overfetches_since_and_marks_partial_context(
    capsys,
):
    newer = _alert_row(
        "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "KXWCGAME-26JUN20GERCIV-GER",
        "2026-06-20T20:48:01+00:00",
        review_label="fp",
    )
    hidden = _alert_row(
        "bbbbbbbb-cccc-dddd-eeee-ffffffffffff",
        "KXWCGAME-26JUN20GERCIV-CIV",
        "2026-06-20T20:35:55+00:00",
    )
    args = _list_args(group_cofire=True, expand=True)
    args.since = "2026-06-20T20:40:00+00:00"

    rc, out, pool = _invoke_alerts_list(args, [newer, hidden], capsys)

    assert rc == 0
    params = pool.fetch.call_args[0][1:]
    assert params[0] == datetime(2026, 6, 20, 20, 25, tzinfo=timezone.utc)
    assert params[-1] > args.limit
    payload = json.loads(out)
    assert len(payload) == 1
    group = payload[0]
    assert group["partial_group"] is True
    assert group["hidden_sibling_count"] == 1
    assert group["context_leg_count"] == 2
    assert "since_boundary" in group["partial_reasons"]
    assert group["leg_ids"] == ["aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"]
    assert [leg["alert_id"] for leg in group["legs"]] == [
        "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    ]


def test_alerts_list_group_cofire_marks_limit_boundary_partial(capsys):
    args = _list_args(group_cofire=True, expand=True, limit=1)
    rows = _limit_boundary_rows(51)

    rc, out, pool = _invoke_alerts_list(args, rows, capsys)

    assert rc == 0
    assert pool.fetch.call_args[0][-1] > args.limit
    payload = json.loads(out)
    assert len(payload) == 1
    group = payload[0]
    assert group["partial_group"] is True
    assert "limit_boundary" in group["partial_reasons"]
    assert group["hidden_sibling_indicator"]
    assert group["context_leg_count"] == 51


def test_alerts_list_group_cofire_marks_interleaved_frontier_group_partial(capsys):
    from pmfi.commands.alerts import _cofire_non_partial_reduction

    frontier = datetime(2026, 7, 6, 11, 51, tzinfo=timezone.utc)
    rows = [
        _alert_row(
            "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            "KXCTRL-26JUL06-YES",
            "2026-07-06T12:20:00+00:00",
        ),
        _alert_row(
            "bbbbbbbb-cccc-dddd-eeee-ffffffffffff",
            "KXDROP-26JUL06-YES",
            "2026-07-06T12:00:00+00:00",
        ),
        _alert_row(
            "cccccccc-dddd-eeee-ffff-000000000000",
            "KXDROP-26JUL06-NO",
            "2026-07-06T11:59:00+00:00",
        ),
    ]
    rows.extend(
        _alert_row(
            f"f{idx:07d}-0000-0000-0000-000000000000",
            f"KXFILL{idx:02d}-26JUL06-YES",
            (frontier + timedelta(seconds=idx * 8)).isoformat(),
        )
        for idx in range(49)
    )

    rc, out, pool = _invoke_alerts_list(
        _list_args(group_cofire=True, expand=True, limit=2),
        rows,
        capsys,
    )

    assert rc == 0
    assert pool.fetch.call_args[0][-1] == 52
    payload = json.loads(out)
    assert [group["event_ticker"] for group in payload] == [
        "KXCTRL-26JUL06",
        "KXDROP-26JUL06",
    ]
    control_group, dropped_sibling_group = payload
    assert control_group["partial_group"] is False
    assert "limit_boundary" not in control_group["partial_reasons"]
    assert dropped_sibling_group["partial_group"] is True
    assert "limit_boundary" in dropped_sibling_group["partial_reasons"]
    assert dropped_sibling_group["hidden_sibling_indicator"]
    assert set(dropped_sibling_group["leg_ids"]) == {
        "bbbbbbbb-cccc-dddd-eeee-ffffffffffff",
        "cccccccc-dddd-eeee-ffff-000000000000",
    }
    assert _cofire_non_partial_reduction(payload) == 0


def test_alerts_list_group_cofire_limit_applies_to_groups_after_overfetch(capsys):
    rows = [
        _alert_row(
            "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            "KXNEWEST-26JUL06-YES",
            "2026-07-06T12:00:00+00:00",
        ),
        _alert_row(
            "bbbbbbbb-cccc-dddd-eeee-ffffffffffff",
            "KXMIDDLE-26JUL06-YES",
            "2026-07-06T11:00:00+00:00",
        ),
        _alert_row(
            "cccccccc-dddd-eeee-ffff-000000000000",
            "KXOLDEST-26JUL06-YES",
            "2026-07-06T10:00:00+00:00",
        ),
    ]

    rc, out, _pool = _invoke_alerts_list(
        _list_args(group_cofire=True, limit=2),
        rows,
        capsys,
    )

    assert rc == 0
    payload = json.loads(out)
    assert [group["leg_ids"] for group in payload] == [
        ["aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"],
        ["bbbbbbbb-cccc-dddd-eeee-ffffffffffff"],
    ]


def test_alerts_list_grouped_expand_json_preserves_tp_alert_ids(capsys):
    rows = _cofire_rows()

    ungrouped_rc, ungrouped_out, _ = _invoke_alerts_list(
        _list_args(group_cofire=False),
        rows,
        capsys,
    )
    grouped_rc, grouped_out, _ = _invoke_alerts_list(
        _list_args(group_cofire=True, expand=True),
        rows,
        capsys,
    )

    assert ungrouped_rc == 0
    assert grouped_rc == 0
    ungrouped_ids = {row["alert_id"] for row in json.loads(ungrouped_out)}
    groups = json.loads(grouped_out)
    expanded_ids = {
        leg["alert_id"]
        for group in groups
        for leg in group.get("legs", [])
    }
    tp_ids = {
        row["alert_id"]
        for row in json.loads(ungrouped_out)
        if row.get("review_label") == "tp"
    }
    assert expanded_ids == ungrouped_ids
    assert tp_ids <= expanded_ids
    assert tp_ids
    assert all(
        "label" not in group
        and "review_label" not in group
        and "review_category" not in group
        for group in groups
    )


def test_alerts_review_has_no_group_label_path(tmp_path):
    from pmfi.cli import _build_parser

    parser = _build_parser()
    args = parser.parse_args(["alerts", "review", "aaaaaaaa", "--label", "fp"])

    assert args.alert_id == "aaaaaaaa"
    assert not hasattr(args, "group_cofire")
    with pytest.raises(SystemExit):
        parser.parse_args(["alerts", "review", "aaaaaaaa", "--group-cofire", "--label", "fp"])


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


def test_alerts_review_packet_group_cofire_off_preserves_filters_and_limit(
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
    calls = []

    async def _fake_packet(_conn, **kwargs):
        calls.append(kwargs)
        assert _conn is conn
        return packet

    args = _packet_args(out_path, group_cofire=False, limit=7)
    args.since = "2026-06-20T20:00:00+00:00"
    args.rule = "volume_spike_v1"
    args.review_label = "fp"
    args.category = "cross_market_hedge"

    with patch("pmfi.commands.alerts.asyncio.run", side_effect=_run), \
            patch("pmfi.commands.alerts._review_packet_output_root", return_value=packet_root), \
            patch.object(asyncpg, "create_pool", side_effect=lambda *a, **kw: _create_pool(pool)), \
            patch("pmfi.db.repos.alerts.get_review_packet", side_effect=_fake_packet), \
            patch("pmfi.config.load_config") as mock_cfg:
        mock_cfg.return_value = MagicMock(database=MagicMock(url="postgresql://localhost/test"))
        rc = cmd_alerts_review_packet(args)

    assert rc == 0
    assert json.loads(out_path.read_text(encoding="utf-8")) == packet
    assert calls == [
        {
            "since": datetime(2026, 6, 20, 20, 0, tzinfo=timezone.utc),
            "rule": "volume_spike_v1",
            "review_state": "reviewed",
            "review_label": "fp",
            "category": "cross_market_hedge",
            "limit": 7,
        }
    ]
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

    args = _packet_args(out_path, group_cofire=True, expand=True)
    args.since = "2026-06-20T20:00:00+00:00"

    with patch("pmfi.commands.alerts.asyncio.run", side_effect=_run), \
            patch("pmfi.commands.alerts._review_packet_output_root", return_value=packet_root), \
            patch.object(asyncpg, "create_pool", side_effect=lambda *a, **kw: _create_pool(pool)), \
            patch("pmfi.db.repos.alerts.get_review_packet", side_effect=_fake_packet), \
            patch("pmfi.config.load_config") as mock_cfg:
        mock_cfg.return_value = MagicMock(database=MagicMock(url="postgresql://localhost/test"))
        rc = cmd_alerts_review_packet(args)

    assert rc == 0
    saved = json.loads(out_path.read_text(encoding="utf-8"))
    assert saved["alerts"] == packet["alerts"]
    assert saved["co_fire_groups"]["schema_version"] == "co_fire_groups.v1"
    assert saved["co_fire_groups"]["window_s"] == 900
    assert saved["co_fire_groups"]["partial_group_count"] == 0
    assert saved["co_fire_groups"]["non_partial_reduction"] == 1
    assert [item["leg_count"] for item in saved["co_fire_groups"]["groups"]] == [1, 1, 2]
    group = saved["co_fire_groups"]["groups"][2]
    assert group["partial_group"] is False
    assert group["partial_reasons"] == []
    assert group["event_ticker"] == "KXWCGAME-26JUN20GERCIV"
    assert group["worst_severity"] == "high"
    assert [leg["alert_id"] for leg in group["legs"]] == [
        "bbbbbbbb-cccc-dddd-eeee-ffffffffffff",
        "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
    ]
    assert "alerts=4" in capsys.readouterr().out


def test_alerts_review_packet_group_cofire_unreviewed_marks_filter_boundary(
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

    args = _packet_args(out_path, group_cofire=True, expand=True)
    args.review_state = "unreviewed"
    args.since = "2026-06-20T20:00:00+00:00"

    with patch("pmfi.commands.alerts.asyncio.run", side_effect=_run), \
            patch("pmfi.commands.alerts._review_packet_output_root", return_value=packet_root), \
            patch.object(asyncpg, "create_pool", side_effect=lambda *a, **kw: _create_pool(pool)), \
            patch("pmfi.db.repos.alerts.get_review_packet", side_effect=_fake_packet), \
            patch("pmfi.config.load_config") as mock_cfg:
        mock_cfg.return_value = MagicMock(database=MagicMock(url="postgresql://localhost/test"))
        rc = cmd_alerts_review_packet(args)

    assert rc == 0
    saved = json.loads(out_path.read_text(encoding="utf-8"))
    assert saved["co_fire_groups"]["partial_group_count"] == 3
    assert saved["co_fire_groups"]["non_partial_reduction"] == 0
    assert all(
        "filter_boundary" in group["partial_reasons"]
        for group in saved["co_fire_groups"]["groups"]
    )
    assert "[review-packet]" in capsys.readouterr().out


def test_alerts_review_packet_group_cofire_overfetches_and_limits_groups(
    tmp_path,
    capsys,
):
    import asyncpg
    from pmfi.commands.alerts import cmd_alerts_review_packet

    packet_root = tmp_path / "reports" / "review-packets"
    out_path = packet_root / "packet.json"
    visible = _packet_alert(
        "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "KXWCGAME-26JUN20GERCIV-GER",
        "2026-06-20T20:48:01+00:00",
        latest_label="fp",
    )
    hidden = _packet_alert(
        "bbbbbbbb-cccc-dddd-eeee-ffffffffffff",
        "KXWCGAME-26JUN20GERCIV-CIV",
        "2026-06-20T20:35:55+00:00",
    )
    second_group = _packet_alert(
        "cccccccc-dddd-eeee-ffff-000000000000",
        "KXOTHER-26JUN20-YES",
        "2026-06-20T20:41:00+00:00",
        latest_label="tp",
    )
    packet = _cofire_packet()
    packet["alerts"] = [visible, hidden, second_group]
    packet["cohort_totals"] = {"alerts": 3}
    packet["reviewed_cohort_totals"] = packet["cohort_totals"]
    pool = _make_pool()
    conn = AsyncMock()
    pool.acquire = MagicMock()
    pool.acquire.return_value.__aenter__.return_value = conn
    calls = []

    async def _fake_packet(_conn, **kwargs):
        calls.append(kwargs)
        assert _conn is conn
        return packet

    args = _packet_args(out_path, group_cofire=True, expand=True, limit=1)
    args.since = "2026-06-20T20:40:00+00:00"

    with patch("pmfi.commands.alerts.asyncio.run", side_effect=_run), \
            patch("pmfi.commands.alerts._review_packet_output_root", return_value=packet_root), \
            patch.object(asyncpg, "create_pool", side_effect=lambda *a, **kw: _create_pool(pool)), \
            patch("pmfi.db.repos.alerts.get_review_packet", side_effect=_fake_packet), \
            patch("pmfi.config.load_config") as mock_cfg:
        mock_cfg.return_value = MagicMock(database=MagicMock(url="postgresql://localhost/test"))
        rc = cmd_alerts_review_packet(args)

    assert rc == 0
    assert calls[0]["since"] == datetime(2026, 6, 20, 20, 25, tzinfo=timezone.utc)
    assert calls[0]["limit"] > args.limit
    saved = json.loads(out_path.read_text(encoding="utf-8"))
    assert saved["export_metadata"]["filters"]["since"] == "2026-06-20T20:40:00+00:00"
    assert saved["export_metadata"]["filters"]["limit"] == 1
    groups = saved["co_fire_groups"]["groups"]
    assert len(groups) == 1
    assert groups[0]["partial_group"] is True
    assert "since_boundary" in groups[0]["partial_reasons"]
    assert groups[0]["hidden_sibling_count"] == 1
    assert groups[0]["leg_ids"] == ["aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"]
    assert [alert["alert_id"] for alert in saved["alerts"]] == [
        "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    ]
    assert "[review-packet]" in capsys.readouterr().out


def test_cofire_view_does_not_touch_emission_paths():
    root = Path(__file__).resolve().parents[1]
    for rel_path in [
        "src/pmfi/pipeline/runner.py",
        "src/pmfi/pipeline/engine.py",
        "src/pmfi/pipeline/rules.py",
    ]:
        text = (root / rel_path).read_text(encoding="utf-8").lower()
        assert "cofire" not in text
