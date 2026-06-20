from __future__ import annotations
from pathlib import Path
from pmfi.config import load_config, AppConfig, DatabaseConfig

ROOT = Path(__file__).resolve().parents[1]

def test_load_config_defaults():
    cfg = load_config(ROOT / "config" / "app.example.yaml")
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


def test_database_pool_size_from_yaml(tmp_path):
    import yaml
    cfg_file = tmp_path / "app.yaml"
    cfg_file.write_text(
        yaml.dump({"database": {"pool_min_size": 2, "pool_max_size": 7}}),
        encoding="utf-8",
    )

    cfg = load_config(cfg_file)

    assert cfg.database.pool_min_size == 2
    assert cfg.database.pool_max_size == 7


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


def test_polymarket_silent_stream_watchdogs_from_yaml(tmp_path):
    """Polymarket silence watchdogs are parsed from the ingestion block."""
    import yaml
    cfg_file = tmp_path / "app.yaml"
    cfg_file.write_text(
        yaml.dump(
            {
                "ingestion": {
                    "polymarket_subscription_timeout_seconds": 7.5,
                    "polymarket_receive_timeout_seconds": 45.0,
                }
            }
        ),
        encoding="utf-8",
    )

    cfg = load_config(cfg_file)

    assert cfg.ingestion.polymarket_subscription_timeout_seconds == 7.5
    assert cfg.ingestion.polymarket_receive_timeout_seconds == 45.0


def test_polymarket_silent_stream_watchdogs_from_example_yaml():
    cfg = load_config(ROOT / "config" / "app.example.yaml")

    assert cfg.ingestion.polymarket_subscription_timeout_seconds == 30.0
    assert cfg.ingestion.polymarket_receive_timeout_seconds == 60.0


def test_unattended_durability_settings_from_yaml(tmp_path):
    import yaml
    cfg_file = tmp_path / "app.yaml"
    cfg_file.write_text(
        yaml.dump(
            {
                "ingestion": {
                    "circuit_breaker_failure_threshold": 4,
                    "circuit_breaker_window_seconds": 120,
                    "directional_accumulator_max_markets": 25,
                    "directional_accumulator_ttl_seconds": 900,
                }
            }
        ),
        encoding="utf-8",
    )

    cfg = load_config(cfg_file)

    assert cfg.ingestion.circuit_breaker_failure_threshold == 4
    assert cfg.ingestion.circuit_breaker_window_seconds == 120.0
    assert cfg.ingestion.directional_accumulator_max_markets == 25
    assert cfg.ingestion.directional_accumulator_ttl_seconds == 900.0


def test_unattended_durability_settings_from_example_yaml():
    cfg = load_config(ROOT / "config" / "app.example.yaml")

    assert cfg.ingestion.circuit_breaker_failure_threshold == 10
    assert cfg.ingestion.circuit_breaker_window_seconds == 300.0
    assert cfg.ingestion.directional_accumulator_max_markets == 5000
    assert cfg.ingestion.directional_accumulator_ttl_seconds == 3600.0
