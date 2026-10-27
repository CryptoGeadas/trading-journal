"""Demo data: a realistic journal to explore before connecting an exchange.

Every demo row is marked at its source: trades and funding belong to demo
exchange accounts (exchanges.is_demo = 1); demo strategies and notebook
notes carry is_demo = 1. So one rule loads, finds and removes them, and
real rows are never touched.

`generate(seed, today)` is deterministic: the same seed and day always
give the same data (the video and screenshots depend on that).
"""

from __future__ import annotations

import random
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone

from app import paths, timeutil

DEFAULT_SEED = 7
MONTHS = 6

DEMO_ACCOUNTS = {
    "demo_binance": ("binance", "Binance (demo)"),
    "demo_kraken": ("kraken", "Kraken (demo)"),
}

STRATEGIES = [
    ("Breakout", "#34d399", "Buy the close above a 4h range high.",
     "1. Range of at least 3 days.\n2. Volume above the 20-bar average.\n3. Stop under the range, target 2R."),
    ("Mean Reversion", "#6c9cfc", "Fade a move far from the daily VWAP.",
     "1. Price 2 ATR from VWAP.\n2. RSI divergence on the 1h.\n3. Take profit at VWAP."),
    ("Trend Pullback", "#fbbf24", "Join the trend on a pullback to the 21 EMA.",
     "1. Higher highs on the daily.\n2. Pullback to the 21 EMA.\n3. Trail the stop under swing lows."),
]

EMOTIONS = ["Followed Plan", "High Conviction", "FOMO", "Impulse", "Revenge Trade", "Overtraded", "News-Driven"]

TRADE_NOTES = [
    "Entry was clean, exit too early.",
    "Moved the stop to break-even after 1R.",
    "Chased the candle. Should have waited for the retest.",
    "News spike, took half off into it.",
    "Textbook setup, sized normally.",
    "Held through a wick that nearly stopped me out.",
]

NOTEBOOK = [
    ("Weekly review", "Best trades came from the Breakout playbook. Cut size on Fridays."),
    ("Rules", "Max 3 trades a day. No trades in the first 15 minutes after big news."),
    ("Ideas to test", "Does Mean Reversion work better on SOL than on ETH?"),
]

# (pair, trade_type, account, start price, typical size)
MARKETS = [
    ("BTC/USDT", "spot", "demo_binance", 62000.0, 0.05),
    ("ETH/USDT", "spot", "demo_binance", 3100.0, 0.8),
    ("SOL/USDT", "spot", "demo_kraken", 145.0, 12.0),
    ("ETH/USDT", "spot", "demo_kraken", 3100.0, 0.5),
    ("BTC/USDT:USDT", "futures", "demo_binance", 62000.0, 0.03),
    ("ETH/USDT:USDT", "futures", "demo_binance", 3100.0, 0.6),
]

FEE_RATE = {"spot": 0.001, "futures": 0.0004}


@dataclass
class DemoData:
    trades: list[dict] = field(default_factory=list)
    funding: list[dict] = field(default_factory=list)
    strategies: list[dict] = field(default_factory=list)
    notes: list[dict] = field(default_factory=list)


