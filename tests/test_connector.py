"""Tests for exchange client creation and the read-only permission gate."""

import ccxt
import pytest

from app.exchanges import connector


def test_gateio_uses_the_current_ccxt_class():
    client = connector.create_client("gateio", "key", "secret")
    assert isinstance(client, ccxt.gate)


@pytest.mark.parametrize("exchange", ["binance", "gateio", "kraken"])
def test_every_stored_exchange_name_has_a_client_and_an_adapter(exchange):
    assert connector.create_client(exchange, "key", "secret") is not None
    assert connector.get_adapter(exchange).exchange_id == exchange


def test_permission_check_uses_the_stored_exchange_name_not_ccxts_id(monkeypatch):
    """ccxt calls Gate.io 'gate'; the journal stores 'gateio'. The check must still run."""
    client = connector.create_client("gateio", "key", "secret")
    blocked = {"is_read_only": False, "permissions": ["withdraw"], "warnings": [], "error": "withdrawal enabled"}
    monkeypatch.setattr(connector.ADAPTERS["gateio"], "check_permissions", lambda c: blocked)

    assert connector.validate_read_only("gateio", client) == blocked


def test_unknown_exchange_is_refused_not_trusted():
    class Client:
        def fetch_balance(self):
            return {}

    result = connector.validate_read_only("someexchange", Client())
    assert result["is_read_only"] is False
    assert result["error"]


def test_ccxt_version_is_bounded():
    import tomllib
    from pathlib import Path

    pyproject = tomllib.loads((Path(__file__).resolve().parent.parent / "pyproject.toml").read_text(encoding="utf-8"))
    ccxt_req = next(d for d in pyproject["project"]["dependencies"] if d.startswith("ccxt"))
    assert "<" in ccxt_req


@pytest.mark.parametrize("exchange", ["binance", "gateio", "kraken"])
def test_adapters_only_call_methods_the_ccxt_client_has(exchange):
    """A renamed ccxt method must fail here, not silently at sync time."""
    import inspect
    import re
    from app.exchanges import base

    adapter = connector.get_adapter(exchange)
    source = inspect.getsource(type(adapter)) + inspect.getsource(base)
    client = connector.create_client(exchange, "key", "secret")
    missing = sorted({m for m in re.findall(r"client\.(\w+)", source) if not hasattr(client, m)})
    assert missing == []


def test_installed_ccxt_satisfies_the_declared_range():
    import tomllib
    from pathlib import Path
    from packaging.requirements import Requirement

    pyproject = tomllib.loads((Path(__file__).resolve().parent.parent / "pyproject.toml").read_text(encoding="utf-8"))
    ccxt_req = Requirement(next(d for d in pyproject["project"]["dependencies"] if d.startswith("ccxt")))
    assert ccxt_req.specifier.contains(ccxt.__version__)


_OK = {"is_read_only": True, "permissions": ["read (verified)"], "warnings": [], "error": None}


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as c:
        yield c


def _add_gateio(client):
    return client.post("/api/exchanges", json={"exchange": "gateio", "api_key": "k", "api_secret": "s"})


def test_adding_a_gateio_key_runs_the_gateio_check_and_stores_gateio(client, monkeypatch):
    from app.database import get_db

    monkeypatch.setattr(connector.ADAPTERS["gateio"], "check_permissions", lambda c: _OK)
    resp = _add_gateio(client)

    assert resp.status_code == 201, resp.text
    db = get_db()
    assert db.execute("SELECT exchange FROM exchanges").fetchone()["exchange"] == "gateio"
    db.close()


def test_a_refused_key_is_blocked_and_not_stored(client, monkeypatch):
    from app.database import get_db

    refused = {**_OK, "is_read_only": False, "error": "withdrawal enabled"}
    monkeypatch.setattr(connector.ADAPTERS["gateio"], "check_permissions", lambda c: refused)
    resp = _add_gateio(client)

    assert resp.status_code == 403
    db = get_db()
    assert db.execute("SELECT COUNT(*) FROM exchanges").fetchone()[0] == 0
    db.close()


def test_a_stored_gateio_row_syncs_through_the_gateio_adapter(client, monkeypatch):
    from app.database import get_db
    from app.exchanges import sync
    from app.exchanges.gateio import GateioAdapter

    monkeypatch.setattr(connector.ADAPTERS["gateio"], "check_permissions", lambda c: _OK)
    exchange_id = _add_gateio(client).json()["exchange_id"]

    seen = {}

    def fake_spot(self, c, since, tracked, extra):
        seen["client"] = c
        return [{
            "id": "t1", "order": "o1", "timestamp": 1_700_000_000_000, "symbol": "BTC/USDT",
            "side": "buy", "amount": 0.1, "price": 30000.0, "cost": 3000.0,
            "fee": {"cost": 1.0, "currency": "USDT"},
        }]

    monkeypatch.setattr(GateioAdapter, "sync_spot", fake_spot)
    monkeypatch.setattr(GateioAdapter, "sync_futures", lambda self, *a: [])
    monkeypatch.setattr(GateioAdapter, "sync_funding", lambda self, *a: 0)

    sync.sync_exchange(exchange_id)

    assert isinstance(seen["client"], ccxt.gate)
    db = get_db()
    row = db.execute("SELECT exchange, pair FROM trades").fetchone()
    status = db.execute("SELECT last_sync_status FROM exchanges").fetchone()[0]
    db.close()
    assert (row["exchange"], row["pair"], status) == ("gateio", "BTC/USDT", "success")
