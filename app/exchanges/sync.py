"""Trade sync engine — pulls trades from exchanges with pagination and dedup."""

import uuid
import time
import json
from datetime import datetime, timezone
from typing import Optional

import ccxt

from app.database import get_db
from app.exchanges.connector import create_client, ExchangeError
from app.encryption import decrypt


def sync_exchange(exchange_id: str) -> dict:
    """
    Sync trades for a given exchange connection.

    Returns:
        {"status": "success"|"error", "trades_fetched": int, "error": str|None}
    """
    db = get_db()
    log_id = None

    try:
        # Load exchange config
        row = db.execute(
            "SELECT * FROM exchanges WHERE id = ?", [exchange_id]
        ).fetchone()
        if not row:
            raise ExchangeError(f"Exchange {exchange_id} not found")

        # Start sync log entry
        db.execute(
            "INSERT INTO sync_log (exchange_id, status) VALUES (?, 'running')",
            [exchange_id],
        )
        db.commit()
        log_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]

        # Create ccxt client with decrypted credentials
        client = create_client(
            exchange=row["exchange"],
            api_key=decrypt(row["api_key_enc"]),
            api_secret=decrypt(row["api_secret_enc"]),
            passphrase=decrypt(row["passphrase_enc"]) if row["passphrase_enc"] else None,
        )

        # Determine the starting point for sync
        since = _get_sync_since(db, exchange_id, row["last_sync_at"])

        # Fetch trades with pagination
        all_trades = _fetch_all_trades(client, since)

        # Insert into database (with dedup)
        inserted = _insert_trades(db, exchange_id, row["exchange"], all_trades)

        # Update exchange record
        now = datetime.now(timezone.utc).isoformat()
        db.execute(
            "UPDATE exchanges SET last_sync_at = ?, last_sync_status = 'success' WHERE id = ?",
            [now, exchange_id],
        )

        # Complete sync log
        db.execute(
            """UPDATE sync_log
               SET completed_at = ?, status = 'success', trades_fetched = ?
               WHERE id = ?""",
            [now, inserted, log_id],
        )
        db.commit()

        print(f"[sync] {exchange_id}: fetched {len(all_trades)} trades, inserted {inserted} new")
        return {"status": "success", "trades_fetched": inserted, "error": None}

    except Exception as e:
        error_msg = f"{type(e).__name__}: {e}"
        print(f"[sync] {exchange_id}: error — {error_msg}")

        # Update sync log with error
        if log_id:
            now = datetime.now(timezone.utc).isoformat()
            db.execute(
                """UPDATE sync_log
                   SET completed_at = ?, status = 'error', error_message = ?
                   WHERE id = ?""",
                [now, error_msg[:500], log_id],
            )
            db.execute(
                "UPDATE exchanges SET last_sync_status = 'error' WHERE id = ?",
                [exchange_id],
            )
            db.commit()

        return {"status": "error", "trades_fetched": 0, "error": error_msg}

    finally:
        db.close()


def _get_sync_since(db, exchange_id: str, last_sync_at: Optional[str]) -> Optional[int]:
    """
    Determine the `since` timestamp (ms) for fetching trades.
    - If we have existing trades, start from the latest one.
    - If no trades but a last_sync, use that.
    - Otherwise, fetch all available history (None = exchange default).
    """
    # Check latest trade timestamp for this exchange
    row = db.execute(
        "SELECT MAX(timestamp) as latest FROM trades WHERE exchange_id = ?",
        [exchange_id],
    ).fetchone()

    if row and row["latest"]:
        # Start 1 second after the latest trade to avoid re-fetching it
        dt = datetime.fromisoformat(row["latest"].replace("Z", "+00:00"))
        return int(dt.timestamp() * 1000) + 1000

    # No trades yet — try to go back 1 year for initial sync
    one_year_ago = int((time.time() - 365 * 86400) * 1000)
    return one_year_ago


