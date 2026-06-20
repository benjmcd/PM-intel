from __future__ import annotations

import json
import os
from argparse import Namespace
from datetime import datetime, timezone

import pytest

import pmfi.calibration_decisions as calibration_decisions
from pmfi.calibration_decisions import build_calibration_decision_record


def test_build_calibration_decision_record_has_required_shape() -> None:
    comparison = {
        "removed_volume_spike_alerts": 2,
        "added_volume_spike_alerts": 0,
        "review_data_provided": True,
    }
    generated_at = datetime(2026, 6, 18, 12, 34, 56, tzinfo=timezone.utc)

    record = build_calibration_decision_record(
        comparison,
        selected_packet_names=["current.json", "candidate.json"],
        decision="accept_candidate",
        rationale="candidate removes reviewed false positives",
        output_artifact_path="reports/calibration-decisions/decision.json",
        output_artifact_name="decision.json",
        generated_at=generated_at,
    )

    assert record["schema_version"] == "calibration_decision_record.v1"
    assert record["generated_at"] == "2026-06-18T12:34:56+00:00"
    assert record["decision"] == "accept_candidate"
    assert record["rationale"] == "candidate removes reviewed false positives"
    assert record["packet_selection"] == {
        "names": ["current.json", "candidate.json"],
        "count": 2,
    }
    assert record["output_artifact"] == {
        "path": "reports/calibration-decisions/decision.json",
        "name": "decision.json",
    }
    assert record["comparison"] == comparison
    json.dumps(record)


def test_build_calibration_decision_record_can_embed_review_summary() -> None:
    review_summary = {
        "schema_version": "calibration_packet_review_summary.v1",
        "recommendation": "change-ready-candidate",
        "risk_counts": {
            "removed_reviewed_noise_or_fp": 2,
            "removed_reviewed_tp": 0,
            "removed_unmatched": 0,
        },
    }

    record = build_calibration_decision_record(
        {"aggregate": {"removed_records": 2}},
        selected_packet_names=["candidate.json"],
        decision="change-ready",
        rationale="reviewed packet summary supports change-ready decision",
        review_summary=review_summary,
    )

    assert record["review_summary"] == review_summary
    review_summary["recommendation"] = "mutated"
    assert record["review_summary"]["recommendation"] == "change-ready-candidate"


def test_build_calibration_decision_record_can_embed_cluster_review_coverage() -> None:
    cluster_review_coverage = {
        "schema_version": "calibration_cluster_review_coverage.v1",
        "totals": {
            "market_cluster_count": 3,
            "covered_market_cluster_count": 3,
            "uncovered_market_cluster_count": 0,
            "assessment_counts": {"uncertain": 3},
        },
    }

    record = build_calibration_decision_record(
        {"aggregate": {"removed_records": 25}},
        selected_packet_names=["m20-no.json"],
        decision="needs-more-evidence",
        rationale="cluster reviews are covered but unresolved",
        cluster_review_coverage=cluster_review_coverage,
    )

    assert record["cluster_review_coverage"] == cluster_review_coverage
    cluster_review_coverage["totals"]["covered_market_cluster_count"] = 0
    assert record["cluster_review_coverage"]["totals"][
        "covered_market_cluster_count"
    ] == 3


def test_calibration_decision_record_sets_boolean_safeguards() -> None:
    record = build_calibration_decision_record(
        {},
        selected_packet_names=[],
        decision="defer",
        rationale="no comparable packets selected",
    )

    assert record["local_only"] is True
    assert record["validate_only"] is True
    assert record["config_mutation"] is False
    assert record["db_mutation"] is False
    assert record["live_calls"] is False


def test_calibration_decision_record_preserves_packet_names_and_count() -> None:
    packet_names = ("candidate-b.json", "candidate-a.json", "candidate-b.json")

    record = build_calibration_decision_record(
        {"alerts_delta": -1},
        selected_packet_names=packet_names,
        decision="defer",
        rationale="needs more review evidence",
    )

    assert record["packet_selection"]["names"] == [
        "candidate-b.json",
        "candidate-a.json",
        "candidate-b.json",
    ]
    assert record["packet_selection"]["count"] == 3


