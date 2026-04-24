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


import csv as _csv  # avoid clash with monitor.csv usage


def _seed_rows(conn, n: int) -> None:
    for i in range(n):
        ts = f"2026-04-24 10:{i:02d}:00+00"
        conn.execute(
            "INSERT INTO checks (timestamp, pair, ok) VALUES (?, ?, ?)",
            [ts, "eth-usdfc", i % 2],
        )
    conn.commit()


class ExportCsvTests(unittest.TestCase):
    def test_writes_header_and_all_rows_in_ascending_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            conn = monitor.init_db(os.path.join(tmpdir, "t.sqlite"))
            _seed_rows(conn, 5)
            csv_path = os.path.join(tmpdir, "out.csv")
            n = monitor.export_csv(conn, csv_path)
            conn.close()
            with open(csv_path, newline="") as f:
                rows = list(_csv.reader(f))
        self.assertEqual(n, 5)
        self.assertEqual(rows[0], ["id", "timestamp", "pair", "ok"])
        timestamps = [r[1] for r in rows[1:]]
        self.assertEqual(timestamps, sorted(timestamps))

    def test_ok_column_emits_true_false_strings(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            conn = monitor.init_db(os.path.join(tmpdir, "t.sqlite"))
            conn.execute("INSERT INTO checks (timestamp, pair, ok) VALUES (?, ?, 1)",
                         ["2026-04-24 10:00:00+00", "eth-usdfc"])
            conn.execute("INSERT INTO checks (timestamp, pair, ok) VALUES (?, ?, 0)",
                         ["2026-04-24 10:05:00+00", "eth-usdfc"])
            conn.commit()
            csv_path = os.path.join(tmpdir, "out.csv")
            monitor.export_csv(conn, csv_path)
            conn.close()
            with open(csv_path, newline="") as f:
                rows = list(_csv.reader(f))
        # rows[0] is header; rows[1], rows[2] are data
        self.assertEqual(rows[1][3], "true")
        self.assertEqual(rows[2][3], "false")

    def test_limit_keeps_newest_rows_in_chronological_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            conn = monitor.init_db(os.path.join(tmpdir, "t.sqlite"))
            _seed_rows(conn, 10)  # 10:00..10:09
            csv_path = os.path.join(tmpdir, "out.csv")
            n = monitor.export_csv(conn, csv_path, limit=3)
            conn.close()
            with open(csv_path, newline="") as f:
                rows = list(_csv.DictReader(f))
        self.assertEqual(n, 3)
        minutes = [r["timestamp"][14:16] for r in rows]  # extract MM
        self.assertEqual(minutes, ["07", "08", "09"])

    def test_replaces_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            conn = monitor.init_db(os.path.join(tmpdir, "t.sqlite"))
            _seed_rows(conn, 2)
            csv_path = os.path.join(tmpdir, "out.csv")
            monitor.export_csv(conn, csv_path)
            with open(csv_path) as f:
                self.assertEqual(len(f.readlines()), 3)  # header + 2
            _seed_rows(conn, 5)  # adds 5 more starting at minute 00..04 (dup minutes, but ids unique)
            monitor.export_csv(conn, csv_path)
            with open(csv_path) as f:
                lines = f.readlines()
            conn.close()
        self.assertEqual(len(lines), 8)  # header + 7

    def test_no_tmp_file_left_behind(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            conn = monitor.init_db(os.path.join(tmpdir, "t.sqlite"))
            _seed_rows(conn, 1)
            csv_path = os.path.join(tmpdir, "out.csv")
            monitor.export_csv(conn, csv_path)
            conn.close()
            tmps = [f for f in os.listdir(tmpdir) if f.endswith(".tmp")]
        self.assertEqual(tmps, [])

    def test_creates_missing_parent_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            conn = monitor.init_db(os.path.join(tmpdir, "t.sqlite"))
            _seed_rows(conn, 1)
            csv_path = os.path.join(tmpdir, "nested", "dir", "out.csv")
            monitor.export_csv(conn, csv_path)
            conn.close()
            self.assertTrue(os.path.exists(csv_path))


from unittest.mock import MagicMock, patch


class RunChecksTests(unittest.TestCase):
    def test_run_checks_adds_one_row_per_pair(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            conn = monitor.init_db(os.path.join(tmpdir, "t.sqlite"))

            fake_resp = MagicMock()
            fake_resp.status_code = 200
            fake_resp.json.return_value = {
                "route": {
                    "estimate": {
                        "exchangeRate": "0.0005",
                        "toAmountUSD": "9.94",
                        "aggregatePriceImpact": "0.0",
                        "estimatedRouteDuration": 120,
                        "gasCosts": [{"amountUSD": "0.25"}],
                    }
                }
            }
            client = MagicMock()
            client.post.return_value = fake_resp
            client.get.return_value = MagicMock(
                json=MagicMock(return_value={"ethereum": {"usd": 2000}, "filecoin": {"usd": 1}})
            )

            monitor.run_checks(conn, client, as_json=False)
            rows = conn.execute("SELECT pair, ok FROM checks").fetchall()
            conn.close()

        self.assertEqual(len(rows), len(monitor.PAIRS))
        self.assertEqual({r[0] for r in rows}, set(monitor.PAIRS.keys()))
        self.assertTrue(all(r[1] == 1 for r in rows))


if __name__ == "__main__":
    unittest.main()
