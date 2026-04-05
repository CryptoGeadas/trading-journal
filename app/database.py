"""Database initialisation and connection management."""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "journal.db"

SCHEMA_VERSION = 3

MIGRATIONS = {
    1: """
    CREATE TABLE IF NOT EXISTS exchanges (
        id              TEXT PRIMARY KEY,
        exchange        TEXT NOT NULL,
        label           TEXT,
        api_key_enc     TEXT NOT NULL,
        api_secret_enc  TEXT NOT NULL,
        passphrase_enc  TEXT,
        permissions     TEXT,
        is_read_only    INTEGER NOT NULL DEFAULT 1,
        last_sync_at    TEXT,
        last_sync_status TEXT,
        created_at      TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS trades (
        id              TEXT PRIMARY KEY,
        exchange_id     TEXT NOT NULL,
        exchange        TEXT NOT NULL,
        external_id     TEXT NOT NULL,
        timestamp       TEXT NOT NULL,
        pair            TEXT NOT NULL,
        base_currency   TEXT NOT NULL,
        quote_currency  TEXT NOT NULL,
        side            TEXT NOT NULL CHECK(side IN ('buy', 'sell')),
        quantity        REAL NOT NULL,
        price           REAL NOT NULL,
        total           REAL NOT NULL,
        fee             REAL NOT NULL DEFAULT 0,
        fee_currency    TEXT NOT NULL DEFAULT '',
        trade_type      TEXT NOT NULL DEFAULT 'spot',
        strategy        TEXT,
        notes           TEXT,
        synced_at       TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE(exchange_id, external_id),
        FOREIGN KEY (exchange_id) REFERENCES exchanges(id)
    );

    CREATE TABLE IF NOT EXISTS sync_log (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        exchange_id     TEXT NOT NULL,
        started_at      TEXT NOT NULL DEFAULT (datetime('now')),
        completed_at    TEXT,
        status          TEXT NOT NULL DEFAULT 'running',
        trades_fetched  INTEGER DEFAULT 0,
        error_message   TEXT,
        FOREIGN KEY (exchange_id) REFERENCES exchanges(id)
    );

    CREATE INDEX IF NOT EXISTS idx_trades_timestamp ON trades(timestamp);
    CREATE INDEX IF NOT EXISTS idx_trades_exchange ON trades(exchange);
    CREATE INDEX IF NOT EXISTS idx_trades_pair ON trades(pair);
    CREATE INDEX IF NOT EXISTS idx_trades_strategy ON trades(strategy);

    PRAGMA user_version = 1;
    """,
    2: """
    ALTER TABLE trades ADD COLUMN order_id TEXT;

    CREATE INDEX IF NOT EXISTS idx_trades_order_id ON trades(order_id);

    PRAGMA user_version = 2;
    """,
    3: """
    CREATE TABLE IF NOT EXISTS strategies (
        id              TEXT PRIMARY KEY,
        name            TEXT NOT NULL UNIQUE,
        description     TEXT,
        colour          TEXT NOT NULL DEFAULT '#6c9cfc',
        playbook        TEXT,
        created_at      TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
    );

    PRAGMA user_version = 3;
    """,
}


def get_db() -> sqlite3.Connection:
    """Get a database connection with row factory."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """Run any pending migrations."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = get_db()
    current_version = conn.execute("PRAGMA user_version").fetchone()[0]

    for version in range(current_version + 1, SCHEMA_VERSION + 1):
        if version in MIGRATIONS:
            conn.executescript(MIGRATIONS[version])
            print(f"[db] Applied migration v{version}")

    conn.close()
    print(f"[db] Database ready at {DB_PATH} (v{SCHEMA_VERSION})")