def test_calibration_decision_files_list_direct_json_newest_first(monkeypatch, tmp_path) -> None:
    decision_root = tmp_path / "decisions"
    decision_root.mkdir()
    old_path = decision_root / "old.json"
    new_path = decision_root / "new.json"
    ignored_path = decision_root / "notes.txt"
    nested_root = decision_root / "nested"
    nested_root.mkdir()
    old_path.write_text('{"decision": "old"}', encoding="utf-8")
    new_path.write_text('{"decision": "new"}', encoding="utf-8")
    ignored_path.write_text("not json", encoding="utf-8")
    (nested_root / "nested.json").write_text("{}", encoding="utf-8")
    os.utime(old_path, (1000, 1000))
    os.utime(new_path, (2000, 2000))

    monkeypatch.setattr(
        calibration_decisions,
        "calibration_decision_root",
        lambda: decision_root,
    )

    assert calibration_decisions.resolve_calibration_decision_file("old.json") == old_path
    listed = calibration_decisions.list_calibration_decision_files()

    assert [item["name"] for item in listed] == ["new.json", "old.json"]
    assert listed[0]["size_bytes"] == new_path.stat().st_size
    assert listed[0]["modified_at"] == datetime.fromtimestamp(
        new_path.stat().st_mtime,
        timezone.utc,
    ).isoformat()


def test_load_calibration_decision_reads_object_and_rejects_missing_or_invalid(
    monkeypatch,
    tmp_path,
) -> None:
    decision_root = tmp_path / "decisions"
    decision_root.mkdir()
    (decision_root / "valid.json").write_text('{"decision": "accept"}', encoding="utf-8")
    (decision_root / "invalid.json").write_text("[]", encoding="utf-8")
    monkeypatch.setattr(
        calibration_decisions,
        "calibration_decision_root",
        lambda: decision_root,
    )

    assert calibration_decisions.load_calibration_decision("valid.json") == {
        "decision": "accept",
    }
    with pytest.raises(FileNotFoundError):
        calibration_decisions.load_calibration_decision("missing.json")
    with pytest.raises(TypeError, match="calibration decision record"):
        calibration_decisions.load_calibration_decision("invalid.json")


@pytest.mark.parametrize(
    "name",
    ["", "../decision.json", "..\\decision.json", "nested/decision.json", "decision.txt"],
)
def test_resolve_calibration_decision_file_rejects_unsafe_names(
    monkeypatch,
    tmp_path,
    name,
) -> None:
    monkeypatch.setattr(
        calibration_decisions,
        "calibration_decision_root",
        lambda: tmp_path,
    )

    with pytest.raises(ValueError):
        calibration_decisions.resolve_calibration_decision_file(name)


