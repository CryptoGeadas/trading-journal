"""Trades endpoints — grouped orders with fill expansion."""

from typing import Optional
from fastapi import APIRouter, Query, HTTPException
from app.database import get_db
from app.models import TradeOut, TradeUpdate, TradesPage, OrderOut, OrdersPage

router = APIRouter()


def _build_where(exchange, pair, side, strategy, date_from, date_to):
    """Build WHERE clause and params from filter values."""
    conditions = []
    params = []

    if exchange:
        conditions.append("exchange = ?")
        params.append(exchange)
    if pair:
        conditions.append("pair = ?")
        params.append(pair)
    if side:
        conditions.append("side = ?")
        params.append(side)
    if strategy:
        if strategy == "__untagged__":
            conditions.append("strategy IS NULL")
        else:
            conditions.append("strategy = ?")
            params.append(strategy)
    if date_from:
        conditions.append("timestamp >= ?")
        params.append(date_from)
    if date_to:
        conditions.append("timestamp <= ?")
        params.append(date_to)

    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    return where, params


@router.get("/trades", response_model=OrdersPage)
async def list_trades(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    exchange: Optional[str] = None,
    pair: Optional[str] = None,
    side: Optional[str] = None,
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
        where, params = _build_where(exchange, pair, side, strategy, date_from, date_to)

        # Grouped query: aggregate fills into orders
        # COALESCE(order_id, id) ensures fills without an order_id are treated individually
        group_key = "COALESCE(order_id, id)"

        # Count distinct orders
        count_sql = f"""
            SELECT COUNT(*) FROM (
                SELECT {group_key} as gk
                FROM trades {where}
                GROUP BY {group_key}, exchange_id, pair, side
            )
        """
        total = db.execute(count_sql, params).fetchone()[0]

        # Fetch grouped page
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
        # Try matching by order_id first, then by trade id (for single-fill orders)
        rows = db.execute(
            "SELECT * FROM trades WHERE order_id = ? ORDER BY timestamp ASC",
            [order_id],
        ).fetchall()

        if not rows:
            # Might be a single fill with no order_id — try by trade id
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

        if updates:
            set_clause = ", ".join(f"{k} = ?" for k in updates)
            values = list(updates.values())

            # If this fill has an order_id, update ALL fills in the same order
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
