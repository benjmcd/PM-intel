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

@dataclass
class FeaturesConfig:
    enable_polymarket_live: bool = False
    enable_kalshi_live: bool = False
    enable_orderbook_reconstruction: bool = False

@dataclass
class AlertsConfig:
    default_delivery: str = "console"
    allowed_delivery_modes: list[str] = field(default_factory=lambda: ["console", "file"])
    suppression_window_seconds: int = 300

@dataclass
class AppConfig:
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    ingestion: IngestionConfig = field(default_factory=IngestionConfig)
    features: FeaturesConfig = field(default_factory=FeaturesConfig)
    alerts: AlertsConfig = field(default_factory=AlertsConfig)
    log_level: str = "INFO"
    live_mode_enabled: bool = False

def load_config(path: Path | None = None) -> AppConfig:
    if path is None:
        for candidate in [ROOT / "config" / "app.yaml", ROOT / "config" / "app.example.yaml"]:
            if candidate.exists():
                path = candidate
                break
    raw: dict = {}
    if path and path.exists():
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    db_section = raw.get("database", {})
    db_url = os.environ.get("DATABASE_URL") or db_section.get("url", "postgresql://pmfi:pmfi_local_password_change_me@localhost:5433/pmfi")
    db = DatabaseConfig(url=db_url, schema=db_section.get("schema", "pmfi"))
    feats_raw = raw.get("features", {})
    features = FeaturesConfig(
        enable_polymarket_live=feats_raw.get("enable_polymarket_live", False),
        enable_kalshi_live=feats_raw.get("enable_kalshi_live", False),
    )
    alerts_raw = raw.get("alerts", {})
    alerts = AlertsConfig(
        default_delivery=alerts_raw.get("default_delivery", "console"),
        allowed_delivery_modes=alerts_raw.get("allowed_delivery_modes", ["console", "file"]),
        suppression_window_seconds=alerts_raw.get("suppression_window_seconds", 300),
    )
    ingest_raw = raw.get("ingestion", {})
    ingestion = IngestionConfig(
        raw_retention_days=ingest_raw.get("raw_retention_days", 90),
        live_api_timeout_seconds=ingest_raw.get("live_api_timeout_seconds", 10),
    )
    app_raw = raw.get("app", {})
    return AppConfig(
        database=db, ingestion=ingestion, features=features, alerts=alerts,
        log_level=app_raw.get("log_level", "INFO"),
        live_mode_enabled=app_raw.get("live_mode_enabled", False),
    )
