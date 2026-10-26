"""Sync through the real adapters with a fake exchange client.

The fake answers like ccxt: markets, balances, trades per symbol (filtered
by `since`), positions and Binance funding. Errors are ccxt's own classes.
"""

import ccxt
import pytest

from app.database import get_db
from app.exchanges import binance, connector, sync

DAY = 86_400_000
T0 = 1_767_225_600_000  # 2026-01-01T00:00:00Z


def trade(tid, symbol, side, ts, amount=1.0, price=100.0, fee=0.0):
    return {"id": tid, "order": tid, "symbol": symbol, "side": side, "timestamp": ts,
            "amount": amount, "price": price, "cost": amount * price,
            "fee": {"cost": fee, "currency": symbol.split("/")[1].split(":")[0]}}


class FakeClient:
    """Enough of a ccxt client for the adapters."""

    def __init__(self, trades=(), balance=None, positions=(), funding=None, fail=None,
                 max_window=None, newest_first_page=None):
        self.trades = list(trades)
        self.balance = balance or {}
        self.positions = list(positions)
        self.funding = funding or []
        self.fail = fail or {}  # method name or symbol -> exception to raise
        self.markets = {s: {} for s in ("BTC/USDT", "ETH/USDT", "SOL/USDT", "ADA/USDT",
                                        "BTC/USDT:USDT", "ETH/USDT:USDT")}
        self.calls = []
        self.apiKey, self.secret = "k", "s"
        self.max_window = max_window  # Binance futures: 7 days per request, `until` required
        self.newest_first_page = newest_first_page  # Kraken: N newest trades after `since`, `ofs` pages

    def _maybe_fail(self, key):
        if key in self.fail:
            raise self.fail[key]

    def load_markets(self):
        return self.markets

    def fetch_balance(self):
        self._maybe_fail("fetch_balance")
        return {"total": self.balance}

    def fetch_positions(self):
        self._maybe_fail("fetch_positions")
        return self.positions

    def fetch_my_trades(self, symbol=None, since=None, limit=100, params=None):
        params = params or {}
        self.calls.append((symbol, since))
        self._maybe_fail(symbol)
        self._maybe_fail("fetch_my_trades")
        until = params.get("until")
        if self.max_window and (since is None or until is None or until - since >= self.max_window):
            raise ccxt.BadRequest("-1127 More than 7 days between startTime and endTime")
        found = sorted((t for t in self.trades
                        if (symbol is None or t["symbol"] == symbol)
                        and (since is None or t["timestamp"] >= since)
                        and (until is None or t["timestamp"] <= until)),
                       key=lambda t: t["timestamp"])
        if self.newest_first_page:
            offset = params.get("ofs", 0)
            return list(reversed(found))[offset:offset + self.newest_first_page]
        return found[:limit]

    def fapiPrivateGetIncome(self, params):
        self._maybe_fail("fapiPrivateGetIncome")
        return [f for f in self.funding if f["time"] >= params.get("startTime", 0)]


@pytest.fixture
def journal():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app):
        db = get_db()
        db.execute("""INSERT INTO exchanges (id, exchange, api_key_enc, api_secret_enc, sync_start_date)
                      VALUES ('bn', 'binance', 'k', 's', '2026-01-01')""")
        db.commit()
        db.close()
        yield


def run(monkeypatch, spot_client, futures_client=None):
    """Sync the 'bn' connection; Binance builds its futures client itself."""
    monkeypatch.setattr(sync, "decrypt", lambda v: v)
    monkeypatch.setattr(sync, "create_client", lambda **kw: spot_client)
    monkeypatch.setattr(binance.ccxt, "binance", lambda cfg: futures_client or FakeClient())
    monkeypatch.setattr(binance.time, "sleep", lambda s: None)
    monkeypatch.setattr("app.exchanges.base.time.sleep", lambda s: None)
    return sync.sync_exchange("bn")


def rows(sql, *params):
    db = get_db()
    out = [tuple(r) for r in db.execute(sql, params).fetchall()]
    db.close()
    return out


def last_log():
    return rows("SELECT status, error_message FROM sync_log ORDER BY id DESC LIMIT 1")[0]


# ---- #17: errors are reported, not swallowed ----

