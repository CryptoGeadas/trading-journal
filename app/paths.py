"""Where the journal keeps user data.

Everything private (database, encryption key, screenshots, backups, extra
pairs) lives in one folder in the user's profile, never inside the repo, so
it can't be committed by accident. Set TRADING_JOURNAL_DATA_DIR to use
another folder.
"""

import os
import shutil
from pathlib import Path

DATA_DIR_ENV = "TRADING_JOURNAL_DATA_DIR"

# Older versions kept data inside the repo; moved out on first start.
LEGACY_DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# Placeholder that kept the old in-repo folder in git; never user data.
_LEGACY_PLACEHOLDERS = {".gitkeep"}

# Files that belong to one journal and only make sense together.
_CORE_FILES = ("journal.db", "journal.db-wal", "journal.db-shm", ".encryption_key")


def data_dir() -> Path:
    override = os.path.expandvars(os.getenv(DATA_DIR_ENV, "").strip())
    if not override:
        return Path.home() / ".trading-journal"
    if override == "~" or override.startswith(("~/", "~\\")):
        return Path.home() / override[2:]
    path = Path(override)
    # A relative folder would change with the folder the app is started from.
    return path if path.is_absolute() else Path.home() / path


def db_path() -> Path:
    return data_dir() / "journal.db"


def encryption_key_path() -> Path:
    return data_dir() / ".encryption_key"


def screenshots_dir() -> Path:
    return data_dir() / "screenshots"


def backups_dir() -> Path:
    return data_dir() / "backups"


def extra_pairs_file() -> Path:
    return data_dir() / "extra_pairs.txt"


# Old files the last migration could not move (shown on the Settings page).
left_behind: list[Path] = []


def migrate_legacy_data(legacy: Path | None = None, target: Path | None = None) -> list[Path]:
    """Move data from an old in-repo folder into the data folder.

    Never overwrites: a file that already exists in the target stays as it
    is, and the old copy is left in place (and reported) so nothing is lost.
    The database, its WAL/SHM files and the encryption key move as one set:
    all of them or none, so a database never ends up next to another
    journal's WAL or key. Empty folders left behind are removed.
    Returns the files moved; files that stay behind are in `left_behind`.
    """
    legacy = LEGACY_DATA_DIR if legacy is None else legacy
    target = data_dir() if target is None else target
    left_behind.clear()
    if not legacy.is_dir() or legacy.resolve() == target.resolve():
        return []
    if os.path.ismount(legacy):
        # e.g. a Docker volume mounted at the old path: moving would take the
        # data out of the volume.
        print(f"[data] {legacy} is a mounted volume, not moving it. Mount it at {target} instead.")
        left_behind.append(legacy)
        return []

    moved = _move_core_set(legacy, target)
    core = {legacy / name for name in _CORE_FILES}
    for src in sorted(legacy.rglob("*")):
        if not src.is_file() or src.name in _LEGACY_PLACEHOLDERS or src in core:
            continue
        dest = target / src.relative_to(legacy)
        if dest.exists():
            print(f"[data] Not moving {src}: {dest} already exists. Check both and delete the one you don't need.")
            left_behind.append(src)
            continue
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dest))
        except OSError as e:
            # e.g. a file locked by another program; it stays and moves on a later start
            print(f"[data] Could not move {src} to {dest}: {e}")
            left_behind.append(src)
            continue
        moved.append(dest)

    for placeholder in _LEGACY_PLACEHOLDERS:
        (legacy / placeholder).unlink(missing_ok=True)
    for folder in sorted((p for p in legacy.rglob("*") if p.is_dir()), reverse=True):
        if not any(folder.iterdir()):
            folder.rmdir()
    if not any(legacy.iterdir()):
        legacy.rmdir()

    if moved:
        print(f"[data] Moved {len(moved)} file(s) from {legacy} to {target}")
    return moved


def _move_core_set(legacy: Path, target: Path) -> list[Path]:
    """Move database + WAL/SHM + key together, or leave all of them."""
    names = [name for name in _CORE_FILES if (legacy / name).exists()]
    if not names:
        return []
    if any((target / name).exists() for name in _CORE_FILES):
        print(
            f"[data] Not moving the database and key from {legacy}: {target} already has a journal. "
            "Check both and keep the one you need."
        )
        left_behind.extend(legacy / name for name in names)
        return []

    target.mkdir(parents=True, exist_ok=True)
    done: list[str] = []
    try:
        for name in names:
            shutil.move(str(legacy / name), str(target / name))
            done.append(name)
    except OSError as e:
        # Put back what already moved so the set stays together.
        for name in done:
            shutil.move(str(target / name), str(legacy / name))
        print(f"[data] Could not move the database and key to {target} ({e}); they stay in {legacy} for now.")
        left_behind.extend(legacy / name for name in names)
        return []
    return [target / name for name in names]
