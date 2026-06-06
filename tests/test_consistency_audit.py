from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_consistency_audit_passes():
    import importlib.util

    spec = importlib.util.spec_from_file_location("consistency_audit", ROOT / "scripts" / "consistency_audit.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.main() == 0
