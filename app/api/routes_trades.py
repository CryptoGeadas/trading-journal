"""Trades endpoints — list, detail, update."""

from typing import Optional
from fastapi import APIRouter, Query, HTTPException
from app.database import get_db
from app.models import TradeOut, TradeUpdate, TradesPage

router = APIRouter()


@router.get("/trades", response_model=TradesPage)
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
    """List trades with filtering, sorting, and pagination."""
    db = get_db()
    try:
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

        # Count
        total = db.execute(f"SELECT COUNT(*) FROM trades{where}", params).fetchone()[0]

        # Fetch page
        offset = (page - 1) * page_size
        rows = db.execute(
            f"SELECT * FROM trades{where} ORDER BY {order_by} {order_dir} LIMIT ? OFFSET ?",
            params + [page_size, offset],
        ).fetchall()

        trades = [TradeOut(**dict(r)) for r in rows]
        total_pages = max(1, (total + page_size - 1) // page_size)

        return TradesPage(
            trades=trades,
            total=total,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
        )
    finally:
        db.close()


@router.get("/trades/{trade_id}", response_model=TradeOut)
async def get_trade(trade_id: str):
    """Get a single trade by ID."""
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
    """Update strategy or notes on a trade."""
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
            db.execute(
                f"UPDATE trades SET {set_clause} WHERE id = ?",
                list(updates.values()) + [trade_id],
            )
            db.commit()

        row = db.execute("SELECT * FROM trades WHERE id = ?", [trade_id]).fetchone()
        return TradeOut(**dict(row))
    finally:
        db.close()


@router.get("/strategies")
async def list_strategies():
    """List all strategy tags with trade counts."""
    db = get_db()
    try:
        rows = db.execute(
            """SELECT strategy, COUNT(*) as trade_count
               FROM trades
               WHERE strategy IS NOT NULL
               GROUP BY strategy
               ORDER BY trade_count DESC"""
        ).fetchall()
        return [{"strategy": r["strategy"], "trade_count": r["trade_count"]} for r in rows]
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
