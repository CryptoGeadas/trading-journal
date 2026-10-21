"""Tests for the data-location module: where user data lives, and moving old in-repo data."""

from pathlib import Path

import pytest

from app import paths


@pytest.fixture
def no_override(monkeypatch):
    monkeypatch.delenv(paths.DATA_DIR_ENV, raising=False)


def test_default_root_is_in_user_profile(no_override, monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert paths.data_dir() == tmp_path / ".trading-journal"


def test_default_root_is_outside_the_repo(no_override):
    repo_root = Path(paths.__file__).resolve().parent.parent
    assert repo_root not in paths.data_dir().resolve().parents


def test_override_setting_moves_every_path(monkeypatch, tmp_path):
    root = tmp_path / "elsewhere"
    monkeypatch.setenv(paths.DATA_DIR_ENV, str(root))
    assert paths.data_dir() == root
    for p in (
        paths.db_path(),
        paths.encryption_key_path(),
        paths.screenshots_dir(),
        paths.backups_dir(),
        paths.extra_pairs_file(),
    ):
        assert p.parent == root


def test_override_expands_user_home(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setenv(paths.DATA_DIR_ENV, "~/journal")
    assert paths.data_dir() == tmp_path / "journal"


def _make_legacy(legacy: Path):
    (legacy / "screenshots").mkdir(parents=True)
    (legacy / "backups").mkdir()
    (legacy / ".gitkeep").write_text("")
    (legacy / "journal.db").write_bytes(b"db")
    (legacy / "journal.db-wal").write_bytes(b"wal")
    (legacy / ".encryption_key").write_bytes(b"key")
    (legacy / "extra_pairs.txt").write_text("BTC/USDT\n")
    (legacy / "screenshots" / "a.png").write_bytes(b"png")
    (legacy / "backups" / "journal_backup_1.zip").write_bytes(b"zip")


def test_migration_moves_everything(tmp_path):
    legacy, target = tmp_path / "repo" / "data", tmp_path / "home"
    _make_legacy(legacy)

    moved = paths.migrate_legacy_data(legacy, target)

    assert (target / "journal.db").read_bytes() == b"db"
    assert (target / "journal.db-wal").read_bytes() == b"wal"
    assert (target / ".encryption_key").read_bytes() == b"key"
    assert (target / "extra_pairs.txt").read_text() == "BTC/USDT\n"
    assert (target / "screenshots" / "a.png").read_bytes() == b"png"
    assert (target / "backups" / "journal_backup_1.zip").read_bytes() == b"zip"
    assert not (target / ".gitkeep").exists()
    assert not legacy.exists()  # nothing private left behind in the repo
    assert len(moved) == 6


def test_migration_never_overwrites_existing_files(tmp_path):
    legacy, target = tmp_path / "repo" / "data", tmp_path / "home"
    _make_legacy(legacy)
    target.mkdir()
    (target / "journal.db").write_bytes(b"newer")
    (target / "screenshots").mkdir()
    (target / "screenshots" / "a.png").write_bytes(b"newer-png")

    paths.migrate_legacy_data(legacy, target)

    assert (target / "journal.db").read_bytes() == b"newer"
    assert (target / "screenshots" / "a.png").read_bytes() == b"newer-png"
    # the conflicting files stay where they were, so nothing is lost
    assert (legacy / "journal.db").read_bytes() == b"db"
    assert (legacy / "screenshots" / "a.png").read_bytes() == b"png"
    # the key and WAL belong to the old database, so they stay with it
    assert (legacy / ".encryption_key").read_bytes() == b"key"
    assert (legacy / "journal.db-wal").read_bytes() == b"wal"
    assert not (target / ".encryption_key").exists()
    # files without a conflict were moved
    assert (target / "extra_pairs.txt").exists()
    assert set(paths.left_behind) == {
        legacy / "journal.db", legacy / "journal.db-wal", legacy / ".encryption_key",
        legacy / "screenshots" / "a.png",
    }


def test_migration_twice_is_a_no_op(tmp_path):
    legacy, target = tmp_path / "repo" / "data", tmp_path / "home"
    _make_legacy(legacy)
    paths.migrate_legacy_data(legacy, target)
    before = sorted(p.relative_to(target) for p in target.rglob("*"))

    assert paths.migrate_legacy_data(legacy, target) == []
    assert sorted(p.relative_to(target) for p in target.rglob("*")) == before


def test_migration_without_legacy_folder_is_a_no_op(tmp_path):
    assert paths.migrate_legacy_data(tmp_path / "missing", tmp_path / "home") == []


def test_migration_into_same_folder_is_a_no_op(tmp_path):
    legacy = tmp_path / "data"
    _make_legacy(legacy)
    assert paths.migrate_legacy_data(legacy, legacy) == []
    assert (legacy / "journal.db").exists()


def test_app_start_moves_legacy_data_and_settings_shows_folder(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from app.main import app

    import sqlite3
    from app.database import MIGRATIONS, SCHEMA_VERSION

    legacy, root = tmp_path / "repo" / "data", tmp_path / "home"
    _make_legacy(legacy)
    (legacy / "journal.db-wal").unlink()
    # A real old journal with one marker row, so we can see it arrive intact
    # (and that startup moves it BEFORE creating a new empty database).
    (legacy / "journal.db").unlink()
    conn = sqlite3.connect(legacy / "journal.db")
    for version in range(1, SCHEMA_VERSION + 1):
        if version in MIGRATIONS:
            conn.executescript(MIGRATIONS[version])
    conn.execute("PRAGMA user_version = %d" % SCHEMA_VERSION)
    conn.execute("INSERT INTO strategies (id, name) VALUES ('marker', 'Marker')")
    conn.commit()
    conn.close()
    monkeypatch.setattr(paths, "LEGACY_DATA_DIR", legacy)
    monkeypatch.setenv(paths.DATA_DIR_ENV, str(root))

    with TestClient(app) as client:
        assert (root / ".encryption_key").read_bytes() == b"key"
        assert (root / "screenshots" / "a.png").exists()
        assert not legacy.exists()
        moved = sqlite3.connect(root / "journal.db")
        assert moved.execute("SELECT name FROM strategies WHERE id = 'marker'").fetchone() == ("Marker",)
        moved.close()
        assert client.get("/api/system").json() == {"data_dir": str(root), "left_behind": []}


def test_database_is_never_paired_with_another_journals_key(tmp_path):
    legacy, target = tmp_path / "repo" / "data", tmp_path / "home"
    _make_legacy(legacy)
    target.mkdir()
    (target / ".encryption_key").write_bytes(b"other-key")

    paths.migrate_legacy_data(legacy, target)

    assert not (target / "journal.db").exists()
    assert not (target / "journal.db-wal").exists()
    assert (target / ".encryption_key").read_bytes() == b"other-key"
    assert (legacy / "journal.db").exists() and (legacy / ".encryption_key").exists()


def test_failed_move_keeps_database_and_key_together(monkeypatch, tmp_path):
    legacy, target = tmp_path / "repo" / "data", tmp_path / "home"
    _make_legacy(legacy)
    real_move = paths.shutil.move

    def locked_db(src, dest):
        if src.endswith("journal.db"):
            raise PermissionError("file is in use")
        return real_move(src, dest)

    monkeypatch.setattr(paths.shutil, "move", locked_db)
    paths.migrate_legacy_data(legacy, target)

    for name in ("journal.db", "journal.db-wal", ".encryption_key"):
        assert (legacy / name).exists(), name
        assert not (target / name).exists(), name
    assert legacy / "journal.db" in paths.left_behind
    # unrelated files still moved
    assert (target / "screenshots" / "a.png").exists()

    monkeypatch.setattr(paths.shutil, "move", real_move)
    paths.migrate_legacy_data(legacy, target)  # next start finishes the job
    assert (target / "journal.db").read_bytes() == b"db"
    assert (target / ".encryption_key").read_bytes() == b"key"
    assert not legacy.exists()


def test_mounted_legacy_folder_is_left_alone(monkeypatch, tmp_path):
    legacy, target = tmp_path / "app" / "data", tmp_path / "data"
    _make_legacy(legacy)
    monkeypatch.setattr(paths.os.path, "ismount", lambda p: Path(p) == legacy)

    assert paths.migrate_legacy_data(legacy, target) == []
    assert (legacy / "journal.db").exists()
    assert not target.exists()


def test_relative_override_is_under_home_not_the_current_folder(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setenv(paths.DATA_DIR_ENV, "journal")
    assert paths.data_dir() == tmp_path / "journal"


def test_override_expands_environment_variables(monkeypatch, tmp_path):
    monkeypatch.setenv("TJ_TEST_ROOT", str(tmp_path))
    monkeypatch.setenv(paths.DATA_DIR_ENV, "$TJ_TEST_ROOT/journal")
    assert paths.data_dir() == tmp_path / "journal"


def test_blank_override_uses_default(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setenv(paths.DATA_DIR_ENV, "   ")
    assert paths.data_dir() == tmp_path / ".trading-journal"


def test_encryption_key_is_created_in_the_data_folder(data_root):
    from app import encryption

    assert encryption.decrypt(encryption.encrypt("secret")) == "secret"
    assert (data_root / ".encryption_key").exists()


def test_backups_use_the_data_folder(data_root):
    import io
    import sqlite3
    import zipfile
    from app.services import backup

    sqlite3.connect(data_root / "journal.db").close()
    (data_root / "screenshots").mkdir()
    (data_root / "screenshots" / "a.png").write_bytes(b"png")
    (data_root / "extra_pairs.txt").write_text("BTC/USDT\n")

    zip_bytes, _ = backup.create_backup_zip()
    names = set(zipfile.ZipFile(io.BytesIO(zip_bytes)).namelist())
    assert {"journal.db", "screenshots/a.png", "extra_pairs.txt"} <= names

    backup.write_auto_backup(retention_count=1)
    assert len(list((data_root / "backups").glob("journal_backup_*.zip"))) == 1


def test_extra_pairs_are_read_from_the_data_folder(data_root):
    from app.exchanges.base import ExchangeAdapter

    (data_root / "extra_pairs.txt").write_text("btc/usdt\nETH/USDT:USDT\n")
    assert ExchangeAdapter.load_extra_pairs() == {"BTC/USDT"}
    assert ExchangeAdapter.load_extra_pairs(futures=True) == {"ETH/USDT:USDT"}


def test_no_module_builds_a_path_into_the_repo():
    import re

    app_dir = Path(paths.__file__).resolve().parent
    offenders = [
        str(f.relative_to(app_dir))
        for f in app_dir.rglob("*.py")
        if f.name != "paths.py" and re.search(r"""/\s*["']data["']""", f.read_text(encoding="utf-8"))
    ]
    assert offenders == []
