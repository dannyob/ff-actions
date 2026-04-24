#!/usr/bin/env -S uv run --script
# SPDX-License-Identifier: AGPL-3.0-or-later
# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx"]
# ///
"""
Monitor USDFC liquidity on Squid Router across multiple pairs.

Checks routes to USDFC on Filecoin from:
  - ETH (Ethereum mainnet)
  - FIL (Filecoin native)
  - USDC on Base
  - USDC on Polygon

Results land in a local SQLite database. Optionally writes a slim CSV
for downstream consumers (e.g. Google Sheets IMPORTDATA).

External services used (no credentials required):
  - Squid Router v2 API (https://v2.api.squidrouter.com/v2/route) — route quotes.
    We send the same `x-integrator-id: squid-swap-widget` that the public
    Squid widget uses; no API key is needed.
  - CoinGecko free price API (https://api.coingecko.com/api/v3/simple/price) —
    used to size each probe to roughly $10 worth of the source token so all
    pairs are comparable. Rate-limited but no key required.

Usage:
    ./usdfc-liquidity-monitor.py --once            # one-shot (CI default)
    ./usdfc-liquidity-monitor.py --loop 3600       # repeat every hour
    ./usdfc-liquidity-monitor.py --once \\
        --db data.sqlite \\
        --csv data.csv --csv-limit 20000           # with CSV export
"""

import argparse
import csv
import datetime
import json
import os
import sqlite3
import sys
import time

import httpx


SQUID_API = "https://v2.api.squidrouter.com/v2/route"
HEADERS = {
    "x-integrator-id": "squid-swap-widget",
    "Content-Type": "application/json",
}

NATIVE = "0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE"
USDFC = "0x80B98d3aa09ffff255c3ba4A241111Ff1262F045"
TARGET_USD = 10.0
COINGECKO_API = "https://api.coingecko.com/api/v3/simple/price"

PAIRS = {
    "eth-usdfc": {
        "fromChain": "1",
        "fromToken": NATIVE,
        "decimals": 18,
        "coingecko_id": "ethereum",
        "label": "ETH → USDFC",
    },
    "fil-usdfc": {
        "fromChain": "314",
        "fromToken": NATIVE,
        "decimals": 18,
        "coingecko_id": "filecoin",
        "label": "FIL → USDFC",
    },
    "base-usdc-usdfc": {
        "fromChain": "8453",
        "fromToken": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
        "decimals": 6,
        "coingecko_id": None,
        "label": "Base USDC → USDFC",
    },
    "polygon-usdc-usdfc": {
        "fromChain": "137",
        "fromToken": "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359",
        "decimals": 6,
        "coingecko_id": None,
        "label": "Polygon USDC → USDFC",
    },
}


def _ensure_parent(path: str) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)