def test_summarize_calibration_decision_record_extracts_dashboard_fields() -> None:
    record = {
        "schema_version": "calibration_decision_record.v1",
        "local_only": True,
        "validate_only": True,
        "config_mutation": False,
        "db_mutation": False,
        "live_calls": False,
        "generated_at": "2026-06-18T12:34:56+00:00",
        "decision": "accept_candidate",
        "rationale": "candidate removes reviewed false positives",
        "packet_selection": {
            "names": ["first.json", "second.json"],
            "count": 2,
        },
        "comparison": {
            "packet_count": 2,
            "candidate_groups": 1,
            "aggregate": {
                "removed_records": 4,
                "added_records": 1,
                "removed_review_labels": {"false-positive": 3},
                "added_review_labels": {"true-positive": 1},
                "repeated_removed_raw_event_ids": [
                    {"raw_event_id": "raw-1", "packets": 2},
                ],
                "repeated_added_raw_event_ids": [],
            },
        },
        "review_summary": {
            "recommendation": "blocked-by-true-positive-risk",
            "risk_counts": {
                "removed_reviewed_tp": 1,
                "removed_unmatched": 0,
            },
        },
        "cluster_review_coverage": {
            "totals": {
                "market_cluster_count": 3,
                "covered_market_cluster_count": 2,
                "uncovered_market_cluster_count": 1,
                "assessment_counts": {"uncertain": 2},
                "candidate_readiness_counts": {"blocked-true-positive-risk": 1},
                "candidate_signal_counts": {"mixed_outcome_keys": 1},
                "candidate_next_action_counts": {
                    "narrow-rule-before-config-review": 1,
                },
                "raw_event_lookup_payload_status_counts": {"full-payload": 2},
            },
        },
    }

    summary = calibration_decisions.summarize_calibration_decision_record(
        "decision.json",
        record,
    )

    assert summary == {
        "name": "decision.json",
        "schema_version": "calibration_decision_record.v1",
        "decision": "accept_candidate",
        "decision_readiness": "blocked-by-cluster-true-positive-risk",
        "generated_at": "2026-06-18T12:34:56+00:00",
        "rationale": "candidate removes reviewed false positives",
        "local_only": True,
        "validate_only": True,
        "config_mutation": False,
        "db_mutation": False,
        "live_calls": False,
        "packet_count": 2,
        "packet_names": ["first.json", "second.json"],
        "comparison_packet_count": 2,
        "candidate_groups": 1,
        "removed_records": 4,
        "added_records": 1,
        "removed_review_labels": {"false-positive": 3},
        "added_review_labels": {"true-positive": 1},
        "review_recommendation": "blocked-by-true-positive-risk",
        "review_risk_counts": {
            "removed_reviewed_tp": 1,
            "removed_unmatched": 0,
        },
        "cluster_review_queue_clusters": 3,
        "cluster_review_covered_clusters": 2,
        "cluster_review_uncovered_clusters": 1,
        "cluster_review_assessment_counts": {"uncertain": 2},
        "cluster_review_readiness_counts": {"blocked-true-positive-risk": 1},
        "cluster_review_signal_counts": {"mixed_outcome_keys": 1},
        "cluster_review_next_action_counts": {
            "narrow-rule-before-config-review": 1,
        },
        "cluster_review_payload_status_counts": {"full-payload": 2},
        "repeated_removed_raw_event_ids": [
            {"raw_event_id": "raw-1", "packets": 2},
        ],
        "repeated_added_raw_event_ids": [],
    }


def test_summarize_calibration_decision_record_readiness_prefers_cluster_review_blocker() -> None:
    summary = calibration_decisions.summarize_calibration_decision_record(
        "decision.json",
        {
            "decision": "no-change",
            "review_summary": {
                "recommendation": "needs-more-evidence",
            },
            "cluster_review_coverage": {
                "totals": {
                    "market_cluster_count": 1,
                    "covered_market_cluster_count": 1,
                    "uncovered_market_cluster_count": 0,
                    "assessment_counts": {"true-positive-risk": 1},
                    "candidate_readiness_counts": {"blocked-true-positive-risk": 1},
                },
            },
        },
    )

    assert summary["decision_readiness"] == "blocked-by-cluster-true-positive-risk"


def test_summarize_calibration_decision_record_readiness_flags_uncovered_clusters() -> None:
    summary = calibration_decisions.summarize_calibration_decision_record(
        "decision.json",
        {
            "decision": "needs-more-evidence",
            "review_summary": {
                "recommendation": "change-ready-candidate",
            },
            "cluster_review_coverage": {
                "totals": {
                    "market_cluster_count": 3,
                    "covered_market_cluster_count": 1,
                    "uncovered_market_cluster_count": 2,
                },
            },
        },
    )

    assert summary["decision_readiness"] == "needs-cluster-review"


def test_summarize_calibration_decision_record_readiness_flags_reviewed_tp_risk() -> None:
    summary = calibration_decisions.summarize_calibration_decision_record(
        "decision.json",
        {
            "decision": "no-change",
            "review_summary": {
                "recommendation": "blocked-by-true-positive-risk",
            },
        },
    )

    assert summary["decision_readiness"] == "blocked-by-reviewed-true-positive-risk"


