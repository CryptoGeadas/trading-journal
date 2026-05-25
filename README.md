# Trading Journal

A self-hosted crypto trading journal that connects to your exchange accounts, syncs your trade history, and provides analytics through a browser dashboard.

**Supported exchanges:** Binance (spot + USDT/USDC-margined futures) · Gate.io (spot) · Kraken (spot)

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

### Configuration

Copy `.env.example` to `.env` and adjust as needed:

```bash
cp .env.example .env
```

| Variable | Default | Description |
|----------|---------|-------------|
| `HOST` | `0.0.0.0` | Server bind address |
| `PORT` | `8000` | Server port |
| `SYNC_INTERVAL_HOURS` | `6` | How often to pull new trades from exchanges |
| `DEFAULT_QUOTE_CURRENCY` | `USDT` | Default quote currency for display |
| `DEFAULT_PNL_METHOD` | `fifo` | PnL calculation method (`fifo` or `avg_cost`) |
| `AUTH_USER` | *(empty)* | Basic auth username (leave empty to disable auth) |
| `AUTH_PASS` | *(empty)* | Basic auth password (leave empty to disable auth) |
| `BACKUP_RETENTION_COUNT` | `7` | Number of auto-backups to keep |

---

## Features

### Exchange Connectivity
- Connect to **Binance** (spot + futures), **Gate.io** (spot), and **Kraken** (spot) with API keys
- **Adapter-based sync engine**: each exchange has its own adapter handling exchange-specific quirks, with a shared orchestrator
- **Permission validation**: hard-blocks keys with withdrawal or transfer permissions. Allows futures/spot trading permissions with a warning (Binance requires these for reading futures trade history; the app contains no order-placement code)
- API credentials encrypted at rest (Fernet symmetric encryption)
- Automatic sync every 6 hours + manual "Sync Now" button
- **Per-exchange sync start date**: choose how far back to pull trades when connecting an exchange
- **Smart pair discovery**: finds trades via balance detection + tracked pairs history + `extra_pairs.txt` for fully-sold positions
- **Tracked pairs**: pairs that have been seen in trades are remembered in the database and always re-checked on future syncs
- Exchange-specific sync handling (Gate.io per-symbol fetching, Binance spot + futures separate clients, Kraken bulk fetch with per-symbol fallback)
- **Binance futures**: syncs USDT-margined and USDC-margined perpetual trades + funding fee history
- Sync log with status tracking and error messages
- Stale sync warnings when data is >24 hours old

### Trades Table
- Full trade table with sorting (time, pair, exchange, total) and pagination
- **Order grouping**: multiple fills from the same order shown as one row with combined totals and weighted average price
- **Expandable fills**: click the fill count to see individual execution details
- **Trade type filter**: filter between Spot, Futures, or both. Futures trades show a yellow "PERP" badge
- Filter bar: by exchange, pair, trade type, side, strategy, and date range
- **Chart screenshots**: upload up to 2 chart images per trade (10 MB max), shown as thumbnails with click-to-enlarge lightbox
- **Trade notes**: click any trade row to open a detail panel with an editable notes field
- **Emotion tagging**: assign an emotion tag (e.g. FOMO, Followed Plan, Revenge Trade) to each trade from a dropdown
- **Trade rating**: rate each trade 1-5 stars to track execution quality over time
- **Manual trade entry**: add trades manually for OTC deals, unlisted exchanges, or corrections

### PnL & Analytics
- **FIFO PnL engine**: calculates realised profit/loss by matching buys against sells using First-In-First-Out cost basis
- **PnL breakdown**: group by pair, exchange, strategy, emotion tag, month, or week
- **Performance stats**: win rate, profit factor, average win/loss, best/worst pair
- **Equity curve**: interactive area chart on the overview page showing cumulative PnL over time (powered by lightweight-charts)
- **Overview dashboard**: real-time stats including total PnL, monthly/weekly PnL, fees, win rate
- **Pattern detection**: win/loss streaks, time-of-day performance analysis, average hold time per trade

### Emotion Tags
- **7 built-in tags**: Followed Plan, FOMO, Revenge Trade, High Conviction, Impulse, Overtraded, News-Driven
- Create custom tags with name and colour
- Rename or delete tags (cascades to tagged trades)
- **Import/Export**: download all tags as JSON backup, restore from file
- **PnL grouping**: break down profitability by emotion to identify behavioural patterns

