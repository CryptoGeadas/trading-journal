"""Engine-level tests for FIFO matching (app.services.pnl_engine)."""

from datetime import datetime, timedelta
from itertools import count

from app.services import pnl_engine

_clock = count()
_START = datetime(2026, 1, 1)


def _trade(side, qty, price, fee=0.0):
    return {"side": side, "quantity": qty, "price": price, "fee": fee, "fee_currency": "USDT", "pair": "BTC/USDT",
            "trade_type": "spot", "exchange": "x", "timestamp": (_START + timedelta(minutes=next(_clock))).strftime("%Y-%m-%dT%H:%M:%SZ")}


def _fifo_match(pair, trades):
    """Old result shape, built from the engine, so these tests read as before."""
    result = pnl_engine.match(trades)
    s = pnl_engine.summarise(result.closed)
    return {
        "realised_pnl": s["total_pnl"],
        "total_fees": s["total_fees"],
        "closed_trades": s["trade_count"],
        "wins": s["wins"],
        "losses": s["losses"],
        "total_win_amount": s["win_amount"],
        "total_loss_amount": s["loss_amount"],
        "open_qty": sum(lot.quantity for lot in result.open_lots),
    }


class TestFifoMatch:
    def test_single_buy_sell_profit(self):
        trades = [_trade("buy", 1.0, 100.0), _trade("sell", 1.0, 150.0)]
        result = _fifo_match("BTC/USDT", trades)
        assert result["realised_pnl"] == 50.0
        assert result["closed_trades"] == 1
        assert result["wins"] == 1
        assert result["losses"] == 0
        assert result["open_qty"] < 0.001

    def test_single_buy_sell_loss(self):
        trades = [_trade("buy", 1.0, 100.0), _trade("sell", 1.0, 80.0)]
        result = _fifo_match("BTC/USDT", trades)
        assert result["realised_pnl"] == -20.0
        assert result["closed_trades"] == 1
        assert result["wins"] == 0
        assert result["losses"] == 1

    def test_partial_sell(self):
        trades = [_trade("buy", 10.0, 100.0), _trade("sell", 5.0, 120.0)]
        result = _fifo_match("BTC/USDT", trades)
        assert result["realised_pnl"] == 100.0  # 5 * (120 - 100)
        assert result["closed_trades"] == 1
        assert abs(result["open_qty"] - 5.0) < 0.001

    def test_fifo_order_multiple_lots(self):
        """Buy at 100, buy at 200, sell at 150 — FIFO matches against 100 first."""
        trades = [
            _trade("buy", 1.0, 100.0),
            _trade("buy", 1.0, 200.0),
            _trade("sell", 1.0, 150.0),
        ]
        result = _fifo_match("BTC/USDT", trades)
        assert result["realised_pnl"] == 50.0  # 150 - 100
        assert result["closed_trades"] == 1
        assert abs(result["open_qty"] - 1.0) < 0.001

    def test_sell_across_multiple_lots(self):
        """Sell quantity spans two buy lots."""
        trades = [
            _trade("buy", 3.0, 100.0),
            _trade("buy", 2.0, 200.0),
            _trade("sell", 4.0, 150.0),
        ]
        result = _fifo_match("BTC/USDT", trades)
        # First 3 units cost 300, next 1 unit costs 200 = total cost 500
        # Proceeds = 4 * 150 = 600. PnL = 100
        assert result["realised_pnl"] == 100.0
        assert result["closed_trades"] == 1
        assert abs(result["open_qty"] - 1.0) < 0.001

    def test_no_sells(self):
        trades = [_trade("buy", 1.0, 100.0), _trade("buy", 2.0, 200.0)]
        result = _fifo_match("BTC/USDT", trades)
        assert result["realised_pnl"] == 0.0
        assert result["closed_trades"] == 0
        assert abs(result["open_qty"] - 3.0) < 0.001

    def test_all_sold(self):
        trades = [
            _trade("buy", 1.0, 100.0),
            _trade("buy", 1.0, 200.0),
            _trade("sell", 1.0, 180.0),
            _trade("sell", 1.0, 180.0),
        ]
        result = _fifo_match("BTC/USDT", trades)
        # First sell: 180 - 100 = 80
        # Second sell: 180 - 200 = -20
        assert result["realised_pnl"] == 60.0
        assert result["closed_trades"] == 2
        assert result["wins"] == 1
        assert result["losses"] == 1
        assert result["open_qty"] < 0.001

    def test_fees_reduce_pnl(self):
        trades = [
            _trade("buy", 1.0, 100.0, fee=2.0),
            _trade("sell", 1.0, 150.0, fee=3.0),
        ]
        result = _fifo_match("BTC/USDT", trades)
        # PnL = (150 - 100) - 2 (buy fee) - 3 (sell fee) = 45
        assert result["realised_pnl"] == 45.0
        assert result["total_fees"] == 5.0

    def test_empty_trades(self):
        result = _fifo_match("BTC/USDT", [])
        assert result["realised_pnl"] == 0.0
        assert result["closed_trades"] == 0
        assert result["open_qty"] == 0.0

    def test_sell_without_buys(self):
        """Sell with no preceding buy — no cost basis, not counted as closed."""
        trades = [_trade("sell", 1.0, 100.0)]
        result = _fifo_match("BTC/USDT", trades)
        assert result["closed_trades"] == 0
        assert result["realised_pnl"] == 0.0

    def test_win_loss_amounts(self):
        trades = [
            _trade("buy", 1.0, 100.0),
            _trade("buy", 1.0, 100.0),
            _trade("sell", 1.0, 150.0),  # win: +50
            _trade("sell", 1.0, 80.0),   # loss: -20
        ]
        result = _fifo_match("BTC/USDT", trades)
        assert result["total_win_amount"] == 50.0
        assert result["total_loss_amount"] == -20.0


def test_a_fill_with_no_quantity_is_skipped_not_a_closed_trade():
    trades = [_trade("buy", 1.0, 100.0), _trade("sell", 0.0, 150.0), _trade("sell", 5e-9, 100.0),
              _trade("sell", 1.0, 100.0)]
    result = _fifo_match("BTC/USDT", trades)
    assert (result["closed_trades"], result["realised_pnl"]) == (1, 0.0)


def test_fee_with_no_coin_named_is_unknown():
    trades = [_trade("buy", 1.0, 100.0, fee=1.0), _trade("sell", 1.0, 150.0)]
    trades[0]["fee_currency"] = ""
    result = pnl_engine.match(trades)
    assert result.closed[0].fee_unknown and result.closed[0].pnl == 50.0
