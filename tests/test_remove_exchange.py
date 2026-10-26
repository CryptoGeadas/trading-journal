"""Removing an exchange connection: keys are wiped, trades are kept, the connection is gone everywhere."""

import pytest
from fastapi.testclient import TestClient

from app.database import get_db
from app.exchanges import connector
from app.main import app

_OK = {"is_read_only": True, "permissions": ["read"], "warnings": [], "error": None}


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setattr(connector.ADAPTERS["kraken"], "check_permissions", lambda c: _OK)
    with TestClient(app) as client:
        yield client


def _connect_with_a_trade(api):
    exchange_id = api.post("/api/exchanges", json={
        "exchange": "kraken", "api_key": "my-key", "api_secret": "my-secret", "passphrase": "my-pass",
    }).json()["exchange_id"]
    db = get_db()
    db.execute(
        """INSERT INTO trades (id, exchange_id, exchange, external_id, timestamp, pair,
               base_currency, quote_currency, side, quantity, price, total)
           VALUES ('t1', ?, 'kraken', 'e1', '2026-01-01T00:00:00Z', 'BTC/USD', 'BTC', 'USD', 'buy', 1, 100, 100)""",
        [exchange_id],
    )
    db.execute("INSERT INTO sync_log (exchange_id, status) VALUES (?, 'success')", [exchange_id])
    db.commit()
    db.close()
    return exchange_id


def test_removing_an_exchange_with_trades_succeeds(api):
    exchange_id = _connect_with_a_trade(api)
    resp = api.delete(f"/api/exchanges/{exchange_id}", params={"confirm": "true"})
    assert resp.status_code == 200, resp.text


def test_removed_exchange_keys_are_wiped(api):
    exchange_id = _connect_with_a_trade(api)
    api.delete(f"/api/exchanges/{exchange_id}", params={"confirm": "true"})
    db = get_db()
    row = db.execute("SELECT api_key_enc, api_secret_enc, passphrase_enc FROM exchanges WHERE id = ?", [exchange_id]).fetchone()
    db.close()
    assert row is None or (row["api_key_enc"], row["api_secret_enc"], row["passphrase_enc"] or "") == ("", "", "")


def test_trades_of_a_removed_exchange_are_kept(api):
    exchange_id = _connect_with_a_trade(api)
    api.delete(f"/api/exchanges/{exchange_id}", params={"confirm": "true"})
    trades = api.get("/api/trades").json()["trades"]
    assert [t["exchange"] for t in trades] == ["kraken"]


def test_removed_exchange_is_gone_from_the_list_sync_and_counts(api):
    exchange_id = _connect_with_a_trade(api)
    api.delete(f"/api/exchanges/{exchange_id}", params={"confirm": "true"})
    assert api.get("/api/exchanges").json() == []
    assert api.post(f"/api/exchanges/{exchange_id}/sync").status_code == 404
    assert api.get("/api/overview").json()["connected_exchanges"] == 0


def test_scheduler_skips_removed_exchanges(api, monkeypatch):
    from app import scheduler

    exchange_id = _connect_with_a_trade(api)
    api.delete(f"/api/exchanges/{exchange_id}", params={"confirm": "true"})
    synced = []
    monkeypatch.setattr(scheduler, "sync_exchange", lambda eid: synced.append(eid) or {"status": "success", "trades_fetched": 0})
    scheduler._sync_all_exchanges()
    assert exchange_id not in synced


def test_removing_twice_or_unknown_gives_404(api):
    exchange_id = _connect_with_a_trade(api)
    assert api.delete(f"/api/exchanges/{exchange_id}", params={"confirm": "true"}).status_code == 200
    assert api.delete(f"/api/exchanges/{exchange_id}", params={"confirm": "true"}).status_code == 404
    assert api.delete("/api/exchanges/nope", params={"confirm": "true"}).status_code == 404


def test_existing_journal_upgrades_and_keeps_its_connections(tmp_path, monkeypatch):
    import sqlite3
    from app import paths
    from app.database import MIGRATIONS, SCHEMA_VERSION, init_db

    root = tmp_path / "old-journal"
    root.mkdir()
    monkeypatch.setenv(paths.DATA_DIR_ENV, str(root))
    conn = sqlite3.connect(root / "journal.db")
    for version in range(1, 8):
        conn.executescript(MIGRATIONS[version])
    conn.execute("INSERT INTO exchanges (id, exchange, api_key_enc, api_secret_enc) VALUES ('old', 'kraken', 'k', 's')")
    conn.commit()
    conn.close()

    init_db()

    conn = sqlite3.connect(root / "journal.db")
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert conn.execute("SELECT id, removed_at FROM exchanges").fetchall() == [("old", None)]
    conn.close()
