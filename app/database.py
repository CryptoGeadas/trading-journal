"""Database initialisation and connection management."""

import sqlite3
import uuid
from app import paths

SCHEMA_VERSION = 14

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
    4: """
    CREATE TABLE IF NOT EXISTS trade_screenshots (
        id              TEXT PRIMARY KEY,
        trade_id        TEXT NOT NULL,
        order_id        TEXT,
        filename        TEXT NOT NULL,
        original_name   TEXT,
        uploaded_at     TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE INDEX IF NOT EXISTS idx_screenshots_trade ON trade_screenshots(trade_id);
    CREATE INDEX IF NOT EXISTS idx_screenshots_order ON trade_screenshots(order_id);

    PRAGMA user_version = 4;
    """,
    5: """
    CREATE TABLE IF NOT EXISTS funding_fees (
        id              TEXT PRIMARY KEY,
        exchange_id     TEXT NOT NULL,
        exchange        TEXT NOT NULL,
        external_id     TEXT,
        timestamp       TEXT NOT NULL,
        pair            TEXT NOT NULL,
        amount          REAL NOT NULL,
        asset           TEXT NOT NULL,
        synced_at       TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE(exchange_id, timestamp, pair)
    );

    CREATE INDEX IF NOT EXISTS idx_funding_timestamp ON funding_fees(timestamp);
    CREATE INDEX IF NOT EXISTS idx_funding_pair ON funding_fees(pair);
    CREATE INDEX IF NOT EXISTS idx_trades_trade_type ON trades(trade_type);

    PRAGMA user_version = 5;
    """,
    6: """
    CREATE TABLE IF NOT EXISTS notes (
        id              TEXT PRIMARY KEY,
        title           TEXT NOT NULL,
        content         TEXT NOT NULL DEFAULT '',
        created_at      TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
    );

    PRAGMA user_version = 6;
    """,
    7: """
    CREATE TABLE IF NOT EXISTS tracked_pairs (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        exchange_id     TEXT NOT NULL,
        pair            TEXT NOT NULL,
        discovered_at   TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE(exchange_id, pair),
        FOREIGN KEY (exchange_id) REFERENCES exchanges(id)
    );

    CREATE TABLE IF NOT EXISTS emotion_tags (
        id              TEXT PRIMARY KEY,
        name            TEXT NOT NULL UNIQUE,
        colour          TEXT NOT NULL DEFAULT '#6c9cfc',
        is_default      INTEGER NOT NULL DEFAULT 0,
        created_at      TEXT NOT NULL DEFAULT (datetime('now'))
    );

    ALTER TABLE exchanges ADD COLUMN sync_start_date TEXT;

    ALTER TABLE trades ADD COLUMN emotion_tag TEXT;
    ALTER TABLE trades ADD COLUMN trade_rating INTEGER;

    CREATE INDEX IF NOT EXISTS idx_tracked_pairs_exchange ON tracked_pairs(exchange_id);
    CREATE INDEX IF NOT EXISTS idx_trades_emotion ON trades(emotion_tag);

    PRAGMA user_version = 7;
    """,
    8: """
    -- Removing a connection keeps its trades (they reference it), wipes its
    -- keys and marks it removed instead of deleting the row.
    ALTER TABLE exchanges ADD COLUMN removed_at TEXT;

    PRAGMA user_version = 8;
    """,
    # 9: every trade and funding time in one UTC format (Python step below).
    9: "SELECT 1;",  # the Python step sets user_version = 9 when it has finished
    10: """
    -- Synced futures fills were stored under the spot pair name (BTC/USDT).
    -- Give them their futures name (BTC/USDT:USDT), the same for funding
    -- payments, and keep those futures pairs tracked for the next sync.
    UPDATE trades SET pair = pair || ':' || quote_currency
     WHERE trade_type = 'futures' AND instr(pair, ':') = 0 AND quote_currency != '';
    UPDATE OR IGNORE funding_fees SET pair = pair || ':' || substr(pair, instr(pair, '/') + 1)
     WHERE instr(pair, ':') = 0 AND instr(pair, '/') > 0;
    -- A row the rename skipped already exists under the new name: drop the old copy.
    DELETE FROM funding_fees
     WHERE instr(pair, ':') = 0 AND instr(pair, '/') > 0
       AND EXISTS (SELECT 1 FROM funding_fees f2
                    WHERE f2.exchange_id = funding_fees.exchange_id AND f2.timestamp = funding_fees.timestamp
                      AND f2.pair = funding_fees.pair || ':' || substr(funding_fees.pair, instr(funding_fees.pair, '/') + 1));
    INSERT OR IGNORE INTO tracked_pairs (exchange_id, pair)
     SELECT DISTINCT t.exchange_id, t.pair FROM trades t JOIN exchanges e ON e.id = t.exchange_id
      WHERE t.trade_type = 'futures' AND e.id NOT LIKE 'manual_%';

    PRAGMA user_version = 10;
    """,
    11: """
    -- Manual trades need an exchanges row (trades reference it), but it is
    -- not a connection: never synced, counted or listed as one.
    ALTER TABLE exchanges ADD COLUMN is_manual INTEGER NOT NULL DEFAULT 0;
    UPDATE exchanges SET is_manual = 1 WHERE substr(id, 1, 7) = 'manual_' AND api_key_enc = '';

    PRAGMA user_version = 11;
    """,
    12: """
    -- Kept when a connection is removed (the key itself is wiped), so adding
    -- the same key again reconnects the old connection instead of importing
    -- its trades a second time.
    ALTER TABLE exchanges ADD COLUMN api_key_fingerprint TEXT;

    PRAGMA user_version = 12;
    """,
    13: """
    -- When each pair was last fetched completely (ms). The next sync starts
    -- there, so a quiet pair is not fetched again from the start date.
    ALTER TABLE tracked_pairs ADD COLUMN checked_until INTEGER;
    -- Manual futures typed as BTC/USDT:USDT were stored with quote 'USDT:USDT'.
    UPDATE trades SET quote_currency = substr(quote_currency, 1, instr(quote_currency, ':') - 1)
     WHERE instr(quote_currency, ':') > 0;

    PRAGMA user_version = 13;
    """,
    14: """
    -- Demo data is marked where it comes from, so it can be removed in one go.
    ALTER TABLE exchanges ADD COLUMN is_demo INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE strategies ADD COLUMN is_demo INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE notes ADD COLUMN is_demo INTEGER NOT NULL DEFAULT 0;

    PRAGMA user_version = 14;
    """,
}