@pytest.mark.parametrize("error, words", [
    (ccxt.AuthenticationError("Invalid API-key"), "refused the API key"),
    (ccxt.NetworkError("connection reset"), "Could not reach"),
    (ccxt.RateLimitExceeded("429"), "rate limit"),
])
def test_an_auth_network_or_rate_limit_error_marks_the_sync_as_error(journal, monkeypatch, error, words):
    client = FakeClient(balance={"SOL": 1}, fail={"SOL/USDT": error})
    result = run(monkeypatch, client)
    status, message = last_log()
    assert result["status"] == status == "error"
    assert words in message
    assert rows("SELECT last_sync_status FROM exchanges")[0][0] == "error"


def test_a_failed_balance_call_fails_the_sync_instead_of_finding_no_pairs(journal, monkeypatch):
    client = FakeClient(fail={"fetch_balance": ccxt.AuthenticationError("bad key")})
    assert run(monkeypatch, client)["status"] == "error"


def test_a_bad_symbol_is_skipped_and_the_sync_still_succeeds(journal, monkeypatch):
    client = FakeClient(balance={"ADA": 1, "SOL": 1},
                        trades=[trade("s1", "SOL/USDT", "buy", T0 + DAY)],
                        fail={"ADA/USDT": ccxt.BadSymbol("delisted")})
    result = run(monkeypatch, client)
    assert (result["status"], result["trades_fetched"]) == ("success", 1)


def test_a_funding_fetch_failure_is_reported_not_counted_as_zero(journal, monkeypatch):
    spot = FakeClient(balance={"SOL": 1}, trades=[trade("b1", "SOL/USDT", "buy", T0 + DAY)],
                      fail={"fapiPrivateGetIncome": ccxt.NetworkError("timeout")})
    result = run(monkeypatch, spot)
    status, message = last_log()
    assert result["status"] == status == "partial"
    assert "Funding fees not synced" in message
    assert result["trades_fetched"] == 1  # spot still saved


def test_futures_errors_are_reported_and_spot_is_kept(journal, monkeypatch):
    spot = FakeClient(balance={"SOL": 1}, trades=[trade("b1", "SOL/USDT", "buy", T0 + DAY)])
    futures = FakeClient(fail={"fetch_balance": ccxt.ExchangeNotAvailable("maintenance")})
    result = run(monkeypatch, spot, futures)
    assert result["status"] == "partial"
    assert "Futures trades not synced" in last_log()[1]
    assert rows("SELECT pair FROM trades") == [("SOL/USDT",)]


def test_a_key_without_futures_access_still_syncs_as_success_with_a_note(journal, monkeypatch):
    spot = FakeClient(balance={"SOL": 1}, trades=[trade("b1", "SOL/USDT", "buy", T0 + DAY)],
                      fail={"fapiPrivateGetIncome": ccxt.PermissionDenied("-2015")})
    futures = FakeClient(fail={"fetch_balance": ccxt.AuthenticationError("-2015 Invalid API-key, IP, or permissions")})
    result = run(monkeypatch, spot, futures)
    status, message = last_log()
    assert result["status"] == status == "success"
    assert "Futures skipped" in message and "Funding skipped" in message


def test_no_catch_all_is_left_in_the_sync_path():
    import inspect
    import re
    from app.exchanges import base, gateio, kraken

    for module in (sync, base, binance, gateio, kraken):
        source = inspect.getsource(module)
        assert not re.search(r"except Exception:\s*\n\s*(pass|continue)", source), module.__name__
        assert not re.search(r"except Exception as \w+:\s*\n\s*print\(", source), module.__name__


# ---- #30: futures keep their own pair name ----

def test_futures_and_spot_of_the_same_coin_never_match(journal, monkeypatch, data_root):
    spot = FakeClient(trades=[trade("s1", "BTC/USDT", "buy", T0 + DAY, price=100)])
    futures = FakeClient(positions=[{"symbol": "BTC/USDT:USDT"}],
                         trades=[trade("f1", "BTC/USDT:USDT", "sell", T0 + 2 * DAY, price=150)])
    (data_root / "extra_pairs.txt").write_text("BTC/USDT\n")
    run(monkeypatch, spot, futures)
    assert sorted(rows("SELECT pair, trade_type FROM trades")) == [("BTC/USDT", "spot"), ("BTC/USDT:USDT", "futures")]
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as api:
        assert api.get("/api/pnl").json()["total_pnl"] == 0  # the spot buy is not the futures short's cost


