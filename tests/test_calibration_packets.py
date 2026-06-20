from __future__ import annotations

from typing import Any

import pytest

from pmfi.calibration_packets import (
    calibration_packet_review_queue,
    calibration_packet_review_summary,
)


def _packet(
    removed: list[dict[str, Any]],
    *,
    added: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    candidate = {"volume_spike_v1": {"min_trade_usd": 1000}}
    return {
        "export_metadata": {
            "schema_version": "volume_spike_calibration_packet.v1",
            "candidate": candidate,
        },
        "calibration_summary": {
            "candidate": candidate,
            "comparison": {
                "removed_volume_spike_records": removed,
                "added_volume_spike_records": added or [],
            },
        },
    }


def _record(
    raw_event_id: int,
    *,
    matched: bool,
    label: str | None,
    category: str | None = None,
    venue_trade_id: str | None = None,
    market: str | None = None,
    venue: str = "kalshi",
    venue_market_id: str | None = None,
    market_slug: str | None = "btc-market",
    market_title: str | None = "Bitcoin above threshold",
    title: str | None = None,
    this_trade_usd: float = 750.0,
    baseline_median_usd: float = 20.0,
    spike_multiplier: float = 37.5,
    triage_flags: list[str] | None = None,
) -> dict[str, Any]:
    record = {
        "raw_event_id": raw_event_id,
        "venue_trade_id": venue_trade_id,
        "venue": venue,
        "market_slug": market_slug,
        "market_title": market_title,
        "this_trade_usd": this_trade_usd,
        "baseline_median_usd": baseline_median_usd,
        "spike_multiplier": spike_multiplier,
        "triage_flags": triage_flags
        if triage_flags is not None
        else ["low_notional", "thin_baseline"],
        "review": {
            "matched": matched,
            "alert_id": "alert-1" if matched else None,
            "trade_id": "trade-1" if matched else None,
            "label": label,
            "category": category,
            "reviewed_at": "2026-06-18T17:00:00+00:00" if matched else None,
        },
    }
    if market is not None:
        record["market"] = market
    if venue_market_id is not None:
        record["venue_market_id"] = venue_market_id
    if title is not None:
        record["title"] = title
    return record


def test_review_summary_unmatched_removals_need_persisted_review_evidence() -> None:
    summary = calibration_packet_review_summary([
        (
            "candidate.json",
            _packet([
                _record(101, matched=False, label=None),
            ]),
        ),
    ])

    assert summary["schema_version"] == "calibration_packet_review_summary.v1"
    assert summary["local_only"] is True
    assert summary["validate_only"] is True
    assert summary["config_mutation"] is False
    assert summary["db_mutation"] is False
    assert summary["live_calls"] is False
    assert summary["recommendation"] == "needs-persisted-review-evidence"
    assert "replay-only" in summary["rationale"]
    assert summary["risk_counts"]["removed_unmatched"] == 1
    assert summary["samples"][0]["risk"] == "removed_unmatched_replay_only"
    assert summary["samples"][0]["state"] == "removed"
    assert summary["comparison"]["aggregate"]["removed_review_unmatched"] == 1
    removed = summary["removed_volume_spike_records"]
    assert removed["counts"]["unmatched_replay_only"] == 1
    assert removed["samples"]["unmatched_replay_only"] == [
        {
            "packet_name": "candidate.json",
            "raw_event_id": 101,
            "venue": "kalshi",
            "review": {
                "matched": False,
                "label": None,
                "category": None,
            },
            "market_slug": "btc-market",
            "market_title": "Bitcoin above threshold",
            "this_trade_usd": 750.0,
        },
    ]


def test_review_summary_reviewed_true_positive_removal_blocks_change() -> None:
    summary = calibration_packet_review_summary([
        (
            "candidate.json",
            _packet([
                _record(201, matched=True, label="tp", category="legit_spike"),
            ]),
        ),
    ])

    assert summary["recommendation"] == "blocked-by-true-positive-risk"
    assert summary["risk_counts"]["removed_reviewed_tp"] == 1
    removed = summary["removed_volume_spike_records"]
    assert removed["counts"]["matched_tp"] == 1
    assert removed["samples"]["matched_tp"][0]["review"] == {
        "matched": True,
        "label": "tp",
        "category": "legit_spike",
    }


def test_review_summary_reviewed_noise_fp_removals_only_are_change_ready() -> None:
    summary = calibration_packet_review_summary([
        (
            "candidate.json",
            _packet([
                _record(301, matched=True, label="noise", category="low_notional"),
                _record(302, matched=True, label="fp", category="thin_baseline"),
            ]),
        ),
    ])

    assert summary["recommendation"] == "change-ready-candidate"
    assert summary["risk_counts"]["removed_reviewed_noise_or_fp"] == 2
    assert summary["risk_counts"]["removed_reviewed_tp"] == 0
    assert summary["risk_counts"]["removed_unmatched"] == 0
    assert summary["comparison"]["aggregate"]["removed_review_labels"] == {
        "fp": 1,
        "noise": 1,
    }
    removed = summary["removed_volume_spike_records"]
    assert removed["counts"]["matched_noise"] == 1
    assert removed["counts"]["matched_fp"] == 1
    assert removed["counts"]["unmatched_replay_only"] == 0


@pytest.mark.parametrize(
    ("added_record", "risk_key"),
    [
        (
            _record(402, matched=True, label="tp", category="legit_spike"),
            "added_reviewed_tp",
        ),
        (
            _record(403, matched=False, label=None),
            "added_unmatched",
        ),
        (
            _record(404, matched=True, label=None),
            "added_reviewed_unreviewed",
        ),
        (
            _record(405, matched=True, label="needs-review"),
            "added_reviewed_other",
        ),
    ],
)
def test_review_summary_added_unsafe_records_prevent_change_ready(
    added_record: dict[str, Any],
    risk_key: str,
) -> None:
    summary = calibration_packet_review_summary([
        (
            "candidate.json",
            _packet(
                [
                    _record(401, matched=True, label="noise", category="low_notional"),
                ],
                added=[added_record],
            ),
        ),
    ])

    assert summary["recommendation"] != "change-ready-candidate"
    assert summary["recommendation"] in {
        "blocked-by-true-positive-risk",
        "needs-more-evidence",
    }
    assert summary["risk_counts"]["removed_reviewed_noise_or_fp"] == 1
    assert summary["risk_counts"][risk_key] == 1


def test_review_queue_marks_unmatched_removed_rows_for_manual_packet_review() -> None:
    queue = calibration_packet_review_queue([
        (
            "m20-no.json",
            _packet([
                _record(
                    501,
                    matched=False,
                    label=None,
                    venue_trade_id="kalshi-trade-501",
                    market="KXBTCD",
                ),
            ]),
        ),
    ])

    assert queue["schema_version"] == "calibration_packet_review_queue.v1"
    assert queue["local_only"] is True
    assert queue["validate_only"] is True
    assert queue["config_mutation"] is False
    assert queue["db_mutation"] is False
    assert queue["live_calls"] is False
    assert queue["packet_count"] == 1
    assert queue["candidate_groups"] == 1
    assert queue["filters"] == {
        "state": "all",
        "review_group": "all",
        "market_cluster": None,
        "limit": 0,
    }
    assert queue["totals"]["available_rows"] == 1
    assert queue["totals"]["filtered_rows"] == 1
    assert queue["totals"]["returned_rows"] == 1
    assert queue["totals"]["truncated"] is False
    assert queue["groups"]["removed"]["unmatched_replay_only"] == 1
    row = queue["rows"][0]
    assert row["packet_name"] == "m20-no.json"
    assert row["state"] == "removed"
    assert row["review_group"] == "unmatched_replay_only"
    assert row["risk"] == "removed_unmatched_replay_only"
    assert row["raw_event_id"] == 501
    assert row["venue"] == "kalshi"
    assert row["venue_trade_id"] == "kalshi-trade-501"
    assert row["market"] == "KXBTCD"
    assert row["market_cluster"] == "KXBTCD"
    assert row["market_slug"] == "btc-market"
    assert row["market_title"] == "Bitcoin above threshold"
    assert row["this_trade_usd"] == 750.0
    assert row["trade_usd"] == 750.0
    assert row["baseline_median_usd"] == 20.0
    assert row["spike_multiplier"] == 37.5
    assert row["triage_flags"] == ["low_notional", "thin_baseline"]
    assert row["review"]["matched"] is False
    assert row["persisted_alert_reviewable"] is False
    assert "manual packet/raw-event inspection" in row["review_action"]
    assert "alert review write" in row["review_action"]


def test_review_queue_filters_and_truncates_rows() -> None:
    queue = calibration_packet_review_queue(
        [
            (
                "m20-a.json",
                _packet(
                    [
                        _record(601, matched=False, label=None),
                        _record(602, matched=True, label="noise"),
                    ],
                    added=[
                        _record(603, matched=True, label="tp"),
                        _record(604, matched=False, label=None),
                    ],
                ),
            ),
        ],
        state="added",
        review_group="unmatched_replay_only",
        limit=1,
    )

    assert queue["filters"] == {
        "state": "added",
        "review_group": "unmatched_replay_only",
        "market_cluster": None,
        "limit": 1,
    }
    assert queue["totals"]["available_rows"] == 4
    assert queue["totals"]["filtered_rows"] == 1
    assert queue["totals"]["returned_rows"] == 1
    assert queue["totals"]["truncated"] is False
    assert queue["groups"]["removed"]["matched_noise"] == 1
    assert queue["groups"]["removed"]["unmatched_replay_only"] == 1
    assert queue["groups"]["added"]["matched_tp"] == 1
    assert queue["groups"]["added"]["unmatched_replay_only"] == 1
    assert [row["raw_event_id"] for row in queue["rows"]] == [604]
    assert queue["rows"][0]["state"] == "added"
    assert queue["rows"][0]["persisted_alert_reviewable"] is False

    truncated = calibration_packet_review_queue(
        [
            (
                "m20-a.json",
                _packet(
                    [
                        _record(701, matched=False, label=None),
                        _record(702, matched=False, label=None),
                    ],
                ),
            ),
        ],
        state="removed",
        review_group="unmatched_replay_only",
        limit=1,
    )

    assert truncated["totals"]["filtered_rows"] == 2
    assert truncated["totals"]["returned_rows"] == 1
    assert truncated["totals"]["truncated"] is True
    assert truncated["totals"]["truncated_rows"] == 1
    assert [row["raw_event_id"] for row in truncated["rows"]] == [701]


def test_review_queue_clusters_filtered_rows_before_truncation() -> None:
    queue = calibration_packet_review_queue(
        [
            (
                "m20-a.json",
                _packet(
                    [
                        _record(
                            801,
                            matched=False,
                            label=None,
                            market="KX-BETA",
                            this_trade_usd=300.0,
                            baseline_median_usd=10.0,
                            spike_multiplier=30.0,
                            triage_flags=["low_notional"],
                        ),
                        _record(
                            802,
                            matched=False,
                            label=None,
                            market="KX-ALPHA",
                            this_trade_usd=100.0,
                            baseline_median_usd=5.0,
                            spike_multiplier=20.0,
                            triage_flags=["low_notional", "thin_baseline"],
                        ),
                        _record(
                            803,
                            matched=False,
                            label=None,
                            market="KX-ALPHA",
                            this_trade_usd=900.0,
                            baseline_median_usd=25.0,
                            spike_multiplier=36.0,
                            triage_flags=["thin_baseline"],
                        ),
                    ],
                    added=[
                        _record(
                            804,
                            matched=False,
                            label=None,
                            market="KX-ALPHA",
                        ),
                        _record(
                            805,
                            matched=True,
                            label="noise",
                            market="KX-ALPHA",
                        ),
                    ],
                ),
            ),
            (
                "m20-b.json",
                _packet([
                    _record(
                        806,
                        matched=False,
                        label=None,
                        market="KX-BETA",
                        venue="polymarket",
                        this_trade_usd=500.0,
                        baseline_median_usd=15.0,
                        spike_multiplier=33.0,
                        triage_flags=["low_notional"],
                    ),
                ]),
            ),
        ],
        state="removed",
        review_group="unmatched_replay_only",
        limit=1,
    )

    assert [row["raw_event_id"] for row in queue["rows"]] == [801]
    assert queue["totals"]["filtered_rows"] == 4
    assert queue["totals"]["returned_rows"] == 1
    assert queue["totals"]["truncated"] is True
    assert [cluster["market_key"] for cluster in queue["market_clusters"]] == [
        "KX-ALPHA",
        "KX-BETA",
    ]

    alpha = queue["market_clusters"][0]
    assert alpha["row_count"] == 2
    assert alpha["packet_count"] == 1
    assert alpha["packet_names"] == ["m20-a.json"]
    assert alpha["venues"] == ["kalshi"]
    assert alpha["states"] == {"removed": 2}
    assert alpha["review_groups"] == {"unmatched_replay_only": 2}
    assert alpha["raw_event_id_count"] == 2
    assert alpha["raw_event_ids_sample"] == [802, 803]
    assert alpha["this_trade_usd_min"] == 100.0
    assert alpha["this_trade_usd_max"] == 900.0
    assert alpha["baseline_median_usd_min"] == 5.0
    assert alpha["baseline_median_usd_max"] == 25.0
    assert alpha["spike_multiplier_min"] == 20.0
    assert alpha["spike_multiplier_max"] == 36.0
    assert alpha["persisted_alert_reviewable_count"] == 0
    assert alpha["replay_only_count"] == 2
    assert alpha["top_triage_flags"] == [
        {"flag": "thin_baseline", "count": 2},
        {"flag": "low_notional", "count": 1},
    ]

    beta = queue["market_clusters"][1]
    assert beta["row_count"] == 2
    assert beta["packet_count"] == 2
    assert beta["packet_names"] == ["m20-a.json", "m20-b.json"]
    assert beta["venues"] == ["kalshi", "polymarket"]


def test_review_queue_market_cluster_filter_uses_cluster_key_before_limit() -> None:
    queue = calibration_packet_review_queue(
        [
            (
                "clusters.json",
                _packet(
                    [
                        _record(
                            851,
                            matched=False,
                            label=None,
                            market="KX-BETA",
                        ),
                        _record(
                            852,
                            matched=False,
                            label=None,
                            market="KX-ALPHA",
                        ),
                        _record(
                            853,
                            matched=False,
                            label=None,
                            market="KX-ALPHA",
                        ),
                    ],
                    added=[
                        _record(
                            854,
                            matched=False,
                            label=None,
                            market="KX-ALPHA",
                        ),
                    ],
                ),
            ),
        ],
        state="removed",
        review_group="unmatched_replay_only",
        market_cluster="KX-ALPHA",
        limit=1,
    )

    assert queue["filters"] == {
        "state": "removed",
        "review_group": "unmatched_replay_only",
        "market_cluster": "KX-ALPHA",
        "limit": 1,
    }
    assert queue["totals"]["available_rows"] == 4
    assert queue["totals"]["filtered_rows"] == 2
    assert queue["totals"]["returned_rows"] == 1
    assert queue["totals"]["truncated"] is True
    assert [row["raw_event_id"] for row in queue["rows"]] == [852]
    assert [cluster["market_key"] for cluster in queue["market_clusters"]] == [
        "KX-ALPHA",
    ]
    assert queue["market_clusters"][0]["row_count"] == 2


def test_review_queue_market_cluster_filter_uses_same_fallback_key() -> None:
    queue = calibration_packet_review_queue(
        [
            (
                "fallbacks.json",
                _packet([
                    _record(
                        861,
                        matched=False,
                        label=None,
                        market=None,
                        venue_market_id="VENUE-1",
                    ),
                    _record(
                        862,
                        matched=False,
                        label=None,
                        market=None,
                        venue_market_id=None,
                        market_slug="slug-1",
                    ),
                ]),
            ),
        ],
        state="removed",
        review_group="unmatched_replay_only",
        market_cluster="VENUE-1",
    )

    assert queue["filters"]["market_cluster"] == "VENUE-1"
    assert [row["raw_event_id"] for row in queue["rows"]] == [861]
    assert [cluster["market_key"] for cluster in queue["market_clusters"]] == [
        "VENUE-1",
    ]


def test_review_queue_blank_market_cluster_filter_is_no_filter() -> None:
    queue = calibration_packet_review_queue(
        [
            (
                "clusters.json",
                _packet([
                    _record(871, matched=False, label=None, market="KX-ALPHA"),
                    _record(872, matched=False, label=None, market="KX-BETA"),
                ]),
            ),
        ],
        state="removed",
        review_group="unmatched_replay_only",
        market_cluster="   ",
    )

    assert queue["filters"]["market_cluster"] is None
    assert queue["totals"]["available_rows"] == 2
    assert queue["totals"]["filtered_rows"] == 2
    assert [row["raw_event_id"] for row in queue["rows"]] == [871, 872]
    assert [cluster["market_key"] for cluster in queue["market_clusters"]] == [
        "KX-ALPHA",
        "KX-BETA",
    ]


def test_review_queue_no_match_market_cluster_preserves_available_count() -> None:
    queue = calibration_packet_review_queue(
        [
            (
                "clusters.json",
                _packet([
                    _record(881, matched=False, label=None, market="KX-ALPHA"),
                ]),
            ),
        ],
        state="removed",
        review_group="unmatched_replay_only",
        market_cluster="KX-MISSING",
    )

    assert queue["filters"]["market_cluster"] == "KX-MISSING"
    assert queue["totals"]["available_rows"] == 1
    assert queue["totals"]["filtered_rows"] == 0
    assert queue["totals"]["returned_rows"] == 0
    assert queue["market_clusters"] == []
    assert queue["rows"] == []


def test_review_queue_market_cluster_filter_uses_key_precedence() -> None:
    named_packets = [
        (
            "precedence.json",
            _packet([
                _record(
                    891,
                    matched=False,
                    label=None,
                    market="MARKET-FIRST",
                    venue_market_id="VENUE-SECOND",
                    market_slug="SLUG-THIRD",
                    market_title="Title fallback",
                ),
            ]),
        ),
    ]

    fallback_queue = calibration_packet_review_queue(
        named_packets,
        state="removed",
        review_group="unmatched_replay_only",
        market_cluster="VENUE-SECOND",
    )
    primary_queue = calibration_packet_review_queue(
        named_packets,
        state="removed",
        review_group="unmatched_replay_only",
        market_cluster="MARKET-FIRST",
    )

    assert fallback_queue["totals"]["filtered_rows"] == 0
    assert fallback_queue["market_clusters"] == []
    assert [row["raw_event_id"] for row in primary_queue["rows"]] == [891]
    assert primary_queue["rows"][0]["market_cluster"] == "MARKET-FIRST"
    assert [cluster["market_key"] for cluster in primary_queue["market_clusters"]] == [
        "MARKET-FIRST",
    ]


def test_review_queue_cluster_market_key_fallbacks_are_stable_and_unknown() -> None:
    queue = calibration_packet_review_queue(
        [
            (
                "fallbacks.json",
                _packet([
                    _record(
                        901,
                        matched=False,
                        label=None,
                        market=None,
                        venue_market_id="VENUE-1",
                    ),
                    _record(
                        902,
                        matched=False,
                        label=None,
                        market=None,
                        venue_market_id=None,
                        market_slug="slug-1",
                    ),
                    _record(
                        903,
                        matched=False,
                        label=None,
                        market=None,
                        venue_market_id=None,
                        market_slug=None,
                        market_title=None,
                        title="Title fallback",
                    ),
                    _record(
                        904,
                        matched=False,
                        label=None,
                        market=None,
                        venue_market_id=None,
                        market_slug=None,
                        market_title=None,
                    ),
                ]),
            ),
        ],
        state="removed",
        review_group="unmatched_replay_only",
    )

    assert [cluster["market_key"] for cluster in queue["market_clusters"]] == [
        "Title fallback",
        "VENUE-1",
        "slug-1",
        "unknown",
    ]


@pytest.mark.parametrize(
    ("state", "review_group", "message"),
    [
        ("bad", "all", "state"),
        ("all", "bad", "review_group"),
    ],
)
def test_review_queue_rejects_invalid_filters(
    state: str,
    review_group: str,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        calibration_packet_review_queue(
            [("candidate.json", _packet([]))],
            state=state,
            review_group=review_group,
        )