def _normalise_timestamps(conn: sqlite3.Connection):
    """Rewrite stored times as UTC `...000Z` so text order is time order."""
    from app import timeutil

    for table in ("trades", "funding_fees"):
        rows = conn.execute(f"SELECT id, timestamp FROM {table}").fetchall()
        for row_id, ts in rows:
            try:
                new = timeutil.to_utc(ts)
            except ValueError:
                print(f"[db] Warning: {table} row {row_id} has an unreadable time {ts!r}; left as it is")
                continue
            if new == ts:
                continue
            try:
                conn.execute(f"UPDATE {table} SET timestamp = ? WHERE id = ?", [new, row_id])
            except sqlite3.IntegrityError:
                # Same payment stored twice in two formats: keep one.
                conn.execute(f"DELETE FROM {table} WHERE id = ?", [row_id])
    conn.execute("PRAGMA user_version = 9")
    conn.commit()


# Python steps that run right after a migration's SQL.
MIGRATION_STEPS = {9: _normalise_timestamps}


_DEFAULT_EMOTION_TAGS = [
    ("Followed Plan", "#34d399"),
    ("FOMO", "#f87171"),
    ("Revenge Trade", "#f87171"),
    ("High Conviction", "#6c9cfc"),
    ("Impulse", "#fbbf24"),
    ("Overtraded", "#fbbf24"),
    ("News-Driven", "#8b8d98"),
]


def _seed_emotion_tags(conn: sqlite3.Connection):
    """Insert default emotion tags if they don't exist yet."""
    for name, colour in _DEFAULT_EMOTION_TAGS:
        tag_id = str(uuid.uuid4())[:8]
        try:
            conn.execute(
                "INSERT INTO emotion_tags (id, name, colour, is_default) VALUES (?, ?, ?, 1)",
                [tag_id, name, colour],
            )
        except Exception:
            pass
    conn.commit()


def get_db() -> sqlite3.Connection:
    """Get a database connection with row factory."""
    conn = sqlite3.connect(str(paths.db_path()))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """Run any pending migrations."""
    paths.db_path().parent.mkdir(parents=True, exist_ok=True)
    conn = get_db()
    current_version = conn.execute("PRAGMA user_version").fetchone()[0]

    for version in range(current_version + 1, SCHEMA_VERSION + 1):
        if version in MIGRATIONS:
            conn.executescript(MIGRATIONS[version])
            print(f"[db] Applied migration v{version}")
            if version == 7:
                _seed_emotion_tags(conn)
            if version in MIGRATION_STEPS:
                MIGRATION_STEPS[version](conn)

    conn.close()
    print(f"[db] Database ready at {paths.db_path()} (v{SCHEMA_VERSION})")