def generate(seed: int = DEFAULT_SEED, today: date | None = None) -> DemoData:
    """Six months of round trips ending yesterday, the same for the same seed and day."""
    rng = random.Random(seed)
    today = today or datetime.now(timezone.utc).date()
    start = today - timedelta(days=30 * MONTHS)
    data = DemoData()
    ids = _ids(rng)

    for name, colour, description, playbook in STRATEGIES:
        data.strategies.append({"id": next(ids)[:8], "name": name, "colour": colour,
                                "description": description, "playbook": playbook})

    prices = {}
    for pair, trade_type, account, start_price, size in MARKETS:
        price = prices.get(pair.split(":")[0], start_price)
        day = start + timedelta(days=rng.randint(0, 6))
        last_closed = datetime.min.replace(tzinfo=timezone.utc)
        while day < today - timedelta(days=1):
            hold_hours = rng.choice([2, 5, 9, 20, 30, 52, 80])
            opened = datetime.combine(day, time(rng.randint(0, 23), rng.randint(0, 59)), timezone.utc)
            # One position per market at a time (also keeps funding times unique).
            opened = max(opened, last_closed + timedelta(hours=1))
            closed = opened + timedelta(hours=hold_hours, minutes=rng.randint(0, 59))
            if closed.date() >= today:
                break
            short = trade_type == "futures" and rng.random() < 0.4
            win = rng.random() < 0.56
            move = rng.uniform(0.006, 0.05) * (1 if win else -1) * (-1 if short else 1)
            entry = round(price * rng.uniform(0.97, 1.03), 2)
            exit_price = round(entry * (1 + move), 2)
            qty = round(size * rng.uniform(0.5, 1.5), 4)
            strategy = rng.choice(STRATEGIES)[0] if rng.random() < 0.85 else None
            emotion = rng.choice(EMOTIONS[:3] if win else EMOTIONS) if rng.random() < 0.7 else None
            rating = (rng.randint(3, 5) if win else rng.randint(1, 3)) if rng.random() < 0.6 else None
            note = rng.choice(TRADE_NOTES) if rng.random() < 0.3 else None
            order = next(ids)[:10]

            for when, side, px in ((opened, "sell" if short else "buy", entry),
                                   (closed, "buy" if short else "sell", exit_price)):
                fills = 2 if rng.random() < 0.25 else 1  # some orders fill in two parts
                for part in range(fills):
                    part_qty = round(qty / fills, 6)
                    fee = round(part_qty * px * FEE_RATE[trade_type], 6)
                    data.trades.append({
                        "id": next(ids), "exchange_id": account, "exchange": DEMO_ACCOUNTS[account][0],
                        "external_id": f"demo-{next(ids)[:12]}", "order_id": f"{order}-{side}",
                        "timestamp": timeutil.format_utc(when + timedelta(seconds=part)),
                        "pair": pair, "side": side, "quantity": part_qty, "price": px,
                        "fee": fee, "fee_currency": "USDT", "trade_type": trade_type,
                        "strategy": strategy, "emotion_tag": emotion, "trade_rating": rating, "notes": note,
                    })

            if trade_type == "futures":  # funding every 8 hours while the position is open
                paid = opened.replace(minute=0, second=0) + timedelta(hours=8 - opened.hour % 8)
                while paid < closed:
                    rate = rng.uniform(-0.0001, 0.0003)
                    amount = -qty * entry * rate * (-1 if short else 1)
                    data.funding.append({
                        "id": next(ids)[:12], "exchange_id": account, "exchange": DEMO_ACCOUNTS[account][0],
                        "external_id": f"demo-{next(ids)[:8]}", "timestamp": timeutil.format_utc(paid),
                        "pair": pair, "amount": round(amount, 6), "asset": "USDT",
                    })
                    paid += timedelta(hours=8)

            price = exit_price
            last_closed = closed
            day = closed.date() + timedelta(days=rng.randint(0, 4))
        prices[pair.split(":")[0]] = price

    for i, (title, content) in enumerate(NOTEBOOK):
        data.notes.append({"id": next(ids)[:8], "title": title, "content": content,
                           "created_at": timeutil.format_utc(datetime.combine(today - timedelta(days=20 * (3 - i)),
                                                                              time(9), timezone.utc))})
    return data


def _ids(rng: random.Random):
    while True:
        yield str(uuid.UUID(int=rng.getrandbits(128), version=4))


def is_loaded(db) -> bool:
    return bool(db.execute("SELECT 1 FROM exchanges WHERE is_demo = 1 LIMIT 1").fetchone())


