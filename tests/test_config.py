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
    assert cfg.alerts.default_delivery == "console"
    assert "console" in cfg.alerts.allowed_delivery_modes

def test_load_config_env_override(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost:9999/test")
    cfg = load_config()
    assert "9999" in cfg.database.url
