"""PnL calculation engine — FIFO cost basis, realised PnL, performance stats."""

from typing import Optional
from collections import defaultdict, deque

from fastapi import APIRouter, Query
from app.database import get_db

router = APIRouter()


def _calculate_pnl(
    exchange: Optional[str] = None,
    pair: Optional[str] = None,
    trade_type: Optional[str] = None,
    strategy: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    group_by: Optional[str] = None,
) -> dict:
    """
    Calculate realised PnL using FIFO matching.

    For each pair:
    1. Sort all trades by timestamp
    2. Buys go into a FIFO queue (quantity, price, total, fee)
    3. Each sell is matched against the oldest available buy lots
    4. Realised PnL = sell proceeds - cost basis - buy fees - sell fees
    """
    db = get_db()
    try:
        # Build filter conditions
        conditions = []
        params = []
        if exchange:
            conditions.append("exchange = ?")
            params.append(exchange)
        if pair:
            conditions.append("pair = ?")
            params.append(pair)
        if trade_type:
            conditions.append("trade_type = ?")
            params.append(trade_type)
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

        # Fetch all matching trades (ungrouped fills, sorted by time)
        rows = db.execute(
            f"""SELECT * FROM trades {where}
                ORDER BY pair, timestamp ASC""",
            params,
        ).fetchall()

        # Group trades by pair
        trades_by_pair = defaultdict(list)
        for r in rows:
            trades_by_pair[r["pair"]].append(dict(r))

        # Calculate PnL per pair using FIFO
        all_results = []
        total_pnl = 0.0
        total_fees = 0.0
        total_trades = 0  # counted as completed round-trips
        wins = 0
        losses = 0
        total_win_amount = 0.0
        total_loss_amount = 0.0

        for pair_name, trades in trades_by_pair.items():
            pair_result = _fifo_match(pair_name, trades)
            all_results.append(pair_result)

            total_pnl += pair_result["realised_pnl"]
            total_fees += pair_result["total_fees"]
            total_trades += pair_result["closed_trades"]
            wins += pair_result["wins"]
            losses += pair_result["losses"]
            total_win_amount += pair_result["total_win_amount"]
            total_loss_amount += pair_result["total_loss_amount"]

        # Build breakdown based on group_by
        breakdown = []
        if group_by == "pair":
            breakdown = [
                {
                    "group": r["pair"],
                    "trade_count": r["closed_trades"],
                    "total_pnl": round(r["realised_pnl"], 2),
                    "total_fees": round(r["total_fees"], 2),
                    "win_rate": round(r["wins"] / max(r["closed_trades"], 1) * 100, 1),
                }
                for r in sorted(all_results, key=lambda x: x["realised_pnl"])
            ]
        elif group_by == "exchange":
            breakdown = _group_results_by(rows, trades_by_pair, "exchange")
        elif group_by == "strategy":
            breakdown = _group_results_by(rows, trades_by_pair, "strategy")
        elif group_by == "month":
            breakdown = _group_results_by_time(rows, trades_by_pair, "month")
        elif group_by == "week":
            breakdown = _group_results_by_time(rows, trades_by_pair, "week")

        win_rate = round(wins / max(total_trades, 1) * 100, 1)
        avg_win = round(total_win_amount / max(wins, 1), 2)
        avg_loss = round(total_loss_amount / max(losses, 1), 2)
        profit_factor = round(total_win_amount / max(abs(total_loss_amount), 0.01), 2) if total_loss_amount != 0 else 0

        # Find best/worst pairs
        best_pair = max(all_results, key=lambda x: x["realised_pnl"])["pair"] if all_results else None
        worst_pair = min(all_results, key=lambda x: x["realised_pnl"])["pair"] if all_results else None

        return {
            "total_pnl": round(total_pnl, 2),
            "total_fees": round(total_fees, 2),
            "trade_count": total_trades,
            "win_rate": win_rate,
            "wins": wins,
            "losses": losses,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "profit_factor": profit_factor,
            "best_pair": best_pair,
            "worst_pair": worst_pair,
            "date_from": date_from,
            "date_to": date_to,
            "breakdown": breakdown,
        }
    finally:
        db.close()


