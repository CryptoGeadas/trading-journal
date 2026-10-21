"""Shared fixtures for trading journal tests."""

import sqlite3
import pytest
from app import encryption, paths
from app.database import MIGRATIONS, SCHEMA_VERSION, _seed_emotion_tags


@pytest.fixture(autouse=True)
def data_root(tmp_path, monkeypatch):
    """Every test gets its own data folder; nothing touches the real one."""
    root = tmp_path / "data-root"
    monkeypatch.setenv(paths.DATA_DIR_ENV, str(root))
    monkeypatch.setattr(paths, "LEGACY_DATA_DIR", tmp_path / "no-legacy")
    monkeypatch.setattr(encryption, "_fernet", None)
    root.mkdir()
    return root


@pytest.fixture
def test_db(tmp_path):
    """Create a fresh database with all migrations applied."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    for version in range(1, SCHEMA_VERSION + 1):
        if version in MIGRATIONS:
            conn.executescript(MIGRATIONS[version])
    _seed_emotion_tags(conn)
    yield conn
    conn.close()
