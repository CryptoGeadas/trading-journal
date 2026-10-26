"""Trade sync engine — orchestrates adapters and handles trade insertion.

Statuses written to the sync log and the connection:
- success: spot, futures and funding all synced.
- partial: spot synced, but futures or funding could not be (the message
  says which and why). Common when a key has no futures access.
- error: nothing reliable was synced (bad key, network, rate limit...).
"""

import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Optional

import ccxt

from app import timeutil
from app.database import get_db
from app.exchanges.connector import create_client, get_adapter, ExchangeError
from app.exchanges.base import SyncCursor
from app.encryption import decrypt


def plain_error(e: Exception) -> str:
    """A message a user can act on, with the exchange's own text after it."""
    detail = f"{type(e).__name__}: {e}"
    if isinstance(e, ccxt.InvalidNonce):
        hint = "The exchange says this computer's clock is wrong. Set the clock to sync automatically and try again."
    elif isinstance(e, ccxt.PermissionDenied):
        hint = "The API key is missing a permission the sync needs (read access)."
    elif isinstance(e, ccxt.AuthenticationError):
        hint = "The exchange refused the API key. It may be revoked, expired or limited to other IPs."
    elif isinstance(e, ccxt.RateLimitExceeded) or isinstance(e, ccxt.DDoSProtection):
        hint = "The exchange's rate limit was hit. Try again in a few minutes."
    elif isinstance(e, ccxt.NetworkError):
        hint = "Could not reach the exchange (network problem or the exchange is down)."
    else:
        return detail
    return f"{hint} ({detail})"


