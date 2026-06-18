from __future__ import annotations
from pathlib import Path
import pytest
from pmfi.config import AppConfig, DatabaseConfig, FeaturesConfig, enabled_unsupported_features, load_config

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
    assert enabled_unsupported_features(cfg.features) == []

def test_load_config_env_override(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost:9999/test")
    cfg = load_config()
    assert "9999" in cfg.database.url


def test_enabled_unsupported_features_lists_future_flags_only():
    features = FeaturesConfig(
        enable_polymarket_live=True,
        enable_kalshi_live=True,
        enable_orderbook_reconstruction=True,
        enable_cross_venue_matching=True,
        enable_wallet_intelligence=True,
        enable_ml_scoring=True,
    )

    assert enabled_unsupported_features(features) == [
        "enable_cross_venue_matching",
        "enable_wallet_intelligence",
        "enable_ml_scoring",
    ]

def test_load_config_rejects_default_delivery_not_allowed(tmp_path):
    cfg_path = tmp_path / "app.yaml"
    cfg_path.write_text(
        """
alerts:
  default_delivery: localhost_http_receiver
  allowed_delivery_modes:
    - console
    - file
""".lstrip(),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="alerts.default_delivery"):
        load_config(cfg_path)
