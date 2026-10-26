"""One matching engine for every PnL number in the app.

`match()` takes fills (rows of the trades table) and funding payments and
returns closed trades. Each closed trade carries its PnL, fees, open/close
time, currency and the fields of the fill that closed it, so totals,
breakdowns, the equity curve and the patterns all add up the same numbers.

Matching always runs over the full history of each pair. Filters are applied
to the closed trades afterwards (`TradeFilter.keeps`), so a sale inside a
date range still uses the cost of a buy from before it.

Rules:
- Spot: buys open lots, sells close them (FIFO or average cost). A sell with
  no buy on record is kept apart as "unmatched": its cost is unknown, so it
  is never counted as profit.
- Futures: a sell with no open long opens a short; a buy closes shorts first.
- Fees are turned into quote currency. A fee in the base coin is worth
  fee x price, and a base-coin buy fee also shrinks the lot. A fee in any
  other coin is unknown: it is left out and the trade is flagged.
- Amounts are kept per currency. USDT, USDC, USD and other dollar
  stablecoins count as one currency, "USD". Other quotes (BTC, ETH, EUR...)
  are never added to it.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Iterable, Optional

from app import timeutil

DUST = 1e-8  # quantities below this count as zero

METHODS = ("fifo", "avg_cost")

# Dollar stablecoins grouped as one currency, said so on the page.
USD_STABLES = {"USD", "USDT", "USDC", "BUSD", "FDUSD", "TUSD", "DAI", "USDP", "USDE", "PYUSD"}


def currency_unit(code: str) -> str:
    code = (code or "").upper()
    return "USD" if code in USD_STABLES else code


def money_round(amount: float, currency: str) -> float:
    """Dollars to the cent, coins to 8 decimals (0.0012 BTC must not become 0)."""
    return round(amount, 2 if currency == "USD" else 8)


def split_pair(pair: str) -> tuple[str, str]:
    """'BTC/USDT' and 'BTC/USDT:USDT' -> ('BTC', 'USDT')."""
    base, _, rest = pair.upper().partition("/")
    return base, rest.split(":")[0]


@dataclass
class ClosedTrade:
    pair: str
    trade_type: str
    currency: str
    quantity: float
    pnl: float
    fees: float
    open_time: str
    close_time: str
    exchange: str
    strategy: Optional[str]
    emotion_tag: Optional[str]
    direction: str = "long"
    fee_unknown: bool = False

    def group_value(self, field_name: str) -> str:
        return getattr(self, field_name) or "None"


@dataclass
class Unmatched:
    """The part of a sell with no buy on record: cost unknown, not in PnL."""

    pair: str
    trade_type: str
    quantity: float
    time: str
    exchange: str
    strategy: Optional[str]


@dataclass
class Funding:
    pair: str
    currency: str
    amount: float
    time: str
    exchange: str


@dataclass
class Lot:
    """An open position piece waiting to be closed."""

    pair: str
    quantity: float
    price: float
    fee_per_unit: float
    time: str
    direction: str = "long"
    fee_unknown: bool = False


@dataclass
class MatchResult:
    closed: list[ClosedTrade] = field(default_factory=list)
    open_lots: list[Lot] = field(default_factory=list)
    unmatched: list[Unmatched] = field(default_factory=list)
    funding: list[Funding] = field(default_factory=list)


class BadFilter(ValueError):
    """A filter value the API cannot use (answered with 400)."""


@dataclass
class TradeFilter:
    """Filters from the query string, applied to closed trades (and fills).

    Dates become [start, end) in the stored UTC format; a date-only
    `date_to` includes that whole day.
    """

    exchange: Optional[str] = None
    pair: Optional[str] = None
    trade_type: Optional[str] = None
    strategy: Optional[str] = None
    date_from: Optional[str] = None
    date_to: Optional[str] = None

    def __post_init__(self):
        try:
            self._start, self._end = timeutil.filter_bounds(self.date_from, self.date_to)
        except ValueError:
            raise BadFilter("Dates must look like 2026-01-31 or 2026-01-31T12:00:00Z.") from None

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
        if self._start and timestamp < self._start:
            return False
        if self._end and timestamp >= self._end:
            return False
        return True

    def keeps_closed(self, t: ClosedTrade | Unmatched) -> bool:
        time = t.close_time if isinstance(t, ClosedTrade) else t.time
        return self.keeps(t.exchange, t.pair, t.trade_type, t.strategy, time)

    def keeps_funding(self, f: Funding) -> bool:
        # Funding belongs to futures and to no strategy (it counts as untagged).
        if self.strategy and self.strategy != "__untagged__":
            return False
        return self.keeps(f.exchange, f.pair, "futures", None, f.time)

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
        if self._start:
            conditions.append("timestamp >= ?")
            params.append(self._start)
        if self._end:
            conditions.append("timestamp < ?")
            params.append(self._end)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        return where, params


def fee_in_quote(f: dict) -> tuple[Optional[float], float]:
    """(fee worth in quote currency or None if unknown, base-coin quantity paid as fee)."""
    fee = float(f.get("fee") or 0)
    if fee == 0:
        return 0.0, 0.0
    base, quote = split_pair(f["pair"])
    fee_currency = (f.get("fee_currency") or "").upper()
    if not fee_currency:
        return None, 0.0  # the exchange did not say which coin: unknown
    if currency_unit(fee_currency) == currency_unit(quote):  # USDT fee on a USDC pair is known
        return fee, 0.0
    if fee_currency == base:
        return fee * float(f["price"]), fee
    return None, 0.0


def fees_paid(fills: Iterable[dict], currency: str) -> tuple[float, int]:
    """(known fees of fills quoted in `currency`, open positions included; fills whose fee is unknown)."""
    total = 0.0
    unknown = 0
    for f in fills:
        if currency_unit(split_pair(f["pair"])[1]) == currency:
            value = fee_in_quote(f)[0]
            if value is None:
                unknown += 1
            else:
                total += value
    return total, unknown


def _fill_order(f: dict):
    # At the same moment a buy goes before a sell, so a quick round trip is
    # never left with a sale that has "no buy on record".
    return f["timestamp"], 0 if f["side"] == "buy" else 1


def match(fills: Iterable[dict], funding: Iterable[dict] = (), method: str = "fifo") -> MatchResult:
    """Match fills per pair and trade type. `method` is 'fifo' or 'avg_cost'."""
    if method not in METHODS:
        raise ValueError(f"unknown PnL method {method!r}")
    by_pair = defaultdict(list)
    for f in fills:
        by_pair[(f["pair"], f.get("trade_type") or "spot")].append(f)

    result = MatchResult()
    for (pair, trade_type), pair_fills in by_pair.items():
        pair_fills.sort(key=_fill_order)
        _match_pair(pair, trade_type, pair_fills, method, result)

    for row in funding:
        result.funding.append(Funding(
            pair=row["pair"],
            currency=currency_unit(row["asset"]),
            amount=float(row["amount"]),
            time=row["timestamp"],
            exchange=row["exchange"],
        ))

    result.closed.sort(key=lambda t: t.close_time)
    result.funding.sort(key=lambda f: f.time)
    return result


def _match_pair(pair, trade_type, fills, method, result):
    currency = currency_unit(split_pair(pair)[1])
    futures = trade_type == "futures"
    lots: deque[Lot] = deque()

    for f in fills:
        qty = float(f["quantity"])
        if qty <= DUST:
            print(f"[pnl] Skipped a fill of {pair} with no quantity ({f.get('id')})")
            continue
        price = float(f["price"])
        fee_value, base_fee = fee_in_quote(f)
        fee_unknown = fee_value is None
        fee_value = fee_value or 0.0
        opens = "long" if f["side"] == "buy" else "short"
        closes = "short" if opens == "long" else "long"

        remaining = qty
        if lots and lots[0].direction == closes:
            cost = 0.0  # entry value of the matched lots
            lot_fees = 0.0
            lot_fee_unknown = False
            open_time = None
            while remaining > DUST and lots:
                lot = lots[0]
                take = min(remaining, lot.quantity)
                open_time = open_time or lot.time
                cost += take * lot.price
                lot_fees += take * lot.fee_per_unit
                lot_fee_unknown = lot_fee_unknown or lot.fee_unknown
                lot.quantity -= take
                remaining -= take
                if lot.quantity <= DUST:
                    lots.popleft()

            matched = qty - remaining
            exit_value = matched * price
            close_fee = fee_value * matched / qty
            gross = exit_value - cost if closes == "long" else cost - exit_value
            result.closed.append(ClosedTrade(
                pair=pair,
                trade_type=trade_type,
                currency=currency,
                quantity=matched,
                pnl=gross - lot_fees - close_fee,
                fees=lot_fees + close_fee,
                open_time=open_time,
                close_time=f["timestamp"],
                exchange=f["exchange"],
                strategy=f.get("strategy"),
                emotion_tag=f.get("emotion_tag"),
                direction=closes,
                fee_unknown=fee_unknown or lot_fee_unknown,
            ))
            fee_value -= close_fee

        if remaining <= DUST:
            continue

        if opens == "short" and not futures:
            # Spot sale with no buy on record: the cost is unknown.
            result.unmatched.append(Unmatched(pair, trade_type, remaining, f["timestamp"],
                                              f["exchange"], f.get("strategy")))
            continue

        lot_qty = remaining
        if opens == "long" and not futures:
            lot_qty -= base_fee  # the base-coin fee never arrived
        if lot_qty <= DUST:
            continue
        lot = Lot(pair, lot_qty, price, fee_value / lot_qty, f["timestamp"], opens, fee_unknown)
        if method == "avg_cost" and lots:
            _merge_into(lots[0], lot)
        else:
            lots.append(lot)

    result.open_lots.extend(lots)


def _merge_into(avg: Lot, lot: Lot):
    total = avg.quantity + lot.quantity
    avg.price = (avg.price * avg.quantity + lot.price * lot.quantity) / total
    avg.fee_per_unit = (avg.fee_per_unit * avg.quantity + lot.fee_per_unit * lot.quantity) / total
    avg.quantity = total
    avg.fee_unknown = avg.fee_unknown or lot.fee_unknown


def summarise(closed: list[ClosedTrade], funding: list[Funding] = ()) -> dict:
    """Totals and win/loss stats for closed trades (and funding) in ONE currency."""
    pnls = [t.pnl for t in closed]
    wins = [p for p in pnls if p >= 0]
    losses = [p for p in pnls if p < 0]
    win_amount = sum(wins)
    loss_amount = sum(losses)
    funding_total = sum(f.amount for f in funding)
    return {
        "trading_pnl": sum(pnls),
        "funding_pnl": funding_total,
        "total_pnl": sum(pnls) + funding_total,
        "total_fees": sum(t.fees for t in closed),
        "trade_count": len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "win_amount": win_amount,
        "loss_amount": loss_amount,
        "fee_unknown_trades": sum(1 for t in closed if t.fee_unknown),
        "win_rate": round(len(wins) / max(len(closed), 1) * 100, 1),
        "avg_win": win_amount / max(len(wins), 1),
        "avg_loss": loss_amount / max(len(losses), 1),
        # No losing trade: nothing to divide by. None is shown as "no losses", never 0.
        "profit_factor": round(win_amount / abs(loss_amount), 2) if loss_amount != 0 else None,
    }
