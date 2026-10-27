# Trading Journal

A crypto trading journal that runs **on your own computer**. It reads your
trade history from your exchanges with read-only keys, and shows your real
PnL, fees, funding, patterns and notes in your browser. Nothing is hosted
anywhere: your trades and your keys never leave your computer.

**Supported exchanges:** Binance (spot + USDT/USDC-margined futures) · Gate.io (spot) · Kraken (spot)

---

## Install it with your coding agent

You don't need to type terminal commands. Open Claude Code (or Codex, Cursor or
a similar agent that can run commands) in an empty folder and paste:

```
Install and start the trading journal from https://github.com/CryptoGeadas/trading-journal
on this computer. Follow the AGENTS.md file in that repository step by step,
and offer me the demo data when it's running.
```

The agent checks Python, installs the journal, starts it and opens it in your
browser. [AGENTS.md](AGENTS.md) holds the exact steps and the safety rules it
must follow, for example: it never asks for your API keys in the chat.

**To start it again later**, run this in the `trading-journal` folder:

- Windows: `.venv\Scripts\python -m app`
- macOS / Linux: `.venv/bin/python -m app`

### Install it yourself

Python 3.11 or newer:

```bash
git clone https://github.com/CryptoGeadas/trading-journal.git
cd trading-journal
python -m venv .venv
.venv/bin/python -m pip install .      # Windows: .venv\Scripts\python -m pip install .
.venv/bin/python -m app                # Windows: .venv\Scripts\python -m app
```

`python -m app` starts the journal and opens **http://127.0.0.1:8000** when it
is ready (add `--no-browser` to skip that). Only your own computer can open it.

Docker works too: `docker-compose up --build` (the port is published on this
computer only).

---

## Safety facts

- **Runs on your computer.** The journal listens on `127.0.0.1` only. The page
  loads nothing from the internet (fonts and chart library are bundled); the
  only outside calls are to the exchanges you connect.
- **Your keys stay local.** API keys are typed into the journal's own Exchanges
  page, stored encrypted in your data folder, and used only to read trades.
- **Read-only use.** The code has no function that places, changes or cancels
  orders.
- **What each exchange lets the app check:**

  | Exchange | What the app checks |
  |----------|---------------------|
  | Binance | Reads the key's permissions. Withdrawal, internal transfer or universal transfer enabled → refused. Permissions not reported (or the call fails) → refused. Trading permissions → accepted with a warning (Binance needs "Enable Futures" to read futures history). |
  | Gate.io | Only that the key can read balances. Gate.io has no API that reports a key's permissions, so a key that can withdraw is **not** blocked. The form says so. |
  | Kraken | Same as Gate.io: read access is checked, withdrawal permission can't be. |

- **Backups are secret.** A backup ZIP holds the encryption key so your keys
  still work after a restore. Keep backup files as private as a password.

---

## Read-only API keys

Paste keys only into **Exchanges → + Add Exchange** in the journal.

- **Binance:** API Management → Create API → enable **Enable Reading**; enable
  **Enable Futures** only if you trade futures. Leave withdrawals and all
  transfers off (the journal refuses those keys).
- **Gate.io:** API Keys → API v4 key → **Spot: Read only**, nothing else. Make
  sure withdrawals are off: the journal can't check this on Gate.io.
- **Kraken:** Settings → API → **Query Funds** and **Query Open Orders &
  Trades** only. Make sure **Withdraw Funds** is off: the journal can't check
  this on Kraken.

---

## What it does

- **Sync:** Sync Now plus an automatic sync every 6 hours; per-pair history, so
  pairs added later get their full history; clear errors when an exchange
  refuses a key or is unreachable. Fully sold pairs can be listed in
  `extra_pairs.txt` in the data folder.
- **Trades:** grouped orders with expandable fills, filters, sorting, notes,
  emotion tags, 1–5 ratings, up to two chart screenshots per trade, manual
  trades (deletable).
- **PnL:** FIFO or average cost; spot and futures (longs and shorts), fees in
  any coin, funding fees as their own line; breakdowns by pair, exchange,
  strategy, emotion, month and week that add up to the total. Amounts stay in
  their own currency: USDT, USDC and other dollar stablecoins count as one,
  BTC or ETH amounts are never added to them.
- **Patterns:** equity curve, win rate, profit factor, streaks, time of day (in
  your time zone), hold times.
- **Strategies and tags:** playbooks per strategy, emotion tags, import/export.
- **Export:** CSV, XLSX, or ZIP with screenshots, following your filters.
- **Demo data:** six months of made-up trades to explore first, removed with
  one click without touching your own data.
- **Backups:** one-click backup/restore and a daily automatic backup.

---

## Settings

Put settings in a `.env` file in the `trading-journal` folder (copy
`.env.example`). A real environment variable with the same name wins.

| Variable | Default | What it does |
|----------|---------|--------------|
| `HOST` | `127.0.0.1` | Who can open the journal. Keep `127.0.0.1` (this computer only). Another value opens it to other devices on your network: the journal warns you, and you should set `AUTH_USER`/`AUTH_PASS` too. |
| `PORT` | `8000` | Port of the journal |
| `SYNC_INTERVAL_HOURS` | `6` | How often to pull new trades |
| `DEFAULT_PNL_METHOD` | `fifo` | `fifo` or `avg_cost`, when none is picked on the Settings page |
| `AUTH_USER` / `AUTH_PASS` | *(empty)* | Optional login (HTTP Basic) |
| `BACKUP_RETENTION_COUNT` | `7` | Automatic backups to keep |
| `TRADING_JOURNAL_DATA_DIR` | `~/.trading-journal` | Where your data lives |

## Where your data lives

One folder in your user profile: `~/.trading-journal` (Windows:
`C:\Users\<you>\.trading-journal`). It holds the database, the encryption key,
screenshots, backups and `extra_pairs.txt`, and is never inside the code
folder, so it can't be committed to Git by accident. Settings shows the exact
place.

---

## For developers

```bash
python -m pip install ".[dev]"
python -m pytest
```

The tests use a temporary data folder and never touch yours. API docs:
http://127.0.0.1:8000/docs while the journal runs. How the code is laid out:
[docs/setup.md](docs/setup.md).