def test_a_closed_futures_pair_stays_tracked_and_is_checked_again(journal, monkeypatch):
    futures = FakeClient(positions=[{"symbol": "ETH/USDT:USDT"}],
                         trades=[trade("f1", "ETH/USDT:USDT", "buy", T0 + DAY)])
    run(monkeypatch, FakeClient(), futures)
    assert ("bn", "ETH/USDT:USDT") in rows("SELECT exchange_id, pair FROM tracked_pairs")

    import time
    now = int(time.time() * 1000)  # a new fill after the first sync checked the pair up to then
    later = FakeClient(trades=[trade("f2", "ETH/USDT:USDT", "sell", now)])  # no position left
    run(monkeypatch, FakeClient(), later)
    assert any(symbol == "ETH/USDT:USDT" for symbol, _ in later.calls)
    assert len(rows("SELECT id FROM trades WHERE pair = 'ETH/USDT:USDT'")) == 2


def test_migration_10_gives_old_futures_rows_their_futures_name(tmp_path):
    import sqlite3
    from app.database import MIGRATIONS

    conn = sqlite3.connect(str(tmp_path / "old.db"))
    for version in range(1, 10):
        conn.executescript(MIGRATIONS[version])
    conn.execute("INSERT INTO exchanges (id, exchange, api_key_enc, api_secret_enc) VALUES ('bn', 'binance', '', '')")
    for tid, ttype in (("s", "spot"), ("f", "futures")):
        conn.execute(
            """INSERT INTO trades (id, exchange_id, exchange, external_id, timestamp, pair, base_currency,
               quote_currency, side, quantity, price, total, trade_type)
               VALUES (?, 'bn', 'binance', ?, '2026-01-01T00:00:00.000Z', 'BTC/USDT', 'BTC', 'USDT', 'buy', 1, 1, 1, ?)""",
            [tid, tid, ttype])
    conn.execute("""INSERT INTO funding_fees (id, exchange_id, exchange, timestamp, pair, amount, asset)
                    VALUES ('x', 'bn', 'binance', '2026-01-01T08:00:00.000Z', 'BTC/USDT', -1, 'USDT')""")
    conn.commit()
    conn.executescript(MIGRATIONS[10])
    assert sorted(conn.execute("SELECT trade_type, pair FROM trades").fetchall()) == [
        ("futures", "BTC/USDT:USDT"), ("spot", "BTC/USDT")]
    assert conn.execute("SELECT pair FROM funding_fees").fetchall() == [("BTC/USDT:USDT",)]
    assert conn.execute("SELECT pair FROM tracked_pairs").fetchall() == [("BTC/USDT:USDT",)]
    conn.close()


def test_funding_is_stored_under_the_futures_pair(journal, monkeypatch):
    spot = FakeClient(funding=[{"time": T0 + DAY, "symbol": "BTCUSDT", "income": "-1.5", "asset": "USDT", "tranId": 9}])
    run(monkeypatch, spot)
    assert rows("SELECT pair, amount FROM funding_fees") == [("BTC/USDT:USDT", -1.5)]


# ---- #31: the cursor is per pair ----

def test_spot_trades_older_than_the_newest_futures_trade_are_still_fetched(journal, monkeypatch):
    futures = FakeClient(positions=[{"symbol": "BTC/USDT:USDT"}],
                         trades=[trade("f1", "BTC/USDT:USDT", "buy", T0 + 10 * DAY)])
    run(monkeypatch, FakeClient(), futures)

    # A spot trade from day 5 shows up later (e.g. the coin was sold before the first sync).
    spot = FakeClient(balance={"SOL": 1}, trades=[trade("s1", "SOL/USDT", "buy", T0 + 5 * DAY)])
    run(monkeypatch, spot, FakeClient(trades=futures.trades))
    assert ("SOL/USDT",) in rows("SELECT pair FROM trades")


