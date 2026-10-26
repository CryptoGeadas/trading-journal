"""Money maths through the public API: post trades, read realised PnL.

Expected numbers are worked out by hand in each test. A case the app gets
wrong today is marked xfail with the bug issue that tracks it.

Funding rows are added straight into the database with `fund()`.
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


def fund(amount, ts, asset="USDT", pair="BTC/USDT:USDT", exchange="manual", fid="f1"):
    from app.database import get_db
    db = get_db()
    db.execute("INSERT INTO funding_fees (id, exchange_id, exchange, timestamp, pair, amount, asset) "
               "VALUES (?, ?, ?, ?, ?, ?, ?)", (fid, "manual_manual", exchange, ts, pair, amount, asset))
    db.commit()
    db.close()


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


# ---- Fixed bugs (each test names the case from its issue) ----

def test_selling_more_than_bought_only_counts_the_matched_part(api):
    add(api, "buy", 1, 100, "2026-01-01T00:00:00Z")
    add(api, "sell", 2, 150, "2026-01-02T00:00:00Z")  # 1 unit has no known cost
    assert pnl(api)["total_pnl"] == 50


def test_buy_fee_paid_in_base_currency(api):
    # Binance-style: buying 1 BTC at 100 with a 0.001 BTC fee leaves 0.999 BTC.
    add(api, "buy", 1, 100, "2026-01-01T00:00:00Z", fee=0.001, fee_currency="BTC")
    add(api, "sell", 0.999, 150, "2026-01-02T00:00:00Z")
    # proceeds 149.85 - cost 100 (the fee was paid in BTC, worth 0.1 USDT) = 49.85
    assert pnl(api)["total_pnl"] == pytest.approx(49.85, abs=0.005)
    assert pnl(api)["total_fees"] == pytest.approx(0.1, abs=0.005)


def test_futures_short_round_trip(api):
    pair = "BTC/USDT:USDT"
    add(api, "sell", 1, 150, "2026-01-01T00:00:00Z", pair=pair, trade_type="futures", fee=0.5)
    add(api, "buy", 1, 100, "2026-01-02T00:00:00Z", pair=pair, trade_type="futures", fee=0.5)
    assert pnl(api)["total_pnl"] == 49  # 150 - 100 - 1


def test_average_cost_method(api):
    add(api, "buy", 1, 100, "2026-01-01T00:00:00Z")
    add(api, "buy", 1, 200, "2026-01-02T00:00:00Z")
    add(api, "sell", 1, 180, "2026-01-03T00:00:00Z")
    # average cost 150 -> +30 (FIFO would say +80)
    assert pnl(api, method="avg_cost")["total_pnl"] == 30


def test_profit_factor_with_no_losses_is_not_zero(api):
    add(api, "buy", 1, 100, "2026-01-01T00:00:00Z")
    add(api, "sell", 1, 150, "2026-01-02T00:00:00Z")
    assert pnl(api)["profit_factor"] is None  # all wins: no loss to divide by, shown as "no losses"


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


@pytest.mark.parametrize("group_by", ["exchange", "strategy"])
def test_breakdown_adds_up_to_the_total_when_buy_and_sell_are_in_different_groups(api, group_by):
    # bought on exchange a / with strategy "Swing", sold on exchange b / with "Scalp"
    add(api, "buy", 1, 100, "2026-01-01T00:00:00Z", exchange="a", strategy="Swing")
    add(api, "sell", 1, 150, "2026-01-02T00:00:00Z", exchange="b", strategy="Scalp")
    r = pnl(api, group_by=group_by)
    assert r["total_pnl"] == 50
    assert sum(g["total_pnl"] for g in r["breakdown"]) == 50


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


@pytest.mark.parametrize("group_by", ["pair", "exchange", "strategy", "emotion_tag", "month", "week"])
def test_every_breakdown_adds_up_to_the_total(api, group_by):
    add(api, "buy", 2, 100, "2026-01-01T00:00:00Z", exchange="a", strategy="Swing")
    add(api, "sell", 1, 130, "2026-01-20T00:00:00Z", exchange="b")
    add(api, "buy", 1, 50, "2026-02-01T00:00:00Z", pair="ETH/USDT", exchange="a", fee=1)
    add(api, "sell", 1, 40, "2026-02-09T00:00:00Z", pair="ETH/USDT", exchange="a", strategy="Scalp", fee=1)
    add(api, "sell", 1, 90, "2026-03-01T00:00:00Z", exchange="a", strategy="Swing")
    r = pnl(api, group_by=group_by)
    assert r["total_pnl"] == 8  # +30, -12, -10
    assert round(sum(g["total_pnl"] for g in r["breakdown"]), 2) == r["total_pnl"]
    assert sum(g["trade_count"] for g in r["breakdown"]) == r["trade_count"]


def test_patterns_use_the_same_closed_trades_as_pnl(api):
    add(api, "buy", 3, 100, "2026-01-01T09:00:00Z")
    add(api, "sell", 1, 130, "2026-01-02T10:00:00Z")
    add(api, "sell", 1, 110, "2026-01-03T10:00:00Z")
    add(api, "sell", 1, 80, "2026-01-04T15:00:00Z")
    patterns = {p["type"]: p["data"] for p in api.get("/api/patterns").json()["patterns"]}
    assert patterns["streaks"]["total_closed_trades"] == pnl(api)["trade_count"] == 3
    assert sum(h["pnl"] for h in patterns["time_of_day"]["by_hour"]) == pnl(api)["total_pnl"]
    assert patterns["hold_time"]["total_round_trips"] == 3


def test_sell_with_no_buy_on_record_is_reported_not_counted(api):
    add(api, "sell", 1, 150, "2026-01-02T00:00:00Z")
    r = pnl(api)
    assert (r["total_pnl"], r["trade_count"], r["unmatched_sells"]) == (0, 0, 1)


def test_fee_in_a_third_coin_is_unknown_not_zero(api):
    add(api, "buy", 1, 100, "2026-01-01T00:00:00Z", fee=0.05, fee_currency="BNB")
    add(api, "sell", 1, 150, "2026-01-02T00:00:00Z")
    r = pnl(api)
    assert r["total_pnl"] == 50  # the BNB fee is left out, not counted as 0.05 USDT
    assert r["fee_unknown_trades"] == 1


def test_futures_long_still_works_and_shorts_flip(api):
    pair = "ETH/USDT:USDT"
    add(api, "buy", 1, 100, "2026-01-01T00:00:00Z", pair=pair, trade_type="futures")
    add(api, "sell", 2, 120, "2026-01-02T00:00:00Z", pair=pair, trade_type="futures")  # close long, open short
    add(api, "buy", 1, 90, "2026-01-03T00:00:00Z", pair=pair, trade_type="futures")    # close short
    r = pnl(api)
    assert r["total_pnl"] == 50  # +20 long, +30 short
    assert r["trade_count"] == 2


def test_average_cost_is_the_default_when_set(api, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "DEFAULT_PNL_METHOD", "avg_cost")
    add(api, "buy", 1, 100, "2026-01-01T00:00:00Z")
    add(api, "buy", 1, 200, "2026-01-02T00:00:00Z")
    add(api, "sell", 1, 180, "2026-01-03T00:00:00Z")
    assert pnl(api)["total_pnl"] == 30
    assert pnl(api, method="fifo")["total_pnl"] == 80


def test_funding_is_its_own_line_and_in_breakdowns(api):
    from app.database import get_db

    pair = "BTC/USDT:USDT"
    add(api, "buy", 1, 100, "2026-01-01T00:00:00Z", pair=pair, trade_type="futures")
    add(api, "sell", 1, 110, "2026-01-03T00:00:00Z", pair=pair, trade_type="futures")
    db = get_db()
    db.execute(
        """INSERT INTO funding_fees (id, exchange_id, exchange, timestamp, pair, amount, asset)
           VALUES ('f1', 'manual_manual', 'manual', '2026-02-02T00:00:00Z', 'BTC/USDT:USDT', -2.0, 'USDT')"""
    )
    db.commit()
    db.close()
    r = pnl(api, group_by="month")
    assert (r["trading_pnl"], r["funding_pnl"], r["total_pnl"]) == (10, -2, 8)
    assert {g["group"]: g["total_pnl"] for g in r["breakdown"]} == {"2026-01": 10, "2026-02": -2}
    assert pnl(api, trade_type="spot")["total_pnl"] == 0
    assert api.get("/api/equity-curve").json()[-1]["cumulative_pnl"] == 8


# ---- Currencies ----

def test_usdt_and_btc_amounts_are_never_added_together(api):
    add(api, "buy", 1, 100, "2026-01-01T00:00:00Z", pair="SOL/USDT")
    add(api, "sell", 1, 110, "2026-01-02T00:00:00Z", pair="SOL/USDT")             # +10 USDT
    add(api, "buy", 1, 0.05, "2026-01-01T00:00:00Z", pair="ETH/BTC", fee_currency="BTC")
    add(api, "sell", 1, 0.06, "2026-01-02T00:00:00Z", pair="ETH/BTC", fee_currency="BTC")  # +0.01 BTC
    add(api, "buy", 1, 100, "2026-01-01T00:00:00Z", pair="ADA/USDC")
    add(api, "sell", 1, 105, "2026-01-02T00:00:00Z", pair="ADA/USDC")             # +5 USDC
    r = pnl(api, group_by="exchange")
    assert (r["currency"], r["total_pnl"]) == ("USD", 15)  # stablecoins grouped as USD
    assert {c["currency"]: c["total_pnl"] for c in r["by_currency"]} == {"USD": 15, "BTC": 0.01}
    assert {(g["group"], g["currency"]): g["total_pnl"] for g in r["breakdown"]} == {
        ("manual", "USD"): 15, ("manual", "BTC"): 0.01}
    btc = pnl(api, currency="BTC")
    assert (btc["currency"], btc["total_pnl"]) == ("BTC", 0.01)


def test_small_coin_amounts_are_not_rounded_away(api):
    add(api, "buy", 1, 0.05, "2026-01-01T00:00:00Z", pair="ETH/BTC", fee_currency="BTC")
    add(api, "sell", 1, 0.0512, "2026-01-02T00:00:00Z", pair="ETH/BTC", fee_currency="BTC")
    r = pnl(api)
    assert (r["currency"], r["total_pnl"]) == ("BTC", 0.0012)


def test_overview_shows_one_currency_and_lists_the_others(api):
    add(api, "buy", 1, 100, "2026-01-01T00:00:00Z", pair="SOL/USDT", fee=1)
    add(api, "sell", 1, 110, "2026-01-02T00:00:00Z", pair="SOL/USDT", fee=1)
    add(api, "buy", 1, 0.05, "2026-01-01T00:00:00Z", pair="ETH/BTC", fee=0.001, fee_currency="ETH")
    add(api, "sell", 0.999, 0.06, "2026-01-02T00:00:00Z", pair="ETH/BTC", fee_currency="BTC")
    o = api.get("/api/overview").json()
    assert (o["currency"], o["total_pnl"], o["total_fees"]) == ("USD", 8, 2)  # BTC fees not added in
    assert [(c["currency"], c["total_pnl"]) for c in o["other_currencies"]] == [("BTC", 0.00994)]


def test_asked_for_currency_is_kept_even_with_no_trades_in_it(api):
    add(api, "buy", 1, 0.05, "2026-01-01T00:00:00Z", pair="ETH/BTC", fee_currency="BTC")
    add(api, "sell", 1, 0.06, "2026-01-02T00:00:00Z", pair="ETH/BTC", fee_currency="BTC")
    r = pnl(api, currency="USDT")
    assert (r["currency"], r["total_pnl"], r["trade_count"]) == ("USD", 0, 0)
    assert [(c["currency"], c["total_pnl"]) for c in r["by_currency"]] == [("BTC", 0.01)]


def test_headline_counts_and_averages_use_the_same_trades(api):
    add(api, "buy", 2, 100, "2026-01-01T00:00:00Z", pair="SOL/USDT")
    add(api, "sell", 1, 110, "2026-01-02T00:00:00Z", pair="SOL/USDT")  # +10 USD
    add(api, "sell", 1, 120, "2026-01-03T00:00:00Z", pair="SOL/USDT")  # +20 USD
    add(api, "buy", 1, 0.06, "2026-01-01T00:00:00Z", pair="ETH/BTC", fee_currency="BTC")
    add(api, "sell", 1, 0.05, "2026-01-02T00:00:00Z", pair="ETH/BTC", fee_currency="BTC")  # -0.01 BTC
    r = pnl(api)
    assert (r["currency"], r["trade_count"], r["wins"], r["losses"]) == ("USD", 2, 2, 0)
    assert r["profit_factor"] is None and r["avg_loss"] == 0


def test_untagged_filter_keeps_funding(api):
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
    assert pnl(api, strategy="__untagged__")["total_pnl"] == 8
    assert pnl(api, strategy="Swing")["total_pnl"] == 0


def test_patterns_follow_the_chosen_currency(api):
    add(api, "buy", 1, 100, "2026-01-01T00:00:00Z", pair="SOL/USDT")
    add(api, "sell", 1, 110, "2026-01-02T10:00:00Z", pair="SOL/USDT")
    add(api, "buy", 1, 0.05, "2026-01-01T00:00:00Z", pair="ETH/BTC", fee_currency="BTC")
    add(api, "sell", 1, 0.06, "2026-01-02T15:00:00Z", pair="ETH/BTC", fee_currency="BTC")
    resp = api.get("/api/patterns", params={"currency": "BTC"}).json()
    tod = next(p["data"] for p in resp["patterns"] if p["type"] == "time_of_day")
    assert tod["currency"] == "BTC"
    assert [(h["hour"], h["pnl"]) for h in tod["by_hour"]] == [(15, 0.01)]



@pytest.mark.parametrize("group_by", ["pair", "exchange", "strategy", "emotion_tag", "month", "week"])
def test_every_breakdown_adds_up_to_the_total_with_funding(api, group_by):
    p = "BTC/USDT:USDT"
    add(api, "buy", 1, 100, "2026-01-01T00:00:00Z", pair=p, trade_type="futures", strategy="Swing")
    add(api, "sell", 1, 110, "2026-01-03T00:00:00Z", pair=p, trade_type="futures", strategy="Swing")
    fund(-2, "2026-01-02T00:00:00Z")
    fund(-3, "2026-02-02T00:00:00Z", fid="f2")
    r = pnl(api, group_by=group_by)
    assert r["total_pnl"] == 5
    assert round(sum(g["total_pnl"] for g in r["breakdown"]), 2) == 5
    assert sum(g["funding_pnl"] for g in r["breakdown"]) == -5


def test_breakdown_rows_add_up_to_the_total_to_the_cent(api):
    for m in (1, 2, 3):
        add(api, "buy", 1, 100, f"2026-0{m}-01T00:00:00Z")
        add(api, "sell", 1, 100.005, f"2026-0{m}-02T00:00:00Z")
    r = pnl(api, group_by="month")
    assert round(sum(g["total_pnl"] for g in r["breakdown"]), 2) == r["total_pnl"] == 0.01


def test_oversell_reports_the_rest_and_charges_only_the_matched_fee(api):
    add(api, "buy", 1, 100, "2026-01-01T00:00:00Z")
    add(api, "sell", 2, 150, "2026-01-02T00:00:00Z", fee=2)
    r = pnl(api)
    assert (r["total_pnl"], r["total_fees"], r["unmatched_sells"], r["trade_count"]) == (49, 1, 1, 1)


def test_futures_flip_splits_the_fee_between_close_and_new_short(api):
    p = "ETH/USDT:USDT"
    add(api, "buy", 1, 100, "2026-01-01T00:00:00Z", pair=p, trade_type="futures")
    add(api, "sell", 2, 120, "2026-01-02T00:00:00Z", pair=p, trade_type="futures", fee=2)
    add(api, "buy", 1, 90, "2026-01-03T00:00:00Z", pair=p, trade_type="futures")
    r = pnl(api)
    assert (r["total_pnl"], r["total_fees"]) == (48, 2)  # long +20-1, short +30-1


def test_average_cost_works_for_shorts_and_flips(api):
    p = "ETH/USDT:USDT"
    add(api, "sell", 1, 200, "2026-01-01T00:00:00Z", pair=p, trade_type="futures")
    add(api, "sell", 1, 100, "2026-01-02T00:00:00Z", pair=p, trade_type="futures")
    add(api, "buy", 1, 120, "2026-01-03T00:00:00Z", pair=p, trade_type="futures")
    assert pnl(api, method="avg_cost")["total_pnl"] == 30  # average short entry 150
    assert pnl(api, method="fifo")["total_pnl"] == 80
    q = "SOL/USDT:USDT"
    add(api, "buy", 1, 100, "2026-01-01T00:00:00Z", pair=q, trade_type="futures")
    add(api, "buy", 1, 200, "2026-01-02T00:00:00Z", pair=q, trade_type="futures")
    add(api, "sell", 3, 180, "2026-01-03T00:00:00Z", pair=q, trade_type="futures")  # +60, opens short 1@180
    add(api, "buy", 1, 170, "2026-01-04T00:00:00Z", pair=q, trade_type="futures")   # +10
    assert pnl(api, method="avg_cost", pair=q)["total_pnl"] == 70


def test_average_cost_after_partial_sell_and_rebuy_with_fees(api):
    add(api, "buy", 1, 100, "2026-01-01T00:00:00Z", fee=1)
    add(api, "sell", 0.5, 120, "2026-01-02T00:00:00Z")      # +10 - 0.5 fee
    add(api, "buy", 1, 200, "2026-01-03T00:00:00Z", fee=2)  # avg (50+200)/1.5
    add(api, "sell", 1.5, 200, "2026-01-04T00:00:00Z")      # 300 - 250 - 2.5
    r = pnl(api, method="avg_cost")
    assert (r["total_pnl"], r["total_fees"]) == (57, 3)


def test_every_view_uses_the_chosen_method(api):
    add(api, "buy", 1, 100, "2026-01-01T00:00:00Z")
    add(api, "buy", 1, 200, "2026-01-02T00:00:00Z")
    add(api, "sell", 1, 180, "2026-01-03T00:00:00Z")
    q = {"method": "avg_cost"}
    assert api.get("/api/equity-curve", params=q).json()[-1]["cumulative_pnl"] == 30
    assert api.get("/api/overview", params=q).json()["total_pnl"] == 30
    tod = next(p["data"] for p in api.get("/api/patterns", params=q).json()["patterns"] if p["type"] == "time_of_day")
    assert sum(h["pnl"] for h in tod["by_hour"]) == 30


@pytest.mark.parametrize("path", ["/api/pnl", "/api/equity-curve", "/api/patterns", "/api/overview"])
def test_unknown_method_is_refused(api, path):
    assert api.get(path, params={"method": "lifo"}).status_code == 422


def test_bad_default_method_in_config_falls_back_to_fifo(api, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "DEFAULT_PNL_METHOD", "LIFO")
    assert pnl(api)["method"] == "fifo"


def test_equity_curve_is_in_one_currency(api):
    add(api, "buy", 1, 100, "2026-01-01T00:00:00Z", pair="SOL/USDT")
    add(api, "sell", 1, 110, "2026-01-02T00:00:00Z", pair="SOL/USDT")
    add(api, "buy", 1, 0.05, "2026-01-01T00:00:00Z", pair="ETH/BTC", fee_currency="BTC")
    add(api, "sell", 1, 0.06, "2026-01-02T00:00:00Z", pair="ETH/BTC", fee_currency="BTC")
    usd = api.get("/api/equity-curve").json()
    assert [(p["currency"], p["cumulative_pnl"]) for p in usd] == [("USD", 10)]
    btc = api.get("/api/equity-curve", params={"currency": "BTC"}).json()
    assert [(p["currency"], p["cumulative_pnl"]) for p in btc] == [("BTC", 0.01)]


def test_funding_follows_date_and_pair_filters_and_keeps_its_currency(api):
    p = "BTC/USDT:USDT"
    add(api, "buy", 1, 100, "2026-01-01T00:00:00Z", pair=p, trade_type="futures")
    add(api, "sell", 1, 110, "2026-01-03T00:00:00Z", pair=p, trade_type="futures")
    fund(-2, "2026-01-02T00:00:00Z")
    fund(-3, "2026-02-02T00:00:00Z", fid="f2")
    fund(0.0001, "2026-01-02T08:00:00Z", asset="BTC", fid="f3")
    r = pnl(api, date_to="2026-01-31T23:59:59Z")
    assert (r["total_pnl"], r["funding_pnl"]) == (8, -2)
    assert {c["currency"]: c["funding_pnl"] for c in r["by_currency"]} == {"USD": -2, "BTC": 0.0001}
    assert pnl(api, pair="ETH/USDT:USDT")["funding_pnl"] == 0
    assert pnl(api, strategy="Swing")["funding_pnl"] == 0
    assert pnl(api, date_from="2026-02-01")["total_pnl"] == -3


def test_date_filter_keeps_earlier_cost_for_avg_cost_and_shorts(api):
    add(api, "buy", 1, 100, "2026-01-01T00:00:00Z")
    add(api, "buy", 1, 200, "2026-01-02T00:00:00Z")
    add(api, "sell", 1, 180, "2026-02-03T00:00:00Z")
    p = "ETH/USDT:USDT"
    add(api, "sell", 1, 150, "2026-01-05T00:00:00Z", pair=p, trade_type="futures")
    add(api, "buy", 1, 100, "2026-02-06T00:00:00Z", pair=p, trade_type="futures")
    assert pnl(api, date_from="2026-02-01", method="avg_cost")["total_pnl"] == 80  # 30 + 50


def test_base_coin_fee_on_a_sell_is_worth_fee_times_price(api):
    add(api, "buy", 1, 100, "2026-01-01T00:00:00Z")
    add(api, "sell", 1, 150, "2026-01-02T00:00:00Z", fee=0.01, fee_currency="BTC")
    r = pnl(api)
    assert (r["total_pnl"], r["total_fees"], r["fee_unknown_trades"]) == (48.5, 1.5, 0)


def test_third_coin_fee_on_a_sell_is_unknown(api):
    add(api, "buy", 1, 100, "2026-01-01T00:00:00Z")
    add(api, "sell", 1, 150, "2026-01-02T00:00:00Z", fee=0.05, fee_currency="BNB")
    r = pnl(api)
    assert (r["total_pnl"], r["total_fees"], r["fee_unknown_trades"]) == (50, 0, 1)


def test_fee_in_another_dollar_stablecoin_counts(api):
    add(api, "buy", 1, 100, "2026-01-01T00:00:00Z", fee=1, fee_currency="USDC")  # BTC/USDT pair
    add(api, "sell", 1, 150, "2026-01-02T00:00:00Z")
    r = pnl(api)
    assert (r["total_pnl"], r["fee_unknown_trades"]) == (49, 0)


def test_same_timestamp_buy_is_matched_before_the_sell(api):
    add(api, "sell", 1, 150, "2026-01-01T00:00:00Z")
    add(api, "buy", 1, 100, "2026-01-01T00:00:00Z")
    r = pnl(api)
    assert (r["total_pnl"], r["unmatched_sells"]) == (50, 0)


def test_best_and_worst_pair_ignore_open_positions(api):
    add(api, "buy", 1, 100, "2026-01-01T00:00:00Z")
    add(api, "sell", 1, 200, "2026-01-02T00:00:00Z")
    add(api, "buy", 1, 100, "2026-01-01T00:00:00Z", pair="ETH/USDT")  # still open
    r = pnl(api)
    assert (r["best_pair"], r["worst_pair"]) == ("BTC/USDT", "BTC/USDT")


def test_overview_period_pnl_uses_cost_from_before_the_period(api):
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    add(api, "buy", 1, 100, (now - timedelta(days=90)).strftime("%Y-%m-%dT%H:%M:%SZ"))
    add(api, "sell", 1, 150, (now - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ"))
    o = api.get("/api/overview").json()
    assert (o["pnl_this_month"], o["pnl_this_week"], o["total_pnl"]) == (50, 50, 50)


def test_overview_counts_unknown_fees_and_unmatched_sales(api):
    add(api, "buy", 1, 100, "2026-01-01T00:00:00Z", fee=0.05, fee_currency="BNB")
    add(api, "sell", 2, 150, "2026-01-02T00:00:00Z")
    o = api.get("/api/overview").json()
    assert (o["fees_unknown"], o["unmatched_sells"], o["closed_trades"]) == (1, 1, 1)
