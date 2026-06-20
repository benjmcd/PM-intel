from __future__ import annotations

import json
from argparse import Namespace
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from pmfi.calibration_cluster_reviews import (
    build_calibration_cluster_review_record,
    calibration_cluster_review_coverage,
    summarize_calibration_cluster_review_record,
)


def _packet(*records: dict) -> dict:
    return {
        "export_metadata": {
            "schema_version": "volume_spike_calibration_packet.v1",
            "candidate": {"low_notional_min_baseline_median_usd": 20},
        },
        "calibration_summary": {
            "candidate": {"low_notional_min_baseline_median_usd": 20},
            "comparison": {
                "removed_volume_spike_records": list(records),
                "added_volume_spike_records": [],
            },
        },
    }


def _record(raw_event_id: int, market: str = "KXBTCD") -> dict:
    return {
        "raw_event_id": raw_event_id,
        "venue": "kalshi",
        "venue_trade_id": f"trade-{raw_event_id}",
        "market": market,
        "this_trade_usd": 750.0,
        "trade_usd": 750.0,
        "baseline_median_usd": 20.0,
        "spike_multiplier": 37.5,
        "triage_flags": ["low_notional", "thin_baseline"],
        "review": {"matched": False},
    }


def test_summarize_calibration_cluster_review_record_profiles_raw_lookup_trades() -> None:
    summary = summarize_calibration_cluster_review_record(
        "cluster.json",
        {
            "schema_version": "calibration_cluster_review.v1",
            "generated_at": "2026-06-19T06:00:00+00:00",
            "market_cluster": "KXBTCD",
            "assessment": {"label": "uncertain", "rationale": "needs rows"},
            "cluster": {"row_count": 3},
            "raw_event_ids": [101, 102, 103],
            "raw_event_lookup": {
                "found_count": 3,
                "missing_raw_event_ids": [],
                "include_payload": False,
                "rows": [
                    {
                        "exchange_ts": "2026-06-19T06:01:00+00:00",
                        "trade": {
                            "outcome_key": "yes",
                            "directional_side": "yes",
                            "capital_at_risk_usd": "750.50",
                            "price": "0.45",
                        },
                    },
                    {
                        "exchange_ts": "2026-06-19T06:03:00+00:00",
                        "trade": {
                            "outcome_key": "yes",
                            "directional_side": "no",
                            "capital_at_risk_usd": 1200,
                            "price": 0.52,
                        },
                    },
                    {
                        "exchange_ts": "2026-06-19T06:02:00+00:00",
                        "trade": {
                            "outcome_key": "no",
                            "directional_side": "yes",
                            "capital_at_risk_usd": None,
                            "price": None,
                        },
                    },
                    {
                        "exchange_ts": "2026-06-19T06:04:00+00:00",
                        "trade": {},
                    },
                ],
            },
        },
    )

    assert summary["raw_event_lookup_trade_row_count"] == 3
    assert summary["raw_event_lookup_directional_side_counts"] == {
        "no": 1,
        "yes": 2,
    }
    assert summary["raw_event_lookup_outcome_key_counts"] == {
        "no": 1,
        "yes": 2,
    }
    assert summary["raw_event_lookup_capital_at_risk_usd_min"] == 750.5
    assert summary["raw_event_lookup_capital_at_risk_usd_max"] == 1200.0
    assert summary["raw_event_lookup_price_min"] == 0.45
    assert summary["raw_event_lookup_price_max"] == 0.52
    assert summary["raw_event_lookup_exchange_ts_min"] == "2026-06-19T06:01:00+00:00"
    assert summary["raw_event_lookup_exchange_ts_max"] == "2026-06-19T06:03:00+00:00"
    assert summary["raw_event_lookup_payload_status"] == "preview-only"
    assert summary["calibration_candidate_readiness"] == "needs-more-evidence"
    assert summary["calibration_candidate_blockers"] == ["assessment_uncertain"]
    assert summary["calibration_candidate_signals"] == [
        "mixed_directional_sides",
        "mixed_outcome_keys",
    ]
    assert summary["calibration_candidate_next_action"] == "rerun-with-full-payload"
    assert summary["calibration_candidate_next_action_reasons"] == [
        "assessment_uncertain",
        "payload_preview_only",
        "mixed_directional_sides",
        "mixed_outcome_keys",
    ]

    empty_summary = summarize_calibration_cluster_review_record(
        "empty.json",
        {"raw_event_lookup": {"rows": []}},
    )
    assert empty_summary["raw_event_lookup_trade_row_count"] == 0
    assert empty_summary["raw_event_lookup_directional_side_counts"] == {}
    assert empty_summary["raw_event_lookup_outcome_key_counts"] == {}
    assert empty_summary["raw_event_lookup_capital_at_risk_usd_min"] is None
    assert empty_summary["raw_event_lookup_capital_at_risk_usd_max"] is None
    assert empty_summary["raw_event_lookup_price_min"] is None
    assert empty_summary["raw_event_lookup_price_max"] is None
    assert empty_summary["raw_event_lookup_exchange_ts_min"] is None
    assert empty_summary["raw_event_lookup_exchange_ts_max"] is None
    assert empty_summary["raw_event_lookup_payload_status"] == "preview-only"
    assert empty_summary["calibration_candidate_readiness"] == "needs-more-evidence"
    assert empty_summary["calibration_candidate_blockers"] == [
        "assessment_uncertain",
        "raw_lookup_no_trade_facts",
    ]
    assert empty_summary["calibration_candidate_signals"] == []
    assert empty_summary["calibration_candidate_next_action"] == "embed-raw-lookup"