def test_a_pair_added_to_extra_pairs_gets_its_full_history(journal, monkeypatch, data_root):
    first = FakeClient(balance={"SOL": 1}, trades=[trade("b1", "SOL/USDT", "buy", T0 + 20 * DAY)])
    run(monkeypatch, first)

    (data_root / "extra_pairs.txt").write_text("ETH/USDT\n")
    second = FakeClient(balance={"SOL": 1}, trades=first.trades + [trade("e1", "ETH/USDT", "buy", T0 + 2 * DAY)])
    run(monkeypatch, second)
    eth_since = [since for symbol, since in second.calls if symbol == "ETH/USDT"]
    assert eth_since == [T0]  # from the start date, not from the newest SOL trade
    assert ("ETH/USDT",) in rows("SELECT pair FROM trades")


def test_kraken_bulk_fetch_starts_at_the_start_date_for_a_new_extra_pair():
    from app.exchanges.base import SyncCursor
    cursor = SyncCursor(T0, {"BTC/USDT": T0 + 20 * DAY, "BTC/USDT:USDT": T0 + 30 * DAY})
    assert cursor.bulk_since(False) == T0 + 20 * DAY  # futures time never hides spot
    assert cursor.bulk_since(False, {"ETH/USDT"}) == T0


# ---- #36: manual trades are not a connection ----

def test_manual_trades_do_not_create_a_syncable_connection(monkeypatch):
    from fastapi.testclient import TestClient
    from app import scheduler
    from app.main import app

    with TestClient(app) as api:
        api.post("/api/trades", json={"pair": "BTC/USDT", "side": "buy", "quantity": 1, "price": 1,
                                      "exchange": "binance"})
        assert api.get("/api/exchanges").json() == []
        overview = api.get("/api/overview").json()
        assert (overview["connected_exchanges"], overview["last_sync"]) == (0, None)
        assert api.post("/api/exchanges/manual_binance/sync").status_code == 404

        synced = []
        monkeypatch.setattr(scheduler, "sync_exchange", lambda eid: synced.append(eid) or {"status": "success", "trades_fetched": 0})
        scheduler._sync_all_exchanges()
        assert synced == []
        assert sync.sync_exchange("manual_binance")["status"] == "error"


def test_migration_11_marks_old_manual_sources(tmp_path):
    import sqlite3
    from app.database import MIGRATIONS

    conn = sqlite3.connect(str(tmp_path / "old.db"))
    for version in range(1, 11):
        conn.executescript(MIGRATIONS[version])
    conn.execute("INSERT INTO exchanges (id, exchange, api_key_enc, api_secret_enc) VALUES ('manual_kraken', 'kraken', '', '')")
    conn.execute("INSERT INTO exchanges (id, exchange, api_key_enc, api_secret_enc) VALUES ('kraken_1', 'kraken', 'k', 's')")
    conn.commit()
    conn.executescript(MIGRATIONS[11])
    assert sorted(conn.execute("SELECT id, is_manual FROM exchanges").fetchall()) == [("kraken_1", 0), ("manual_kraken", 1)]
    conn.close()


def test_a_manual_futures_trade_gets_the_futures_pair_name():
    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as api:
        resp = api.post("/api/trades", json={"pair": "btc/usdt", "side": "buy", "quantity": 1, "price": 1,
                                             "trade_type": "futures"})
        assert resp.json()["pair"] == "BTC/USDT:USDT"
        row = api.get("/api/trades").json()["trades"][0]
        assert (row["pair"], row["quote_currency"]) == ("BTC/USDT:USDT", "USDT")
        assert [e["id"] for e in api.get("/api/exchanges", params={"include_manual": True}).json()] == ["manual_manual"]


