"""Shared backup logic — used by both the API route and the auto-backup scheduler."""

import io
import zipfile
from datetime import datetime, timezone

from app import paths


def create_backup_zip() -> tuple[bytes, str]:
    """Create a ZIP backup and return (zip_bytes, filename)."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"journal_backup_{ts}.zip"

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        db_path = paths.db_path()
        if db_path.exists():
            zf.write(str(db_path), "journal.db")
        screenshots_dir = paths.screenshots_dir()
        if screenshots_dir.exists():
            for img in screenshots_dir.iterdir():
                if img.is_file():
                    zf.write(str(img), f"screenshots/{img.name}")
        extra_pairs = paths.extra_pairs_file()
        if extra_pairs.exists():
            zf.write(str(extra_pairs), "extra_pairs.txt")

    return zip_buffer.getvalue(), filename


def write_auto_backup(retention_count: int = 7):
    """Write a backup to the backups folder and enforce retention."""
    backups_dir = paths.backups_dir()
    backups_dir.mkdir(parents=True, exist_ok=True)

    zip_bytes, filename = create_backup_zip()
    backup_path = backups_dir / filename
    backup_path.write_bytes(zip_bytes)
    print(f"[backup] Auto-backup saved: {backup_path} ({len(zip_bytes)} bytes)")

    backups = sorted(backups_dir.glob("journal_backup_*.zip"))
    while len(backups) > retention_count:
        oldest = backups.pop(0)
        oldest.unlink()
        print(f"[backup] Deleted old backup: {oldest.name}")
