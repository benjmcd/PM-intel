from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "tests" / "qualification" / "dq1_capture_manifest.yaml"


def test_dq1_manifest_declares_exact_truth_and_required_facets() -> None:
    from pmfi.qualification.dq1_capture import _expected_raw_identities, load_dq1_manifest

    manifest = load_dq1_manifest(MANIFEST)

    assert manifest["scenario_id"] == "DQ-1"
    assert manifest["profile"] == "offline_postgres_capture_gauntlet"
    assert manifest["buffer_limit_events"] == 4
    assert len(_expected_raw_identities(manifest)) == manifest["expected_unique_raw_events"]
    assert manifest["expected_counts"]["generated_observations"] == 19
    assert manifest["expected_counts"]["accepted_observations"] == (
        manifest["expected_counts"]["db_persisted_unique_raw_events"]
    )
    assert manifest["concurrency_probe"]["attempts"] == 8
    assert manifest["deferred_facets"] == [
        {
            "facet": "FAULT_INJECTION",
            "reason": (
                "Real capture-boundary outage/overflow injection is not exercised by this DQ-1 "
                "offline harness; defer to DQ-3 fault qualification."
            ),
        }
    ]


def test_dq1_manifest_has_no_secret_markers() -> None:
    text = MANIFEST.read_text(encoding="utf-8").lower()
    assert "api_key" not in text
    assert "password" not in text
    assert "private_key" not in text
    assert "bearer " not in text
    assert "authorization" not in text
    yaml.safe_load(text)