def test_cmd_calibration_decision_writes_ignored_local_record(monkeypatch, tmp_path, capsys) -> None:
    from pmfi.commands.alerts import cmd_calibration_decision

    decision_root = tmp_path / "decisions"
    packets = {
        "first.json": {"name": "first"},
        "second.json": {"name": "second"},
    }

    def fake_load_packet(name: str) -> dict:
        return packets[name]

    def fake_compare(named_packets: list[tuple[str, dict]]) -> dict:
        assert named_packets == [
            ("first.json", {"name": "first"}),
            ("second.json", {"name": "second"}),
        ]
        return {
            "schema_version": "calibration_packet_comparison.v1",
            "local_only": True,
            "validate_only": True,
            "packet_count": 2,
            "aggregate": {"removed_records": 4, "added_records": 0},
        }

    monkeypatch.setattr(
        "pmfi.commands.alerts._calibration_decision_output_root",
        lambda: decision_root,
    )
    monkeypatch.setattr("pmfi.calibration_packets.load_calibration_packet", fake_load_packet)
    monkeypatch.setattr("pmfi.calibration_packets.calibration_packet_comparison", fake_compare)

    rc = cmd_calibration_decision(
        Namespace(
            packet=["first.json", "second.json"],
            decision="no-change",
            rationale="comparison removes only unmatched replay emissions",
            output="decision.json",
            format="text",
        )
    )

    assert rc == 0
    out = capsys.readouterr().out
    assert "config_mutation=false" in out

    artifact = decision_root / "decision.json"
    record = json.loads(artifact.read_text(encoding="utf-8"))
    assert record["schema_version"] == "calibration_decision_record.v1"
    assert record["local_only"] is True
    assert record["validate_only"] is True
    assert record["config_mutation"] is False
    assert record["db_mutation"] is False
    assert record["live_calls"] is False
    assert record["decision"] == "no-change"
    assert record["packet_selection"] == {
        "names": ["first.json", "second.json"],
        "count": 2,
    }
    assert record["comparison"]["aggregate"]["removed_records"] == 4
    assert record["output_artifact"]["name"] == "decision.json"


def test_cmd_calibration_decision_can_embed_review_summary(monkeypatch, tmp_path, capsys) -> None:
    from pmfi.commands.alerts import cmd_calibration_decision

    decision_root = tmp_path / "decisions"
    packets = {
        "first.json": {"name": "first"},
        "second.json": {"name": "second"},
    }
    review_summary = {
        "schema_version": "calibration_packet_review_summary.v1",
        "recommendation": "needs-persisted-review-evidence",
        "risk_counts": {
            "removed_reviewed_noise_or_fp": 0,
            "removed_reviewed_tp": 0,
            "removed_unmatched": 4,
        },
    }

    def fake_load_packet(name: str) -> dict:
        return packets[name]

    def fake_compare(named_packets: list[tuple[str, dict]]) -> dict:
        assert [name for name, _ in named_packets] == ["first.json", "second.json"]
        return {
            "schema_version": "calibration_packet_comparison.v1",
            "local_only": True,
            "validate_only": True,
            "packet_count": 2,
            "aggregate": {"removed_records": 4, "added_records": 0},
        }

    def fake_review_summary(named_packets: list[tuple[str, dict]]) -> dict:
        assert [name for name, _ in named_packets] == ["first.json", "second.json"]
        return review_summary

    monkeypatch.setattr(
        "pmfi.commands.alerts._calibration_decision_output_root",
        lambda: decision_root,
    )
    monkeypatch.setattr("pmfi.calibration_packets.load_calibration_packet", fake_load_packet)
    monkeypatch.setattr("pmfi.calibration_packets.calibration_packet_comparison", fake_compare)
    monkeypatch.setattr(
        "pmfi.calibration_packets.calibration_packet_review_summary",
        fake_review_summary,
    )

    rc = cmd_calibration_decision(
        Namespace(
            packet=["first.json", "second.json"],
            decision="needs-more-evidence",
            rationale="review summary shows unmatched replay-only removals",
            include_review_summary=True,
            output="decision.json",
            format="text",
        )
    )

    assert rc == 0
    out = capsys.readouterr().out
    assert "recommendation=needs-persisted-review-evidence" in out
    artifact = decision_root / "decision.json"
    record = json.loads(artifact.read_text(encoding="utf-8"))
    assert record["review_summary"] == review_summary