def _fifo_match(pair: str, trades: list) -> dict:
    """
    FIFO matching for a single pair.
    Returns stats for this pair.
    """
    buy_queue = deque()  # Each entry: {"qty": float, "price": float, "fee_per_unit": float}
    realised_pnl = 0.0
    total_fees = 0.0
    closed_trades = 0
    wins = 0
    losses = 0
    total_win_amount = 0.0
    total_loss_amount = 0.0

    for t in trades:
        qty = float(t["quantity"])
        price = float(t["price"])
        fee = float(t["fee"])
        total_fees += fee
        side = t["side"]

        if side == "buy":
            buy_queue.append({
                "qty": qty,
                "price": price,
                "fee_per_unit": fee / max(qty, 0.00000001),
            })
        elif side == "sell":
            remaining_sell_qty = qty
            sell_proceeds = qty * price
            cost_basis = 0.0
            buy_fees = 0.0

            while remaining_sell_qty > 0 and buy_queue:
                lot = buy_queue[0]
                match_qty = min(remaining_sell_qty, lot["qty"])

                cost_basis += match_qty * lot["price"]
                buy_fees += match_qty * lot["fee_per_unit"]

                lot["qty"] -= match_qty
                remaining_sell_qty -= match_qty

                if lot["qty"] <= 0.00000001:
                    buy_queue.popleft()

            # PnL for this sell = proceeds - cost - buy fees - sell fee
            pnl = sell_proceeds - cost_basis - buy_fees - fee

            if cost_basis > 0:  # Only count as closed trade if we had matching buys
                closed_trades += 1
                realised_pnl += pnl

                if pnl >= 0:
                    wins += 1
                    total_win_amount += pnl
                else:
                    losses += 1
                    total_loss_amount += pnl

    return {
        "pair": pair,
        "realised_pnl": realised_pnl,
        "total_fees": total_fees,
        "closed_trades": closed_trades,
        "wins": wins,
        "losses": losses,
        "total_win_amount": total_win_amount,
        "total_loss_amount": total_loss_amount,
        "open_qty": sum(lot["qty"] for lot in buy_queue),
    }


def _group_results_by(rows, trades_by_pair, field):
    """Group PnL results by a trade field (exchange, strategy)."""
    grouped = defaultdict(list)
    for r in rows:
        key = r[field] or "None"
        grouped[key].append(dict(r))

    results = []
    for group_name, group_trades in grouped.items():
        # Re-group by pair and calculate
        by_pair = defaultdict(list)
        for t in group_trades:
            by_pair[t["pair"]].append(t)

        total_pnl = 0
        total_fees = 0
        total_closed = 0
        total_wins = 0
        for pair_name, pair_trades in by_pair.items():
            pr = _fifo_match(pair_name, sorted(pair_trades, key=lambda x: x["timestamp"]))
            total_pnl += pr["realised_pnl"]
            total_fees += pr["total_fees"]
            total_closed += pr["closed_trades"]
            total_wins += pr["wins"]

        results.append({
            "group": group_name,
            "trade_count": total_closed,
            "total_pnl": round(total_pnl, 2),
            "total_fees": round(total_fees, 2),
            "win_rate": round(total_wins / max(total_closed, 1) * 100, 1),
        })

    return sorted(results, key=lambda x: x["total_pnl"])


def _group_results_by_time(rows, trades_by_pair, period):
    """Group PnL results by time period (month or week)."""
    # For time grouping, we calculate PnL across all pairs but segment by time
    # This is approximate — we attribute a sell's PnL to the sell's timestamp
    from datetime import datetime

    grouped = defaultdict(lambda: {"pnl": 0, "fees": 0, "trades": 0, "wins": 0})

    for pair_name, trades in trades_by_pair.items():
        buy_queue = deque()
        for t in trades:
            qty = float(t["quantity"])
            price = float(t["price"])
            fee = float(t["fee"])

            if t["side"] == "buy":
                buy_queue.append({"qty": qty, "price": price, "fee_per_unit": fee / max(qty, 0.00000001)})
            elif t["side"] == "sell":
                remaining = qty
                cost_basis = 0.0
                buy_fees = 0.0

                while remaining > 0 and buy_queue:
                    lot = buy_queue[0]
                    match_qty = min(remaining, lot["qty"])
                    cost_basis += match_qty * lot["price"]
                    buy_fees += match_qty * lot["fee_per_unit"]
                    lot["qty"] -= match_qty
                    remaining -= match_qty
                    if lot["qty"] <= 0.00000001:
                        buy_queue.popleft()

                pnl = qty * price - cost_basis - buy_fees - fee

                # Determine time group
                ts = t["timestamp"][:10]
                try:
                    dt = datetime.fromisoformat(ts)
                    if period == "month":
                        key = dt.strftime("%Y-%m")
                    else:
                        key = f"{dt.isocalendar()[0]}-W{dt.isocalendar()[1]:02d}"
                except Exception:
                    key = ts[:7]

                if cost_basis > 0:
                    grouped[key]["pnl"] += pnl
                    grouped[key]["fees"] += fee
                    grouped[key]["trades"] += 1
                    if pnl >= 0:
                        grouped[key]["wins"] += 1

    results = []
    for key in sorted(grouped.keys()):
        g = grouped[key]
        results.append({
            "group": key,
            "trade_count": g["trades"],
            "total_pnl": round(g["pnl"], 2),
            "total_fees": round(g["fees"], 2),
            "win_rate": round(g["wins"] / max(g["trades"], 1) * 100, 1),
        })

    return results


@router.get("/pnl")
async def calculate_pnl(
    group_by: Optional[str] = Query(None, pattern="^(pair|exchange|strategy|month|week)$"),
    exchange: Optional[str] = None,
    pair: Optional[str] = None,
    trade_type: Optional[str] = None,
    strategy: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
):
    """Calculate realised PnL using FIFO cost basis matching."""
    return _calculate_pnl(
        exchange=exchange,
        pair=pair,
        trade_type=trade_type,
        strategy=strategy,
        date_from=date_from,
        date_to=date_to,
        group_by=group_by,
    )
