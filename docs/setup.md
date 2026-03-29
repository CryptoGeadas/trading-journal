# Trading Journal

A self-hosted crypto trading journal that connects to your exchange accounts (read-only), syncs your trade history, and provides analytics through a browser dashboard.

**Supported exchanges:** Binance · Bybit · Gate.io

## Quick Start

### Option A: Docker (recommended)

```bash
# 1. Clone or copy this folder
# 2. Start everything with one command:
docker-compose up --build

# 3. Open your browser:
#    http://localhost:8000
```

### Option B: Run directly with Python

```bash
# 1. Requires Python 3.11+
python -m pip install .

# 2. Start the server
uvicorn app.main:app --host 0.0.0.0 --port 8000

# 3. Open your browser:
#    http://localhost:8000
```

## Project Status

| Phase | Status | What it does |
|-------|--------|-------------|
| Phase 0 — Skeleton | ✅ Done | Empty dashboard, database, all API endpoints scaffolded |
| Phase 1 — Exchange Connectivity | 🔲 Next | Connect to Binance/Bybit/Gate.io, sync trades |
| Phase 2 — Trades & Filtering | 🔲 | Browse, filter, tag, and export trades |
| Phase 3 — PnL & Analytics | 🔲 | PnL calculations, win rate, performance stats |
| Phase 4 — Hardening | 🔲 | Encryption, error handling, backups, mobile |

## Architecture

```
Browser Dashboard  ←→  FastAPI (Python)  ←→  SQLite Database
                            ↕
                    Exchange APIs (read-only)
                    via ccxt library
```

All data stays local. No external services. No trading capability.

## File Structure

```
trading-journal/
├── app/
│   ├── main.py              # FastAPI entry point
│   ├── config.py            # Environment settings
│   ├── database.py          # SQLite + migrations
│   ├── models.py            # Data schemas
│   └── api/
│       ├── routes_overview.py
│       ├── routes_trades.py
│       ├── routes_exchanges.py
│       ├── routes_pnl.py
│       └── routes_export.py
├── frontend/
│   ├── index.html           # Dashboard (single page)
│   ├── css/app.css          # Dark theme styles
│   └── js/
│       ├── api.js           # API client
│       └── app.js           # Navigation, data loading
├── data/
│   └── journal.db           # SQLite database (auto-created)
├── docker-compose.yml
├── Dockerfile
└── pyproject.toml
```

## API Docs

When the server is running, visit:
- **Swagger UI:** http://localhost:8000/docs
- **Health check:** http://localhost:8000/health