def test_cmd_calibration_decision_can_embed_cluster_review_coverage(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    from pmfi.commands.alerts import cmd_calibration_decision

    decision_root = tmp_path / "decisions"
    packets = {
        "m20-no.json": {"name": "m20-no"},
    }
    reviews = {
        "tie.json": {"market_cluster": "TIE"},
        "kor.json": {"market_cluster": "KOR"},
    }
    coverage = {
        "schema_version": "calibration_cluster_review_coverage.v1",
        "totals": {
            "market_cluster_count": 2,
            "covered_market_cluster_count": 2,
            "uncovered_market_cluster_count": 0,
            "assessment_counts": {"uncertain": 2},
        },
    }

    def fake_load_packet(name: str) -> dict:
        return packets[name]

    def fake_load_review(name: str) -> dict:
        return reviews[name]

    def fake_compare(named_packets: list[tuple[str, dict]]) -> dict:
        assert [name for name, _ in named_packets] == ["m20-no.json"]
        return {
            "schema_version": "calibration_packet_comparison.v1",
            "local_only": True,
            "validate_only": True,
            "packet_count": 1,
            "aggregate": {"removed_records": 25, "added_records": 0},
        }

    def fake_coverage(
        named_packets: list[tuple[str, dict]],
        review_records: list[tuple[str, dict]],
        *,
        state: str,
        review_group: str,
    ) -> dict:
        assert [name for name, _ in named_packets] == ["m20-no.json"]
        assert review_records == [
            ("tie.json", {"market_cluster": "TIE"}),
            ("kor.json", {"market_cluster": "KOR"}),
        ]
        assert state == "removed"
        assert review_group == "unmatched_replay_only"
        return coverage

    monkeypatch.setattr(
        "pmfi.commands.alerts._calibration_decision_output_root",
        lambda: decision_root,
    )
    monkeypatch.setattr("pmfi.calibration_packets.load_calibration_packet", fake_load_packet)
    monkeypatch.setattr("pmfi.calibration_packets.calibration_packet_comparison", fake_compare)
    monkeypatch.setattr(
        "pmfi.calibration_cluster_reviews.load_calibration_cluster_review",
        fake_load_review,
    )
    monkeypatch.setattr(
        "pmfi.calibration_cluster_reviews.calibration_cluster_review_coverage",
        fake_coverage,
    )

    rc = cmd_calibration_decision(
        Namespace(
            packet=["m20-no.json"],
            review=["tie.json", "kor.json"],
            decision="needs-more-evidence",
            rationale="cluster reviews are covered but unresolved",
            include_review_summary=False,
            include_cluster_review_summary=True,
            output="decision.json",
            format="text",
        )
    )

    assert rc == 0
    out = capsys.readouterr().out
    assert "cluster_review_coverage: covered=2 uncovered=0 clusters=2" in out
    artifact = decision_root / "decision.json"
    record = json.loads(artifact.read_text(encoding="utf-8"))
    assert record["cluster_review_coverage"] == coverage


def test_cmd_calibration_decision_refuses_blank_rationale(monkeypatch, tmp_path, capsys) -> None:
    from pmfi.commands.alerts import cmd_calibration_decision

    monkeypatch.setattr(
        "pmfi.commands.alerts._calibration_decision_output_root",
        lambda: tmp_path / "decisions",
    )

    rc = cmd_calibration_decision(
        Namespace(
            packet=["first.json"],
            decision="no-change",
            rationale="   ",
            include_review_summary=False,
            output="decision.json",
            format="text",
        )
    )

    assert rc == 1
    assert "--rationale is required" in capsys.readouterr().out
