"""Overview endpoint — summary stats for the dashboard."""

from typing import Optional

from fastapi import APIRouter, Query
from app.api.routes_pnl import METHOD_PATTERN, _calculate_pnl, match_all
from app import timeutil
from app.database import get_db
from app.services import pnl_engine
from app.services.pnl_engine import money_round

router = APIRouter()


@router.get("/overview")
async def get_overview(
    method: Optional[str] = Query(None, pattern=METHOD_PATTERN),
    currency: Optional[str] = None,
):
    """Return high-level stats for the overview screen, including real PnL."""
    db = get_db()
    try:
        # Total trades (grouped orders)
        total_trades = db.execute(
            "SELECT COUNT(DISTINCT COALESCE(order_id, id)) FROM trades"
        ).fetchone()[0]

        # Connected exchanges
        connected = db.execute("SELECT COUNT(*) FROM exchanges WHERE removed_at IS NULL").fetchone()[0]

        # Last sync across all exchanges
        last_sync_row = db.execute(
            "SELECT MAX(last_sync_at) FROM exchanges WHERE removed_at IS NULL"
        ).fetchone()
        last_sync = last_sync_row[0] if last_sync_row else None

        # Trades this month
        trades_this_month = db.execute(
            "SELECT COUNT(DISTINCT COALESCE(order_id, id)) FROM trades WHERE timestamp >= ?", [_start_of_month()]
        ).fetchone()[0]

        # Trades this week
        # Same week start (Monday, UTC) as the week PnL below.
        trades_this_week = db.execute(
            "SELECT COUNT(DISTINCT COALESCE(order_id, id)) FROM trades WHERE timestamp >= ?", [_start_of_week()]
        ).fetchone()[0]

        # PnL (and fees) in one currency; other currencies are listed, never added in.
        matched = match_all(method)
        fills = matched[1]
        pnl_all = _calculate_pnl(method=method, currency=currency, matched=matched)
        main = pnl_all["currency"]
        pnl_month = _calculate_pnl(date_from=_start_of_month(), method=method, currency=main, matched=matched)
        pnl_week = _calculate_pnl(date_from=_start_of_week(), method=method, currency=main, matched=matched)

        fees, fees_unknown = pnl_engine.fees_paid(fills, main)

        def others(result):
            return [c for c in result["by_currency"] if c["currency"] != main]

        return {
            "total_trades": total_trades,
            "closed_trades": pnl_all["trade_count"],
            "total_pnl": pnl_all["total_pnl"],
            "currency": main,
            "other_currencies": others(pnl_all),
            "month_other_currencies": others(pnl_month),
            "week_other_currencies": others(pnl_week),
            "unmatched_sells": pnl_all["unmatched_sells"],
            "total_fees": money_round(fees, main),
            "fees_unknown": fees_unknown,
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
    return timeutil.format_utc(now.replace(day=1, hour=0, minute=0, second=0, microsecond=0))


def _start_of_week():
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=now.weekday())
    return timeutil.format_utc(start.replace(hour=0, minute=0, second=0, microsecond=0))
