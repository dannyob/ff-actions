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


if __name__ == "__main__":
    unittest.main()
