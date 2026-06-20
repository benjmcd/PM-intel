from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_consistency_audit():
    import importlib.util

    spec = importlib.util.spec_from_file_location("consistency_audit", ROOT / "scripts" / "consistency_audit.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_consistency_audit_passes():
    module = _load_consistency_audit()
    assert module.main() == 0


def test_consistency_audit_skips_generated_reports():
    module = _load_consistency_audit()

    assert module._skip(ROOT / "reports" / "handoff" / "snapshot.md") is True
