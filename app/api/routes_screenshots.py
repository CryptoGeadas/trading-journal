"""Screenshot endpoints — upload, serve, and delete trade screenshots."""

import uuid
import shutil
from pathlib import Path
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from app.database import get_db

router = APIRouter()

SCREENSHOTS_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "screenshots"
ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB
MAX_PER_TRADE = 2


def _ensure_dir():
    SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)


@router.post("/trades/{trade_id}/screenshots")
async def upload_screenshot(trade_id: str, file: UploadFile = File(...)):
    """Upload a screenshot for a trade (max 2 per order)."""
    _ensure_dir()

    # Validate file extension
    ext = Path(file.filename).suffix.lower() if file.filename else ""
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"File type '{ext}' not allowed. Use: {', '.join(ALLOWED_EXTENSIONS)}",
        )

    # Read file and check size
    contents = await file.read()
    if len(contents) > MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail="File too large. Maximum is 10 MB.")

    db = get_db()
    try:
        # Get the trade and its order_id
        trade = db.execute("SELECT * FROM trades WHERE id = ?", [trade_id]).fetchone()
        if not trade:
            raise HTTPException(status_code=404, detail="Trade not found")

        order_id = trade["order_id"]

        # Check screenshot count for this order/trade
        if order_id:
            count = db.execute(
                "SELECT COUNT(*) FROM trade_screenshots WHERE order_id = ?", [order_id]
            ).fetchone()[0]
        else:
            count = db.execute(
                "SELECT COUNT(*) FROM trade_screenshots WHERE trade_id = ?", [trade_id]
            ).fetchone()[0]

        if count >= MAX_PER_TRADE:
            raise HTTPException(
                status_code=400,
                detail=f"Maximum {MAX_PER_TRADE} screenshots per trade. Delete one first.",
            )

        # Save file
        screenshot_id = str(uuid.uuid4())[:12]
        stored_name = f"{screenshot_id}{ext}"
        file_path = SCREENSHOTS_DIR / stored_name

        with open(file_path, "wb") as f:
            f.write(contents)

        # Save to database
        db.execute(
            """INSERT INTO trade_screenshots (id, trade_id, order_id, filename, original_name, uploaded_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            [screenshot_id, trade_id, order_id, stored_name, file.filename,
             datetime.now(timezone.utc).isoformat()],
        )
        db.commit()

        return {
            "id": screenshot_id,
            "filename": stored_name,
            "original_name": file.filename,
            "url": f"/api/screenshots/{stored_name}",
        }
    finally:
        db.close()


@router.get("/screenshots/{filename}")
async def serve_screenshot(filename: str):
    """Serve a screenshot file."""
    file_path = SCREENSHOTS_DIR / filename
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="Screenshot not found")

    # Security: don't allow path traversal
    if ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    return FileResponse(file_path)


@router.get("/trades/{trade_id}/screenshots")
async def list_screenshots(trade_id: str):
    """List screenshots for a trade (or its order)."""
    db = get_db()
    try:
        # Check if trade exists and get order_id
        trade = db.execute("SELECT * FROM trades WHERE id = ?", [trade_id]).fetchone()
        if not trade:
            raise HTTPException(status_code=404, detail="Trade not found")

        order_id = trade["order_id"]

        # Get screenshots for this order (or trade if no order_id)
        if order_id:
            rows = db.execute(
                "SELECT * FROM trade_screenshots WHERE order_id = ? ORDER BY uploaded_at",
                [order_id],
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT * FROM trade_screenshots WHERE trade_id = ? ORDER BY uploaded_at",
                [trade_id],
            ).fetchall()

        return [
            {
                "id": r["id"],
                "filename": r["filename"],
                "original_name": r["original_name"],
                "url": f"/api/screenshots/{r['filename']}",
                "uploaded_at": r["uploaded_at"],
            }
            for r in rows
        ]
    finally:
        db.close()


@router.delete("/screenshots/{screenshot_id}")
async def delete_screenshot(screenshot_id: str):
    """Delete a screenshot."""
    db = get_db()
    try:
        row = db.execute(
            "SELECT * FROM trade_screenshots WHERE id = ?", [screenshot_id]
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Screenshot not found")

        # Delete file
        file_path = SCREENSHOTS_DIR / row["filename"]
        if file_path.exists():
            file_path.unlink()

        # Delete from database
        db.execute("DELETE FROM trade_screenshots WHERE id = ?", [screenshot_id])
        db.commit()

        return {"status": "deleted", "id": screenshot_id}
    finally:
        db.close()
