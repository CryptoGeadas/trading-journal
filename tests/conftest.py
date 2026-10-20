"""Shared fixtures for trading journal tests."""

import sqlite3
import pytest
from app.database import MIGRATIONS, SCHEMA_VERSION, _seed_emotion_tags


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
