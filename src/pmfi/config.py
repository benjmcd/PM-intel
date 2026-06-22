from __future__ import annotations
import os
from dataclasses import dataclass, field
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[2]

@dataclass
class DatabaseConfig:
    url: str = "postgresql://pmfi:pmfi_local_password_change_me@localhost:5433/pmfi"
    schema: str = "pmfi"
    pool_min_size: int = 1
    pool_max_size: int = 10

@dataclass
class IngestionConfig:
    raw_retention_days: int = 90
    live_api_timeout_seconds: int = 10
    polymarket_subscription_timeout_seconds: float = 30.0
    polymarket_receive_timeout_seconds: float = 60.0
    circuit_breaker_failure_threshold: int = 10
    circuit_breaker_window_seconds: float = 300.0
    circuit_breaker_recovery_seconds: float = 60.0
    circuit_breaker_progress_reset_min_events: int = 2
    directional_accumulator_max_markets: int = 5000
    directional_accumulator_ttl_seconds: float = 3600.0
    retention_enabled: bool = False
    retention_operator_acknowledged: bool = False
    recovery_backlog_convergence_max_iterations: int = 10
    dead_letter_rate_p1_threshold_fraction: float = 0.05
    dead_letter_unresolved_halt_count: int = 10000
    pool_acquire_wait_p95_alarm_ms: int = 100
    disk_headroom_min_bytes: int = 5 * 1024 * 1024 * 1024
    disk_headroom_min_fraction: float = 0.10
    reconnect_initial_backoff: float = 1.0
    reconnect_max_backoff: float = 60.0
    reconnect_jitter: bool = True
    kalshi_poll_interval_seconds: float = 5.0
    kalshi_trade_poll_limit: int = 200
    kalshi_trade_poll_max_pages: int = 1

@dataclass
class FeaturesConfig:
    enable_polymarket_live: bool = False
    enable_kalshi_live: bool = False
    enable_orderbook_reconstruction: bool = False
    enable_cross_venue_matching: bool = False
    enable_wallet_intelligence: bool = False
    enable_ml_scoring: bool = False

@dataclass
class AlertsConfig:
    default_delivery: str = "console"
    allowed_delivery_modes: list[str] = field(default_factory=lambda: ["console", "file"])
    suppression_window_seconds: int = 300

@dataclass
class BaselinesConfig:
    recompute_enabled: bool = True
    recompute_interval_minutes: int = 1440
    window_days: int = 30
    min_samples: int = 10

@dataclass
class HealthConfig:
    venue_stale_seconds: int = 600

@dataclass
class BackupConfig:
    backup_dir: str = ".pmfi-backups"
    retention_days: int | None = None

@dataclass
class AppConfig:
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    ingestion: IngestionConfig = field(default_factory=IngestionConfig)
    features: FeaturesConfig = field(default_factory=FeaturesConfig)
    alerts: AlertsConfig = field(default_factory=AlertsConfig)
    baselines: BaselinesConfig = field(default_factory=BaselinesConfig)
    health: HealthConfig = field(default_factory=HealthConfig)
    backup: BackupConfig = field(default_factory=BackupConfig)
    log_level: str = "INFO"
    log_file: str | None = None
    live_mode_enabled: bool = False

_KNOWN_TOP_KEYS = {"database", "features", "alerts", "ingestion", "app", "baselines", "health", "backup"}


def _parse_bool(raw: object, default: bool = False) -> bool:
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        normalized = raw.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off", ""}:
            return False
        return default
    if isinstance(raw, (int, float)):
        return bool(raw)
    return default


