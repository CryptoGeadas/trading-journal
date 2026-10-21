"""Money maths through the public API: post trades, read realised PnL.

Expected numbers are worked out by hand in each test. A case the app gets
wrong today is marked xfail with the bug issue that tracks it.
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def api():
    with TestClient(app) as client:
        yield client


def add(api, side, qty, price, ts, pair="BTC/USDT", fee=0.0, fee_currency="USDT", trade_type="spot",
        exchange="manual", strategy=None):
    resp = api.post("/api/trades", json={
        "pair": pair, "side": side, "quantity": qty, "price": price, "timestamp": ts,
        "fee": fee, "fee_currency": fee_currency, "trade_type": trade_type, "exchange": exchange,
        "strategy": strategy,
    })
    if resp.status_code != 201:  # not an assert: a broken setup must not pass as an expected failure
        raise RuntimeError(f"adding trade failed: {resp.status_code} {resp.text}")


def pnl(api, **params):
    resp = api.get("/api/pnl", params=params)
    if resp.status_code != 200:
        raise RuntimeError(f"/api/pnl failed: {resp.status_code} {resp.text}")
    return resp.json()


# ---- FIFO ----

def test_fifo_single_round_trip_with_quote_fees(api):
    add(api, "buy", 1, 100, "2026-01-01T00:00:00Z", fee=1)
    add(api, "sell", 1, 150, "2026-01-02T00:00:00Z", fee=1.5)
    r = pnl(api)
    assert r["total_pnl"] == 47.5  # 150 - 100 - 1 - 1.5
    assert r["total_fees"] == 2.5
    assert (r["trade_count"], r["wins"], r["losses"]) == (1, 1, 0)


def test_fifo_matches_oldest_lot_first(api):
    add(api, "buy", 1, 100, "2026-01-01T00:00:00Z")
    add(api, "buy", 1, 200, "2026-01-02T00:00:00Z")
    add(api, "sell", 1, 150, "2026-01-03T00:00:00Z")
    assert pnl(api)["total_pnl"] == 50  # sold the 100 lot


def test_fifo_uses_trade_time_not_insert_order(api):
    add(api, "buy", 1, 200, "2026-01-02T00:00:00Z")  # inserted first, but newer
    add(api, "buy", 1, 100, "2026-01-01T00:00:00Z")
    add(api, "sell", 1, 150, "2026-01-03T00:00:00Z")
    assert pnl(api)["total_pnl"] == 50


def test_fifo_sell_spanning_lots_with_partial_fills(api):
    add(api, "buy", 0.3, 100, "2026-01-01T00:00:00Z", fee=0.3)   # fee 1/unit
    add(api, "buy", 0.2, 200, "2026-01-02T00:00:00Z", fee=0.4)   # fee 2/unit
    add(api, "sell", 0.4, 150, "2026-01-03T00:00:00Z", fee=0.6)
    # proceeds 60; cost 0.3*100 + 0.1*200 = 50; buy fees 0.3 + 0.1*2 = 0.5; sell fee 0.6
    assert pnl(api)["total_pnl"] == pytest.approx(8.9, abs=0.005)


def test_partial_close_leaves_open_lot_out_of_pnl(api):
    add(api, "buy", 10, 100, "2026-01-01T00:00:00Z")
    add(api, "sell", 4, 90, "2026-01-02T00:00:00Z")
    r = pnl(api)
    assert r["total_pnl"] == -40
    assert (r["wins"], r["losses"]) == (0, 1)


def test_pairs_are_matched_separately(api):
    add(api, "buy", 1, 100, "2026-01-01T00:00:00Z", pair="BTC/USDT")
    add(api, "sell", 1, 50, "2026-01-02T00:00:00Z", pair="ETH/USDT")  # no ETH bought: no PnL
    add(api, "sell", 1, 130, "2026-01-03T00:00:00Z", pair="BTC/USDT")
    r = pnl(api, group_by="pair")
    assert r["total_pnl"] == 30
    assert {g["group"]: g["total_pnl"] for g in r["breakdown"]} == {"BTC/USDT": 30, "ETH/USDT": 0}


def test_tiny_amounts_do_not_drift(api):
    for i in range(10):
        add(api, "buy", 0.1, 0.1, f"2026-01-01T00:00:{i:02d}Z")
    add(api, "sell", 1, 0.3, "2026-01-02T00:00:00Z")
    assert pnl(api)["total_pnl"] == pytest.approx(0.2, abs=0.005)


def test_month_breakdown_adds_up_to_total(api):
    add(api, "buy", 2, 100, "2026-01-10T00:00:00Z")
    add(api, "sell", 1, 120, "2026-01-20T00:00:00Z")
    add(api, "sell", 1, 90, "2026-02-05T00:00:00Z")
    r = pnl(api, group_by="month")
    months = {g["group"]: g["total_pnl"] for g in r["breakdown"]}
    assert months == {"2026-01": 20, "2026-02": -10}
    assert sum(months.values()) == r["total_pnl"]


def test_profit_factor_and_averages(api):
    add(api, "buy", 3, 100, "2026-01-01T00:00:00Z")
    add(api, "sell", 1, 130, "2026-01-02T00:00:00Z")  # +30
    add(api, "sell", 1, 110, "2026-01-03T00:00:00Z")  # +10
    add(api, "sell", 1, 80, "2026-01-04T00:00:00Z")   # -20
    r = pnl(api)
    assert (r["avg_win"], r["avg_loss"], r["profit_factor"]) == (20, -20, 2)
    assert r["win_rate"] == pytest.approx(66.7)


def test_week_breakdown_uses_iso_weeks(api):
    add(api, "buy", 2, 100, "2026-01-01T00:00:00Z")
    add(api, "sell", 1, 110, "2026-01-04T23:00:00Z")  # Sunday: ISO week 1
    add(api, "sell", 1, 120, "2026-01-05T01:00:00Z")  # Monday: ISO week 2
    weeks = {g["group"]: g["total_pnl"] for g in pnl(api, group_by="week")["breakdown"]}
    assert weeks == {"2026-W01": 10, "2026-W02": 20}


@pytest.mark.parametrize("qty, price", [(0, 100), (-1, 100), (1, 0)])
def test_zero_or_negative_amounts_are_refused(api, qty, price):
    resp = api.post("/api/trades", json={"pair": "BTC/USDT", "side": "buy", "quantity": qty, "price": price})
    assert resp.status_code == 400
    assert pnl(api)["trade_count"] == 0


def test_filters_limit_the_trades_used(api):
    add(api, "buy", 1, 100, "2026-01-01T00:00:00Z", exchange="a")
    add(api, "sell", 1, 150, "2026-01-02T00:00:00Z", exchange="a")
    add(api, "buy", 1, 100, "2026-01-01T00:00:00Z", pair="ETH/USDT", exchange="b")
    add(api, "sell", 1, 90, "2026-01-02T00:00:00Z", pair="ETH/USDT", exchange="b")
    assert pnl(api, exchange="a")["total_pnl"] == 50
    assert pnl(api, pair="ETH/USDT")["total_pnl"] == -10
    assert pnl(api)["total_pnl"] == 40


# ---- Known wrong today (each has its own bug issue) ----

@pytest.mark.xfail(strict=True, raises=AssertionError, reason="wrong today, see #18")
def test_selling_more_than_bought_only_counts_the_matched_part(api):
    add(api, "buy", 1, 100, "2026-01-01T00:00:00Z")
    add(api, "sell", 2, 150, "2026-01-02T00:00:00Z")  # 1 unit has no known cost
    assert pnl(api)["total_pnl"] == 50


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="wrong today, see #19")
def test_buy_fee_paid_in_base_currency(api):
    # Binance-style: buying 1 BTC at 100 with a 0.001 BTC fee leaves 0.999 BTC.
    add(api, "buy", 1, 100, "2026-01-01T00:00:00Z", fee=0.001, fee_currency="BTC")
    add(api, "sell", 0.999, 150, "2026-01-02T00:00:00Z")
    # proceeds 149.85 - cost 100 (the fee was paid in BTC, worth 0.1 USDT) = 49.85
    assert pnl(api)["total_pnl"] == pytest.approx(49.85, abs=0.005)
    assert pnl(api)["total_fees"] == pytest.approx(0.1, abs=0.005)


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="wrong today, see #20")
def test_futures_short_round_trip(api):
    pair = "BTC/USDT:USDT"
    add(api, "sell", 1, 150, "2026-01-01T00:00:00Z", pair=pair, trade_type="futures", fee=0.5)
    add(api, "buy", 1, 100, "2026-01-02T00:00:00Z", pair=pair, trade_type="futures", fee=0.5)
    assert pnl(api)["total_pnl"] == 49  # 150 - 100 - 1


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="wrong today, see #21")
def test_average_cost_method(api):
    add(api, "buy", 1, 100, "2026-01-01T00:00:00Z")
    add(api, "buy", 1, 200, "2026-01-02T00:00:00Z")
    add(api, "sell", 1, 180, "2026-01-03T00:00:00Z")
    # average cost 150 -> +30 (FIFO would say +80)
    assert pnl(api, method="avg_cost")["total_pnl"] == 30


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="wrong today, see #22")
def test_profit_factor_with_no_losses_is_not_zero(api):
    add(api, "buy", 1, 100, "2026-01-01T00:00:00Z")
    add(api, "sell", 1, 150, "2026-01-02T00:00:00Z")
    assert pnl(api)["profit_factor"] is None  # all wins: no loss to divide by, shown as "no losses"


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="wrong today, see #23")
def test_funding_fees_reduce_futures_pnl(api):
    from app.database import get_db

    pair = "BTC/USDT:USDT"
    add(api, "buy", 1, 100, "2026-01-01T00:00:00Z", pair=pair, trade_type="futures")
    add(api, "sell", 1, 110, "2026-01-03T00:00:00Z", pair=pair, trade_type="futures")
    db = get_db()
    db.execute(
        """INSERT INTO funding_fees (id, exchange_id, exchange, timestamp, pair, amount, asset)
           VALUES ('f1', 'manual_manual', 'manual', '2026-01-02T00:00:00Z', 'BTC/USDT:USDT', -2.0, 'USDT')"""
    )
    db.commit()
    db.close()
    assert pnl(api)["total_pnl"] == 8  # 10 trading - 2 funding


def test_equity_curve_ends_at_the_total_pnl(api):
    add(api, "buy", 2, 100, "2026-01-01T00:00:00Z", fee=1)
    add(api, "sell", 1, 130, "2026-01-05T00:00:00Z", fee=1)
    add(api, "sell", 1, 90, "2026-02-01T00:00:00Z")
    curve = api.get("/api/equity-curve").json()
    assert curve[-1]["cumulative_pnl"] == pnl(api)["total_pnl"]


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="wrong today, see #24")
@pytest.mark.parametrize("group_by", ["exchange", "strategy"])
def test_breakdown_adds_up_to_the_total_when_buy_and_sell_are_in_different_groups(api, group_by):
    # bought on exchange a / with strategy "Swing", sold on exchange b / with "Scalp"
    add(api, "buy", 1, 100, "2026-01-01T00:00:00Z", exchange="a", strategy="Swing")
    add(api, "sell", 1, 150, "2026-01-02T00:00:00Z", exchange="b", strategy="Scalp")
    r = pnl(api, group_by=group_by)
    assert r["total_pnl"] == 50
    assert sum(g["total_pnl"] for g in r["breakdown"]) == 50


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="wrong today, see #25")
def test_date_filter_keeps_the_cost_of_earlier_buys(api):
    add(api, "buy", 1, 100, "2026-01-01T00:00:00Z")
    add(api, "sell", 1, 150, "2026-01-31T00:00:00Z")
    # the sale is inside the range, so its PnL counts, using the earlier buy as cost
    assert pnl(api, date_from="2026-01-02")["total_pnl"] == 50


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="wrong today, see #26")
def test_date_to_includes_the_whole_last_day(api):
    add(api, "buy", 1, 100, "2026-01-01T00:00:00Z")
    add(api, "sell", 1, 150, "2026-01-31T12:00:00Z")
    assert pnl(api, date_to="2026-01-31")["total_pnl"] == 50  # the page sends a date, not a time


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="wrong today, see #27")
def test_time_zones_are_compared_as_real_moments(api):
    add(api, "buy", 1, 100, "2026-01-01T00:00:00Z")
    # 01:00 at UTC+2 is 23:00 UTC the day BEFORE the buy: nothing to sell yet
    add(api, "sell", 1, 150, "2026-01-01T01:00:00+02:00")
    assert pnl(api)["total_pnl"] == 0
