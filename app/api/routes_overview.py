"""Overview endpoint — summary stats for the dashboard."""

from fastapi import APIRouter
from app.database import get_db

router = APIRouter()


@router.get("/overview")
async def get_overview():
    """Return high-level stats for the overview screen, including real PnL."""
    db = get_db()
    try:
        # Total trades (grouped orders)
        total_trades = db.execute(
            "SELECT COUNT(DISTINCT COALESCE(order_id, id)) FROM trades"
        ).fetchone()[0]

        # Connected exchanges
        connected = db.execute("SELECT COUNT(*) FROM exchanges").fetchone()[0]

        # Last sync across all exchanges
        last_sync_row = db.execute(
            "SELECT MAX(last_sync_at) FROM exchanges"
        ).fetchone()
        last_sync = last_sync_row[0] if last_sync_row else None

        # Trades this month
        trades_this_month = db.execute(
            "SELECT COUNT(DISTINCT COALESCE(order_id, id)) FROM trades WHERE timestamp >= date('now', 'start of month')"
        ).fetchone()[0]

        # Trades this week
        trades_this_week = db.execute(
            "SELECT COUNT(DISTINCT COALESCE(order_id, id)) FROM trades WHERE timestamp >= date('now', 'weekday 0', '-7 days')"
        ).fetchone()[0]

        # Total fees
        total_fees = db.execute(
            "SELECT COALESCE(SUM(fee), 0) FROM trades"
        ).fetchone()[0]

        # Calculate PnL using FIFO (import inline to avoid circular deps)
        from app.api.routes_pnl import _calculate_pnl

        pnl_all = _calculate_pnl()
        pnl_month = _calculate_pnl(
            date_from=_start_of_month(),
        )
        pnl_week = _calculate_pnl(
            date_from=_start_of_week(),
        )

        return {
            "total_trades": total_trades,
            "total_pnl": pnl_all["total_pnl"],
            "total_fees": round(total_fees, 2),
            "win_rate": pnl_all["win_rate"],
            "connected_exchanges": connected,
            "last_sync": last_sync,
            "trades_this_month": trades_this_month,
            "trades_this_week": trades_this_week,
            "pnl_this_month": pnl_month["total_pnl"],
            "pnl_this_week": pnl_week["total_pnl"],
            "best_pair": pnl_all["best_pair"],
            "worst_pair": pnl_all["worst_pair"],
            "profit_factor": pnl_all["profit_factor"],
            "avg_win": pnl_all["avg_win"],
            "avg_loss": pnl_all["avg_loss"],
        }
    finally:
        db.close()


def _start_of_month():
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()


def _start_of_week():
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=now.weekday())
    return start.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
