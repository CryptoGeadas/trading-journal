# Trading Journal

A self-hosted crypto trading journal that connects to your exchange accounts (read-only), syncs your trade history, and provides analytics through a browser dashboard.

**Supported exchanges:** Binance · Gate.io  
**Note:** Bybit currently requires third-party app approval for API key creation, which blocks direct use.

---

## Quick Start

### Requirements

- Python 3.11+
- pip

### Install & Run

```bash
cd trading-journal
pip install fastapi uvicorn pydantic python-multipart ccxt apscheduler cryptography openpyxl
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open **http://localhost:8000** in your browser.

### Docker (alternative)

```bash
docker-compose up --build
```

---

## Features

### Exchange Connectivity (Phase 1)
- Connect to Binance and Gate.io with **read-only API keys**
- Permission validation — rejects keys with trading/withdrawal access
- API credentials encrypted at rest (Fernet encryption)
- Automatic sync every 6 hours + manual "Sync Now" button
- Smart pair discovery: finds trades via balance detection + `extra_pairs.txt` for fully-sold positions
- Gate.io and Binance-specific sync handling (per-symbol fetching, time filter quirks)
- Sync log with status tracking and error messages
- 2026 trades only (configurable in sync engine)

### Trades Table (Phase 1–2)
- Full trade table with sorting (time, pair, exchange, total) and pagination
- **Order grouping**: multiple fills from the same order shown as one row with combined totals
- Expandable fills: click the fill count to see individual execution details
- Filter bar: by exchange, pair, side, strategy, and date range
- **Chart screenshots**: upload up to 2 chart images per trade, shown as thumbnails with click-to-enlarge lightbox

### Export (Phase 2)
- Download filtered trades as **CSV** or **XLSX**
- Exports respect all active filters (date range, exchange, pair, side, strategy)
- Grouped orders exported as single rows with combined quantities and weighted average prices

### Strategy System (Phase 2)
- **Strategies page**: create, edit, and delete trading strategies
- Each strategy has: name, colour, short description, and a playbook (rules, entry/exit criteria, notes)
- 9 colour options for visual differentiation
- **Trade tagging**: click the Strategy column on any trade to assign a strategy from a dropdown
- Strategy-coloured badges in the trades table
- **Import/Export**: download all strategies as JSON backup, restore from file (protects against database resets)

### Dashboard (Phase 0)
- Overview screen with stat cards (total trades, fees, exchange count)
- PnL stats are placeholder — full calculation coming in Phase 3

---

## Project Structure

```
trading-journal/
├── app/
│   ├── main.py               # FastAPI entry point + route registration
│   ├── config.py             # Environment settings
│   ├── database.py           # SQLite + versioned migrations (v1–v4)
│   ├── models.py             # Pydantic request/response schemas
│   ├── encryption.py         # API key encryption (Fernet)
│   ├── scheduler.py          # APScheduler for periodic sync
│   ├── exchanges/
│   │   ├── connector.py      # ccxt wrapper + permission validation
│   │   └── sync.py           # Trade sync engine with pair discovery
│   └── api/
│       ├── routes_overview.py
│       ├── routes_trades.py   # Grouped orders, fill expansion, tagging
│       ├── routes_exchanges.py
│       ├── routes_pnl.py      # Stub — Phase 3
│       ├── routes_export.py   # CSV/XLSX export
│       ├── routes_strategies.py # Strategy CRUD + import/export
│       └── routes_screenshots.py # Upload/serve/delete trade screenshots
├── frontend/
│   ├── index.html            # Single-page dashboard
│   ├── css/app.css           # Dark terminal theme
│   └── js/
│       ├── api.js            # API client
│       └── app.js            # Navigation, rendering, interactions
├── data/
│   ├── journal.db            # SQLite database (auto-created)
│   ├── .encryption_key       # Fernet key (auto-generated)
│   ├── extra_pairs.txt       # Additional pairs for sync discovery
│   └── screenshots/          # Uploaded chart images
├── docker-compose.yml
├── Dockerfile
├── pyproject.toml
└── README.md
```

---

## Database Migrations

The app uses versioned migrations that run automatically on startup:

| Version | What it adds |
|---------|-------------|
| v1 | exchanges, trades, sync_log tables + indexes |
| v2 | order_id column on trades (fill grouping) |
| v3 | strategies table |
| v4 | trade_screenshots table |

You never need to run migrations manually. If you delete `journal.db`, the app recreates everything from scratch.

---

## API Documentation

When the server is running, visit **http://localhost:8000/docs** for the auto-generated Swagger UI listing all endpoints.

---

## Roadmap

| Phase | Status | Description |
|-------|--------|-------------|
| Phase 0 — Skeleton | ✅ Done | Project structure, empty dashboard, API scaffolding |
| Phase 1 — Exchange Connectivity | ✅ Done | Binance + Gate.io sync, permission validation, encryption |
| Phase 2 — Trades & Features | ✅ Done | Export, strategy system, order grouping, screenshots |
| Phase 3 — PnL & Analytics | 🔲 Next | PnL calculations (FIFO/avg cost), win rate, performance stats |
| Phase 4 — Hardening | 🔲 | Error handling, mobile responsive, backup/restore |
| Future — AI Layer | 🔲 | Optional conversational interface over the existing API |

---

## Data Safety

- **All data stays local** — SQLite file + screenshot images on your machine
- **API keys encrypted at rest** — never stored in plaintext, never logged
- **Read-only enforcement** — the app validates API key permissions and rejects keys with trading access
- **No trading capability** — the codebase has no functions that can place, modify, or cancel orders
- **Strategy backup** — export/import as JSON to protect against database resets
- **Extra pairs file** — `data/extra_pairs.txt` survives database resets (add fully-sold pairs here)
