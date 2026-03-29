"""Overview endpoint — summary stats for the dashboard."""

from fastapi import APIRouter
from app.database import get_db
from app.models import OverviewStats

router = APIRouter()


@router.get("/overview", response_model=OverviewStats)
async def get_overview():
    """Return high-level stats for the overview screen."""
    db = get_db()
    try:
        # Total trades
        total_trades = db.execute("SELECT COUNT(*) FROM trades").fetchone()[0]

        # Connected exchanges
        connected = db.execute("SELECT COUNT(*) FROM exchanges").fetchone()[0]

        # Last sync across all exchanges
        last_sync_row = db.execute(
            "SELECT MAX(last_sync_at) FROM exchanges"
        ).fetchone()
        last_sync = last_sync_row[0] if last_sync_row else None

        # Trades this month
        trades_this_month = db.execute(
            "SELECT COUNT(*) FROM trades WHERE timestamp >= date('now', 'start of month')"
        ).fetchone()[0]

        # Trades this week
        trades_this_week = db.execute(
            "SELECT COUNT(*) FROM trades WHERE timestamp >= date('now', 'weekday 0', '-7 days')"
        ).fetchone()[0]

        # Total fees
        total_fees = db.execute(
            "SELECT COALESCE(SUM(fee), 0) FROM trades"
        ).fetchone()[0]

        # Strategy / pair lists (for future use)
        strategies = db.execute(
            "SELECT DISTINCT strategy FROM trades WHERE strategy IS NOT NULL"
        ).fetchall()

        return OverviewStats(
            total_trades=total_trades,
            total_pnl=0.0,  # Phase 3: real PnL calculation
            total_fees=round(total_fees, 2),
            win_rate=0.0,  # Phase 3
            connected_exchanges=connected,
            last_sync=last_sync,
            trades_this_month=trades_this_month,
            trades_this_week=trades_this_week,
            pnl_this_month=0.0,  # Phase 3
            pnl_this_week=0.0,  # Phase 3
            best_pair=None,  # Phase 3
            worst_pair=None,  # Phase 3
        )
    finally:
        db.close()
