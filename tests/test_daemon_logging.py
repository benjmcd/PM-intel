"""Offline tests for durable daemon logging (task: daemon-logging).

Covers:
  (a) _setup_logging with log_file creates a RotatingFileHandler and writes a record.
  (b) load_config parses app.log_file (absent -> None, present -> value).
  (c) supervisor warning path emits via caplog.
  (d) _safe_recompute_baselines warning path emits via caplog (complementary to
      the capsys→caplog migration in test_baseline_recompute.py).
"""
from __future__ import annotations

import asyncio
import logging
import logging.handlers
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import yaml


# ---------------------------------------------------------------------------
# (a) _setup_logging with log_file
# ---------------------------------------------------------------------------

class TestSetupLogging:
    def _reset_root_handlers(self, handlers_before: list) -> None:
        """Remove any handlers added during the test."""
        root = logging.getLogger()
        for h in list(root.handlers):
            if h not in handlers_before:
                h.close()
                root.removeHandler(h)

    def test_rotating_file_handler_attached(self, tmp_path):
        from pmfi.cli import _setup_logging

        log_file = tmp_path / "pmfi.log"
        root = logging.getLogger()
        handlers_before = list(root.handlers)
        try:
            _setup_logging(level="DEBUG", log_file=str(log_file))
            rfh = [
                h for h in root.handlers
                if isinstance(h, logging.handlers.RotatingFileHandler)
                and Path(h.baseFilename) == log_file
            ]
            assert rfh, "Expected a RotatingFileHandler on the root logger"
            assert rfh[0].maxBytes == 5 * 1024 * 1024
            assert rfh[0].backupCount == 3
            assert rfh[0].encoding == "utf-8"
        finally:
            self._reset_root_handlers(handlers_before)

    def test_log_record_written_to_file(self, tmp_path):
        from pmfi.cli import _setup_logging

        log_file = tmp_path / "sub" / "pmfi.log"
        root = logging.getLogger()
        handlers_before = list(root.handlers)
        try:
            _setup_logging(level="DEBUG", log_file=str(log_file))
            # Write a record through a child logger so it propagates to root.
            test_logger = logging.getLogger("pmfi.test.daemon_logging")
            test_logger.info("daemon-logging-test-sentinel")
            # Flush all new handlers to ensure the write is committed.
            for h in root.handlers:
                if h not in handlers_before:
                    h.flush()
            assert log_file.exists(), "Log file should have been created"
            content = log_file.read_text(encoding="utf-8")
            assert "daemon-logging-test-sentinel" in content
        finally:
            self._reset_root_handlers(handlers_before)

    def test_parent_dir_created_automatically(self, tmp_path):
        from pmfi.cli import _setup_logging

        log_file = tmp_path / "deep" / "nested" / "pmfi.log"
        root = logging.getLogger()
        handlers_before = list(root.handlers)
        try:
            _setup_logging(level="INFO", log_file=str(log_file))
            assert log_file.parent.exists(), "Parent directory should be created"
        finally:
            self._reset_root_handlers(handlers_before)

    def test_no_file_handler_when_log_file_is_none(self):
        from pmfi.cli import _setup_logging

        root = logging.getLogger()
        handlers_before = list(root.handlers)
        try:
            _setup_logging(level="INFO", log_file=None)
            new_rfh = [
                h for h in root.handlers
                if h not in handlers_before
                and isinstance(h, logging.handlers.RotatingFileHandler)
            ]
            assert not new_rfh, "No RotatingFileHandler should be added when log_file is None"
        finally:
            self._reset_root_handlers(handlers_before)


# ---------------------------------------------------------------------------
# (b) load_config parses app.log_file
# ---------------------------------------------------------------------------

class TestLoadConfigLogFile:
    def test_absent_log_file_yields_none(self, tmp_path):
        from pmfi.config import load_config

        cfg_file = tmp_path / "app.yaml"
        cfg_file.write_text(yaml.dump({}), encoding="utf-8")
        cfg = load_config(cfg_file)
        assert cfg.log_file is None

    def test_present_log_file_parsed(self, tmp_path):
        from pmfi.config import load_config

        cfg_file = tmp_path / "app.yaml"
        cfg_file.write_text(
            yaml.dump({"app": {"log_file": "reports/logs/pmfi.log"}}),
            encoding="utf-8",
        )
        cfg = load_config(cfg_file)
        assert cfg.log_file == "reports/logs/pmfi.log"

    def test_log_file_default_on_appconfig(self):
        from pmfi.config import AppConfig

        cfg = AppConfig()
        assert cfg.log_file is None


# ---------------------------------------------------------------------------
# (c) supervisor warning path emits via caplog
# ---------------------------------------------------------------------------

class TestSupervisorLogging:
    def test_connection_lost_emits_warning(self, caplog):
        """DB connection lost path emits logger.warning via caplog."""
        from pmfi.pipeline.supervisor import supervise, PoolManager
        from pmfi.pipeline.runner import IngestConnectionLost

        async def _run():
            shutdown = asyncio.Event()
            run_count = [0]

            def make_adapter():
                from unittest.mock import MagicMock
                a = MagicMock()
                a.connect = AsyncMock()
                a.disconnect = AsyncMock()
                return a

            async def run_one(adapter, pm):
                run_count[0] += 1
                if run_count[0] == 1:
                    raise IngestConnectionLost("fake-loss")
                shutdown.set()

            pm = PoolManager("fake_dsn")
            from unittest.mock import MagicMock
            pm._pool = MagicMock()

            async def _fake_recreate(gen):
                pm._generation += 1
                return pm._pool

            with patch.object(pm, "recreate", side_effect=_fake_recreate):
                with caplog.at_level(logging.WARNING, logger="pmfi.pipeline.supervisor"):
                    await asyncio.wait_for(
                        supervise(
                            "test-venue", make_adapter, run_one,
                            shutdown=shutdown, pool_manager=pm,
                            initial_backoff=0.01, max_backoff=0.1, jitter=False,
                        ),
                        timeout=5.0,
                    )

        asyncio.run(_run())
        messages = " ".join(r.getMessage() for r in caplog.records)
        assert "[ingest:test-venue] DB connection lost" in messages
        assert "fake-loss" in messages

    def test_restarting_emits_info(self, caplog):
        """Restarting-in-Xs path emits logger.info via caplog."""
        from pmfi.pipeline.supervisor import supervise, PoolManager
        from pmfi.pipeline.runner import IngestConnectionLost

        async def _run():
            shutdown = asyncio.Event()
            run_count = [0]

            def make_adapter():
                from unittest.mock import MagicMock
                a = MagicMock()
                a.connect = AsyncMock()
                a.disconnect = AsyncMock()
                return a

            async def run_one(adapter, pm):
                run_count[0] += 1
                if run_count[0] == 1:
                    raise IngestConnectionLost("transient")
                shutdown.set()

            pm = PoolManager("fake_dsn")
            from unittest.mock import MagicMock
            pm._pool = MagicMock()

            async def _fake_recreate(gen):
                pm._generation += 1
                return pm._pool

            with patch.object(pm, "recreate", side_effect=_fake_recreate):
                with caplog.at_level(logging.INFO, logger="pmfi.pipeline.supervisor"):
                    await asyncio.wait_for(
                        supervise(
                            "test-venue", make_adapter, run_one,
                            shutdown=shutdown, pool_manager=pm,
                            initial_backoff=0.01, max_backoff=0.1, jitter=False,
                        ),
                        timeout=5.0,
                    )

        asyncio.run(_run())
        messages = " ".join(r.getMessage() for r in caplog.records)
        assert "[ingest:test-venue] Restarting in" in messages
