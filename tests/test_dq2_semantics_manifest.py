from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "tests" / "qualification" / "dq2_semantics_manifest.yaml"


def test_dq2_manifest_fixture_provenance_hashes_are_valid() -> None:
    from pmfi.qualification.dq2_semantics import (
        expected_canonical_hash,
        load_dq2_manifest,
        validate_fixture_provenance,
    )

    manifest = load_dq2_manifest(MANIFEST)
    result = validate_fixture_provenance(manifest)

    assert result["fixture_count"] == 24
    assert result["invalid_hashes"] == []
    assert result["missing_required_fields"] == []
    assert result["origin_classes"] == ["MALFORMED", "SCHEMA_DRIFT", "SYNTHETIC"]
    assert result["not_applicable"]["orderbook_events_default_off"] == "NOT_APPLICABLE"
    normalized = [
        fixture for fixture in manifest["fixtures"]
        if fixture.get("expected_disposition") == "NORMALIZED" and fixture.get("expected_canonical")
    ]
    assert len(normalized) == 13
    assert all("expected_canonical_sha256" in fixture for fixture in normalized)
    assert all(
        fixture["expected_canonical_sha256"] == expected_canonical_hash(fixture)
        for fixture in normalized
    )
