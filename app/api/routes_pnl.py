"""PnL calculation endpoints — Phase 3 implementation."""

from typing import Optional
from fastapi import APIRouter, Query
from app.models import PnlResult

router = APIRouter()


@router.get("/pnl", response_model=PnlResult)
async def calculate_pnl(
    method: str = Query("fifo", pattern="^(fifo|avg_cost)$"),
    group_by: Optional[str] = Query(None, pattern="^(pair|exchange|strategy|month|week)$"),
    exchange: Optional[str] = None,
    pair: Optional[str] = None,
    strategy: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
):
    """Calculate realised PnL. Full implementation in Phase 3."""
    return PnlResult(
        total_pnl=0.0,
        total_fees=0.0,
        trade_count=0,
        method=method,
        date_from=date_from,
        date_to=date_to,
        breakdown=[],
    )