def test_migration_9_sets_its_version_only_after_the_rewrite(tmp_path):
    import sqlite3
    from app.database import MIGRATIONS, MIGRATION_STEPS

    conn = sqlite3.connect(str(tmp_path / "old.db"))
    for version in range(1, 10):
        conn.executescript(MIGRATIONS[version])
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 8  # not 9 until the step ran
    MIGRATION_STEPS[9](conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 9
    conn.close()


def test_migration_10_drops_a_funding_copy_already_stored_under_the_new_name(tmp_path):
    import sqlite3
    from app.database import MIGRATIONS

    conn = sqlite3.connect(str(tmp_path / "old.db"))
    for version in range(1, 10):
        conn.executescript(MIGRATIONS[version])
    for fid, pair in (("old", "BTC/USDT"), ("new", "BTC/USDT:USDT")):
        conn.execute("""INSERT INTO funding_fees (id, exchange_id, exchange, timestamp, pair, amount, asset)
                        VALUES (?, 'bn', 'binance', '2026-01-01T08:00:00.000Z', ?, -1, 'USDT')""", [fid, pair])
    conn.commit()
    conn.executescript(MIGRATIONS[10])
    assert conn.execute("SELECT id, pair FROM funding_fees").fetchall() == [("new", "BTC/USDT:USDT")]
    conn.close()


def test_binance_ip_or_key_error_on_the_restrictions_call_says_so():
    class Key:
        def fetch_balance(self):
            return {}

        def sapiGetAccountApiRestrictions(self):
            raise ccxt.AuthenticationError("-2015 Invalid API-key, IP")

    result = connector.ADAPTERS["binance"].check_permissions(Key())
    assert "Authentication failed" in result["error"]



# ---- Fetching like the real exchanges (review of #17/#31) ----

WEEK = 7 * DAY


def _now():
    import time
    return int(time.time() * 1000)


def test_binance_futures_walks_7_day_windows_up_to_now(journal, monkeypatch):
    # Last fill in January; a new one today. One request may cover only 7 days.
    futures = FakeClient(positions=[{"symbol": "ETH/USDT:USDT"}], max_window=WEEK,
                         trades=[trade("f1", "ETH/USDT:USDT", "buy", T0 + DAY),
                                 trade("f2", "ETH/USDT:USDT", "sell", _now() - 60_000)])
    result = run(monkeypatch, FakeClient(), futures)
    assert result["status"] == "success"
    assert len(rows("SELECT id FROM trades WHERE pair = 'ETH/USDT:USDT'")) == 2


def test_a_checked_pair_is_not_walked_from_the_start_date_again(journal, monkeypatch):
    futures = FakeClient(positions=[{"symbol": "ETH/USDT:USDT"}], max_window=WEEK,
                         trades=[trade("f1", "ETH/USDT:USDT", "buy", T0 + DAY)])
    run(monkeypatch, FakeClient(), futures)
    first_calls = len(futures.calls)
    again = FakeClient(max_window=WEEK, trades=futures.trades)
    run(monkeypatch, FakeClient(), again)
    assert first_calls > 40          # 1 Jan to today in 7-day steps
    assert len(again.calls) <= 2     # only since the last check


def test_fills_sharing_a_millisecond_across_a_page_are_all_kept(journal, monkeypatch):
    fills = [trade(f"s{i}", "SOL/USDT", "buy", T0 + DAY + (0 if i < 99 else 5)) for i in range(99)]
    fills += [trade(f"s{i}", "SOL/USDT", "buy", T0 + DAY + 5) for i in range(99, 101)]  # 100th and 101st share a ms
    run(monkeypatch, FakeClient(balance={"SOL": 1}, trades=fills))
    assert len(rows("SELECT id FROM trades")) == 101


def test_two_existing_pairs_each_use_their_own_start(journal, monkeypatch):
    first = FakeClient(balance={"SOL": 1, "ADA": 1},
                       trades=[trade("s1", "SOL/USDT", "buy", T0 + 20 * DAY), trade("a1", "ADA/USDT", "buy", T0 + 5 * DAY)])
    run(monkeypatch, first)
    db = get_db()
    db.execute("UPDATE tracked_pairs SET checked_until = NULL")  # as if checked before checked_until existed
    db.commit()
    db.close()
    second = FakeClient(balance={"SOL": 1, "ADA": 1}, trades=first.trades + [trade("a2", "ADA/USDT", "buy", T0 + 10 * DAY)])
    run(monkeypatch, second)
    assert ("ADA/USDT", T0 + 5 * DAY) in second.calls
    assert len(rows("SELECT id FROM trades WHERE pair = 'ADA/USDT'")) == 2


def test_a_bad_request_that_is_not_a_bad_symbol_fails_the_sync(journal, monkeypatch):
    client = FakeClient(balance={"SOL": 1}, fail={"SOL/USDT": ccxt.BadRequest("-1100 Illegal characters")})
    assert run(monkeypatch, client)["status"] == "error"


def test_a_known_pair_the_exchange_now_rejects_is_reported(journal, monkeypatch):
    run(monkeypatch, FakeClient(balance={"SOL": 1}, trades=[trade("s1", "SOL/USDT", "buy", T0 + DAY)]))
    client = FakeClient(balance={"SOL": 1}, fail={"SOL/USDT": ccxt.BadSymbol("delisted")})
    result = run(monkeypatch, client)
    assert result["status"] == "partial"
    assert "SOL/USDT" in result["error"]


def test_losing_futures_access_after_having_futures_is_partial(journal, monkeypatch):
    futures = FakeClient(positions=[{"symbol": "ETH/USDT:USDT"}], trades=[trade("f1", "ETH/USDT:USDT", "buy", T0 + DAY)])
    run(monkeypatch, FakeClient(), futures)
    broken = FakeClient(fail={"fetch_balance": ccxt.AuthenticationError("-2015 IP not allowed")})
    result = run(monkeypatch, FakeClient(), broken)
    assert result["status"] == "partial"
    assert "Futures trades not synced" in result["error"]


def test_binance_funding_pages_past_1000_rows(journal, monkeypatch):
    rows_in = [{"time": T0 + i * 1000, "symbol": "BTCUSDT", "income": "-0.1", "asset": "USDT", "tranId": i}
               for i in range(2500)]

    class Paged(FakeClient):
        def fapiPrivateGetIncome(self, params):
            start = params.get("startTime", 0)
            return [f for f in self.funding if f["time"] >= start][: params["limit"]]

    run(monkeypatch, Paged(funding=rows_in))
    assert rows("SELECT COUNT(*) FROM funding_fees")[0][0] == 2500


def _kraken(monkeypatch, client):
    db = get_db()
    db.execute("UPDATE exchanges SET exchange = 'kraken'")
    db.commit()
    db.close()
    monkeypatch.setattr(sync, "decrypt", lambda v: v)
    monkeypatch.setattr(sync, "create_client", lambda **kw: client)
    monkeypatch.setattr("app.exchanges.kraken.time.sleep", lambda s: None)
    return sync.sync_exchange("bn")


def test_kraken_pages_back_through_all_trades(journal, monkeypatch):
    fills = [trade(f"k{i}", "SOL/USDT", "buy", T0 + DAY + i) for i in range(120)]
    result = _kraken(monkeypatch, FakeClient(trades=fills, newest_first_page=50))
    assert (result["status"], result["trades_fetched"]) == ("success", 120)


def test_kraken_extra_pair_with_no_trades_does_not_restart_every_sync(journal, monkeypatch, data_root):
    (data_root / "extra_pairs.txt").write_text("ADA/USDT\n")
    fills = [trade("k1", "SOL/USDT", "buy", T0 + 5 * DAY)]
    _kraken(monkeypatch, FakeClient(trades=fills, newest_first_page=50))
    second = FakeClient(trades=fills, newest_first_page=50)
    _kraken(monkeypatch, second)
    assert second.calls[0][1] > T0 + 5 * DAY  # from the last check, not from the start date


def test_kraken_too_many_trades_is_an_error_not_a_quiet_cut(journal, monkeypatch):
    from app.exchanges import kraken
    monkeypatch.setattr(kraken, "MAX_PAGES", 2)
    fills = [trade(f"k{i}", "SOL/USDT", "buy", T0 + DAY + i) for i in range(150)]
    result = _kraken(monkeypatch, FakeClient(trades=fills, newest_first_page=50))
    assert result["status"] == "error"
    assert "more than 100 trades" in result["error"]


def test_gateio_gets_the_full_history_of_a_new_pair(journal, monkeypatch):
    db = get_db()
    db.execute("UPDATE exchanges SET exchange = 'gateio'")
    db.commit()
    db.close()
    fills = [trade(f"g{i}", "SOL/USDT", "buy", T0 + DAY + i * 3_600_000) for i in range(250)]
    fills.append(trade("old", "SOL/USDT", "buy", T0 - DAY))  # before the start date
    client = FakeClient(balance={"SOL": 1}, trades=fills)
    monkeypatch.setattr(sync, "decrypt", lambda v: v)
    monkeypatch.setattr(sync, "create_client", lambda **kw: client)
    monkeypatch.setattr("app.exchanges.base.time.sleep", lambda s: None)
    result = sync.sync_exchange("bn")
    assert (result["status"], result["trades_fetched"]) == ("success", 250)


def test_sync_route_answers_200_for_partial_and_502_for_error(journal, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app

    spot = FakeClient(balance={"SOL": 1}, fail={"fapiPrivateGetIncome": ccxt.NetworkError("timeout")})
    monkeypatch.setattr(sync, "decrypt", lambda v: v)
    monkeypatch.setattr(sync, "create_client", lambda **kw: spot)
    monkeypatch.setattr(binance.ccxt, "binance", lambda cfg: FakeClient())
    monkeypatch.setattr("app.exchanges.base.time.sleep", lambda s: None)
    with TestClient(app) as api:
        ok = api.post("/api/exchanges/bn/sync")
        assert (ok.status_code, ok.json()["status"]) == (200, "partial")
        assert api.get("/api/exchanges").json()[0]["last_sync_status"] == "partial"
        spot.fail = {"fetch_balance": ccxt.AuthenticationError("revoked")}
        bad = api.post("/api/exchanges/bn/sync")
        assert bad.status_code == 502 and "refused the API key" in bad.json()["detail"]


def test_a_journal_from_version_8_upgrades_in_order(tmp_path, monkeypatch):
    import sqlite3
    from app import paths
    from app.database import MIGRATIONS, SCHEMA_VERSION, init_db

    root = tmp_path / "old"
    root.mkdir()
    monkeypatch.setenv(paths.DATA_DIR_ENV, str(root))
    conn = sqlite3.connect(root / "journal.db")
    for version in range(1, 9):
        conn.executescript(MIGRATIONS[version])
    conn.execute("INSERT INTO exchanges (id, exchange, api_key_enc, api_secret_enc) VALUES ('bn', 'binance', 'k', 's')")
    conn.execute("INSERT INTO exchanges (id, exchange, api_key_enc, api_secret_enc) VALUES ('manual_manual', 'manual', '', '')")
    for tid, ex, ts in (("f", "bn", "2026-01-01T02:00:00+02:00"), ("m", "manual_manual", "2026-01-02T00:00:00Z")):
        conn.execute("""INSERT INTO trades (id, exchange_id, exchange, external_id, timestamp, pair, base_currency,
                        quote_currency, side, quantity, price, total, trade_type)
                        VALUES (?, ?, 'x', ?, ?, 'BTC/USDT', 'BTC', 'USDT', 'buy', 1, 1, 1, 'futures')""", [tid, ex, tid, ts])
    for fid, pair, ts in (("a", "BTC/USDT", "2026-01-01T08:00:00+00:00"), ("b", "BTC/USDT:USDT", "2026-01-01T08:00:00.000Z")):
        conn.execute("""INSERT INTO funding_fees (id, exchange_id, exchange, timestamp, pair, amount, asset)
                        VALUES (?, 'bn', 'binance', ?, ?, -1, 'USDT')""", [fid, ts, pair])
    conn.commit()
    conn.close()

    init_db()

    conn = sqlite3.connect(root / "journal.db")
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert sorted(conn.execute("SELECT id, timestamp, pair FROM trades").fetchall()) == [
        ("f", "2026-01-01T00:00:00.000Z", "BTC/USDT:USDT"), ("m", "2026-01-02T00:00:00.000Z", "BTC/USDT:USDT")]
    assert conn.execute("SELECT pair, timestamp FROM funding_fees").fetchall() == [("BTC/USDT:USDT", "2026-01-01T08:00:00.000Z")]
    assert conn.execute("SELECT exchange_id, pair FROM tracked_pairs").fetchall() == [("bn", "BTC/USDT:USDT")]
    assert sorted(conn.execute("SELECT id, is_manual FROM exchanges").fetchall()) == [("bn", 0), ("manual_manual", 1)]
    conn.close()
