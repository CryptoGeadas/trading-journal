"""Backup and restore endpoints — full journal backup as ZIP."""

import io
import zipfile
import shutil
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile, File
from fastapi.responses import StreamingResponse
from app.database import DB_PATH, init_db
from app.services.backup import create_backup_zip, SCREENSHOTS_DIR, EXTRA_PAIRS_FILE

router = APIRouter()


@router.get("/backup")
async def download_backup():
    """
    Download a full backup as a ZIP file containing:
    - journal.db (database)
    - screenshots/ (all chart images)
    - extra_pairs.txt (custom pair list)
    """
    if not DB_PATH.exists():
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
    with zipfile.ZipFile(io.BytesIO(contents)) as zf:
        # Restore database
        with zf.open("journal.db") as src:
            DB_PATH.write_bytes(src.read())

        # Restore screenshots
        if SCREENSHOTS_DIR.exists():
            shutil.rmtree(SCREENSHOTS_DIR)
        SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

        for name in names:
            if name.startswith("screenshots/") and not name.endswith("/"):
                img_name = Path(name).name
                with zf.open(name) as src:
                    (SCREENSHOTS_DIR / img_name).write_bytes(src.read())

        # Restore extra pairs
        if "extra_pairs.txt" in names:
            with zf.open("extra_pairs.txt") as src:
                EXTRA_PAIRS_FILE.write_bytes(src.read())

    # Re-run migrations in case the backup is from an older version
    init_db()

    screenshot_count = len(list(SCREENSHOTS_DIR.iterdir())) if SCREENSHOTS_DIR.exists() else 0
    print(f"[restore] Backup restored: journal.db + {screenshot_count} screenshots")

    return {
        "status": "restored",
        "message": "Backup restored successfully. Please restart the server.",
        "screenshots_restored": screenshot_count,
    }