### Export
- Download filtered trades as **CSV** or **XLSX**
- **ZIP export with screenshots**: check "Charts" before exporting to get a ZIP containing the spreadsheet plus a `screenshots/` folder with chart images named by pair and date
- Exports include a "Type" column (Spot/Futures) and respect all active filters
- Grouped orders exported as single rows with combined quantities and weighted average prices

### Strategy System
- **Strategies page**: create, edit, and delete trading strategies with dedicated sidebar tab
- Each strategy has: name, colour (9 options), short description, and a **playbook** (rules, entry/exit criteria, notes)
- **Trade tagging**: click the Strategy column on any trade to assign a strategy from a dropdown
- Strategy-coloured badges in the trades table
- **Import/Export**: download all strategies as JSON backup, restore from file

### Notebook
- **Notes page**: dedicated sidebar tab for general notes, research, and observations (max 4 notes)
- Each note has an editable title and a large text area
- Auto-saves on blur

### Hardening
- **Optional basic auth**: set `AUTH_USER` and `AUTH_PASS` in `.env` to protect the dashboard with HTTP Basic Authentication
- **Backup/restore**: one-click download of a ZIP containing database, screenshots, and extra_pairs.txt. Restore from any backup file
- **Daily auto-backup**: the scheduler automatically saves a backup every 24 hours to `data/backups/`, keeping the most recent backups (configurable via `BACKUP_RETENTION_COUNT`)
- **Toast notifications**: all confirmations and errors shown as slide-in toasts (no browser alert popups)
- **Stale data warnings**: overview page and exchange cards flag when sync is overdue (>24 hours)

---

## API Key Setup

### Binance

1. Go to **API Management** in your Binance account settings
2. Create a new API key
3. Permissions to **enable**:
   - ☑ Enable Reading (required)
   - ☑ Enable Futures (required for futures trade history — this also grants trade capability, but the app never places orders)
4. Permissions to **disable**:
   - ☐ Enable Spot & Margin Trading (not needed for spot read access)
   - ☐ Enable Withdrawals (hard-blocked by the app)
   - ☐ Enable Internal Transfer (hard-blocked by the app)
5. Copy the API Key and Secret

### Gate.io

1. Go to **API Key Management** in your Gate.io account settings
2. Create a new **API v4 Key**
3. Enable **Spot Trade** with **Read Only** selected
4. Leave all other permissions off
5. Copy the API Key, Secret, and Passphrase

### Kraken

1. Go to **Settings > API** in your Kraken account
2. Create a new API key
3. Enable **Query Funds** and **Query Open Orders & Trades** permissions
4. Leave all other permissions off
5. Copy the API Key and Private Key

---

## Security

### Permission Model

The app uses a two-layer security model:

1. **Code-level protection** (primary): The codebase contains **no functions** that can place, modify, or cancel orders. Even if an API key has trading permissions, the app physically cannot use them.

2. **Permission validation** (secondary): When connecting an exchange, the app checks the API key's permissions:
   - **Hard-blocked**: Withdrawal and Transfer permissions are rejected outright — these are never needed for a trading journal
   - **Allowed with warning**: Futures and Spot trading permissions are accepted because Binance requires them for reading futures trade history. A warning is logged noting the key has this capability

### Data Safety

- **All data stays local** — SQLite file + screenshot images on your machine
- **API keys encrypted at rest** — Fernet encryption, key stored locally in `data/.encryption_key`
- **Never committed to Git** — the `.gitignore` excludes the entire `data/` directory (except `.gitkeep`)
- **Strategy & emotion tag backup** — export/import as JSON to protect against database resets
- **Extra pairs file** — `data/extra_pairs.txt` survives database resets (add fully-sold pairs here)
- **Full backup/restore** — download everything as a ZIP from the Settings page
- **Auto-backup** — daily snapshots with automatic rotation

### GitHub Safety

The `.gitignore` uses a blanket `data/*` rule with only `data/.gitkeep` whitelisted. This protects:
- `data/journal.db` (contains encrypted API keys and trade data)
- `data/.encryption_key` (the encryption key — if this leaks, API keys can be decrypted)
- `data/screenshots/` (personal chart images)
- `data/backups/` (auto-backup ZIPs)
- `data/extra_pairs.txt` (custom pair list)
- `.env` (environment variables including auth credentials)

