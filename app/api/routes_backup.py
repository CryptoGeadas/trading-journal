"""Backup and restore endpoints — full journal backup as ZIP."""

import io
import zipfile
import shutil
from pathlib import Path
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, UploadFile, File
from fastapi.responses import StreamingResponse
from app.database import DB_PATH, init_db

router = APIRouter()

DATA_DIR = DB_PATH.parent
SCREENSHOTS_DIR = DATA_DIR / "screenshots"
EXTRA_PAIRS_FILE = DATA_DIR / "extra_pairs.txt"


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

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"journal_backup_{ts}.zip"

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        # Database
        zf.write(str(DB_PATH), "journal.db")

        # Screenshots
        if SCREENSHOTS_DIR.exists():
            for img in SCREENSHOTS_DIR.iterdir():
                if img.is_file():
                    zf.write(str(img), f"screenshots/{img.name}")

        # Extra pairs
        if EXTRA_PAIRS_FILE.exists():
            zf.write(str(EXTRA_PAIRS_FILE), "extra_pairs.txt")

    zip_buffer.seek(0)
    size = len(zip_buffer.getvalue())
    print(f"[backup] Created backup: {filename} ({size} bytes)")

    return StreamingResponse(
        iter([zip_buffer.getvalue()]),
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
