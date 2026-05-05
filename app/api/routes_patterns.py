"""Pattern detection — streaks, time-of-day analysis, avg hold time."""

from collections import defaultdict
from datetime import datetime

from fastapi import APIRouter
from app.database import get_db

router = APIRouter()


@router.get("/patterns")
async def detect_patterns():
    """Analyse trading patterns: streaks, time-of-day, hold times."""
    db = get_db()
    try:
        patterns = []

        # ---- Winning/Losing Streaks ----
        streak_data = _detect_streaks(db)
        if streak_data:
            patterns.append(streak_data)

        # ---- Time of Day Analysis ----
        tod_data = _time_of_day_analysis(db)
        if tod_data:
            patterns.append(tod_data)

        # ---- Average Hold Time ----
        hold_data = _avg_hold_time(db)
        if hold_data:
            patterns.append(hold_data)

        return {"patterns": patterns}
    finally:
        db.close()


def _detect_streaks(db) -> dict | None:
    """Find winning and losing streaks based on closed round-trip trades."""
    # Get all sells with their cost basis (simplified: pair-level)
    rows = db.execute(
        """SELECT pair, side, timestamp, quantity, price, total, fee
           FROM trades
           ORDER BY pair, timestamp ASC"""
    ).fetchall()

    if not rows:
        return None

    # FIFO per pair to determine win/loss per sell
    from collections import deque
    buy_queues = defaultdict(deque)
    results = []  # list of (timestamp, pnl)

    for r in rows:
        pair = r["pair"]
        if r["side"] == "buy":
            buy_queues[pair].append({
                "qty": float(r["quantity"]),
                "price": float(r["price"]),
                "fee_per_unit": float(r["fee"]) / max(float(r["quantity"]), 0.00000001),
            })
        elif r["side"] == "sell":
            sell_qty = float(r["quantity"])
            sell_price = float(r["price"])
            sell_fee = float(r["fee"])
            cost_basis = 0.0
            buy_fees = 0.0
            remaining = sell_qty

            queue = buy_queues[pair]
            while remaining > 0 and queue:
                lot = queue[0]
                match_qty = min(remaining, lot["qty"])
                cost_basis += match_qty * lot["price"]
                buy_fees += match_qty * lot["fee_per_unit"]
                lot["qty"] -= match_qty
                remaining -= match_qty
                if lot["qty"] <= 0.00000001:
                    queue.popleft()

            if cost_basis > 0:
                pnl = sell_qty * sell_price - cost_basis - buy_fees - sell_fee
                results.append({"timestamp": r["timestamp"], "pnl": pnl, "pair": pair})

    if len(results) < 2:
        return None

    # Find streaks
    current_streak = 0
    current_type = None
    max_win_streak = 0
    max_loss_streak = 0
    current_win = 0
    current_loss = 0

    for r in results:
        if r["pnl"] >= 0:
            current_win += 1
            current_loss = 0
            max_win_streak = max(max_win_streak, current_win)
        else:
            current_loss += 1
            current_win = 0
            max_loss_streak = max(max_loss_streak, current_loss)

    # Current streak
    if results:
        last_type = "win" if results[-1]["pnl"] >= 0 else "loss"
        current_count = 1
        for i in range(len(results) - 2, -1, -1):
            this_type = "win" if results[i]["pnl"] >= 0 else "loss"
            if this_type == last_type:
                current_count += 1
            else:
                break

    return {
        "type": "streaks",
        "title": "Win/Loss Streaks",
        "data": {
            "total_closed_trades": len(results),
            "max_win_streak": max_win_streak,
            "max_loss_streak": max_loss_streak,
            "current_streak": current_count,
            "current_streak_type": last_type,
        },
        "insight": _streak_insight(max_win_streak, max_loss_streak, current_count, last_type),
    }


def _streak_insight(max_win, max_loss, current, current_type):
    parts = []
    if max_win >= 3:
        parts.append(f"Best winning streak: {max_win} trades in a row.")
    if max_loss >= 3:
        parts.append(f"Worst losing streak: {max_loss} trades in a row.")
    if current >= 3:
        parts.append(f"Currently on a {current}-trade {current_type} streak.")
    if not parts:
        return "No significant streaks detected yet."
    return " ".join(parts)