def sync_exchange(exchange_id: str) -> dict:
    """Sync trades for a given exchange connection."""
    db = get_db()
    log_id = None

    try:
        row = db.execute(
            "SELECT * FROM exchanges WHERE id = ?", [exchange_id]
        ).fetchone()
        if not row:
            raise ExchangeError(f"Exchange {exchange_id} not found")
        if row["removed_at"]:
            raise ExchangeError(f"Exchange {exchange_id} was removed")
        if row["is_manual"]:
            raise ExchangeError(f"{exchange_id} holds manual trades; there is nothing to sync")
        if row["is_demo"]:
            raise ExchangeError(f"{exchange_id} is demo data; it is never synced")

        db.execute(
            "INSERT INTO sync_log (exchange_id, status) VALUES (?, 'running')",
            [exchange_id],
        )
        db.commit()
        log_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]

        client = create_client(
            exchange=row["exchange"],
            api_key=decrypt(row["api_key_enc"]),
            api_secret=decrypt(row["api_secret_enc"]),
            passphrase=decrypt(row["passphrase_enc"]) if row["passphrase_enc"] else None,
        )

        adapter = get_adapter(row["exchange"])
        sync_started_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        cursor = _get_sync_cursor(db, exchange_id, row["sync_start_date"])

        tracked_pairs = _get_tracked_pairs(db, exchange_id)
        extra_pairs_spot = adapter.load_extra_pairs(futures=False)
        extra_pairs_futures = adapter.load_extra_pairs(futures=True)

        # Spot errors fail the whole sync. Futures and funding are optional
        # parts: their errors are reported, never counted as "0 new".
        spot_trades = adapter.sync_spot(client, cursor, tracked_pairs, extra_pairs_spot)
        problems = []
        notes = []
        # Spot just worked with this key, so an auth error on futures means no
        # futures access. That is fine for an account that never had futures;
        # for one that had, futures data is now missing: report it.
        had_futures = _has_futures_history(db, exchange_id)
        try:
            futures_trades = adapter.sync_futures(client, cursor, tracked_pairs, extra_pairs_futures)
        except ccxt.AuthenticationError as e:
            futures_trades = []
            if had_futures:
                problems.append(f"Futures trades not synced: {plain_error(e)}")
            else:
                notes.append(f"Futures skipped: this key has no futures access ({e}).")
        except Exception as e:
            futures_trades = []
            problems.append(f"Futures trades not synced: {plain_error(e)}")
        try:
            funding_count = adapter.sync_funding(client, db, exchange_id, _get_funding_since(db, exchange_id, cursor))
        except ccxt.AuthenticationError as e:
            funding_count = 0
            if had_futures:
                problems.append(f"Funding fees not synced: {plain_error(e)}")
            else:
                notes.append(f"Funding skipped: this key has no futures access ({e}).")
        except Exception as e:
            funding_count = 0
            problems.append(f"Funding fees not synced: {plain_error(e)}")

        # A rejected symbol we knew about (traded before, tracked or listed) is data we lost.
        lost = sorted(p for p in cursor.skipped
                      if cursor.known(p) or p in tracked_pairs or p in extra_pairs_spot | extra_pairs_futures)
        if lost:
            problems.append("The exchange rejected these pairs, so they were not synced: " + ", ".join(lost) + ".")

        inserted_spot = _insert_trades(db, exchange_id, row["exchange"], spot_trades, "spot")
        inserted_futures = _insert_trades(db, exchange_id, row["exchange"], futures_trades, "futures")
        inserted = inserted_spot + inserted_futures

        _update_tracked_pairs(db, exchange_id, spot_trades, futures_trades, cursor.checked, sync_started_ms)

        status = "partial" if problems else "success"
        message = " ".join(problems + notes)[:500] or None
        now = timeutil.now_utc()
        db.execute(
            "UPDATE exchanges SET last_sync_at = ?, last_sync_status = ? WHERE id = ?",
            [now, status, exchange_id],
        )
        db.execute(
            """UPDATE sync_log SET completed_at = ?, status = ?, trades_fetched = ?, error_message = ?
               WHERE id = ?""",
            [now, status, inserted, message, log_id],
        )
        db.commit()

        parts = []
        if inserted_spot:
            parts.append(f"{inserted_spot} spot")
        if inserted_futures:
            parts.append(f"{inserted_futures} futures")
        if funding_count:
            parts.append(f"{funding_count} funding fees")
        detail = ", ".join(parts) if parts else "0 new"
        print(f"[sync] {exchange_id}: {status}, {detail}" + (f" — {message}" if message else ""))
        return {"status": status, "trades_fetched": inserted, "error": message}

    except Exception as e:
        error_msg = plain_error(e)
        print(f"[sync] {exchange_id}: error — {error_msg}")

        if log_id:
            now = timeutil.now_utc()
            db.execute(
                """UPDATE sync_log SET completed_at = ?, status = 'error', error_message = ?
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


def _get_tracked_pairs(db, exchange_id: str) -> set[str]:
    """Load tracked pairs for this exchange from the DB (futures keep their ':SETTLE')."""
    rows = db.execute(
        "SELECT pair FROM tracked_pairs WHERE exchange_id = ?", [exchange_id]
    ).fetchall()
    return {r["pair"] for r in rows}


def _update_tracked_pairs(db, exchange_id: str, spot_trades: list, futures_trades: list,
                         checked: set[str] = frozenset(), checked_until: int | None = None):
    """Remember every pair seen, so it is checked again after the position is gone,
    and when each fetched pair was last checked completely."""
    seen_pairs = set()
    for t in spot_trades + futures_trades:
        pair = t.get("symbol", "")
        if pair and "/" in pair:
            seen_pairs.add(pair)

    for pair in seen_pairs | set(checked):
        db.execute(
            "INSERT OR IGNORE INTO tracked_pairs (exchange_id, pair) VALUES (?, ?)",
            [exchange_id, pair],
        )
    if checked_until is not None:
        for pair in checked:
            db.execute(
                "UPDATE tracked_pairs SET checked_until = ? WHERE exchange_id = ? AND pair = ?",
                [checked_until, exchange_id, pair],
            )
    db.commit()


def _has_futures_history(db, exchange_id: str) -> bool:
    return bool(db.execute(
        """SELECT 1 FROM trades WHERE exchange_id = ? AND trade_type = 'futures'
           UNION SELECT 1 FROM funding_fees WHERE exchange_id = ? LIMIT 1""",
        [exchange_id, exchange_id],
    ).fetchone())


def _start_ms(sync_start_date: Optional[str]) -> int:
    if sync_start_date:
        try:
            dt = datetime.fromisoformat(sync_start_date)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return int(dt.timestamp() * 1000)
        except ValueError:
            print(f"[sync] Warning: sync start date {sync_start_date!r} is not a date; using 1 January")
    return int(datetime(datetime.now().year, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)


def _ms(timestamp: str) -> int:
    return int(datetime.fromisoformat(timestamp.replace("Z", "+00:00")).timestamp() * 1000)


def _get_sync_cursor(db, exchange_id: str, sync_start_date: Optional[str]) -> SyncCursor:
    """Start point per pair: its newest stored trade, or the start date for a new pair.

    One cursor for the whole connection made a newer futures fill hide
    older spot fills that were not fetched yet, and a pair added later
    never got its history.
    """
    rows = db.execute(
        "SELECT pair, MAX(timestamp) AS latest FROM trades WHERE exchange_id = ? GROUP BY pair",
        [exchange_id],
    ).fetchall()
    latest = {}
    for r in rows:
        try:
            latest[r["pair"]] = _ms(r["latest"])
        except ValueError:
            raise ExchangeError(f"A stored {r['pair']} trade has an unreadable time ({r['latest']!r}).") from None
    checked = {
        r["pair"]: r["checked_until"]
        for r in db.execute(
            "SELECT pair, checked_until FROM tracked_pairs WHERE exchange_id = ? AND checked_until IS NOT NULL",
            [exchange_id],
        ).fetchall()
    }
    return SyncCursor(_start_ms(sync_start_date), latest, checked)


def _get_funding_since(db, exchange_id: str, cursor: SyncCursor) -> int:
    row = db.execute(
        "SELECT MAX(timestamp) AS latest FROM funding_fees WHERE exchange_id = ?", [exchange_id]
    ).fetchone()
    return _ms(row["latest"]) if row and row["latest"] else cursor.start_ms


def _insert_trades(
    db, exchange_id: str, exchange: str, trades: list, trade_type: str = "spot"
) -> int:
    """Insert trades into the database, skipping duplicates.

    Futures keep their full symbol (BTC/USDT:USDT), so they never share a
    pair name with spot.
    """
    inserted = 0
    for t in trades:
        trade_id = str(uuid.uuid4())
        external_id = str(t.get("id", t.get("order", trade_id)))
        order_id = str(t.get("order", "")) if t.get("order") else None

        symbol = t.get("symbol", "")
        market = symbol.split(":")[0]
        parts = market.split("/") if "/" in market else [market, ""]
        base = parts[0] if len(parts) > 0 else ""
        quote = parts[1] if len(parts) > 1 else ""
        pair = symbol if trade_type == "futures" else market

        fee_info = t.get("fee", {}) or {}
        fee_cost = fee_info.get("cost", 0) or 0
        fee_currency = fee_info.get("currency", "") or ""

        ts = t.get("timestamp", 0)
        if ts:
            timestamp_str = timeutil.from_ms(ts)
        elif t.get("datetime"):
            timestamp_str = timeutil.to_utc(t["datetime"])
        else:
            print(f"[sync] Warning: trade {external_id} has no time, skipped")
            continue

        amount = float(t.get("amount", 0) or 0)
        price = float(t.get("price", 0) or 0)
        cost = float(t.get("cost", 0) or 0)
        total = cost if cost else amount * price

        try:
            db.execute(
                """INSERT INTO trades
                   (id, exchange_id, exchange, external_id, order_id, timestamp,
                    pair, base_currency, quote_currency, side,
                    quantity, price, total, fee, fee_currency,
                    trade_type, synced_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
                [
                    trade_id, exchange_id, exchange, external_id, order_id,
                    timestamp_str, pair, base, quote, t.get("side", "buy"),
                    amount, price, total, float(fee_cost), fee_currency, trade_type,
                ],
            )
            inserted += 1
        except sqlite3.IntegrityError as e:
            if "UNIQUE" in str(e).upper():
                continue
            raise ExchangeError(f"Could not save {pair} trade {external_id}: {e}") from e

    if inserted > 0:
        db.commit()
    return inserted
