"""Tests for trade insertion and deduplication logic."""

import uuid
from app.exchanges.sync import _insert_trades


def _fake_trade(ext_id="t1", order_id="o1", symbol="BTC/USDT", side="buy",
                amount=1.0, price=100.0, cost=100.0, timestamp=1700000000000):
    return {
        "id": ext_id,
        "order": order_id,
        "symbol": symbol,
        "side": side,
        "amount": amount,
        "price": price,
        "cost": cost,
        "timestamp": timestamp,
        "fee": {"cost": 0.1, "currency": "USDT"},
    }


def _setup_exchange(db, exchange_id="test_ex"):
    db.execute(
        """INSERT INTO exchanges (id, exchange, api_key_enc, api_secret_enc, is_read_only)
           VALUES (?, 'binance', 'enc_key', 'enc_secret', 1)""",
        [exchange_id],
    )
    db.commit()
    return exchange_id


class TestInsertTrades:
    def test_insert_new_trades(self, test_db):
        ex_id = _setup_exchange(test_db)
        trades = [
            _fake_trade(ext_id="t1", timestamp=1700000000000),
            _fake_trade(ext_id="t2", timestamp=1700001000000),
            _fake_trade(ext_id="t3", timestamp=1700002000000),
        ]
        inserted = _insert_trades(test_db, ex_id, "binance", trades, "spot")
        assert inserted == 3

        count = test_db.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        assert count == 3

    def test_skip_duplicates(self, test_db):
        ex_id = _setup_exchange(test_db)
        trades = [_fake_trade(ext_id="t1")]
        _insert_trades(test_db, ex_id, "binance", trades, "spot")

        inserted = _insert_trades(test_db, ex_id, "binance", trades, "spot")
        assert inserted == 0

        count = test_db.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        assert count == 1

    def test_mixed_new_and_duplicate(self, test_db):
        ex_id = _setup_exchange(test_db)
        _insert_trades(test_db, ex_id, "binance", [_fake_trade(ext_id="t1")], "spot")

        mixed = [
            _fake_trade(ext_id="t1"),  # duplicate
            _fake_trade(ext_id="t2"),  # new
            _fake_trade(ext_id="t3"),  # new
        ]
        inserted = _insert_trades(test_db, ex_id, "binance", mixed, "spot")
        assert inserted == 2

        count = test_db.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        assert count == 3

    def test_order_id_preserved(self, test_db):
        ex_id = _setup_exchange(test_db)
        trades = [
            _fake_trade(ext_id="f1", order_id="order_abc"),
            _fake_trade(ext_id="f2", order_id="order_abc"),
        ]
        _insert_trades(test_db, ex_id, "binance", trades, "spot")

        rows = test_db.execute(
            "SELECT order_id FROM trades WHERE order_id = 'order_abc'"
        ).fetchall()
        assert len(rows) == 2

    def test_futures_trade_type(self, test_db):
        ex_id = _setup_exchange(test_db)
        trades = [_fake_trade(ext_id="ft1", symbol="BTC/USDT:USDT")]
        _insert_trades(test_db, ex_id, "binance", trades, "futures")

        row = test_db.execute("SELECT trade_type, pair FROM trades").fetchone()
        assert row["trade_type"] == "futures"
        assert row["pair"] == "BTC/USDT"  # colon portion stripped

    def test_empty_trades_list(self, test_db):
        ex_id = _setup_exchange(test_db)
        inserted = _insert_trades(test_db, ex_id, "binance", [], "spot")
        assert inserted == 0

    def test_trade_fields_stored_correctly(self, test_db):
        ex_id = _setup_exchange(test_db)
        trades = [_fake_trade(
            ext_id="t1", order_id="o1", symbol="ETH/USDT",
            side="sell", amount=2.5, price=3000.0, cost=7500.0,
        )]
        _insert_trades(test_db, ex_id, "binance", trades, "spot")

        row = test_db.execute("SELECT * FROM trades").fetchone()
        assert row["pair"] == "ETH/USDT"
        assert row["base_currency"] == "ETH"
        assert row["quote_currency"] == "USDT"
        assert row["side"] == "sell"
        assert row["quantity"] == 2.5
        assert row["price"] == 3000.0
        assert row["total"] == 7500.0
        assert row["fee"] == 0.1
        assert row["fee_currency"] == "USDT"