def _time_of_day_analysis(db) -> dict | None:
    """Analyse performance by hour of day."""
    rows = db.execute(
        """SELECT pair, side, timestamp, quantity, price, total, fee
           FROM trades
           ORDER BY pair, timestamp ASC"""
    ).fetchall()

    if not rows:
        return None

    # FIFO to get PnL per sell, then bucket by hour
    from collections import deque
    buy_queues = defaultdict(deque)
    hourly = defaultdict(lambda: {"count": 0, "pnl": 0.0, "wins": 0})

    for r in rows:
        pair = r["pair"]
        if r["side"] == "buy":
            buy_queues[pair].append({
                "qty": float(r["quantity"]),
                "price": float(r["price"]),
                "fee_per_unit": float(r["fee"]) / max(float(r["quantity"]), 0.00000001),
            })
        elif r["side"] == "sell":
            sell_qty = float(r["quantity"])
            cost_basis = 0.0
            buy_fees = 0.0
            remaining = sell_qty

            queue = buy_queues[pair]
            while remaining > 0 and queue:
                lot = queue[0]
                match_qty = min(remaining, lot["qty"])
                cost_basis += match_qty * lot["price"]
                buy_fees += match_qty * lot["fee_per_unit"]
                lot["qty"] -= match_qty
                remaining -= match_qty
                if lot["qty"] <= 0.00000001:
                    queue.popleft()

            if cost_basis > 0:
                pnl = sell_qty * float(r["price"]) - cost_basis - buy_fees - float(r["fee"])
                try:
                    hour = datetime.fromisoformat(r["timestamp"].replace("Z", "+00:00")).hour
                except Exception:
                    continue

                hourly[hour]["count"] += 1
                hourly[hour]["pnl"] += pnl
                if pnl >= 0:
                    hourly[hour]["wins"] += 1

    if not hourly:
        return None

    # Find best and worst hours
    hours_list = [
        {"hour": h, "count": d["count"], "pnl": round(d["pnl"], 2),
         "win_rate": round(d["wins"] / max(d["count"], 1) * 100, 1)}
        for h, d in sorted(hourly.items())
        if d["count"] >= 1
    ]

    best = max(hours_list, key=lambda x: x["pnl"]) if hours_list else None
    worst = min(hours_list, key=lambda x: x["pnl"]) if hours_list else None

    return {
        "type": "time_of_day",
        "title": "Time of Day Performance",
        "data": {
            "by_hour": hours_list,
            "best_hour": best,
            "worst_hour": worst,
        },
        "insight": _tod_insight(best, worst, hours_list),
    }


def _tod_insight(best, worst, hours_list):
    if not best or not worst:
        return "Not enough data for time-of-day analysis."
    total = sum(h["count"] for h in hours_list)
    parts = [f"Analysed {total} closed trades across {len(hours_list)} trading hours."]
    if best["pnl"] > 0:
        parts.append(f"Best hour: {best['hour']:02d}:00 UTC ({best['count']} trades, +${best['pnl']:.2f}).")
    if worst["pnl"] < 0:
        parts.append(f"Worst hour: {worst['hour']:02d}:00 UTC ({worst['count']} trades, -${abs(worst['pnl']):.2f}).")
    return " ".join(parts)


def _avg_hold_time(db) -> dict | None:
    """Calculate average time between buy and sell for each pair."""
    rows = db.execute(
        """SELECT pair, side, timestamp
           FROM trades
           ORDER BY pair, timestamp ASC"""
    ).fetchall()

    if not rows:
        return None

    # Track first buy → first sell per position per pair
    hold_times = []
    pair_buys = defaultdict(list)

    for r in rows:
        pair = r["pair"]
        ts = r["timestamp"]
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except Exception:
            continue

        if r["side"] == "buy":
            pair_buys[pair].append(dt)
        elif r["side"] == "sell" and pair_buys[pair]:
            entry_time = pair_buys[pair][0]  # FIFO — match with oldest buy
            pair_buys[pair].pop(0)
            hold_seconds = (dt - entry_time).total_seconds()
            if hold_seconds >= 0:
                hold_times.append({
                    "pair": pair,
                    "seconds": hold_seconds,
                })

    if not hold_times:
        return None

    avg_seconds = sum(h["seconds"] for h in hold_times) / len(hold_times)
    min_hold = min(hold_times, key=lambda x: x["seconds"])
    max_hold = max(hold_times, key=lambda x: x["seconds"])

    return {
        "type": "hold_time",
        "title": "Average Hold Time",
        "data": {
            "avg_seconds": round(avg_seconds),
            "avg_formatted": _format_duration(avg_seconds),
            "shortest": {
                "pair": min_hold["pair"],
                "duration": _format_duration(min_hold["seconds"]),
            },
            "longest": {
                "pair": max_hold["pair"],
                "duration": _format_duration(max_hold["seconds"]),
            },
            "total_round_trips": len(hold_times),
        },
        "insight": f"Average hold time across {len(hold_times)} round-trip trades: {_format_duration(avg_seconds)}. Shortest: {min_hold['pair']} ({_format_duration(min_hold['seconds'])}). Longest: {max_hold['pair']} ({_format_duration(max_hold['seconds'])}).",
    }


def _format_duration(seconds: float) -> str:
    """Format seconds into a human-readable duration."""
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    elif seconds < 3600:
        return f"{seconds // 60}m {seconds % 60}s"
    elif seconds < 86400:
        hours = seconds // 3600
        mins = (seconds % 3600) // 60
        return f"{hours}h {mins}m"
    else:
        days = seconds // 86400
        hours = (seconds % 86400) // 3600
        return f"{days}d {hours}h"