def test_build_calibration_cluster_review_record_snapshots_filtered_cluster() -> None:
    generated_at = datetime(2026, 6, 19, 12, 0, 0, tzinfo=timezone.utc)
    record = build_calibration_cluster_review_record(
        [
            (
                "candidate.json",
                _packet(_record(101), _record(102), _record(103, market="OTHER")),
            ),
        ],
        market_cluster="KXBTCD",
        assessment="uncertain",
        rationale="Needs packet/raw-event inspection before candidate readiness.",
        reviewed_by="operator",
        generated_at=generated_at,
        output_artifact_path="reports/calibration-cluster-reviews/cluster.json",
        output_artifact_name="cluster.json",
    )

    assert record["schema_version"] == "calibration_cluster_review.v1"
    assert record["local_only"] is True
    assert record["validate_only"] is True
    assert record["config_mutation"] is False
    assert record["db_mutation"] is False
    assert record["live_calls"] is False
    assert record["persisted_alert_review"] is False
    assert record["generated_at"] == "2026-06-19T12:00:00+00:00"
    assert record["packet_selection"] == {
        "names": ["candidate.json"],
        "count": 1,
    }
    assert record["filters"] == {
        "state": "removed",
        "review_group": "unmatched_replay_only",
        "market_cluster": "KXBTCD",
        "limit": 0,
    }
    assert record["market_cluster"] == "KXBTCD"
    assert record["assessment"]["label"] == "uncertain"
    assert record["assessment"]["reviewed_by"] == "operator"
    assert record["assessment"]["rationale"].startswith("Needs packet/raw-event")
    assert "needs more packet/raw-event evidence" in record["assessment"]["implication"]
    assert record["queue_totals"]["filtered_rows"] == 2
    assert record["cluster"]["market_key"] == "KXBTCD"
    assert record["cluster"]["row_count"] == 2
    assert record["cluster"]["replay_only_count"] == 2
    assert record["raw_event_ids"] == [101, 102]
    assert [row["market_cluster"] for row in record["rows"]] == ["KXBTCD", "KXBTCD"]
    assert record["output_artifact"] == {
        "path": "reports/calibration-cluster-reviews/cluster.json",
        "name": "cluster.json",
    }
    json.dumps(record)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"market_cluster": "   "}, "market_cluster is required"),
        ({"assessment": "maybe"}, "assessment must be one of"),
        ({"rationale": "   "}, "rationale is required"),
        ({"market_cluster": "MISSING"}, "no queue rows matched"),
    ],
)
def test_build_calibration_cluster_review_record_rejects_invalid_inputs(
    kwargs,
    message,
) -> None:
    params = {
        "market_cluster": "KXBTCD",
        "assessment": "noise",
        "rationale": "reviewed packet/raw-event rows",
    }
    params.update(kwargs)

    with pytest.raises(ValueError, match=message):
        build_calibration_cluster_review_record(
            [("candidate.json", _packet(_record(101)))],
            **params,
        )


