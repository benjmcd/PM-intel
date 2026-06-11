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
    reconnect_initial_backoff: float = 1.0
    reconnect_max_backoff: float = 60.0
    reconnect_jitter: bool = True
    kalshi_poll_interval_seconds: float = 5.0

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
class AppConfig:
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    ingestion: IngestionConfig = field(default_factory=IngestionConfig)
    features: FeaturesConfig = field(default_factory=FeaturesConfig)
    alerts: AlertsConfig = field(default_factory=AlertsConfig)
    baselines: BaselinesConfig = field(default_factory=BaselinesConfig)
    health: HealthConfig = field(default_factory=HealthConfig)
    log_level: str = "INFO"
    log_file: str | None = None
    live_mode_enabled: bool = False

_KNOWN_TOP_KEYS = {"database", "features", "alerts", "ingestion", "app", "baselines", "health"}


def load_config(path: Path | None = None, *, warn_default_database_password: bool = True) -> AppConfig:
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
    if warn_default_database_password and "pmfi_local_password_change_me" in db_url:
        _log.warning(
            "config: database URL uses the well-known default password; "
            "set DATABASE_URL (and POSTGRES_PASSWORD for docker) to a non-default value"
        )
    db = DatabaseConfig(url=db_url, schema=db_section.get("schema", "pmfi"))
    feats_raw = raw.get("features", {})
    features = FeaturesConfig(
        enable_polymarket_live=feats_raw.get("enable_polymarket_live", False),
        enable_kalshi_live=feats_raw.get("enable_kalshi_live", False),
        enable_orderbook_reconstruction=feats_raw.get("enable_orderbook_reconstruction", False),
        enable_cross_venue_matching=feats_raw.get("enable_cross_venue_matching", False),
        enable_wallet_intelligence=feats_raw.get("enable_wallet_intelligence", False),
        enable_ml_scoring=feats_raw.get("enable_ml_scoring", False),
    )
    # Warn on feature flags that are declared but not implemented (or blocked) so an
    # operator who enables one is not silently misled into expecting behavior.
    if features.enable_wallet_intelligence:
        _log.warning(
            "config: enable_wallet_intelligence is set but is NOT available from the public "
            "Polymarket feed — the public market stream carries no wallet/maker/taker identity; "
            "wallet-level flow would require authenticated REST access, which is outside the "
            "current local-only scope. This flag currently has no effect."
        )
    if features.enable_ml_scoring:
        _log.info(
            "config: enable_ml_scoring enables transparent corroboration annotations only; "
            "PMFI does not use machine learning."
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
        reconnect_initial_backoff=reconnect_raw.get("initial_backoff_seconds", 1.0),
        reconnect_max_backoff=reconnect_raw.get("max_backoff_seconds", 60.0),
        reconnect_jitter=reconnect_raw.get("jitter", True),
        kalshi_poll_interval_seconds=float(ingest_raw.get("kalshi_poll_interval_seconds", 5.0)),
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
    app_raw = raw.get("app", {})
    return AppConfig(
        database=db, ingestion=ingestion, features=features, alerts=alerts,
        baselines=baselines, health=health,
        log_level=app_raw.get("log_level", "INFO"),
        log_file=app_raw.get("log_file", None),
        live_mode_enabled=app_raw.get("live_mode_enabled", False),
    )
