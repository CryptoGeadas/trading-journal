# Developer setup

How to run the journal from source and where things are. For installing it as
a user, see the [README](../README.md); for coding agents, [AGENTS.md](../AGENTS.md).

## Run from source

```bash
python -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"   # Windows: .venv\Scripts\python -m pip install -e ".[dev]"
.venv/bin/python -m app --no-browser          # http://127.0.0.1:8000
.venv/bin/python -m pytest
```

Set `TRADING_JOURNAL_DATA_DIR` to a throwaway folder while developing, so you
never touch your real journal. The tests do this for you (tests/conftest.py).

## How it fits together

```
Browser page (frontend/)  <->  FastAPI (app/)  <->  SQLite in the data folder
                                    |
                         exchange APIs, read-only, via ccxt
```

- `app/server.py` — `python -m app`: starts on 127.0.0.1 and opens the browser.
- `app/main.py` — the FastAPI app, optional login, security headers, routes.
- `app/paths.py` — every user-data path (data folder outside the repo).
- `app/database.py` — schema and numbered migrations, run on start.
- `app/services/pnl_engine.py` — the one matching engine behind every PnL
  number (FIFO / average cost, shorts, fees, funding, currencies).
- `app/services/demo.py` — demo data (load / remove).
- `app/services/backup.py` — backup ZIPs (SQLite backup API + key file).
- `app/exchanges/` — one adapter per exchange plus `sync.py`, which runs a
  sync and keeps a per-pair start point.
- `app/api/` — one route file per page or feature.
- `frontend/` — one page, plain JavaScript; `frontend/vendor/` holds the
  bundled fonts, chart library and the snapshot of the site's style tokens.

## Rules the code keeps

- No order placement anywhere.
- Nothing on the page loads from outside (tests/test_offline.py checks this).
- Money is never added across currencies; unknown values are shown as
  unknown, never as 0.
