import importlib.util
import os
import sys
import unittest

# Load the dash-named script as a module.
_HERE = os.path.dirname(__file__)
_SCRIPT = os.path.join(_HERE, "..", "usdfc-liquidity-monitor.py")
_spec = importlib.util.spec_from_file_location("monitor", _SCRIPT)
monitor = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(monitor)


import sqlite3
import tempfile


class InitDbTests(unittest.TestCase):
    def test_init_db_creates_table_with_expected_columns(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = os.path.join(tmpdir, "test.sqlite")
            conn = monitor.init_db(db)
            cols = [r[1] for r in conn.execute("PRAGMA table_info(checks)").fetchall()]
            conn.close()
            self.assertEqual(
                cols,
                ["id", "timestamp", "pair", "ok", "message", "exchange_rate",
                 "to_amount_usd", "price_impact", "estimated_duration_s",
                 "gas_cost_usd"],
            )

    def test_init_db_creates_indexes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = os.path.join(tmpdir, "test.sqlite")
            conn = monitor.init_db(db)
            idxs = {
                r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='index'"
                ).fetchall()
            }
            conn.close()
            self.assertIn("idx_checks_ts", idxs)
            self.assertIn("idx_checks_pair", idxs)

    def test_init_db_creates_parent_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = os.path.join(tmpdir, "nested", "dir", "test.sqlite")
            conn = monitor.init_db(db)
            conn.close()
            self.assertTrue(os.path.exists(db))


class LogResultTests(unittest.TestCase):
    def _result(self, **overrides) -> dict:
        base = {
            "timestamp": "2026-04-24 10:00:00+00",
            "pair": "eth-usdfc",
            "ok": True,
            "message": "Route available",
            "exchange_rate": 0.0005,
            "to_amount_usd": 9.99,
            "price_impact": -0.1,
            "estimated_duration_s": 120,
            "gas_cost_usd": 0.5,
        }
        base.update(overrides)
        return base

    def test_log_result_writes_one_row(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            conn = monitor.init_db(os.path.join(tmpdir, "t.sqlite"))
            monitor.log_result(conn, self._result())
            rows = conn.execute(
                "SELECT timestamp, pair, ok, message FROM checks"
            ).fetchall()
            conn.close()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0], ("2026-04-24 10:00:00+00", "eth-usdfc", 1, "Route available"))

    def test_log_result_handles_missing_optional_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            conn = monitor.init_db(os.path.join(tmpdir, "t.sqlite"))
            # Simulate a failed probe: no exchange_rate, no to_amount_usd, etc.
            monitor.log_result(
                conn,
                {
                    "timestamp": "2026-04-24 10:00:00+00",
                    "pair": "eth-usdfc",
                    "ok": False,
                    "message": "Low liquidity",
                },
            )
            row = conn.execute(
                "SELECT ok, message, exchange_rate FROM checks"
            ).fetchone()
            conn.close()
        self.assertEqual(row, (0, "Low liquidity", None))

    def test_log_result_commits_persistently(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = os.path.join(tmpdir, "t.sqlite")
            conn = monitor.init_db(db)
            monitor.log_result(conn, self._result())
            conn.close()
            # Reopen and confirm the row survived.
            conn2 = sqlite3.connect(db)
            count = conn2.execute("SELECT COUNT(*) FROM checks").fetchone()[0]
            conn2.close()
        self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main()
