"""Backup keeps everything a restore needs; the key file is never silently replaced."""

import io
import sqlite3
import zipfile

import pytest
from fastapi.testclient import TestClient

from app import encryption, paths


@pytest.fixture
def api():
    from app.main import app
    with TestClient(app) as client:
        yield client


def _connect_key(api, monkeypatch, key="my-api-key"):
    from app.exchanges import connector
    monkeypatch.setattr(connector.ADAPTERS["gateio"], "check_permissions",
                        lambda c: {"is_read_only": True, "permissions": [], "warnings": [], "error": None})
    resp = api.post("/api/exchanges", json={"exchange": "gateio", "api_key": key, "api_secret": "s"})
    assert resp.status_code == 201, resp.text


def test_backup_then_restore_in_a_new_folder_keeps_trades_and_working_keys(api, monkeypatch, tmp_path):
    _connect_key(api, monkeypatch)
    api.post("/api/trades", json={"pair": "BTC/USDT", "side": "buy", "quantity": 1, "price": 1})
    backup = api.get("/api/backup").content
    assert ".encryption_key" in zipfile.ZipFile(io.BytesIO(backup)).namelist()

    other = tmp_path / "other-computer"
    other.mkdir()
    monkeypatch.setenv(paths.DATA_DIR_ENV, str(other))
    encryption.forget_key()
    resp = api.post("/api/restore", files={"file": ("b.zip", backup, "application/zip")})
    assert resp.status_code == 200 and resp.json()["keys_readable"] is True
    assert len(api.get("/api/trades").json()["trades"]) == 1
    db = sqlite3.connect(str(other / "journal.db"))
    stored = db.execute("SELECT api_key_enc FROM exchanges WHERE api_key_enc != ''").fetchone()[0]
    db.close()
    assert encryption.decrypt(stored) == "my-api-key"


def test_restoring_an_old_backup_without_a_key_says_to_re_enter_keys(api, monkeypatch):
    _connect_key(api, monkeypatch)
    backup = api.get("/api/backup").content
    old = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(backup)) as src, zipfile.ZipFile(old, "w") as dst:
        for name in src.namelist():
            if name != ".encryption_key":
                dst.writestr(name, src.read(name))
    paths.encryption_key_path().write_bytes(encryption.Fernet.generate_key())  # this computer's own key
    encryption.forget_key()
    resp = api.post("/api/restore", files={"file": ("b.zip", old.getvalue(), "application/zip")})
    assert resp.json()["keys_readable"] is False
    assert "add its key again" in resp.json()["message"]


def test_a_missing_key_file_with_saved_keys_is_an_error_not_a_new_key(api, monkeypatch):
    _connect_key(api, monkeypatch)
    paths.encryption_key_path().unlink()
    encryption.forget_key()
    with pytest.raises(encryption.KeyFileMissing):
        encryption.encrypt("x")
    assert not paths.encryption_key_path().exists()
    resp = api.post("/api/exchanges", json={"exchange": "gateio", "api_key": "other", "api_secret": "s"})
    assert resp.status_code == 500 and "key file is missing" in resp.json()["detail"]


def test_a_change_made_just_before_the_backup_is_in_it(api):
    api.post("/api/trades", json={"pair": "BTC/USDT", "side": "buy", "quantity": 1, "price": 1})
    writer = sqlite3.connect(str(paths.db_path()))
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("PRAGMA wal_autocheckpoint=0")  # keep the change in the -wal file
    writer.execute("UPDATE trades SET notes = 'just now'")
    writer.commit()
    try:
        backup = api.get("/api/backup").content
    finally:
        writer.close()
    data = zipfile.ZipFile(io.BytesIO(backup)).read("journal.db")
    copy = paths.data_dir() / "check.db"
    copy.write_bytes(data)
    conn = sqlite3.connect(str(copy))
    assert conn.execute("SELECT notes FROM trades").fetchone() == ("just now",)
    conn.close()


def test_restore_drops_the_old_wal_files(api):
    api.post("/api/trades", json={"pair": "BTC/USDT", "side": "buy", "quantity": 1, "price": 1})
    backup = api.get("/api/backup").content
    wal = paths.db_path().with_name("journal.db-wal")
    wal.write_bytes(b"stale")
    api.post("/api/restore", files={"file": ("b.zip", backup, "application/zip")})
    assert not wal.exists() or wal.read_bytes() != b"stale"