def init_db(db_path: str) -> sqlite3.Connection:
    """Open (or create) the SQLite checks DB. Returns a live connection."""
    _ensure_parent(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS checks (
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
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_checks_ts ON checks(timestamp)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_checks_pair ON checks(pair)")
    conn.commit()
    return conn


def log_result(conn: sqlite3.Connection, r: dict) -> None:
    """Insert one probe result into the checks table. Commits immediately."""
    conn.execute(
        """
        INSERT INTO checks
          (timestamp, pair, ok, message, exchange_rate, to_amount_usd,
           price_impact, estimated_duration_s, gas_cost_usd)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            r["timestamp"],
            r["pair"],
            1 if r.get("ok") else 0,
            r.get("message"),
            r.get("exchange_rate"),
            r.get("to_amount_usd"),
            r.get("price_impact"),
            r.get("estimated_duration_s"),
            r.get("gas_cost_usd"),
        ],
    )
    conn.commit()


def export_csv(conn: sqlite3.Connection, csv_path: str, limit: int | None = None) -> int:
    """Export rows as a slim CSV (id, timestamp, pair, ok). Atomic replace.

    If `limit` is given, keep only the newest `limit` rows (by timestamp),
    written in chronological ascending order.
    Returns the number of rows written.
    """
    _ensure_parent(csv_path)
    tmp = csv_path + ".tmp"
    if limit is None:
        rows = conn.execute(
            "SELECT id, timestamp, pair, ok FROM checks ORDER BY timestamp ASC"
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT id, timestamp, pair, ok FROM (
                   SELECT id, timestamp, pair, ok FROM checks
                   ORDER BY timestamp DESC LIMIT ?
               ) ORDER BY timestamp ASC""",
            (limit,),
        ).fetchall()
    try:
        with open(tmp, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["id", "timestamp", "pair", "ok"])
            for id_, ts, pair, ok in rows:
                w.writerow([id_, ts, pair, "true" if ok else "false"])
        os.rename(tmp, csv_path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return len(rows)


def fetch_prices(client: httpx.Client) -> dict:
    """Fetch USD prices from CoinGecko. Returns {coingecko_id: price_usd}."""
    ids = [p["coingecko_id"] for p in PAIRS.values() if p["coingecko_id"]]
    if not ids:
        return {}
    try:
        resp = client.get(
            COINGECKO_API,
            params={"ids": ",".join(ids), "vs_currencies": "usd"},
            timeout=10,
        )
        data = resp.json()
        return {cid: data[cid]["usd"] for cid in ids if cid in data}
    except Exception:
        return {}


def compute_amount(pair: dict, prices: dict) -> str:
    """Compute the source-token amount targeting ~$TARGET_USD."""
    cid = pair["coingecko_id"]
    decimals = pair["decimals"]
    if cid is None:
        price = 1.0                           # stablecoin
    elif cid in prices:
        price = prices[cid]
    else:
        price = {"ethereum": 2000.0, "filecoin": 1.0}.get(cid, 1.0)
    token_amount = TARGET_USD / price
    return str(int(token_amount * (10 ** decimals)))


def _now_utc() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S+00")


def check_pair(pair_id: str, pair: dict, prices: dict, client: httpx.Client) -> dict:
    body = {
        "fromChain": pair["fromChain"],
        "fromToken": pair["fromToken"],
        "fromAmount": compute_amount(pair, prices),
        "toChain": "314",
        "toToken": USDFC,
        "toAddress": "0x0000000000000000000000000000000000000001",
        "fromAddress": "0x0000000000000000000000000000000000000001",
        "slippage": 1,
        "quoteOnly": True,
    }
    result = {"pair": pair_id, "label": pair["label"], "timestamp": _now_utc()}
    try:
        resp = client.post(SQUID_API, headers=HEADERS, json=body, timeout=30)
    except httpx.HTTPError as e:
        result.update(ok=False, message=f"Request failed: {e}")
        return result
    try:
        data = resp.json()
    except ValueError:
        result.update(ok=False, message=f"HTTP {resp.status_code}: non-JSON response")
        return result
    if resp.status_code == 200 and data.get("route"):
        estimate = data["route"].get("estimate", {})
        gas = sum(float(g.get("amountUSD", 0)) for g in estimate.get("gasCosts", []))
        result.update(
            ok=True,
            message=f"Route available: ~${estimate.get('toAmountUSD', '?')} out",
            exchange_rate=float(estimate["exchangeRate"]) if estimate.get("exchangeRate") else None,
            to_amount_usd=float(estimate["toAmountUSD"]) if estimate.get("toAmountUSD") else None,
            price_impact=float(estimate["aggregatePriceImpact"]) if estimate.get("aggregatePriceImpact") else None,
            estimated_duration_s=estimate.get("estimatedRouteDuration"),
            gas_cost_usd=round(gas, 4),
        )
    else:
        result.update(ok=False, message=data.get("message", f"HTTP {resp.status_code}"))
    return result


def run_checks(conn: sqlite3.Connection, client: httpx.Client, as_json: bool) -> bool:
    prices = fetch_prices(client)
    results = []
    all_ok = True
    for pair_id, pair in PAIRS.items():
        r = check_pair(pair_id, pair, prices, client)
        log_result(conn, r)
        results.append(r)
        if not r.get("ok"):
            all_ok = False
    if as_json:
        print(json.dumps(results, default=str))
    else:
        print(f"[{_now_utc()}]")
        for r in results:
            status = "OK  " if r.get("ok") else "FAIL"
            print(f"  {status}  {r['label']}: {r.get('message', '')}")
        print()
    return all_ok


def main() -> int:
    raise NotImplementedError


if __name__ == "__main__":
    sys.exit(main())
