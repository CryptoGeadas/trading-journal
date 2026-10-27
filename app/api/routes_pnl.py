"""PnL endpoints — realised PnL, breakdowns and the equity curve.

All numbers come from `app.services.pnl_engine`, so they add up. Money is
kept per currency (see the engine): the headline numbers are in one
currency, and `by_currency` lists every currency the trades used.
"""

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Query
from app import config
from app.database import get_db
from app.services import pnl_engine
from app.services.pnl_engine import ClosedTrade, Funding, TradeFilter, Unmatched, money_round

router = APIRouter()

METHOD_PATTERN = "^(fifo|avg_cost)$"


def load_fills(db) -> list[dict]:
    """Every fill, oldest first. Matching needs the full history of each pair."""
    return [dict(r) for r in db.execute(
        """SELECT t.*, COALESCE(e.is_demo, 0) AS is_demo FROM trades t
           LEFT JOIN exchanges e ON e.id = t.exchange_id ORDER BY t.timestamp ASC"""
    ).fetchall()]


def load_funding(db) -> list[dict]:
    return [dict(r) for r in db.execute("SELECT * FROM funding_fees ORDER BY timestamp ASC").fetchall()]


def default_method() -> str:
    method = (config.DEFAULT_PNL_METHOD or "fifo").lower()
    return method if method in pnl_engine.METHODS else "fifo"


@dataclass
class Selection:
    """What a filter keeps of one matching run."""

    closed: list[ClosedTrade]
    funding: list[Funding]
    unmatched: list[Unmatched]
    fills: list[dict]


def match_all(method: Optional[str] = None) -> tuple[pnl_engine.MatchResult, list[dict]]:
    """One matching run over everything; several selections can share it."""
    db = get_db()
    try:
        fills = load_fills(db)
        funding = load_funding(db)
    finally:
        db.close()
    return pnl_engine.match(fills, funding, method or default_method()), fills


def select(flt: TradeFilter, method: Optional[str] = None, matched=None) -> Selection:
    result, fills = matched or match_all(method)
    return Selection(
        closed=[t for t in result.closed if flt.keeps_closed(t)],
        funding=[f for f in result.funding if flt.keeps_funding(f)],
        unmatched=[u for u in result.unmatched if flt.keeps_closed(u)],
        fills=[f for f in fills if flt.keeps(f["exchange"], f["pair"], f["trade_type"],
                                              f["strategy"], f["timestamp"])],
    )


def headline_currency(sel: Selection, wanted: Optional[str] = None) -> str:
    """The asked-for currency, else the one most closed trades use."""
    if wanted:
        return pnl_engine.currency_unit(wanted)
    used = Counter(t.currency for t in sel.closed)
    used.update(f.currency for f in sel.funding if f.currency not in used)
    if not used:
        return "USD"
    return sorted(used.items(), key=lambda kv: (-kv[1], kv[0] != "USD", kv[0]))[0][0]


def _period(timestamp: str, period: str) -> str:
    try:
        dt = datetime.fromisoformat(timestamp[:10])
    except ValueError:
        return timestamp[:7]
    if period == "month":
        return dt.strftime("%Y-%m")
    year, week, _ = dt.isocalendar()
    return f"{year}-W{week:02d}"


def _group_of(item, group_by: str) -> str:
    time = item.close_time if isinstance(item, ClosedTrade) else item.time
    if group_by in ("month", "week"):
        return _period(time, group_by)
    if isinstance(item, Funding):
        if group_by in ("pair", "exchange"):
            return getattr(item, group_by)
        return "Funding"  # funding has no strategy or emotion tag
    return item.group_value(group_by)


def _breakdown(sel: Selection, group_by: str) -> list[dict]:
    """One row per group and currency; the rows of a currency add up to its total."""
    closed = defaultdict(list)
    funding = defaultdict(list)
    if group_by == "pair":
        for f in sel.fills:  # pairs with fills but no closed trade still get a row
            closed[(f["pair"], pnl_engine.currency_unit(pnl_engine.split_pair(f["pair"])[1]))]
    for t in sel.closed:
        closed[(_group_of(t, group_by), t.currency)].append(t)
    for f in sel.funding:
        funding[(_group_of(f, group_by), f.currency)].append(f)

    rows = []
    for key in set(closed) | set(funding):
        group, currency = key
        s = pnl_engine.summarise(closed.get(key, []), funding.get(key, []))
        rows.append({
            "group": group,
            "currency": currency,
            "trade_count": s["trade_count"],
            "total_pnl": money_round(s["total_pnl"], currency),
            "funding_pnl": money_round(s["funding_pnl"], currency),
            "total_fees": money_round(s["total_fees"], currency),
            "win_rate": s["win_rate"],
            "_exact": s["total_pnl"],
        })
    _fix_rounding(rows)
    if group_by in ("month", "week"):
        return sorted(rows, key=lambda r: (r["group"], r["currency"]))
    return sorted(rows, key=lambda r: (r["currency"], r["total_pnl"]))


