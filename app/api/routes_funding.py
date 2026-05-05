"""Funding fees endpoints — view funding fee history."""

from typing import Optional
from fastapi import APIRouter, Query
from app.database import get_db

router = APIRouter()


@router.get("/funding-fees")
async def list_funding_fees(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    pair: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
):
    """List funding fees with optional filtering."""
    db = get_db()
    try:
        conditions = []
        params = []

        if pair:
            conditions.append("pair = ?")
            params.append(pair)
        if date_from:
            conditions.append("timestamp >= ?")
            params.append(date_from)
        if date_to:
            conditions.append("timestamp <= ?")
            params.append(date_to)

        where = " WHERE " + " AND ".join(conditions) if conditions else ""

        total = db.execute(f"SELECT COUNT(*) FROM funding_fees{where}", params).fetchone()[0]

        offset = (page - 1) * page_size
        rows = db.execute(
            f"SELECT * FROM funding_fees{where} ORDER BY timestamp DESC LIMIT ? OFFSET ?",
            params + [page_size, offset],
        ).fetchall()

        total_pages = max(1, (total + page_size - 1) // page_size)

        return {
            "fees": [dict(r) for r in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
        }
    finally:
        db.close()


@router.get("/funding-fees/summary")
async def funding_fees_summary(
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
):
    """Summary of funding fees — total paid/received per pair."""
    db = get_db()
    try:
        conditions = []
        params = []
        if date_from:
            conditions.append("timestamp >= ?")
            params.append(date_from)
        if date_to:
            conditions.append("timestamp <= ?")
            params.append(date_to)

        where = " WHERE " + " AND ".join(conditions) if conditions else ""

        rows = db.execute(
            f"""SELECT pair,
                       SUM(amount) as total_amount,
                       SUM(CASE WHEN amount > 0 THEN amount ELSE 0 END) as received,
                       SUM(CASE WHEN amount < 0 THEN amount ELSE 0 END) as paid,
                       COUNT(*) as count
                FROM funding_fees{where}
                GROUP BY pair
                ORDER BY total_amount ASC""",
            params,
        ).fetchall()

        total = db.execute(
            f"SELECT COALESCE(SUM(amount), 0) as total FROM funding_fees{where}",
            params,
        ).fetchone()

        return {
            "total_funding": round(total["total"], 4),
            "by_pair": [dict(r) for r in rows],
        }
    finally:
        db.close()
