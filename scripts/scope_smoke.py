from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]

SOURCE = "local_scope_contracts"
CHECK_NAMES = [
    "authority_docs",
    "hosted_saas_markers_absent",
    "order_execution_markers_absent",
    "platform_scaffold_paths_absent",
    "config_defaults_local",
    "localhost_http_endpoint_validation",
    "default_tests_offline",
    "github_workflow_absent",
]

AUTHORITY_DOCS = [
    "AGENTS.md",
    "LOCAL_ONLY_SCOPE.md",
    "docs/governance/08_local_only_exclusion_policy.md",
    "docs/SECURITY.md",
    "docs/product/00_product_scope.md",
]

IMPLEMENTATION_SCAN_TARGETS = [
    "src",
    "sql",
    "config",
    "docker-compose.local.yml",
    ".codex/config.toml",
    ".claude/settings.json",
    "pyproject.toml",
]

FORBIDDEN_PLATFORM_PATH_PARTS = {
    ".github",
    "billing",
    "payments",
    "rbac",
    "oidc",
    "oauth",
    "kubernetes",
    "k8s",
    "helm",
    "terraform",
}

HOSTED_SAAS_MARKERS = [
    "stripe",
    "hosted billing",
    "billing reconciliation",
    "tenant management",
    "deployment attestation",
    "registry image attestation",
    "registry push",
    "image signing",
    "cosign",
    "ghcr.io",
    "docker hub",
    "external secret manager",
    "aws secrets manager",
    "azure key vault",
    "gcp secret manager",
    "hashicorp vault",
    "automatic key rotation",
]

ORDER_EXECUTION_MARKERS = [
    "place_order",
    "submit_order",
    "create_order",
    "cancel_order",
    "order_placement",
    "order-placement",
    "execute_trade",
    "trade_execution",
    "private_key",
    "private-key",
    "wallet_signing",
    "wallet signing",
    "sign_transaction",
    "send_transaction",
]

LOCAL_DELIVERY_MODES = {"console", "file", "localhost_http_receiver"}
SKIP_PARTS = {".git", ".pytest_cache", "__pycache__", ".venv"}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def _skip(path: Path) -> bool:
    parts = path.parts
    if ".claude" in parts and "worktrees" in parts:
        return True
    return any(part in SKIP_PARTS or part.endswith(".egg-info") for part in parts)


def _iter_files(root: Path, rel: str) -> list[Path]:
    target = root / rel
    if target.is_file():
        return [target]
    if not target.exists():
        return []
    return [path for path in target.rglob("*") if path.is_file() and not _skip(path)]


def _pass(name: str, message: str, **details: Any) -> dict[str, Any]:
    check: dict[str, Any] = {"name": name, "status": "pass", "message": message}
    if details:
        check["details"] = details
    return check


def _fail(name: str, message: str, **details: Any) -> dict[str, Any]:
    check: dict[str, Any] = {"name": name, "status": "fail", "message": message}
    if details:
        check["details"] = details
    return check


def _check_authority_docs(root: Path) -> dict[str, Any]:
    missing = [rel for rel in AUTHORITY_DOCS if not (root / rel).is_file()]
    if missing:
        return _fail("authority_docs", "missing authoritative scope document(s)", missing=missing)

    texts = {rel: _read(root / rel).lower() for rel in AUTHORITY_DOCS}
    combined = "\n".join(texts.values())
    required_phrases = [
        "local-only",
        "billing",
        "hosted",
        "rbac",
        "oidc",
        "order placement",
    ]
    missing_phrases = [phrase for phrase in required_phrases if phrase not in combined]
    if "no automated trading" not in combined and "trading bot" not in combined:
        missing_phrases.append("no automated trading")
    if "saas" not in combined:
        missing_phrases.append("SaaS")
    if missing_phrases:
        return _fail(
            "authority_docs",
            "authoritative docs do not encode required local-only/no-trading boundaries",
            missing_phrases=missing_phrases,
        )
    return _pass("authority_docs", "authority docs encode local-only, no-SaaS, and no-order-placement boundaries")


