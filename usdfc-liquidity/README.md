# USDFC Bridge Liquidity

Hourly probes of four cross-chain routes into USDFC on Filecoin, via the public
[Squid Router](https://squidrouter.com) API.

## What's measured

Each hour a probe asks Squid for a route quote to swap ~$10 worth of a source
token into USDFC (on Filecoin). If Squid returns a valid route, the probe is
a success; if it returns "Low liquidity" or any other error, it's a failure.

Squid routes through Axelar's cross-chain messaging plus DEX liquidity on each
chain, so a failure reflects Squid's end-to-end quote — not necessarily any
single underlying bridge.

### Routes

| pair slug            | source chain             |
|----------------------|--------------------------|
| `eth-usdfc`          | Ethereum mainnet (native ETH) |
| `fil-usdfc`          | Filecoin (native FIL)    |
| `base-usdc-usdfc`    | Base (USDC)              |
| `polygon-usdc-usdfc` | Polygon (USDC)           |

## Files

- `usdfc-liquidity.sqlite` — canonical data store. Full history since 2026-03-23.
  Open with any SQLite client:
  ```
  sqlite3 usdfc-liquidity.sqlite "SELECT * FROM checks ORDER BY timestamp DESC LIMIT 10"
  ```
- `usdfc-liquidity.csv` — rolling window of the newest 20,000 rows, 4 columns
  (`id,timestamp,pair,ok`). Suitable for Google Sheets `IMPORTDATA`:
  ```
  =IMPORTDATA("https://raw.githubusercontent.com/dannyob/ff-actions/main/usdfc-liquidity/usdfc-liquidity.csv")
  ```
- `usdfc-liquidity-monitor.py` — the probe script (PEP 723 uv script, AGPL-3.0).
  Run locally with `uv run --script ./usdfc-liquidity-monitor.py --once`.

## Schema

```
CREATE TABLE checks (
    id INTEGER PRIMARY KEY,
    timestamp TEXT,                -- ISO 8601 UTC, e.g. "2026-04-24 10:00:00+00"
    pair TEXT,                     -- one of the slugs above
    ok INTEGER,                    -- 1 if Squid returned a route, 0 otherwise
    message TEXT,                  -- Squid's message or error string
    exchange_rate REAL,
    to_amount_usd REAL,
    price_impact REAL,
    estimated_duration_s INTEGER,
    gas_cost_usd REAL
);
```

## How it runs

See `.github/workflows/usdfc-liquidity.yaml` — a GH Actions cron job fires at
`0 * * * *`, runs the script once, commits the updated SQLite + CSV back.
