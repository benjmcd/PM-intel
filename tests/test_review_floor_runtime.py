from __future__ import annotations

import asyncio
import logging


class _Acquire:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _Conn:
    def __init__(self, rows):
        self.rows = rows
        self.queries: list[tuple[str, list[str]]] = []

    async def fetch(self, query, rule_keys):
        self.queries.append((query, list(rule_keys)))
        return self.rows


class _Pool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        return _Acquire(self.conn)


def test_warn_below_fp_review_floors_warns_for_enabled_rules_below_floor(caplog):
    from pmfi.commands.daemon import warn_below_fp_review_floors

    conn = _Conn([
        {"rule_key": "ready_rule", "reviewed": 5},
        {"rule_key": "thin_rule", "reviewed": 4},
    ])
    rules = {
        "rules": {
            "thin_rule": {
                "enabled": True,
                "acceptable_fp_rate_percent": 15,
                "min_reviewed_for_fp_rate_breach": 5,
            },
            "ready_rule": {
                "enabled": True,
                "acceptable_fp_rate_percent": 15,
                "min_reviewed_for_fp_rate_breach": 5,
            },
            "disabled_rule": {
                "enabled": False,
                "acceptable_fp_rate_percent": 15,
                "min_reviewed_for_fp_rate_breach": 5,
            },
        }
    }

    with caplog.at_level(logging.WARNING, logger="pmfi.commands.daemon"):
        below = asyncio.run(
            warn_below_fp_review_floors(_Pool(conn), rules, context="test-ingest")
        )

    assert below == [{"rule_key": "thin_rule", "reviewed": 4, "min_reviewed": 5}]
    assert "rule=thin_rule reviewed=4 min_reviewed_for_fp_rate_breach=5" in caplog.text
    assert "ready_rule" not in caplog.text
    assert "disabled_rule" not in caplog.text
    assert set(conn.queries[0][1]) == {"ready_rule", "thin_rule"}


def test_warn_below_fp_review_floors_is_quiet_at_or_above_floor(caplog):
    from pmfi.commands.daemon import warn_below_fp_review_floors

    conn = _Conn([{"rule_key": "ready_rule", "reviewed": 5}])
    rules = {
        "rules": {
            "ready_rule": {
                "enabled": True,
                "acceptable_fp_rate_percent": 15,
                "min_reviewed_for_fp_rate_breach": 5,
            },
        }
    }

    with caplog.at_level(logging.WARNING, logger="pmfi.commands.daemon"):
        below = asyncio.run(
            warn_below_fp_review_floors(_Pool(conn), rules, context="test-ingest")
        )

    assert below == []
    assert "below FP-rate review floor" not in caplog.text