def _scan_markers(root: Path, name: str, markers: list[str]) -> dict[str, Any]:
    violations: list[str] = []
    for rel in IMPLEMENTATION_SCAN_TARGETS:
        for path in _iter_files(root, rel):
            text = _read(path).lower()
            for marker in markers:
                if marker in text:
                    violations.append(f"{path.relative_to(root).as_posix()}: {marker}")
    if violations:
        return _fail(name, "forbidden implementation marker(s) present", violations=violations)
    return _pass(name, "runtime implementation scan targets contain no forbidden marker(s)")


def _check_platform_paths(root: Path) -> dict[str, Any]:
    offenders: list[str] = []
    for path in root.rglob("*"):
        if not path.is_file() or _skip(path):
            continue
        rel = path.relative_to(root)
        parts = {part.lower() for part in rel.parts}
        if parts & FORBIDDEN_PLATFORM_PATH_PARTS:
            offenders.append(rel.as_posix())
    if offenders:
        return _fail("platform_scaffold_paths_absent", "forbidden platform scaffold path(s) present", offenders=offenders)
    return _pass("platform_scaffold_paths_absent", "no forbidden platform scaffold paths are present")


def _check_config_defaults(root: Path) -> dict[str, Any]:
    path = root / "config" / "app.example.yaml"
    if not path.is_file():
        return _fail("config_defaults_local", "missing config/app.example.yaml")
    try:
        cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        return _fail("config_defaults_local", "config/app.example.yaml is not parseable YAML", error=str(exc))

    app = cfg.get("app") if isinstance(cfg, dict) else {}
    features = cfg.get("features") if isinstance(cfg, dict) else {}
    alerts = cfg.get("alerts") if isinstance(cfg, dict) else {}
    failures: list[str] = []
    if not isinstance(app, dict) or app.get("live_mode_enabled") is not False:
        failures.append("app.live_mode_enabled must default false")
    if not isinstance(features, dict):
        failures.append("features must be an object")
    else:
        for key in ("enable_polymarket_live", "enable_kalshi_live", "enable_wallet_intelligence"):
            if features.get(key) is not False:
                failures.append(f"features.{key} must default false")
        for key in ("enable_hosted_runtime", "enable_user_accounts", "enable_external_notification_services"):
            if key in features:
                failures.append(f"features.{key} must be absent")
    if not isinstance(alerts, dict):
        failures.append("alerts must be an object")
    else:
        modes = set(alerts.get("allowed_delivery_modes") or [])
        if alerts.get("default_delivery") != "console":
            failures.append("alerts.default_delivery must default console")
        if not modes or not modes <= LOCAL_DELIVERY_MODES:
            failures.append("alerts.allowed_delivery_modes must stay local")
    if failures:
        return _fail("config_defaults_local", "local-only config defaults drifted", failures=failures)
    return _pass("config_defaults_local", "config defaults keep live features disabled and delivery local")


