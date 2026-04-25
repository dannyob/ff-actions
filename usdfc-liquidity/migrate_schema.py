#!/usr/bin/env -S uv run --script
# SPDX-License-Identifier: AGPL-3.0-or-later
# /// script
# requires-python = ">=3.11"
# ///
"""One-shot migration: rebuild the checks table with proper PRIMARY KEY.

The table was seeded from an upstream export that had `id INTEGER` (no
PRIMARY KEY). Rebuild as `id INTEGER PRIMARY KEY AUTOINCREMENT` so future
INSERTs get auto-numbered ids. Rows with existing non-null ids keep them;
rows with NULL ids get fresh ids assigned by order of timestamp.
"""

import sqlite3
import sys


def migrate(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("BEGIN")
        # Create new table with correct schema.
        conn.execute("""
            CREATE TABLE checks_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                pair TEXT NOT NULL,
                ok INTEGER NOT NULL,
                message TEXT,
                exchange_rate REAL,
                to_amount_usd REAL,
                price_impact REAL,
                estimated_duration_s INTEGER,
                gas_cost_usd REAL
            )
        """)
        # Copy rows ordered by (id NULL last, then by timestamp) so NULL-id
        # rows get appended with fresh autoincrement ids after numbered ones.
        # We explicitly pass `id` so existing ids are preserved where present;
        # NULL ids get auto-filled by AUTOINCREMENT.
        conn.execute("""
            INSERT INTO checks_new
                (id, timestamp, pair, ok, message, exchange_rate, to_amount_usd,
                 price_impact, estimated_duration_s, gas_cost_usd)
            SELECT id, timestamp, pair, ok, message, exchange_rate, to_amount_usd,
                   price_impact, estimated_duration_s, gas_cost_usd
            FROM checks
            ORDER BY id IS NULL, id, timestamp
        """)
        conn.execute("DROP TABLE checks")
        conn.execute("ALTER TABLE checks_new RENAME TO checks")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_checks_ts ON checks(timestamp)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_checks_pair ON checks(pair)")
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()
    # Compact the file to drop leftover pages from the old table.
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("VACUUM")
    finally:
        conn.close()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: migrate_schema.py <path-to-sqlite>", file=sys.stderr)
        sys.exit(2)
    migrate(sys.argv[1])
    print(f"Migrated {sys.argv[1]}")
