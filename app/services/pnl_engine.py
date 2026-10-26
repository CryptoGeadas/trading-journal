"""One matching engine for every PnL number in the app.

`match()` takes fills (rows of the trades table) and returns closed trades.
Each closed trade carries its PnL, fees, open/close time and the fields of
the fill that closed it, so totals, breakdowns, the equity curve and the
patterns all add up the same numbers.

Matching always runs over the full history of each pair. Filters are applied
to the closed trades afterwards (`TradeFilter.keeps`), so a sale inside a
date range still uses the cost of a buy from before it.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Iterable, Optional

DUST = 1e-8  # quantities below this count as zero


@dataclass
class ClosedTrade:
    pair: str
    trade_type: str
    quantity: float
    pnl: float
    fees: float
    open_time: str
    close_time: str
    exchange: str
    strategy: Optional[str]
    emotion_tag: Optional[str]

    def group_value(self, field_name: str) -> str:
        return getattr(self, field_name) or "None"


@dataclass
class Lot:
    """An open position piece waiting to be closed."""

    pair: str
    quantity: float
    price: float
    fee_per_unit: float
    time: str


@dataclass
class MatchResult:
    closed: list[ClosedTrade] = field(default_factory=list)
    open_lots: list[Lot] = field(default_factory=list)


@dataclass
class TradeFilter:
    """Filters from the query string, applied to closed trades (and fills)."""

    exchange: Optional[str] = None
    pair: Optional[str] = None
    trade_type: Optional[str] = None
    strategy: Optional[str] = None
    date_from: Optional[str] = None
    date_to: Optional[str] = None

    def keeps(self, exchange, pair, trade_type, strategy, timestamp) -> bool:
        if self.exchange and exchange != self.exchange:
            return False
        if self.pair and pair != self.pair:
            return False
        if self.trade_type and trade_type != self.trade_type:
            return False
        if self.strategy:
            if self.strategy == "__untagged__":
                if strategy is not None:
                    return False
            elif strategy != self.strategy:
                return False
        if self.date_from and timestamp < self.date_from:
            return False
        if self.date_to and timestamp > self.date_to:
            return False
        return True

    def keeps_closed(self, t: ClosedTrade) -> bool:
        return self.keeps(t.exchange, t.pair, t.trade_type, t.strategy, t.close_time)

    def sql(self, side: Optional[str] = None) -> tuple[str, list]:
        """WHERE clause for routes that list fills (trades, export)."""
        conditions, params = [], []
        for column, value in (("exchange", self.exchange), ("pair", self.pair),
                              ("side", side), ("trade_type", self.trade_type)):
            if value:
                conditions.append(f"{column} = ?")
                params.append(value)
        if self.strategy:
            if self.strategy == "__untagged__":
                conditions.append("strategy IS NULL")
            else:
                conditions.append("strategy = ?")
                params.append(self.strategy)
        if self.date_from:
            conditions.append("timestamp >= ?")
            params.append(self.date_from)
        if self.date_to:
            conditions.append("timestamp <= ?")
            params.append(self.date_to)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        return where, params


def match(fills: Iterable[dict]) -> MatchResult:
    """Match fills per pair, oldest lot first (FIFO)."""
    by_pair = defaultdict(list)
    for f in fills:
        by_pair[(f["pair"], f.get("trade_type") or "spot")].append(f)

    result = MatchResult()
    for (pair, trade_type), pair_fills in by_pair.items():
        pair_fills.sort(key=lambda f: f["timestamp"])
        lots = deque()
        for f in pair_fills:
            qty = float(f["quantity"])
            price = float(f["price"])
            fee = float(f["fee"] or 0)
            if f["side"] == "buy":
                lots.append(Lot(pair, qty, price, fee / max(qty, DUST), f["timestamp"]))
                continue

            remaining = qty
            cost = 0.0
            buy_fees = 0.0
            open_time = None
            while remaining > DUST and lots:
                lot = lots[0]
                take = min(remaining, lot.quantity)
                open_time = open_time or lot.time
                cost += take * lot.price
                buy_fees += take * lot.fee_per_unit
                lot.quantity -= take
                remaining -= take
                if lot.quantity <= DUST:
                    lots.popleft()

            if cost > 0:
                result.closed.append(ClosedTrade(
                    pair=pair,
                    trade_type=trade_type,
                    quantity=qty - remaining,
                    pnl=qty * price - cost - buy_fees - fee,
                    fees=buy_fees + fee,
                    open_time=open_time,
                    close_time=f["timestamp"],
                    exchange=f["exchange"],
                    strategy=f.get("strategy"),
                    emotion_tag=f.get("emotion_tag"),
                ))
        result.open_lots.extend(lots)

    result.closed.sort(key=lambda t: t.close_time)
    return result


def summarise(closed: list[ClosedTrade]) -> dict:
    """Totals and win/loss stats for a list of closed trades."""
    pnls = [t.pnl for t in closed]
    wins = [p for p in pnls if p >= 0]
    losses = [p for p in pnls if p < 0]
    win_amount = sum(wins)
    loss_amount = sum(losses)
    return {
        "total_pnl": sum(pnls),
        "total_fees": sum(t.fees for t in closed),
        "trade_count": len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "win_amount": win_amount,
        "loss_amount": loss_amount,
        "win_rate": round(len(wins) / max(len(closed), 1) * 100, 1),
        "avg_win": round(win_amount / max(len(wins), 1), 2),
        "avg_loss": round(loss_amount / max(len(losses), 1), 2),
        "profit_factor": round(win_amount / max(abs(loss_amount), 0.01), 2) if loss_amount != 0 else 0,
    }
