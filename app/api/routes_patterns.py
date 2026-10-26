"""Pattern detection — streaks, time-of-day analysis, avg hold time."""

from collections import Counter, defaultdict
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from typing import Optional

from fastapi import APIRouter, Query
from app.api.routes_pnl import METHOD_PATTERN, match_all
from app.services import pnl_engine
from app.services.pnl_engine import ClosedTrade, money_round

router = APIRouter()


@router.get("/patterns")
async def detect_patterns(
    method: Optional[str] = Query(None, pattern=METHOD_PATTERN),
    currency: Optional[str] = None,
    tz: Optional[str] = Query(None, max_length=64),
):
    """Analyse trading patterns: streaks, time-of-day, hold times.

    `tz` is the browser's time zone (e.g. Europe/Lisbon), so hours match the
    trade times on the page. Without it (or if unknown) hours are UTC.
    """
    result, _ = match_all(method)
    closed = result.closed
    money_currency = pnl_engine.currency_unit(currency) if currency else main_currency(closed)
    zone_name, zone = _zone(tz)

    patterns = []
    for data in (_detect_streaks(closed), _time_of_day_analysis(closed, money_currency, zone, zone_name),
                 _avg_hold_time(closed)):
        if data:
            patterns.append(data)
    return {"patterns": patterns}


def _detect_streaks(closed: list[ClosedTrade]) -> dict | None:
    """Find winning and losing streaks based on closed round-trip trades."""
    results = [{"timestamp": t.close_time, "pnl": t.pnl, "pair": t.pair} for t in closed]
    if len(results) < 2:
        return None

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


def main_currency(closed: list[ClosedTrade]) -> str:
    """The currency most closed trades use (money in one currency only)."""
    used = Counter(t.currency for t in closed)
    return sorted(used.items(), key=lambda kv: (-kv[1], kv[0] != "USD", kv[0]))[0][0] if used else "USD"


def _zone(name: Optional[str]):
    if name:
        try:
            return name, ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError, OSError):  # unknown, malformed, or a folder name
            print(f"[patterns] Unknown time zone {name!r}; hours are shown in UTC")
    return "UTC", timezone.utc


def _time_of_day_analysis(closed: list[ClosedTrade], currency: str, zone=timezone.utc,
                          zone_name: str = "UTC") -> dict | None:
    """Analyse performance by hour of day (local hour the trade was closed), in one currency."""
    hourly = defaultdict(lambda: {"count": 0, "pnl": 0.0, "wins": 0})
    for t in closed:
        if t.currency != currency:
            continue
        try:
            hour = datetime.fromisoformat(t.close_time.replace("Z", "+00:00")).astimezone(zone).hour
        except ValueError:
            continue
        hourly[hour]["count"] += 1
        hourly[hour]["pnl"] += t.pnl
        if t.pnl >= 0:
            hourly[hour]["wins"] += 1

    if not hourly:
        return None

    # Find best and worst hours
    hours_list = [
        {"hour": h, "count": d["count"], "pnl": money_round(d["pnl"], currency),
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
            "currency": currency,
            "time_zone": zone_name,
            "by_hour": hours_list,
            "best_hour": best,
            "worst_hour": worst,
        },
        "insight": _tod_insight(best, worst, hours_list, currency, zone_name),
    }


def _money(amount: float, currency: str) -> str:
    if currency == "USD":
        return f"${amount:,.2f}"
    return f"{amount:.8f}".rstrip("0").rstrip(".") + f" {currency}"


def _tod_insight(best, worst, hours_list, currency, zone_name="UTC"):
    if not best or not worst:
        return "Not enough data for time-of-day analysis."
    total = sum(h["count"] for h in hours_list)
    parts = [f"Analysed {total} closed trades across {len(hours_list)} trading hours."]
    if best["pnl"] > 0:
        parts.append(f"Best hour: {best['hour']:02d}:00 {zone_name} ({best['count']} trades, +{_money(best['pnl'], currency)}).")
    if worst["pnl"] < 0:
        parts.append(f"Worst hour: {worst['hour']:02d}:00 {zone_name} ({worst['count']} trades, -{_money(abs(worst['pnl']), currency)}).")
    return " ".join(parts)


def _avg_hold_time(closed: list[ClosedTrade]) -> dict | None:
    """Average time from the first matched buy to the sale."""
    hold_times = []
    for t in closed:
        try:
            opened = datetime.fromisoformat(t.open_time.replace("Z", "+00:00"))
            closed_at = datetime.fromisoformat(t.close_time.replace("Z", "+00:00"))
        except ValueError:
            continue
        hold_seconds = (closed_at - opened).total_seconds()
        if hold_seconds >= 0:
            hold_times.append({"pair": t.pair, "seconds": hold_seconds})

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