def load_config(path: Path | None = None) -> AppConfig:
    import logging as _logging
    _log = _logging.getLogger(__name__)
    if path is None:
        for candidate in [ROOT / "config" / "app.yaml", ROOT / "config" / "app.example.yaml"]:
            if candidate.exists():
                path = candidate
                break
    raw: dict = {}
    if path and path.exists():
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        unknown = set(raw.keys()) - _KNOWN_TOP_KEYS
        if unknown:
            _log.warning("config: unknown top-level key(s) in %s: %s", path.name, sorted(unknown))
    db_section = raw.get("database", {})
    db_url = os.environ.get("DATABASE_URL") or db_section.get("url", "postgresql://pmfi:pmfi_local_password_change_me@localhost:5433/pmfi")
    if "pmfi_local_password_change_me" in db_url:
        _log.warning(
            "config: database URL uses the well-known default password; "
            "set DATABASE_URL (and POSTGRES_PASSWORD for docker) to a non-default value"
        )
    db = DatabaseConfig(
        url=db_url,
        schema=db_section.get("schema", "pmfi"),
        pool_min_size=int(db_section.get("pool_min_size", 1)),
        pool_max_size=int(db_section.get("pool_max_size", 10)),
    )
    feats_raw = raw.get("features", {})
    features = FeaturesConfig(
        enable_polymarket_live=feats_raw.get("enable_polymarket_live", False),
        enable_kalshi_live=feats_raw.get("enable_kalshi_live", False),
        enable_orderbook_reconstruction=feats_raw.get("enable_orderbook_reconstruction", False),
        enable_cross_venue_matching=feats_raw.get("enable_cross_venue_matching", False),
        enable_wallet_intelligence=feats_raw.get("enable_wallet_intelligence", False),
        enable_ml_scoring=feats_raw.get("enable_ml_scoring", False),
    )
    # Warn on flags that are declared but have no implementation yet
    _UNIMPLEMENTED = [
        ("enable_cross_venue_matching", "cross-venue matching"),
        ("enable_wallet_intelligence", "wallet intelligence"),
        ("enable_ml_scoring", "ML scoring"),
    ]
    for _attr, _label in _UNIMPLEMENTED:
        if getattr(features, _attr):
            _log.warning(
                "config: %s is enabled but not yet implemented; the flag has no effect",
                _label,
            )
    alerts_raw = raw.get("alerts", {})
    alerts = AlertsConfig(
        default_delivery=alerts_raw.get("default_delivery", "console"),
        allowed_delivery_modes=alerts_raw.get("allowed_delivery_modes", ["console", "file"]),
        suppression_window_seconds=alerts_raw.get("suppression_window_seconds", 300),
    )
    ingest_raw = raw.get("ingestion", {})
    reconnect_raw = ingest_raw.get("reconnect", {})
    ingestion = IngestionConfig(
        raw_retention_days=ingest_raw.get("raw_retention_days", 90),
        live_api_timeout_seconds=ingest_raw.get("live_api_timeout_seconds", 10),
        polymarket_subscription_timeout_seconds=float(
            ingest_raw.get("polymarket_subscription_timeout_seconds", 30.0)
        ),
        polymarket_receive_timeout_seconds=float(
            ingest_raw.get("polymarket_receive_timeout_seconds", 60.0)
        ),
        circuit_breaker_failure_threshold=int(
            ingest_raw.get("circuit_breaker_failure_threshold", 10)
        ),
        circuit_breaker_window_seconds=float(
            ingest_raw.get("circuit_breaker_window_seconds", 300.0)
        ),
        circuit_breaker_recovery_seconds=float(
            ingest_raw.get("circuit_breaker_recovery_seconds", 60.0)
        ),
        circuit_breaker_progress_reset_min_events=int(
            ingest_raw.get("circuit_breaker_progress_reset_min_events", 2)
        ),
        directional_accumulator_max_markets=int(
            ingest_raw.get("directional_accumulator_max_markets", 5000)
        ),
        directional_accumulator_ttl_seconds=float(
            ingest_raw.get("directional_accumulator_ttl_seconds", 3600.0)
        ),
        retention_enabled=_parse_bool(ingest_raw.get("retention_enabled"), False),
        retention_operator_acknowledged=_parse_bool(
            ingest_raw.get("retention_operator_acknowledged"), False
        ),
        recovery_backlog_convergence_max_iterations=int(
            ingest_raw.get("recovery_backlog_convergence_max_iterations", 10)
        ),
        dead_letter_rate_p1_threshold_fraction=float(
            ingest_raw.get("dead_letter_rate_p1_threshold_fraction", 0.05)
        ),
        dead_letter_unresolved_halt_count=int(
            ingest_raw.get("dead_letter_unresolved_halt_count", 10000)
        ),
        pool_acquire_wait_p95_alarm_ms=int(
            ingest_raw.get("pool_acquire_wait_p95_alarm_ms", 100)
        ),
        disk_headroom_min_bytes=int(
            ingest_raw.get("disk_headroom_min_bytes", 5 * 1024 * 1024 * 1024)
        ),
        disk_headroom_min_fraction=float(
            ingest_raw.get("disk_headroom_min_fraction", 0.10)
        ),
        reconnect_initial_backoff=reconnect_raw.get("initial_backoff_seconds", 1.0),
        reconnect_max_backoff=reconnect_raw.get("max_backoff_seconds", 60.0),
        reconnect_jitter=reconnect_raw.get("jitter", True),
        kalshi_poll_interval_seconds=float(ingest_raw.get("kalshi_poll_interval_seconds", 5.0)),
        kalshi_trade_poll_limit=int(ingest_raw.get("kalshi_trade_poll_limit", 200)),
        kalshi_trade_poll_max_pages=int(ingest_raw.get("kalshi_trade_poll_max_pages", 1)),
    )
    baselines_raw = raw.get("baselines", {})
    baselines = BaselinesConfig(
        recompute_enabled=baselines_raw.get("recompute_enabled", True),
        recompute_interval_minutes=baselines_raw.get("recompute_interval_minutes", 1440),
        window_days=baselines_raw.get("window_days", 30),
        min_samples=baselines_raw.get("min_samples", 10),
    )
    health_raw = raw.get("health", {})
    health = HealthConfig(
        venue_stale_seconds=int(health_raw.get("venue_stale_seconds", 600)),
    )
    backup_raw = raw.get("backup", {})
    retention_days_raw = backup_raw.get("retention_days", None)
    backup = BackupConfig(
        backup_dir=str(backup_raw.get("backup_dir", ".pmfi-backups")),
        retention_days=None if retention_days_raw in (None, "") else int(retention_days_raw),
    )
    app_raw = raw.get("app", {})
    # Warn on deprecated live_mode_enabled
    if app_raw.get("live_mode_enabled", False):
        _log.warning(
            "config: app.live_mode_enabled is deprecated; "
            "use features.enable_polymarket_live and/or features.enable_kalshi_live instead"
        )
    return AppConfig(
        database=db, ingestion=ingestion, features=features, alerts=alerts,
        baselines=baselines, health=health,
        backup=backup,
        log_level=app_raw.get("log_level", "INFO"),
        log_file=app_raw.get("log_file", None),
        live_mode_enabled=app_raw.get("live_mode_enabled", False),
    )
