"""Backup and restore endpoints — full journal backup as ZIP."""

import io
import os
import zipfile
import shutil
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile, File
from fastapi.responses import StreamingResponse
from app import encryption, paths
from app.database import init_db
from app.services.backup import create_backup_zip

router = APIRouter()


@router.get("/backup")
async def download_backup():
    """
    Download a full backup as a ZIP file containing:
    - journal.db (database)
    - screenshots/ (all chart images)
    - extra_pairs.txt (custom pair list)
    """
    if not paths.db_path().exists():
        raise HTTPException(status_code=404, detail="No database found to back up.")

    zip_bytes, filename = create_backup_zip()
    print(f"[backup] Created backup: {filename} ({len(zip_bytes)} bytes)")

    return StreamingResponse(
        iter([zip_bytes]),
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.post("/restore")
async def restore_backup(file: UploadFile = File(...)):
    """
    Restore from a backup ZIP file.
    Replaces the database, screenshots, and extra_pairs.txt.
    The server should be restarted after restore.
    """
    if not file.filename or not file.filename.endswith(".zip"):
        raise HTTPException(status_code=400, detail="Please upload a .zip backup file.")

    contents = await file.read()

    # Validate it's a real ZIP with a journal.db inside
    try:
        zf = zipfile.ZipFile(io.BytesIO(contents))
        names = zf.namelist()
        if "journal.db" not in names:
            raise HTTPException(
                status_code=400,
                detail="Invalid backup file — no journal.db found inside the ZIP.",
            )
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="Invalid ZIP file.")

    # Extract everything
    screenshots_dir = paths.screenshots_dir()
    paths.data_dir().mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(contents)) as zf:
        # Restore database. The old -wal/-shm files belong to the old database;
        # left in place, SQLite could apply them to the restored one.
        db_path = paths.db_path()
        for suffix in ("-wal", "-shm"):
            Path(str(db_path) + suffix).unlink(missing_ok=True)
        tmp_path = db_path.with_name("journal.db.restoring")
        with zf.open("journal.db") as src:
            tmp_path.write_bytes(src.read())
        os.replace(tmp_path, db_path)

        # Restore the encryption key that goes with this database.
        key_restored = ".encryption_key" in names
        if key_restored:
            with zf.open(".encryption_key") as src:
                paths.encryption_key_path().write_bytes(src.read())
            encryption.forget_key()

        # Restore screenshots
        if screenshots_dir.exists():
            shutil.rmtree(screenshots_dir)
        screenshots_dir.mkdir(parents=True, exist_ok=True)

        for name in names:
            if name.startswith("screenshots/") and not name.endswith("/"):
                img_name = Path(name).name
                with zf.open(name) as src:
                    (screenshots_dir / img_name).write_bytes(src.read())

        # Restore extra pairs
        if "extra_pairs.txt" in names:
            with zf.open("extra_pairs.txt") as src:
                paths.extra_pairs_file().write_bytes(src.read())

    # Re-run migrations in case the backup is from an older version
    init_db()

    screenshot_count = len(list(screenshots_dir.iterdir())) if screenshots_dir.exists() else 0
    print(f"[restore] Backup restored: journal.db + {screenshot_count} screenshots")

    message = "Backup restored successfully. Please restart the server."
    keys_ok = encryption.stored_keys_readable()
    if not keys_ok:
        message += (" This backup's exchange API keys can't be read here (it has no matching key file),"
                    " so remove each exchange and add its key again.")
    return {
        "status": "restored",
        "message": message,
        "screenshots_restored": screenshot_count,
        "keys_readable": keys_ok,
    }