def _fetch_all_trades(client: ccxt.Exchange, since: Optional[int]) -> list:
    """
    Fetch all trades using pagination.

    ccxt's fetch_my_trades supports `since` (timestamp ms) and `limit` params.
    We paginate by advancing `since` to the last trade's timestamp.
    """
    all_trades = []
    limit = 100  # trades per page (most exchanges support 100-1000)
    max_pages = 200  # safety limit: 200 pages × 100 = 20,000 trades max per sync

    # First, load markets (required by ccxt before making private calls)
    client.load_markets()

    current_since = since
    page = 0

    while page < max_pages:
        page += 1

        try:
            trades = client.fetch_my_trades(
                symbol=None,  # all symbols
                since=current_since,
                limit=limit,
            )
        except ccxt.BadRequest:
            # Some exchanges require a symbol — fall back to per-market fetching
            trades = _fetch_per_market(client, current_since, limit)
            all_trades.extend(trades)
            break  # per-market fetch handles its own pagination

        if not trades:
            break

        all_trades.extend(trades)

        # Advance pagination: move `since` past the last trade
        last_ts = trades[-1]["timestamp"]
        if last_ts == current_since:
            # No progress — we're stuck, break to avoid infinite loop
            break
        current_since = last_ts + 1

        # If we got fewer trades than the limit, we've reached the end
        if len(trades) < limit:
            break

        # Respect rate limits (ccxt handles this with enableRateLimit,
        # but add a small buffer for safety)
        time.sleep(0.1)

    return all_trades


def _fetch_per_market(
    client: ccxt.Exchange,
    since: Optional[int],
    limit: int,
) -> list:
    """
    Fallback: fetch trades per market symbol.
    Some exchanges (like Gate.io) require a symbol parameter.
    """
    all_trades = []
    markets = client.load_markets()

    # Only fetch markets the user has actually traded
    # We'll try all spot markets and skip ones that return empty
    spot_symbols = [
        s for s, m in markets.items()
        if m.get("spot", False) and m.get("active", True)
    ]

    for symbol in spot_symbols:
        try:
            trades = client.fetch_my_trades(
                symbol=symbol,
                since=since,
                limit=limit,
            )
            if trades:
                all_trades.extend(trades)
                # Paginate within this symbol
                while len(trades) == limit:
                    last_ts = trades[-1]["timestamp"] + 1
                    trades = client.fetch_my_trades(
                        symbol=symbol,
                        since=last_ts,
                        limit=limit,
                    )
                    if trades:
                        all_trades.extend(trades)
                    time.sleep(0.1)
        except (ccxt.BadSymbol, ccxt.BadRequest):
            continue
        except Exception:
            continue  # Skip problematic symbols, don't fail the whole sync
        time.sleep(0.05)

    return all_trades


def _insert_trades(db, exchange_id: str, exchange: str, trades: list) -> int:
    """
    Insert trades into the database, skipping duplicates.
    Returns the number of newly inserted trades.
    """
    inserted = 0

    for t in trades:
        trade_id = str(uuid.uuid4())
        external_id = str(t.get("id", t.get("order", trade_id)))

        # Parse the symbol (e.g. "ETH/USDT")
        symbol = t.get("symbol", "")
        parts = symbol.split("/") if "/" in symbol else [symbol, ""]
        base = parts[0] if len(parts) > 0 else ""
        quote = parts[1] if len(parts) > 1 else ""

        # Parse fee
        fee_info = t.get("fee", {}) or {}
        fee_cost = fee_info.get("cost", 0) or 0
        fee_currency = fee_info.get("currency", "") or ""

        # Timestamp
        ts = t.get("timestamp", 0)
        if ts:
            dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
            timestamp_str = dt.isoformat()
        else:
            timestamp_str = t.get("datetime", datetime.now(timezone.utc).isoformat())

        # Calculate total
        amount = float(t.get("amount", 0) or 0)
        price = float(t.get("price", 0) or 0)
        cost = float(t.get("cost", 0) or 0)
        total = cost if cost else amount * price

        try:
            db.execute(
                """INSERT INTO trades
                   (id, exchange_id, exchange, external_id, timestamp,
                    pair, base_currency, quote_currency, side,
                    quantity, price, total, fee, fee_currency,
                    trade_type, synced_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
                [
                    trade_id, exchange_id, exchange, external_id, timestamp_str,
                    symbol, base, quote, t.get("side", "buy"),
                    amount, price, total, float(fee_cost), fee_currency,
                    t.get("type", "spot") or "spot",
                ],
            )
            inserted += 1
        except Exception as e:
            # Most likely a UNIQUE constraint violation (duplicate) — skip
            if "UNIQUE" in str(e).upper():
                continue
            else:
                print(f"[sync] Warning: failed to insert trade {external_id}: {e}")
                continue

    if inserted > 0:
        db.commit()

    return inserted
