"""Trades endpoints — grouped orders with fill expansion + manual entry."""

import math
import re
import uuid
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Query, HTTPException
from pydantic import BaseModel
from app import paths, timeutil
from app.database import get_db
from app.services.pnl_engine import TradeFilter
from app.models import TradeOut, TradeUpdate, TradesPage, OrderOut, OrdersPage

router = APIRouter()


class ManualTradeCreate(BaseModel):
    exchange: str = "manual"
    pair: str
    side: str  # "buy" or "sell"
    quantity: float
    price: float
    fee: float = 0.0
    fee_currency: str = "USDT"
    trade_type: str = "spot"  # "spot" or "futures"
    timestamp: Optional[str] = None  # ISO format, defaults to now
    strategy: Optional[str] = None
    notes: Optional[str] = None
    order_id: Optional[str] = None  # optional exchange order ID


def _build_where(exchange, pair, side, strategy, date_from, date_to, trade_type=None):
    """Build WHERE clause and params from filter values."""
    return TradeFilter(exchange, pair, trade_type, strategy, date_from, date_to).sql(side=side)


@router.get("/trades", response_model=OrdersPage)
async def list_trades(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    exchange: Optional[str] = None,
    pair: Optional[str] = None,
    side: Optional[str] = None,
    trade_type: Optional[str] = None,
    strategy: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    order_by: str = Query("timestamp", pattern="^(timestamp|pair|exchange|total)$"),
    order_dir: str = Query("desc", pattern="^(asc|desc)$"),
):
    """
    List trades grouped by order.

    Multiple fills from the same exchange order are aggregated into one row.
    Each row includes fill_count showing how many fills make up the order.
    Use /trades/fills/{order_id} to see individual fills.
    """
    db = get_db()
    try:
        where, params = _build_where(exchange, pair, side, strategy, date_from, date_to, trade_type)

        group_key = "COALESCE(order_id, id)"

        count_sql = f"""
            SELECT COUNT(*) FROM (
                SELECT {group_key} as gk
                FROM trades {where}
                GROUP BY {group_key}, exchange_id, pair, side
            )
        """
        total = db.execute(count_sql, params).fetchone()[0]

        offset = (page - 1) * page_size
        query = f"""
            SELECT
                {group_key}          as group_id,
                MIN(id)              as id,
                exchange_id,
                exchange,
                order_id,
                MIN(timestamp)       as timestamp,
                pair,
                base_currency,
                quote_currency,
                side,
                SUM(quantity)        as quantity,
                CASE WHEN SUM(quantity) > 0
                     THEN SUM(total) / SUM(quantity)
                     ELSE 0 END      as price,
                SUM(total)           as total,
                SUM(fee)             as fee,
                MIN(fee_currency)    as fee_currency,
                MIN(trade_type)      as trade_type,
                MIN(strategy)        as strategy,
                MIN(notes)           as notes,
                MIN(emotion_tag)     as emotion_tag,
                MIN(trade_rating)    as trade_rating,
                COUNT(*)             as fill_count
            FROM trades {where}
            GROUP BY {group_key}, exchange_id, pair, side
            ORDER BY {order_by} {order_dir}
            LIMIT ? OFFSET ?
        """
        rows = db.execute(query, params + [page_size, offset]).fetchall()

        orders = []
        for r in rows:
            orders.append(OrderOut(
                id=r["id"],
                group_id=r["group_id"],
                exchange_id=r["exchange_id"],
                exchange=r["exchange"],
                order_id=r["order_id"],
                timestamp=r["timestamp"],
                pair=r["pair"],
                base_currency=r["base_currency"],
                quote_currency=r["quote_currency"],
                side=r["side"],
                quantity=r["quantity"],
                price=round(r["price"], 8),
                total=r["total"],
                fee=r["fee"],
                fee_currency=r["fee_currency"],
                trade_type=r["trade_type"],
                strategy=r["strategy"],
                notes=r["notes"],
                emotion_tag=r["emotion_tag"],
                trade_rating=r["trade_rating"],
                fill_count=r["fill_count"],
            ))

        total_pages = max(1, (total + page_size - 1) // page_size)

        return OrdersPage(
            trades=orders,
            total=total,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
        )
    finally:
        db.close()


@router.get("/trades/fills/{order_id}")
async def get_fills(order_id: str):
    """Get individual fills for a given order_id."""
    db = get_db()
    try:
        rows = db.execute(
            "SELECT * FROM trades WHERE order_id = ? ORDER BY timestamp ASC",
            [order_id],
        ).fetchall()

        if not rows:
            rows = db.execute(
                "SELECT * FROM trades WHERE id = ?",
                [order_id],
            ).fetchall()

        if not rows:
            raise HTTPException(status_code=404, detail="Order not found")

        return [TradeOut(**dict(r)) for r in rows]
    finally:
        db.close()


@router.get("/trades/{trade_id}", response_model=TradeOut)
async def get_trade(trade_id: str):
    """Get a single trade/fill by ID."""
    db = get_db()
    try:
        row = db.execute("SELECT * FROM trades WHERE id = ?", [trade_id]).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Trade not found")
        return TradeOut(**dict(row))
    finally:
        db.close()


@router.patch("/trades/{trade_id}", response_model=TradeOut)
async def update_trade(trade_id: str, update: TradeUpdate):
    """
    Update strategy or notes on a trade.
    If the trade has an order_id, updates all fills in that order.
    """
    db = get_db()
    try:
        existing = db.execute("SELECT * FROM trades WHERE id = ?", [trade_id]).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Trade not found")

        updates = {}
        if update.strategy is not None:
            updates["strategy"] = update.strategy if update.strategy != "" else None
        if update.notes is not None:
            updates["notes"] = update.notes if update.notes != "" else None
        if update.emotion_tag is not None:
            updates["emotion_tag"] = update.emotion_tag if update.emotion_tag != "" else None
        if update.trade_rating is not None:
            if update.trade_rating == 0:
                updates["trade_rating"] = None
            elif 1 <= update.trade_rating <= 5:
                updates["trade_rating"] = update.trade_rating
            else:
                raise HTTPException(status_code=400, detail="Trade rating must be between 1 and 5.")

        if updates:
            set_clause = ", ".join(f"{k} = ?" for k in updates)
            values = list(updates.values())

            if existing["order_id"]:
                db.execute(
                    f"UPDATE trades SET {set_clause} WHERE order_id = ?",
                    values + [existing["order_id"]],
                )
            else:
                db.execute(
                    f"UPDATE trades SET {set_clause} WHERE id = ?",
                    values + [trade_id],
                )
            db.commit()

        row = db.execute("SELECT * FROM trades WHERE id = ?", [trade_id]).fetchone()
        return TradeOut(**dict(row))
    finally:
        db.close()


@router.post("/trades", status_code=201)
async def create_manual_trade(body: ManualTradeCreate):
    """Manually add a trade (for OTC, unlisted exchanges, or corrections)."""
    if body.side not in ("buy", "sell"):
        raise HTTPException(status_code=400, detail="Side must be 'buy' or 'sell'.")
    if body.trade_type not in ("spot", "futures"):
        raise HTTPException(status_code=400, detail="Type must be 'spot' or 'futures'.")
    if not all(math.isfinite(v) for v in (body.quantity, body.price, body.fee)):
        raise HTTPException(status_code=400, detail="Quantity, price and fee must be normal numbers.")
    # A negative fee would count as profit. Maker rebates can go in the notes.
    if body.fee < 0:
        raise HTTPException(status_code=400, detail="Fee cannot be negative (write a rebate in the notes).")
    if body.quantity <= 0:
        raise HTTPException(status_code=400, detail="Quantity must be positive.")
    if body.price <= 0:
        raise HTTPException(status_code=400, detail="Price must be positive.")
    pair = body.pair.upper().strip()
    if not re.fullmatch(r"[A-Z0-9]+/[A-Z0-9]+(:[A-Z0-9]+)?", pair):
        raise HTTPException(status_code=400, detail="Pair must be in BASE/QUOTE format (e.g. BTC/USDT).")
    if ":" in pair and body.trade_type != "futures":
        raise HTTPException(status_code=400, detail="A pair with ':' (e.g. BTC/USDT:USDT) is a futures pair; set the type to futures.")
    base, _, rest = pair.partition("/")
    quote = rest.split(":")[0]
    if body.trade_type == "futures" and ":" not in pair:
        pair = f"{pair}:{quote}"  # futures never share a pair name with spot

    trade_id = str(uuid.uuid4())
    external_id = f"manual_{trade_id[:8]}"
    try:
        ts = timeutil.to_utc(body.timestamp) if body.timestamp else timeutil.now_utc()
    except ValueError:
        raise HTTPException(status_code=400, detail="Time must look like 2026-01-31T12:00:00Z (an offset like +02:00 is fine).")
    total = body.quantity * body.price

    db = get_db()
    try:
        # Ensure a "manual" exchange record exists for manual trades
        exchange_id = f"manual_{body.exchange.lower().replace(' ', '_')}"
        existing_ex = db.execute(
            "SELECT id FROM exchanges WHERE id = ?", [exchange_id]
        ).fetchone()
        if not existing_ex:
            db.execute(
                """INSERT INTO exchanges (id, exchange, label, api_key_enc, api_secret_enc, is_read_only, is_manual)
                   VALUES (?, ?, ?, '', '', 1, 1)""",
                [exchange_id, body.exchange.lower(), f"{body.exchange} (Manual)"],
            )

        db.execute(
            """INSERT INTO trades
               (id, exchange_id, exchange, external_id, order_id, timestamp,
                pair, base_currency, quote_currency, side,
                quantity, price, total, fee, fee_currency,
                trade_type, strategy, notes, synced_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
            [
                trade_id, exchange_id, body.exchange.lower(), external_id,
                body.order_id or None, ts,
                pair, base, quote, body.side,
                body.quantity, body.price, total,
                body.fee, body.fee_currency,
                body.trade_type, body.strategy or None, body.notes or None,
            ],
        )
        db.commit()

        return {
            "status": "created",
            "id": trade_id,
            "pair": pair,
            "side": body.side,
            "total": round(total, 2),
        }
    finally:
        db.close()


@router.delete("/trades/{trade_id}")
async def delete_manual_trade(trade_id: str, confirm: bool = False):
    """Delete a manual trade (all its fills when it has an order ID). Synced trades can't be deleted:
    the next sync would bring them back."""
    if not confirm:
        raise HTTPException(status_code=400, detail="Pass ?confirm=true to delete the trade.")
    db = get_db()
    try:
        row = db.execute(
            """SELECT t.id, t.order_id, t.exchange_id, e.is_manual FROM trades t
               JOIN exchanges e ON e.id = t.exchange_id WHERE t.id = ?""",
            [trade_id],
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Trade not found")
        if not row["is_manual"]:
            raise HTTPException(status_code=403, detail="Only manual trades can be deleted. Synced trades come back on the next sync.")

        if row["order_id"]:
            ids = [r["id"] for r in db.execute(
                "SELECT id FROM trades WHERE order_id = ? AND exchange_id = ?", [row["order_id"], row["exchange_id"]]
            ).fetchall()]
        else:
            ids = [trade_id]
        marks = ",".join("?" * len(ids))
        shots = db.execute(
            f"SELECT filename FROM trade_screenshots WHERE trade_id IN ({marks})", ids
        ).fetchall()
        db.execute(f"DELETE FROM trade_screenshots WHERE trade_id IN ({marks})", ids)
        db.execute(f"DELETE FROM trades WHERE id IN ({marks})", ids)
        # A manual source with no trades left goes too (it only exists for its trades).
        db.execute(
            "DELETE FROM exchanges WHERE id = ? AND is_manual = 1 AND NOT EXISTS (SELECT 1 FROM trades WHERE exchange_id = ?)",
            [row["exchange_id"], row["exchange_id"]],
        )
        db.commit()
        files_left = []
        for s in shots:  # the trade is gone already; a stuck file must not turn that into an error
            try:
                (paths.screenshots_dir() / s["filename"]).unlink(missing_ok=True)
            except OSError as e:
                print(f"[trades] Could not remove screenshot {s['filename']}: {e}")
                files_left.append(s["filename"])
        return {"status": "deleted", "deleted": len(ids), "files_left": files_left}
    finally:
        db.close()


@router.get("/pairs")
async def list_pairs():
    """List all unique trading pairs (for filter dropdowns)."""
    db = get_db()
    try:
        rows = db.execute(
            "SELECT DISTINCT pair FROM trades ORDER BY pair"
        ).fetchall()
        return [r["pair"] for r in rows]
    finally:
        db.close()
