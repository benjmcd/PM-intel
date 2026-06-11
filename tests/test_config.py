from __future__ import annotations
from pathlib import Path
from pmfi.config import load_config, AppConfig, DatabaseConfig

ROOT = Path(__file__).resolve().parents[1]

def test_load_config_defaults():
    cfg = load_config()
    assert isinstance(cfg, AppConfig)
    assert isinstance(cfg.database, DatabaseConfig)
    assert "5433" in cfg.database.url or "localhost" in cfg.database.url
    assert cfg.live_mode_enabled is False
    assert cfg.features.enable_polymarket_live is False

def test_load_config_from_example():
    cfg = load_config(ROOT / "config" / "app.example.yaml")
    assert cfg.alerts.default_delivery == "file"
    assert "console" in cfg.alerts.allowed_delivery_modes

def test_load_config_env_override(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost:9999/test")
    cfg = load_config()
    assert "9999" in cfg.database.url


def test_kalshi_poll_interval_default():
    """kalshi_poll_interval_seconds defaults to 5.0 when not in config."""
    cfg = load_config()
    assert cfg.ingestion.kalshi_poll_interval_seconds == 5.0


def test_kalshi_poll_interval_from_yaml(tmp_path):
    """kalshi_poll_interval_seconds is parsed from the ingestion block."""
    import yaml
    cfg_file = tmp_path / "app.yaml"
    cfg_file.write_text(
        yaml.dump({"ingestion": {"kalshi_poll_interval_seconds": 15.0}}),
        encoding="utf-8",
    )
    cfg = load_config(cfg_file)
    assert cfg.ingestion.kalshi_poll_interval_seconds == 15.0


def test_kalshi_poll_interval_from_example_yaml():
    """app.example.yaml parses kalshi_poll_interval_seconds as 5.0."""
    cfg = load_config(ROOT / "config" / "app.example.yaml")
    assert cfg.ingestion.kalshi_poll_interval_seconds == 5.0


def test_unimplemented_feature_flags_warn(tmp_path, caplog):
    """Enabling a declared-but-unimplemented/blocked feature flag emits a clear warning."""
    import logging
    import yaml
    cfg_file = tmp_path / "app.yaml"
    cfg_file.write_text(
        yaml.dump({"features": {
            "enable_wallet_intelligence": True,
            "enable_ml_scoring": True,
            "enable_cross_venue_matching": True,
        }}),
        encoding="utf-8",
    )
    with caplog.at_level(logging.WARNING, logger="pmfi.config"):
        load_config(cfg_file)
    text = " ".join(r.message for r in caplog.records)
    assert "enable_wallet_intelligence" in text
    assert "enable_ml_scoring" in text
    assert "enable_cross_venue_matching" in text
