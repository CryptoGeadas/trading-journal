"""Trade sync engine — orchestrates adapters and handles trade insertion."""

import uuid
from datetime import datetime, timezone
from typing import Optional

from app.database import get_db
from app.exchanges.connector import create_client, get_adapter, ExchangeError
from app.exchanges.base import ExchangeAdapter
from app.encryption import decrypt


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
        since = _get_sync_since(db, exchange_id, row["sync_start_date"])

        tracked_pairs = _get_tracked_pairs(db, exchange_id)
        extra_pairs_spot = adapter.load_extra_pairs(futures=False)
        extra_pairs_futures = adapter.load_extra_pairs(futures=True)

        spot_trades = adapter.sync_spot(client, since, tracked_pairs, extra_pairs_spot)
        futures_trades = adapter.sync_futures(client, since, tracked_pairs, extra_pairs_futures)
        funding_count = adapter.sync_funding(client, db, exchange_id, since)

        inserted_spot = _insert_trades(db, exchange_id, row["exchange"], spot_trades, "spot")
        inserted_futures = _insert_trades(db, exchange_id, row["exchange"], futures_trades, "futures")
        inserted = inserted_spot + inserted_futures

        _update_tracked_pairs(db, exchange_id, spot_trades + futures_trades)

        now = datetime.now(timezone.utc).isoformat()
        db.execute(
            "UPDATE exchanges SET last_sync_at = ?, last_sync_status = 'success' WHERE id = ?",
            [now, exchange_id],
        )
        db.execute(
            """UPDATE sync_log SET completed_at = ?, status = 'success', trades_fetched = ?
               WHERE id = ?""",
            [now, inserted, log_id],
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
        print(f"[sync] {exchange_id}: {detail}")
        return {"status": "success", "trades_fetched": inserted, "error": None}

    except Exception as e:
        error_msg = f"{type(e).__name__}: {e}"
        print(f"[sync] {exchange_id}: error — {error_msg}")

        if log_id:
            now = datetime.now(timezone.utc).isoformat()
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
    """Load tracked pairs for this exchange from the DB."""
    rows = db.execute(
        "SELECT pair FROM tracked_pairs WHERE exchange_id = ?", [exchange_id]
    ).fetchall()
    return {r["pair"] for r in rows}


def _update_tracked_pairs(db, exchange_id: str, trades: list):
    """Insert any new pairs discovered in this sync batch."""
    seen_pairs = set()
    for t in trades:
        symbol = t.get("symbol", "")
        pair = symbol.split(":")[0] if ":" in symbol else symbol
        if pair and "/" in pair:
            seen_pairs.add(pair)

    for pair in seen_pairs:
        try:
            db.execute(
                "INSERT OR IGNORE INTO tracked_pairs (exchange_id, pair) VALUES (?, ?)",
                [exchange_id, pair],
            )
        except Exception:
            pass
    if seen_pairs:
        db.commit()


def _get_sync_since(
    db, exchange_id: str, sync_start_date: Optional[str]
) -> Optional[int]:
    """Determine the 'since' timestamp for fetching trades."""
    row = db.execute(
        "SELECT MAX(timestamp) as latest FROM trades WHERE exchange_id = ?",
        [exchange_id],
    ).fetchone()

    if row and row["latest"]:
        dt = datetime.fromisoformat(row["latest"].replace("Z", "+00:00"))
        return int(dt.timestamp() * 1000) + 1000

    if sync_start_date:
        try:
            dt = datetime.fromisoformat(sync_start_date)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return int(dt.timestamp() * 1000)
        except ValueError:
            pass

    start_of_year = int(
        datetime(datetime.now().year, 1, 1, tzinfo=timezone.utc).timestamp() * 1000
    )
    return start_of_year


def _insert_trades(
    db, exchange_id: str, exchange: str, trades: list, trade_type: str = "spot"
) -> int:
    """Insert trades into the database, skipping duplicates."""
    inserted = 0
    for t in trades:
        trade_id = str(uuid.uuid4())
        external_id = str(t.get("id", t.get("order", trade_id)))
        order_id = str(t.get("order", "")) if t.get("order") else None

        symbol = t.get("symbol", "")
        display_symbol = symbol.split(":")[0] if ":" in symbol else symbol
        parts = display_symbol.split("/") if "/" in display_symbol else [display_symbol, ""]
        base = parts[0] if len(parts) > 0 else ""
        quote = parts[1] if len(parts) > 1 else ""

        fee_info = t.get("fee", {}) or {}
        fee_cost = fee_info.get("cost", 0) or 0
        fee_currency = fee_info.get("currency", "") or ""

        ts = t.get("timestamp", 0)
        if ts:
            dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
            timestamp_str = dt.isoformat()
        else:
            timestamp_str = t.get("datetime", datetime.now(timezone.utc).isoformat())

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
                    timestamp_str, display_symbol, base, quote, t.get("side", "buy"),
                    amount, price, total, float(fee_cost), fee_currency, trade_type,
                ],
            )
            inserted += 1
        except Exception as e:
            if "UNIQUE" in str(e).upper():
                continue
            else:
                print(f"[sync] Warning: failed to insert trade {external_id}: {e}")

    if inserted > 0:
        db.commit()
    return inserted
