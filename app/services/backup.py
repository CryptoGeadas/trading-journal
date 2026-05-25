"""Shared backup logic — used by both the API route and the auto-backup scheduler."""

import io
import zipfile
from pathlib import Path
from datetime import datetime, timezone

from app.database import DB_PATH

DATA_DIR = DB_PATH.parent
SCREENSHOTS_DIR = DATA_DIR / "screenshots"
EXTRA_PAIRS_FILE = DATA_DIR / "extra_pairs.txt"
BACKUPS_DIR = DATA_DIR / "backups"


def create_backup_zip() -> tuple[bytes, str]:
    """Create a ZIP backup and return (zip_bytes, filename)."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"journal_backup_{ts}.zip"

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        if DB_PATH.exists():
            zf.write(str(DB_PATH), "journal.db")
        if SCREENSHOTS_DIR.exists():
            for img in SCREENSHOTS_DIR.iterdir():
                if img.is_file():
                    zf.write(str(img), f"screenshots/{img.name}")
        if EXTRA_PAIRS_FILE.exists():
            zf.write(str(EXTRA_PAIRS_FILE), "extra_pairs.txt")

    return zip_buffer.getvalue(), filename


def write_auto_backup(retention_count: int = 7):
    """Write a backup to data/backups/ and enforce retention."""
    BACKUPS_DIR.mkdir(parents=True, exist_ok=True)

    zip_bytes, filename = create_backup_zip()
    backup_path = BACKUPS_DIR / filename
    backup_path.write_bytes(zip_bytes)
    print(f"[backup] Auto-backup saved: {backup_path} ({len(zip_bytes)} bytes)")

    backups = sorted(BACKUPS_DIR.glob("journal_backup_*.zip"))
    while len(backups) > retention_count:
        oldest = backups.pop(0)
        oldest.unlink()
        print(f"[backup] Deleted old backup: {oldest.name}")
