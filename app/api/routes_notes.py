"""Notes endpoints — general notebook for non-trade notes (max 4 files)."""

import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from app.database import get_db

router = APIRouter()

MAX_NOTES = 4


class NoteCreate(BaseModel):
    title: str
    content: str = ""


class NoteUpdate(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None


@router.get("/notes")
async def list_notes():
    """List all notebook entries."""
    db = get_db()
    try:
        rows = db.execute(
            "SELECT * FROM notes ORDER BY updated_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        db.close()


@router.post("/notes", status_code=201)
async def create_note(body: NoteCreate):
    """Create a new note (max 4)."""
    if not body.title.strip():
        raise HTTPException(status_code=400, detail="Title is required.")

    db = get_db()
    try:
        count = db.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
        if count >= MAX_NOTES:
            raise HTTPException(
                status_code=400,
                detail=f"Maximum {MAX_NOTES} notes allowed. Delete one first.",
            )

        note_id = str(uuid.uuid4())[:8]
        now = datetime.now(timezone.utc).isoformat()

        db.execute(
            """INSERT INTO notes (id, title, content, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?)""",
            [note_id, body.title.strip(), body.content, now, now],
        )
        db.commit()

        return {"id": note_id, "title": body.title.strip(), "content": body.content,
                "created_at": now, "updated_at": now}
    finally:
        db.close()


@router.patch("/notes/{note_id}")
async def update_note(note_id: str, body: NoteUpdate):
    """Update a note's title or content."""
    db = get_db()
    try:
        existing = db.execute("SELECT * FROM notes WHERE id = ?", [note_id]).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Note not found")

        updates = {}
        if body.title is not None:
            updates["title"] = body.title.strip()
        if body.content is not None:
            updates["content"] = body.content

        if updates:
            updates["updated_at"] = datetime.now(timezone.utc).isoformat()
            set_clause = ", ".join(f"{k} = ?" for k in updates)
            db.execute(
                f"UPDATE notes SET {set_clause} WHERE id = ?",
                list(updates.values()) + [note_id],
            )
            db.commit()

        return {"status": "updated", "id": note_id}
    finally:
        db.close()


@router.delete("/notes/{note_id}")
async def delete_note(note_id: str):
    """Delete a note."""
    db = get_db()
    try:
        existing = db.execute("SELECT * FROM notes WHERE id = ?", [note_id]).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Note not found")

        db.execute("DELETE FROM notes WHERE id = ?", [note_id])
        db.commit()

        return {"status": "deleted", "id": note_id}
    finally:
        db.close()
