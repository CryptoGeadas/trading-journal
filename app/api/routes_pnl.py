"""PnL endpoints — realised PnL, breakdowns and the equity curve.

All numbers come from `app.services.pnl_engine`, so they add up.
"""

from collections import defaultdict
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Query
from app.database import get_db
from app.services import pnl_engine
from app.services.pnl_engine import ClosedTrade, TradeFilter

router = APIRouter()


def load_fills(db) -> list[dict]:
    """Every fill, oldest first. Matching needs the full history of each pair."""
    return [dict(r) for r in db.execute("SELECT * FROM trades ORDER BY timestamp ASC").fetchall()]


def closed_trades(flt: TradeFilter) -> tuple[list[ClosedTrade], list[dict]]:
    """Closed trades kept by the filter, plus the filtered fills."""
    db = get_db()
    try:
        fills = load_fills(db)
    finally:
        db.close()
    result = pnl_engine.match(fills)
    kept = [t for t in result.closed if flt.keeps_closed(t)]
    kept_fills = [f for f in fills if flt.keeps(f["exchange"], f["pair"], f["trade_type"],
                                                f["strategy"], f["timestamp"])]
    return kept, kept_fills


def _period(timestamp: str, period: str) -> str:
    try:
        dt = datetime.fromisoformat(timestamp[:10])
    except ValueError:
        return timestamp[:7]
    if period == "month":
        return dt.strftime("%Y-%m")
    year, week, _ = dt.isocalendar()
    return f"{year}-W{week:02d}"


def _breakdown(closed: list[ClosedTrade], fills: list[dict], group_by: str) -> list[dict]:
    groups = defaultdict(list)
    if group_by == "pair":
        for f in fills:  # pairs with fills but no closed trade still get a row
            groups[f["pair"]]
    for t in closed:
        if group_by in ("month", "week"):
            key = _period(t.close_time, group_by)
        else:
            key = t.group_value(group_by)
        groups[key].append(t)

    rows = []
    for key, trades in groups.items():
        s = pnl_engine.summarise(trades)
        rows.append({
            "group": key,
            "trade_count": s["trade_count"],
            "total_pnl": round(s["total_pnl"], 2),
            "total_fees": round(s["total_fees"], 2),
            "win_rate": s["win_rate"],
        })
    if group_by in ("month", "week"):
        return sorted(rows, key=lambda r: r["group"])
    return sorted(rows, key=lambda r: r["total_pnl"])


def _calculate_pnl(
    exchange: Optional[str] = None,
    pair: Optional[str] = None,
    trade_type: Optional[str] = None,
    strategy: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    group_by: Optional[str] = None,
) -> dict:
    flt = TradeFilter(exchange, pair, trade_type, strategy, date_from, date_to)
    closed, fills = closed_trades(flt)
    s = pnl_engine.summarise(closed)

    by_pair = _breakdown(closed, fills, "pair")
    best_pair = max(by_pair, key=lambda r: r["total_pnl"])["group"] if by_pair else None
    worst_pair = min(by_pair, key=lambda r: r["total_pnl"])["group"] if by_pair else None

    return {
        "total_pnl": round(s["total_pnl"], 2),
        "total_fees": round(s["total_fees"], 2),
        "trade_count": s["trade_count"],
        "win_rate": s["win_rate"],
        "wins": s["wins"],
        "losses": s["losses"],
        "avg_win": s["avg_win"],
        "avg_loss": s["avg_loss"],
        "profit_factor": s["profit_factor"],
        "best_pair": best_pair,
        "worst_pair": worst_pair,
        "date_from": date_from,
        "date_to": date_to,
        "breakdown": _breakdown(closed, fills, group_by) if group_by else [],
    }


@router.get("/pnl")
async def calculate_pnl(
    group_by: Optional[str] = Query(None, pattern="^(pair|exchange|strategy|emotion_tag|month|week)$"),
    exchange: Optional[str] = None,
    pair: Optional[str] = None,
    trade_type: Optional[str] = None,
    strategy: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
):
    """Realised PnL. Matching uses each pair's full history; filters pick closed trades."""
    return _calculate_pnl(
        exchange=exchange,
        pair=pair,
        trade_type=trade_type,
        strategy=strategy,
        date_from=date_from,
        date_to=date_to,
        group_by=group_by,
    )


@router.get("/equity-curve")
async def equity_curve(
    exchange: Optional[str] = None,
    trade_type: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
):
    """Return cumulative daily PnL for the equity curve chart."""
    closed, _ = closed_trades(TradeFilter(exchange=exchange, trade_type=trade_type,
                                          date_from=date_from, date_to=date_to))
    daily_pnl = defaultdict(float)
    for t in closed:
        daily_pnl[t.close_time[:10]] += t.pnl

    result = []
    cumulative = 0.0
    for date in sorted(daily_pnl):
        cumulative += daily_pnl[date]
        result.append({
            "date": date,
            "daily_pnl": round(daily_pnl[date], 2),
            "cumulative_pnl": round(cumulative, 2),
        })
    return result
