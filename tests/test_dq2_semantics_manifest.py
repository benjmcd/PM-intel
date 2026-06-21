from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "tests" / "qualification" / "dq2_semantics_manifest.yaml"


def test_dq2_manifest_fixture_provenance_hashes_are_valid() -> None:
    from pmfi.qualification.dq2_semantics import load_dq2_manifest, validate_fixture_provenance

    manifest = load_dq2_manifest(MANIFEST)
    result = validate_fixture_provenance(manifest)

    assert result["fixture_count"] == 24
    assert result["invalid_hashes"] == []
    assert result["missing_required_fields"] == []
    assert result["origin_classes"] == ["MALFORMED", "SCHEMA_DRIFT", "SYNTHETIC"]
    assert result["not_applicable"]["orderbook_events_default_off"] == "NOT_APPLICABLE"