**If you previously committed without a proper .gitignore**, you should rotate your API keys on the exchanges and delete the old keys.

---

## Project Structure

```
trading-journal/
├── app/
│   ├── main.py               # FastAPI entry point + optional auth middleware
│   ├── config.py             # Environment settings
│   ├── database.py           # SQLite + versioned migrations (v1–v7)
│   ├── models.py             # Pydantic request/response schemas
│   ├── encryption.py         # API key encryption (Fernet)
│   ├── scheduler.py          # APScheduler for periodic sync + auto-backup
│   ├── exchanges/
│   │   ├── base.py           # ExchangeAdapter ABC + shared helpers
│   │   ├── binance.py        # Binance adapter (spot + futures + funding)
│   │   ├── gateio.py         # Gate.io adapter (spot, bulk + per-symbol)
│   │   ├── kraken.py         # Kraken adapter (spot, bulk + per-symbol)
│   │   ├── connector.py      # ccxt wrapper + adapter factory
│   │   └── sync.py           # Sync orchestrator (pair tracking, dedup)
│   ├── services/
│   │   └── backup.py         # Shared backup logic (ZIP creation, retention)
│   └── api/
│       ├── routes_overview.py    # Dashboard stats with real PnL
│       ├── routes_trades.py      # Grouped orders, fills, tagging, manual entry
│       ├── routes_exchanges.py   # Connect, sync, remove exchanges
│       ├── routes_pnl.py         # FIFO PnL engine + equity curve endpoint
│       ├── routes_export.py      # CSV/XLSX/ZIP export
│       ├── routes_strategies.py  # Strategy CRUD + import/export
│       ├── routes_emotion_tags.py # Emotion tag CRUD + import/export
│       ├── routes_screenshots.py # Upload/serve/delete trade screenshots
│       ├── routes_patterns.py    # Streak, time-of-day, hold time analysis
│       ├── routes_funding.py     # Funding fee history
│       ├── routes_notes.py       # General notebook (max 4 notes)
│       └── routes_backup.py      # Full backup/restore as ZIP
├── frontend/
│   ├── index.html            # Single-page dashboard
│   ├── css/app.css           # Dark terminal theme with card effects
│   └── js/
│       ├── api.js            # API client
│       └── app.js            # Navigation, rendering, interactions
├── tests/
│   ├── conftest.py           # Shared fixtures (fresh DB with all migrations)
│   ├── test_pnl.py           # FIFO matching engine tests
│   ├── test_dedup.py         # Trade insert + deduplication tests
│   └── test_encryption.py    # Encryption round-trip tests
├── data/
│   ├── .gitkeep              # Keeps data/ in Git while ignoring contents
│   ├── journal.db            # SQLite database (auto-created, NEVER commit)
│   ├── .encryption_key       # Fernet key (auto-generated, NEVER commit)
│   ├── extra_pairs.txt       # Additional pairs for sync discovery
│   ├── screenshots/          # Uploaded chart images (NEVER commit)
│   └── backups/              # Daily auto-backup ZIPs (NEVER commit)
├── .env.example              # Environment variable template
├── .gitignore                # Blanket data/* protection
├── docker-compose.yml
├── Dockerfile
├── pyproject.toml
└── README.md
```

---

## Database Migrations

Migrations run automatically on startup:

| Version | What it adds |
|---------|-------------|
| v1 | exchanges, trades, sync_log tables + indexes |
| v2 | order_id column on trades (fill grouping) |
| v3 | strategies table |
| v4 | trade_screenshots table |
| v5 | funding_fees table + trade_type index |
| v6 | notes table |
| v7 | tracked_pairs + emotion_tags tables, sync_start_date on exchanges, emotion_tag + trade_rating on trades |

If you delete `journal.db`, the app recreates everything from scratch.

---

## Tests

```bash
pip install ".[dev]"
pytest tests/ -v
```

Covers FIFO PnL matching, trade deduplication, and API key encryption.

---

## API Documentation

When the server is running, visit **http://localhost:8000/docs** for the auto-generated Swagger UI listing all endpoints.