def test_cmd_calibration_cluster_review_writes_ignored_local_artifact(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    from pmfi.commands.alerts import cmd_calibration_cluster_review

    output_root = tmp_path / "cluster-reviews"
    packet = _packet(_record(101), _record(102))

    monkeypatch.setattr(
        "pmfi.commands.alerts._calibration_cluster_review_output_root",
        lambda: output_root,
    )
    monkeypatch.setattr(
        "pmfi.calibration_packets.load_calibration_packet",
        lambda name: packet,
    )
    async def should_not_lookup(*args, **kwargs):
        raise AssertionError("raw event lookup should stay opt-in")

    monkeypatch.setattr(
        "pmfi.raw_event_lookup.query_raw_event_lookup",
        should_not_lookup,
    )

    rc = cmd_calibration_cluster_review(
        Namespace(
            packet=["candidate.json"],
            market_cluster="KXBTCD",
            state="removed",
            review_group="unmatched_replay_only",
            assessment="false-positive",
            rationale="Packet/raw-event inspection supports treating this cluster as false-positive evidence.",
            reviewed_by="operator",
            output="cluster.json",
            include_raw_events=False,
            include_raw_payload=False,
            format="text",
        )
    )

    assert rc == 0
    out = capsys.readouterr().out
    assert "assessment=false-positive" in out
    assert "persisted_alert_review=false" in out

    artifact = output_root / "cluster.json"
    record = json.loads(artifact.read_text(encoding="utf-8"))
    assert record["schema_version"] == "calibration_cluster_review.v1"
    assert record["assessment"]["label"] == "false-positive"
    assert record["assessment"]["reviewed_by"] == "operator"
    assert record["market_cluster"] == "KXBTCD"
    assert record["cluster"]["row_count"] == 2
    assert record["rows"][0]["persisted_alert_reviewable"] is False
    assert record["output_artifact"]["name"] == "cluster.json"
    assert "raw_event_lookup" not in record


def test_cmd_calibration_cluster_review_embeds_raw_lookup_without_payload_by_default(
    monkeypatch,
    tmp_path,
) -> None:
    from pmfi.commands.alerts import cmd_calibration_cluster_review

    output_root = tmp_path / "cluster-reviews"
    packet = _packet(_record(101))

    async def fake_lookup(database_url, raw_event_ids, *, include_payload):
        assert raw_event_ids == [101]
        assert include_payload is False
        return {
            "schema_version": "raw_event_lookup.v1",
            "local_only": True,
            "read_only": True,
            "config_mutation": False,
            "db_mutation": False,
            "live_calls": False,
            "requested_raw_event_ids": [101],
            "found_count": 1,
            "missing_raw_event_ids": [],
            "include_payload": False,
            "rows": [{
                "raw_event_id": 101,
                "venue_market_id": "KXBTCD",
                "payload_preview": '{"ticker":"KXBTCD"}',
                "trade": {"capital_at_risk_usd": 750.0},
            }],
        }

    monkeypatch.setattr(
        "pmfi.commands.alerts._calibration_cluster_review_output_root",
        lambda: output_root,
    )
    monkeypatch.setattr(
        "pmfi.calibration_packets.load_calibration_packet",
        lambda name: packet,
    )
    monkeypatch.setattr(
        "pmfi.config.load_config",
        lambda: SimpleNamespace(
            database=SimpleNamespace(url="postgresql://local/pmfi"),
        ),
    )
    monkeypatch.setattr("pmfi.raw_event_lookup.query_raw_event_lookup", fake_lookup)

    rc = cmd_calibration_cluster_review(
        Namespace(
            packet=["candidate.json"],
            market_cluster="KXBTCD",
            state="removed",
            review_group="unmatched_replay_only",
            assessment="uncertain",
            rationale="Raw event lookup embedded without full payload.",
            reviewed_by=None,
            output="cluster-raw-preview.json",
            include_raw_events=True,
            include_raw_payload=False,
            format="text",
        )
    )

    assert rc == 0
    record = json.loads(
        (output_root / "cluster-raw-preview.json").read_text(encoding="utf-8")
    )
    lookup_row = record["raw_event_lookup"]["rows"][0]
    assert record["raw_event_lookup"]["include_payload"] is False
    assert lookup_row["payload_preview"] == '{"ticker":"KXBTCD"}'
    assert "payload" not in lookup_row


def test_cmd_calibration_cluster_review_fails_closed_for_empty_cluster(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    from pmfi.commands.alerts import cmd_calibration_cluster_review

    monkeypatch.setattr(
        "pmfi.commands.alerts._calibration_cluster_review_output_root",
        lambda: tmp_path / "cluster-reviews",
    )
    monkeypatch.setattr(
        "pmfi.calibration_packets.load_calibration_packet",
        lambda name: _packet(_record(101)),
    )

    rc = cmd_calibration_cluster_review(
        Namespace(
            packet=["candidate.json"],
            market_cluster="MISSING",
            state="removed",
            review_group="unmatched_replay_only",
            assessment="uncertain",
            rationale="missing cluster should not write evidence",
            reviewed_by=None,
            output="cluster.json",
            include_raw_events=False,
            include_raw_payload=False,
            format="text",
        )
    )

    assert rc == 1
    assert "no queue rows matched" in capsys.readouterr().out
    assert not (tmp_path / "cluster-reviews" / "cluster.json").exists()


def test_cmd_calibration_cluster_review_can_embed_raw_event_lookup(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    from pmfi.commands.alerts import cmd_calibration_cluster_review

    output_root = tmp_path / "cluster-reviews"
    packet = _packet(_record(101), _record(102))

    async def fake_lookup(database_url, raw_event_ids, *, include_payload):
        assert database_url == "postgresql://local/pmfi"
        assert raw_event_ids == [101, 102]
        assert include_payload is True
        return {
            "schema_version": "raw_event_lookup.v1",
            "local_only": True,
            "read_only": True,
            "config_mutation": False,
            "db_mutation": False,
            "live_calls": False,
            "requested_raw_event_ids": [101, 102],
            "found_count": 2,
            "missing_raw_event_ids": [],
            "include_payload": True,
            "rows": [
                {
                    "raw_event_id": 101,
                    "venue_market_id": "KXBTCD",
                    "payload": {"ticker": "KXBTCD"},
                    "trade": {"capital_at_risk_usd": 750.0},
                },
                {
                    "raw_event_id": 102,
                    "venue_market_id": "KXBTCD",
                    "payload": {"ticker": "KXBTCD"},
                    "trade": {"capital_at_risk_usd": 750.0},
                },
            ],
        }

    monkeypatch.setattr(
        "pmfi.commands.alerts._calibration_cluster_review_output_root",
        lambda: output_root,
    )
    monkeypatch.setattr(
        "pmfi.calibration_packets.load_calibration_packet",
        lambda name: packet,
    )
    monkeypatch.setattr(
        "pmfi.config.load_config",
        lambda: SimpleNamespace(
            database=SimpleNamespace(url="postgresql://local/pmfi"),
        ),
    )
    monkeypatch.setattr("pmfi.raw_event_lookup.query_raw_event_lookup", fake_lookup)

    rc = cmd_calibration_cluster_review(
        Namespace(
            packet=["candidate.json"],
            market_cluster="KXBTCD",
            state="removed",
            review_group="unmatched_replay_only",
            assessment="uncertain",
            rationale="Raw event lookup embedded for packet-level inspection.",
            reviewed_by="operator",
            output="cluster-raw.json",
            include_raw_events=True,
            include_raw_payload=True,
            format="text",
        )
    )

    assert rc == 0
    out = capsys.readouterr().out
    assert "raw_event_lookup=embedded" in out
    assert "found=2" in out
    artifact = output_root / "cluster-raw.json"
    record = json.loads(artifact.read_text(encoding="utf-8"))
    lookup = record["raw_event_lookup"]
    assert lookup["schema_version"] == "raw_event_lookup.v1"
    assert lookup["artifact_scope"] == "calibration_cluster_review"
    assert lookup["required_for_artifact"] is True
    assert lookup["requested_raw_event_ids"] == [101, 102]
    assert lookup["missing_raw_event_ids"] == []
    assert lookup["include_payload"] is True
    assert lookup["rows"][0]["payload"] == {"ticker": "KXBTCD"}


def test_cmd_calibration_cluster_review_fails_closed_when_embedded_raw_event_missing(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    from pmfi.commands.alerts import cmd_calibration_cluster_review

    output_root = tmp_path / "cluster-reviews"
    packet = _packet(_record(101), _record(102))

    async def fake_lookup(database_url, raw_event_ids, *, include_payload):
        return {
            "schema_version": "raw_event_lookup.v1",
            "local_only": True,
            "read_only": True,
            "config_mutation": False,
            "db_mutation": False,
            "live_calls": False,
            "requested_raw_event_ids": [101, 102],
            "found_count": 1,
            "missing_raw_event_ids": [102],
            "include_payload": False,
            "rows": [{"raw_event_id": 101, "trade": {}}],
        }

    monkeypatch.setattr(
        "pmfi.commands.alerts._calibration_cluster_review_output_root",
        lambda: output_root,
    )
    monkeypatch.setattr(
        "pmfi.calibration_packets.load_calibration_packet",
        lambda name: packet,
    )
    monkeypatch.setattr(
        "pmfi.config.load_config",
        lambda: SimpleNamespace(
            database=SimpleNamespace(url="postgresql://local/pmfi"),
        ),
    )
    monkeypatch.setattr("pmfi.raw_event_lookup.query_raw_event_lookup", fake_lookup)

    rc = cmd_calibration_cluster_review(
        Namespace(
            packet=["candidate.json"],
            market_cluster="KXBTCD",
            state="removed",
            review_group="unmatched_replay_only",
            assessment="uncertain",
            rationale="Raw event lookup must be complete before artifact write.",
            reviewed_by=None,
            output="cluster-missing.json",
            include_raw_events=True,
            include_raw_payload=False,
            format="text",
        )
    )

    assert rc == 1
    assert "missing raw_event_ids: 102" in capsys.readouterr().out
    assert not (output_root / "cluster-missing.json").exists()


def test_calibration_cluster_review_coverage_marks_covered_and_uncovered_clusters() -> None:
    packet = _packet(_record(101), _record(102), _record(201, market="OTHER"))
    older_review = build_calibration_cluster_review_record(
        [("candidate.json", _packet(_record(101)))],
        market_cluster="KXBTCD",
        assessment="uncertain",
        rationale="older partial packet review",
        generated_at=datetime(2026, 6, 19, 11, 0, 0, tzinfo=timezone.utc),
    )
    latest_review = build_calibration_cluster_review_record(
        [("candidate.json", _packet(_record(101), _record(102)))],
        market_cluster="KXBTCD",
        assessment="noise",
        rationale="latest review covers both raw events",
        generated_at=datetime(2026, 6, 19, 12, 0, 0, tzinfo=timezone.utc),
    )
    unrelated_review = build_calibration_cluster_review_record(
        [("other-packet.json", _packet(_record(201, market="OTHER")))],
        market_cluster="OTHER",
        assessment="false-positive",
        rationale="unrelated packet selection must not count",
        generated_at=datetime(2026, 6, 19, 12, 30, 0, tzinfo=timezone.utc),
    )

    coverage = calibration_cluster_review_coverage(
        [("candidate.json", packet)],
        [
            ("old.json", older_review),
            ("latest.json", latest_review),
            ("unrelated.json", unrelated_review),
        ],
    )

    assert coverage["schema_version"] == "calibration_cluster_review_coverage.v1"
    assert coverage["local_only"] is True
    assert coverage["validate_only"] is True
    assert coverage["persisted_alert_review"] is False
    assert coverage["queue_totals"]["filtered_rows"] == 3
    assert coverage["totals"] == {
        "market_cluster_count": 2,
        "covered_market_cluster_count": 1,
        "uncovered_market_cluster_count": 1,
        "assessment_counts": {"noise": 1},
        "candidate_readiness_counts": {"needs-more-evidence": 1},
        "candidate_signal_counts": {},
        "candidate_next_action_counts": {"embed-raw-lookup": 1},
        "raw_event_lookup_payload_status_counts": {"not-embedded": 1},
    }
    by_key = {cluster["market_key"]: cluster for cluster in coverage["market_clusters"]}
    assert by_key["KXBTCD"]["covered"] is True
    assert by_key["KXBTCD"]["latest_review"]["name"] == "latest.json"
    assert by_key["KXBTCD"]["latest_review"]["assessment"] == "noise"
    assert (
        by_key["KXBTCD"]["latest_review"]["calibration_candidate_readiness"]
        == "needs-more-evidence"
    )
    assert (
        by_key["KXBTCD"]["latest_review"]["calibration_candidate_next_action"]
        == "embed-raw-lookup"
    )
    assert by_key["KXBTCD"]["latest_review"]["calibration_candidate_blockers"] == [
        "raw_lookup_not_embedded",
        "raw_lookup_no_trade_facts",
        "packet_review_only",
    ]
    assert by_key["KXBTCD"]["missing_raw_event_id_count"] == 0
    assert by_key["OTHER"]["covered"] is False
    assert by_key["OTHER"]["latest_review"] is None
    assert by_key["OTHER"]["missing_raw_event_id_count"] == 1


def test_calibration_cluster_review_coverage_can_focus_one_market_cluster() -> None:
    packet = _packet(_record(101), _record(201, market="OTHER"))
    review = build_calibration_cluster_review_record(
        [("candidate.json", _packet(_record(101)))],
        market_cluster="KXBTCD",
        assessment="noise",
        rationale="single cluster reviewed",
    )

    coverage = calibration_cluster_review_coverage(
        [("candidate.json", packet)],
        [("cluster.json", review)],
        market_cluster="KXBTCD",
    )

    assert coverage["filters"]["market_cluster"] == "KXBTCD"
    assert coverage["queue_totals"]["filtered_rows"] == 1
    assert [cluster["market_key"] for cluster in coverage["market_clusters"]] == [
        "KXBTCD",
    ]
    assert coverage["totals"]["covered_market_cluster_count"] == 1


def test_cmd_calibration_cluster_review_summary_reports_coverage(
    monkeypatch,
    capsys,
) -> None:
    from pmfi.commands.alerts import cmd_calibration_cluster_review_summary

    packet = _packet(_record(101), _record(102), _record(201, market="OTHER"))
    review = build_calibration_cluster_review_record(
        [("candidate.json", _packet(_record(101), _record(102)))],
        market_cluster="KXBTCD",
        assessment="noise",
        rationale="reviewed packet rows support noise assessment",
    )

    monkeypatch.setattr(
        "pmfi.calibration_packets.load_calibration_packet",
        lambda name: packet,
    )
    monkeypatch.setattr(
        "pmfi.calibration_cluster_reviews.load_calibration_cluster_review",
        lambda name: review,
    )

    rc = cmd_calibration_cluster_review_summary(
        Namespace(
            packet=["candidate.json"],
            review=["cluster.json"],
            state="removed",
            review_group="unmatched_replay_only",
            market_cluster=None,
            format="text",
        )
    )

    assert rc == 0
    out = capsys.readouterr().out
    assert "covered=1" in out
    assert "uncovered=1" in out
    assert "assessment=noise" in out
    assert "candidate_readiness=needs-more-evidence=1" in out
    assert "raw_lookup_payload_status=not-embedded=1" in out
    assert "candidate_next_action=embed-raw-lookup=1" in out
    assert "readiness=needs-more-evidence" in out
    assert "next_action=embed-raw-lookup" in out
    assert "signals=-" in out
    assert "raw_lookup=not-embedded" in out
    assert "persisted_alert_review=false" in out
