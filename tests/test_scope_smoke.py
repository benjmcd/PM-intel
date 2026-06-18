from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

EXPECTED_CHECKS = [
    "authority_docs",
    "hosted_saas_markers_absent",
    "order_execution_markers_absent",
    "platform_scaffold_paths_absent",
    "config_defaults_local",
    "localhost_http_endpoint_validation",
    "default_tests_offline",
    "github_workflow_absent",
]


def _load_scope_smoke():
    path = ROOT / "scripts" / "scope_smoke.py"
    spec = importlib.util.spec_from_file_location("scope_smoke", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _minimal_scope_repo(root: Path, *, skip_docs: set[str] | None = None) -> None:
    authority_text = (
        "local-only hosted deployment billing registry image attestation "
        "automatic key rotation external secret-manager RBAC OIDC "
        "No automated trading or order placement. No SaaS hosted product."
    )
    skip_docs = skip_docs or set()
    for rel in [
        "AGENTS.md",
        "LOCAL_ONLY_SCOPE.md",
        "docs/governance/08_local_only_exclusion_policy.md",
        "docs/SECURITY.md",
        "docs/product/00_product_scope.md",
    ]:
        if rel not in skip_docs:
            _write(root / rel, authority_text)
    _write(
        root / "config/app.example.yaml",
        "\n".join(
            [
                "app:",
                "  live_mode_enabled: false",
                "features:",
                "  enable_polymarket_live: false",
                "  enable_kalshi_live: false",
                "  enable_wallet_intelligence: false",
                "alerts:",
                "  default_delivery: console",
                "  allowed_delivery_modes:",
                "    - console",
                "    - file",
                "    - localhost_http_receiver",
            ]
        ),
    )
    _write(root / "src/pmfi/example.py", "LOCAL_SCOPE = True\n")
    _write(
        root / "src/pmfi/delivery/http.py",
        "\n".join(
            [
                "def validate_loopback_http_endpoint(endpoint):",
                "    if endpoint == 'http://localhost:8765/alerts':",
                "        return endpoint",
                "    raise ValueError('loopback local endpoint required')",
                "",
                "class HttpDelivery:",
                "    def __init__(self, endpoint='http://localhost:8765/alerts'):",
                "        self._endpoint = validate_loopback_http_endpoint(endpoint)",
            ]
        ),
    )
    _write(root / "sql/001_init.sql", "select 1;\n")
    _write(root / "scripts/verify.py", '"""without network access and without a running database"""\n')
    _write(root / ".codex/config.toml", "sandbox_mode = 'workspace-write'\n")
    _write(root / ".claude/settings.json", "{}\n")
    _write(root / "pyproject.toml", "[project]\nname = 'pmfi-test'\n")


def test_scope_smoke_success_payload_contract():
    scope_smoke = _load_scope_smoke()

    payload = scope_smoke.build_payload(ROOT)

    assert payload["ok"] is True
    assert payload["status"] == "pass"
    assert payload["source"] == "local_scope_contracts"
    assert [check["name"] for check in payload["checks"]] == EXPECTED_CHECKS
    assert all(check["status"] == "pass" for check in payload["checks"])
    json.dumps(payload)


def test_scope_smoke_fails_closed_on_forbidden_order_marker(tmp_path):
    scope_smoke = _load_scope_smoke()
    _minimal_scope_repo(tmp_path)
    _write(tmp_path / "src/pmfi/orders.py", "def place_order():\n    return None\n")

    payload = scope_smoke.build_payload(tmp_path)

    assert payload["ok"] is False
    assert payload["status"] == "fail"
    checks = {check["name"]: check for check in payload["checks"]}
    assert checks["order_execution_markers_absent"]["status"] == "fail"
    assert "place_order" in checks["order_execution_markers_absent"]["details"]["violations"][0]


def test_scope_smoke_fails_closed_on_missing_authority_doc(tmp_path):
    scope_smoke = _load_scope_smoke()
    _minimal_scope_repo(tmp_path, skip_docs={"docs/SECURITY.md"})

    payload = scope_smoke.build_payload(tmp_path)

    assert payload["ok"] is False
    checks = {check["name"]: check for check in payload["checks"]}
    assert checks["authority_docs"]["status"] == "fail"
    assert "docs/SECURITY.md" in checks["authority_docs"]["details"]["missing"]


def test_scope_smoke_fails_closed_without_local_http_endpoint_validation(tmp_path):
    scope_smoke = _load_scope_smoke()
    _minimal_scope_repo(tmp_path)
    _write(
        tmp_path / "src/pmfi/delivery/http.py",
        "\n".join(
            [
                "class HttpDelivery:",
                "    def __init__(self, endpoint='http://localhost:8765/alerts'):",
                "        self._endpoint = endpoint",
            ]
        ),
    )

    payload = scope_smoke.build_payload(tmp_path)

    assert payload["ok"] is False
    checks = {check["name"]: check for check in payload["checks"]}
    assert checks["localhost_http_endpoint_validation"]["status"] == "fail"
    assert "loopback" in checks["localhost_http_endpoint_validation"]["message"]


def test_scope_smoke_text_output_reports_failures(tmp_path, capsys):
    scope_smoke = _load_scope_smoke()
    _minimal_scope_repo(tmp_path)
    _write(tmp_path / "src/pmfi/platform.py", "HOST = 'ghcr.io/example'\n")

    rc = scope_smoke.main(["--root", str(tmp_path), "--format", "text"])

    captured = capsys.readouterr()
    assert rc == 1
    assert "scope smoke failed" in captured.out
    assert "hosted_saas_markers_absent" in captured.out