def _check_localhost_http_endpoint_validation(root: Path) -> dict[str, Any]:
    path = root / "src" / "pmfi" / "delivery" / "http.py"
    if not path.is_file():
        return _fail("localhost_http_endpoint_validation", "missing local HTTP delivery module")

    module_name = f"_pmfi_scope_delivery_http_{abs(hash(path))}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        return _fail("localhost_http_endpoint_validation", "could not load local HTTP delivery module")

    old_path = list(sys.path)
    sys.path.insert(0, str(root / "src"))
    try:
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
    except Exception as exc:
        return _fail("localhost_http_endpoint_validation", "could not import local HTTP delivery module", error=str(exc))
    finally:
        sys.path[:] = old_path
        sys.modules.pop(module_name, None)

    validator = getattr(module, "validate_loopback_http_endpoint", None)
    if not callable(validator):
        return _fail("localhost_http_endpoint_validation", "missing loopback/local endpoint validator")

    failures: list[str] = []
    for endpoint in (
        "http://localhost:8765/alerts",
        "http://127.0.0.1:8765/alerts",
        "http://[::1]:8765/alerts",
    ):
        try:
            validator(endpoint)
        except Exception as exc:
            failures.append(f"rejected loopback endpoint {endpoint!r}: {exc}")

    for endpoint in (
        "http://example.com/alerts",
        "http://192.168.1.50/alerts",
        "http://0.0.0.0/alerts",
        "http:///alerts",
        "ftp://localhost/alerts",
    ):
        try:
            validator(endpoint)
        except ValueError as exc:
            message = str(exc).lower()
            if "loopback" not in message and "local endpoint" not in message:
                failures.append(f"unclear rejection for {endpoint!r}: {exc}")
        except Exception as exc:
            failures.append(f"unexpected rejection type for {endpoint!r}: {exc}")
        else:
            failures.append(f"accepted non-loopback endpoint {endpoint!r}")

    delivery = getattr(module, "HttpDelivery", None)
    if not callable(delivery):
        failures.append("missing HttpDelivery class")
    else:
        try:
            delivery("http://example.com/alerts")
        except ValueError:
            pass
        except Exception as exc:
            failures.append(f"HttpDelivery rejected public endpoint with unexpected error: {exc}")
        else:
            failures.append("HttpDelivery accepted public endpoint")

    if failures:
        return _fail(
            "localhost_http_endpoint_validation",
            "local HTTP delivery loopback validation is missing or incomplete",
            failures=failures,
        )
    return _pass("localhost_http_endpoint_validation", "local HTTP delivery accepts only loopback HTTP endpoints")


def _check_default_tests(root: Path) -> dict[str, Any]:
    path = root / "scripts" / "verify.py"
    if not path.is_file():
        return _fail("default_tests_offline", "missing scripts/verify.py")
    text = _read(path).lower()
    failures: list[str] = []
    for phrase in ("without network", "without a running database"):
        if phrase not in text:
            failures.append(f"scripts/verify.py missing phrase: {phrase}")
    for marker in ("live-smoke", "pmfi_enable_live"):
        if marker in text:
            failures.append(f"scripts/verify.py includes opt-in runtime marker: {marker}")
    if failures:
        return _fail("default_tests_offline", "default verification no longer proves offline/no-DB behavior", failures=failures)
    return _pass("default_tests_offline", "default verification remains offline and DB-free")


def _check_github(root: Path) -> dict[str, Any]:
    github = root / ".github"
    if github.exists():
        return _fail("github_workflow_absent", ".github is not part of the local-only current phase")
    return _pass("github_workflow_absent", ".github workflow directory is absent")


def build_payload(root: Path = ROOT) -> dict[str, Any]:
    root = root.resolve()
    checks = [
        _check_authority_docs(root),
        _scan_markers(root, "hosted_saas_markers_absent", HOSTED_SAAS_MARKERS),
        _scan_markers(root, "order_execution_markers_absent", ORDER_EXECUTION_MARKERS),
        _check_platform_paths(root),
        _check_config_defaults(root),
        _check_localhost_http_endpoint_validation(root),
        _check_default_tests(root),
        _check_github(root),
    ]
    ok = all(check.get("status") == "pass" for check in checks)
    return {
        "ok": ok,
        "status": "pass" if ok else "fail",
        "source": SOURCE,
        "checks": checks,
    }


def _print_text(payload: dict[str, Any]) -> None:
    if payload.get("ok") is True:
        print("scope smoke passed")
    else:
        print("scope smoke failed")
    for check in payload.get("checks", []):
        print(f"- {check.get('name')}: {check.get('status')} - {check.get('message')}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scope-smoke")
    parser.add_argument("--format", choices=["text", "json"], default="text")
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)

    payload = build_payload(args.root)
    if args.format == "json":
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        _print_text(payload)
    return 0 if payload.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
