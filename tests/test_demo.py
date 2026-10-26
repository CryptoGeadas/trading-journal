"""Demo data: same seed same data, fills every feature, removes cleanly."""

from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.database import get_db
from app.services import demo

TODAY = date(2026, 10, 26)


@pytest.fixture
def api():
    from app.main import app
    with TestClient(app) as client:
        yield client


def test_same_seed_and_day_give_identical_data():
    assert demo.generate(7, TODAY) == demo.generate(7, TODAY)
    assert demo.generate(7, TODAY) != demo.generate(8, TODAY)


def test_generated_data_covers_every_feature_area():
    data = demo.generate(7, TODAY)
    types = {t["trade_type"] for t in data.trades}
    assert types == {"spot", "futures"}
    assert {t["exchange"] for t in data.trades} == {"binance", "kraken"}
    assert all(t["fee"] > 0 for t in data.trades)
    assert any(t["trade_type"] == "futures" and t["side"] == "sell" for t in data.trades)
    assert data.funding and any(f["amount"] < 0 for f in data.funding)
    assert {t["strategy"] for t in data.trades} >= {s[0] for s in demo.STRATEGIES}
    assert any(t["emotion_tag"] for t in data.trades) and any(t["trade_rating"] for t in data.trades)
    assert any(t["notes"] for t in data.trades) and len(data.notes) == 3
    times = sorted(t["timestamp"] for t in data.trades)
    assert times[0] < "2026-05-01" and times[-1] < "2026-10-26"  # about six months, nothing in the future


def test_loading_fills_every_page(api):
    assert api.get("/api/demo").json() == {"loaded": False}
    loaded = api.post("/api/demo").json()
    assert loaded["trades"] > 100 and loaded["funding"] > 10 and loaded["strategies"] == 3 and loaded["notes"] == 3
    assert api.get("/api/demo").json() == {"loaded": True}

    pnl = api.get("/api/pnl", params={"group_by": "strategy"}).json()
    assert pnl["trade_count"] > 50 and pnl["wins"] and pnl["losses"] and pnl["funding_pnl"] != 0
    assert len(api.get("/api/equity-curve").json()) > 20
    assert {p["type"] for p in api.get("/api/patterns").json()["patterns"]} == {"streaks", "time_of_day", "hold_time"}
    assert len(api.get("/api/strategies").json()) == 3
    assert len(api.get("/api/notes").json()) == 3
    assert api.get("/api/funding-fees").json()["total"] == loaded["funding"]
    assert api.post("/api/demo").status_code == 409  # never twice


def test_demo_accounts_are_not_connections(api):
    api.post("/api/demo")
    assert api.get("/api/exchanges").json() == []
    assert api.get("/api/overview").json()["connected_exchanges"] == 0
    assert {e["id"] for e in api.get("/api/exchanges", params={"include_manual": True}).json()} == set(demo.DEMO_ACCOUNTS)


def test_sync_never_runs_for_the_demo_account(api, monkeypatch):
    from app import scheduler
    from app.exchanges import sync

    api.post("/api/demo")
    assert api.post("/api/exchanges/demo_binance/sync").status_code == 404
    synced = []
    monkeypatch.setattr(scheduler, "sync_exchange", lambda eid: synced.append(eid) or {"status": "success", "trades_fetched": 0})
    scheduler._sync_all_exchanges()
    assert synced == []
    assert sync.sync_exchange("demo_binance")["status"] == "error"


def test_remove_deletes_all_demo_rows_and_no_real_rows(api):
    api.post("/api/trades", json={"pair": "BTC/USDT", "side": "buy", "quantity": 1, "price": 100,
                                  "timestamp": "2026-01-01T00:00:00Z", "strategy": "Breakout"})
    api.post("/api/trades", json={"pair": "BTC/USDT", "side": "sell", "quantity": 1, "price": 130,
                                  "timestamp": "2026-02-01T00:00:00Z"})
    api.post("/api/strategies", json={"name": "Breakout"})  # a real strategy with a demo name
    api.post("/api/notes", json={"title": "Mine", "content": "real"})
    before = api.get("/api/pnl").json()

    api.post("/api/demo")
    assert api.get("/api/pnl").json()["trade_count"] > before["trade_count"]
    assert api.delete("/api/demo").status_code == 400  # needs confirm
    removed = api.delete("/api/demo", params={"confirm": True}).json()
    assert removed["trades"] > 100

    assert api.get("/api/pnl").json() == before
    assert [s["name"] for s in api.get("/api/strategies").json()] == ["Breakout"]
    assert [n["title"] for n in api.get("/api/notes").json()] == ["Mine"]
    db = get_db()
    assert db.execute("SELECT COUNT(*) FROM funding_fees").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM exchanges WHERE is_demo = 1").fetchone()[0] == 0
    db.close()
    assert api.get("/api/demo").json() == {"loaded": False}