def load(db, seed: int = DEFAULT_SEED, today: date | None = None) -> dict:
    """Write the demo data. Returns counts. Refuses to load twice."""
    if is_loaded(db):
        raise ValueError("Demo data is already loaded.")
    data = generate(seed, today)
    for account, (exchange, label) in DEMO_ACCOUNTS.items():
        db.execute("""INSERT INTO exchanges (id, exchange, label, api_key_enc, api_secret_enc, is_read_only, is_demo)
                      VALUES (?, ?, ?, '', '', 1, 1)""", [account, exchange, label])
    existing = {r[0].lower(): r[0] for r in db.execute("SELECT name FROM strategies").fetchall()}
    strategies = [s for s in data.strategies if s["name"].lower() not in existing]  # never touch a real one
    for t in data.trades:  # tag with the real strategy's exact name ("breakout", not "Breakout")
        if t["strategy"] and t["strategy"].lower() in existing:
            t["strategy"] = existing[t["strategy"].lower()]
    for s in strategies:
        db.execute("""INSERT INTO strategies (id, name, description, colour, playbook, is_demo)
                      VALUES (?, ?, ?, ?, ?, 1)""", [s["id"], s["name"], s["description"], s["colour"], s["playbook"]])
    for t in data.trades:
        base, _, rest = t["pair"].partition("/")
        db.execute(
            """INSERT INTO trades (id, exchange_id, exchange, external_id, order_id, timestamp, pair,
               base_currency, quote_currency, side, quantity, price, total, fee, fee_currency, trade_type,
               strategy, notes, emotion_tag, trade_rating)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [t["id"], t["exchange_id"], t["exchange"], t["external_id"], t["order_id"], t["timestamp"], t["pair"],
             base, rest.split(":")[0], t["side"], t["quantity"], t["price"], t["quantity"] * t["price"],
             t["fee"], t["fee_currency"], t["trade_type"], t["strategy"], t["notes"], t["emotion_tag"],
             t["trade_rating"]])
    for f in data.funding:
        db.execute("""INSERT INTO funding_fees (id, exchange_id, exchange, external_id, timestamp, pair, amount, asset)
                      VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                   [f["id"], f["exchange_id"], f["exchange"], f["external_id"], f["timestamp"], f["pair"],
                    f["amount"], f["asset"]])
    for n in data.notes:
        db.execute("""INSERT INTO notes (id, title, content, created_at, updated_at, is_demo)
                      VALUES (?, ?, ?, ?, ?, 1)""", [n["id"], n["title"], n["content"], n["created_at"], n["created_at"]])
    db.commit()
    return {"trades": len(data.trades), "funding": len(data.funding),
            "strategies": len(strategies), "notes": len(data.notes)}


def remove(db) -> dict:
    """Delete every demo row (and only those)."""
    accounts = [r[0] for r in db.execute("SELECT id FROM exchanges WHERE is_demo = 1").fetchall()]
    marks = ",".join("?" * len(accounts)) or "''"
    trade_ids = [r[0] for r in db.execute(f"SELECT id FROM trades WHERE exchange_id IN ({marks})", accounts).fetchall()]
    counts = {"trades": len(trade_ids)}
    shots = []
    if trade_ids:
        id_marks = ",".join("?" * len(trade_ids))
        shots = [r[0] for r in db.execute(
            f"SELECT filename FROM trade_screenshots WHERE trade_id IN ({id_marks})", trade_ids).fetchall()]
        db.execute(f"DELETE FROM trade_screenshots WHERE trade_id IN ({id_marks})", trade_ids)
    counts["funding"] = db.execute(f"DELETE FROM funding_fees WHERE exchange_id IN ({marks})", accounts).rowcount
    db.execute(f"DELETE FROM trades WHERE exchange_id IN ({marks})", accounts)
    db.execute(f"DELETE FROM tracked_pairs WHERE exchange_id IN ({marks})", accounts)
    db.execute(f"DELETE FROM sync_log WHERE exchange_id IN ({marks})", accounts)
    db.execute(f"DELETE FROM exchanges WHERE id IN ({marks})", accounts)
    # A demo strategy the user put on their own trades is theirs now: keep it.
    db.execute("""UPDATE strategies SET is_demo = 0 WHERE is_demo = 1
                  AND name IN (SELECT strategy FROM trades WHERE strategy IS NOT NULL)""")
    counts["strategies"] = db.execute("DELETE FROM strategies WHERE is_demo = 1").rowcount
    counts["notes"] = db.execute("DELETE FROM notes WHERE is_demo = 1").rowcount
    db.commit()
    counts["files_left"] = []
    for name in shots:  # rows are gone already; a locked file must not turn that into an error
        try:
            (paths.screenshots_dir() / name).unlink(missing_ok=True)
        except OSError as e:
            print(f"[demo] Could not remove screenshot {name}: {e}")
            counts["files_left"].append(name)
    return counts