def _fix_rounding(rows: list[dict]):
    """Rounded rows must add up to the rounded total, to the cent.

    Each row is rounded alone, so a few cents can go missing. The rounding
    remainder of each currency goes to its largest row.
    """
    for currency in {r["currency"] for r in rows}:
        mine = [r for r in rows if r["currency"] == currency]
        total = money_round(sum(r["_exact"] for r in mine), currency)
        gap = money_round(total - sum(r["total_pnl"] for r in mine), currency)
        if gap:
            biggest = max(mine, key=lambda r: abs(r["_exact"]))
            biggest["total_pnl"] = money_round(biggest["total_pnl"] + gap, currency)
    for r in rows:
        del r["_exact"]


def _currency_totals(sel: Selection) -> list[dict]:
    rows = []
    for currency in sorted({t.currency for t in sel.closed} | {f.currency for f in sel.funding}):
        s = pnl_engine.summarise([t for t in sel.closed if t.currency == currency],
                                 [f for f in sel.funding if f.currency == currency])
        rows.append({
            "currency": currency,
            "total_pnl": money_round(s["total_pnl"], currency),
            "trading_pnl": money_round(s["trading_pnl"], currency),
            "funding_pnl": money_round(s["funding_pnl"], currency),
            "total_fees": money_round(s["total_fees"], currency),
            "trade_count": s["trade_count"],
        })
    return rows


def _calculate_pnl(
    exchange: Optional[str] = None,
    pair: Optional[str] = None,
    trade_type: Optional[str] = None,
    strategy: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    group_by: Optional[str] = None,
    method: Optional[str] = None,
    currency: Optional[str] = None,
    matched=None,
) -> dict:
    method = method or default_method()
    sel = select(TradeFilter(exchange, pair, trade_type, strategy, date_from, date_to), method, matched)
    main = headline_currency(sel, currency)
    # Every headline number (counts too) is about the trades in the main currency;
    # by_currency lists the rest.
    s = pnl_engine.summarise([t for t in sel.closed if t.currency == main],
                             [f for f in sel.funding if f.currency == main])

    pair_rows = _breakdown(sel, "pair")
    # Best/worst only among pairs that have a result (an open position is not "best").
    by_pair = [r for r in pair_rows if r["currency"] == main and (r["trade_count"] or r["funding_pnl"])]
    best_pair = max(by_pair, key=lambda r: r["total_pnl"])["group"] if by_pair else None
    worst_pair = min(by_pair, key=lambda r: r["total_pnl"])["group"] if by_pair else None

    return {
        "method": method,
        "currency": main,
        "total_pnl": money_round(s["total_pnl"], main),
        "trading_pnl": money_round(s["trading_pnl"], main),
        "funding_pnl": money_round(s["funding_pnl"], main),
        "total_fees": money_round(s["total_fees"], main),
        "avg_win": money_round(s["avg_win"], main),
        "avg_loss": money_round(s["avg_loss"], main),
        "profit_factor": s["profit_factor"],
        "trade_count": s["trade_count"],
        "win_rate": s["win_rate"],
        "wins": s["wins"],
        "losses": s["losses"],
        "fee_unknown_trades": s["fee_unknown_trades"],
        "unmatched_sells": len(sel.unmatched),
        "by_currency": _currency_totals(sel),
        "best_pair": best_pair,
        "worst_pair": worst_pair,
        "date_from": date_from,
        "date_to": date_to,
        "breakdown": pair_rows if group_by == "pair" else (_breakdown(sel, group_by) if group_by else []),
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
    method: Optional[str] = Query(None, pattern=METHOD_PATTERN),
    currency: Optional[str] = None,
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
        method=method,
        currency=currency,
    )


@router.get("/equity-curve")
async def equity_curve(
    exchange: Optional[str] = None,
    trade_type: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    method: Optional[str] = Query(None, pattern=METHOD_PATTERN),
    currency: Optional[str] = None,
):
    """Cumulative daily PnL (trades + funding) in one currency, for the chart."""
    sel = select(TradeFilter(exchange=exchange, trade_type=trade_type,
                             date_from=date_from, date_to=date_to), method)
    main = headline_currency(sel, currency)
    daily_pnl = defaultdict(float)
    for t in sel.closed:
        if t.currency == main:
            daily_pnl[t.close_time[:10]] += t.pnl
    for f in sel.funding:
        if f.currency == main:
            daily_pnl[f.time[:10]] += f.amount

    result = []
    cumulative = 0.0
    for date in sorted(daily_pnl):
        cumulative += daily_pnl[date]
        result.append({
            "date": date,
            "currency": main,
            "daily_pnl": money_round(daily_pnl[date], main),
            "cumulative_pnl": money_round(cumulative, main),
        })
    return result
