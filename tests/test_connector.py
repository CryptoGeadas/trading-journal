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


class _BinanceKey:
    """Fake Binance client: `restrictions` is the API answer, or an exception to raise."""

    def __init__(self, restrictions):
        self.restrictions = restrictions

    def fetch_balance(self):
        return {}

    def sapiGetAccountApiRestrictions(self):
        if isinstance(self.restrictions, Exception):
            raise self.restrictions
        return self.restrictions


_READ_ONLY = {"enableReading": True, "enableWithdrawals": False, "enableInternalTransfer": False}


def test_binance_read_only_key_is_accepted():
    result = connector.ADAPTERS["binance"].check_permissions(_BinanceKey(_READ_ONLY))
    assert result["error"] is None and result["is_read_only"] is True


@pytest.mark.parametrize("flag", ["enableWithdrawals", "enableInternalTransfer"])
def test_binance_key_that_can_move_money_is_refused(flag):
    result = connector.ADAPTERS["binance"].check_permissions(_BinanceKey({**_READ_ONLY, flag: True}))
    assert "Withdrawal or Transfer" in result["error"]


@pytest.mark.parametrize("answer", [ccxt.NetworkError("timeout"), ccxt.ExchangeError("-1002"), {}, None])
def test_binance_key_is_refused_when_its_permissions_cannot_be_read(answer):
    result = connector.ADAPTERS["binance"].check_permissions(_BinanceKey(answer))
    assert result["error"] and "not saved" in result["error"]


@pytest.mark.parametrize("exchange, name", [("gateio", "Gate.io"), ("kraken", "Kraken")])
def test_gateio_and_kraken_say_withdrawal_cannot_be_checked(exchange, name):
    class Key:
        def fetch_balance(self):
            return {}

    result = connector.ADAPTERS[exchange].check_permissions(Key())
    assert result["error"] is None
    assert any("cannot block a key that can withdraw" in w and name in w for w in result["warnings"])


def test_adding_a_gateio_key_returns_the_cannot_check_warning(client, monkeypatch):
    from app.api import routes_exchanges

    class Key:
        def fetch_balance(self):
            return {}

    monkeypatch.setattr(routes_exchanges, "create_client", lambda **kw: Key())
    resp = _add_gateio(client)
    assert resp.status_code == 201, resp.text
    assert any("Gate.io" in w and "read-only" in w for w in resp.json()["warnings"])


def test_the_same_key_cannot_be_connected_twice(client, monkeypatch):
    monkeypatch.setattr(connector.ADAPTERS["gateio"], "check_permissions", lambda c: _OK)
    assert _add_gateio(client).status_code == 201
    second = _add_gateio(client)
    assert second.status_code == 409
    other = client.post("/api/exchanges", json={"exchange": "gateio", "api_key": "k2", "api_secret": "s"})
    assert other.status_code == 201


def test_adding_a_removed_key_again_reconnects_the_old_connection(client, monkeypatch):
    from app.database import get_db

    monkeypatch.setattr(connector.ADAPTERS["gateio"], "check_permissions", lambda c: _OK)
    first = _add_gateio(client).json()["exchange_id"]
    assert client.delete(f"/api/exchanges/{first}", params={"confirm": True}).status_code == 200
    again = _add_gateio(client)
    assert again.status_code == 201
    assert (again.json()["status"], again.json()["exchange_id"]) == ("reconnected", first)
    db = get_db()
    assert db.execute("SELECT COUNT(*) FROM exchanges WHERE exchange = 'gateio'").fetchone()[0] == 1
    db.close()


def test_re_adding_after_an_old_removal_warns_about_duplicates(client, monkeypatch):
    from app.database import get_db

    monkeypatch.setattr(connector.ADAPTERS["gateio"], "check_permissions", lambda c: _OK)
    db = get_db()  # removed before fingerprints existed: keys wiped, no fingerprint
    db.execute("""INSERT INTO exchanges (id, exchange, label, api_key_enc, api_secret_enc, removed_at)
                  VALUES ('old', 'gateio', 'Gate main', '', '', '2026-01-01T00:00:00.000Z')""")
    db.commit()
    db.close()
    resp = _add_gateio(client)
    assert resp.status_code == 201
    assert any("Gate main" in w and "twice" in w for w in resp.json()["warnings"])
