"""Emotion tag management endpoints — create, list, update, delete."""

import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException
from app.database import get_db
from app.models import EmotionTagOut, EmotionTagCreate, EmotionTagUpdate

router = APIRouter()


@router.get("/emotion-tags")
def list_emotion_tags():
    """List all emotion tags with trade counts."""
    db = get_db()
    try:
        rows = db.execute(
            """SELECT et.*,
                      COALESCE(tc.cnt, 0) as trade_count
               FROM emotion_tags et
               LEFT JOIN (
                   SELECT emotion_tag, COUNT(DISTINCT COALESCE(order_id, id)) as cnt
                   FROM trades
                   WHERE emotion_tag IS NOT NULL
                   GROUP BY emotion_tag
               ) tc ON tc.emotion_tag = et.name
               ORDER BY et.name ASC"""
        ).fetchall()
        return [
            EmotionTagOut(
                id=r["id"],
                name=r["name"],
                colour=r["colour"],
                is_default=bool(r["is_default"]),
                trade_count=r["trade_count"],
                created_at=r["created_at"],
            )
            for r in rows
        ]
    finally:
        db.close()


@router.get("/emotion-tags/{tag_id}", response_model=EmotionTagOut)
def get_emotion_tag(tag_id: str):
    """Get a single emotion tag by ID."""
    db = get_db()
    try:
        r = db.execute("SELECT * FROM emotion_tags WHERE id = ?", [tag_id]).fetchone()
        if not r:
            raise HTTPException(status_code=404, detail="Emotion tag not found")

        tc = db.execute(
            "SELECT COUNT(DISTINCT COALESCE(order_id, id)) as cnt FROM trades WHERE emotion_tag = ?",
            [r["name"]],
        ).fetchone()

        return EmotionTagOut(
            id=r["id"],
            name=r["name"],
            colour=r["colour"],
            is_default=bool(r["is_default"]),
            trade_count=tc["cnt"] if tc else 0,
            created_at=r["created_at"],
        )
    finally:
        db.close()


@router.post("/emotion-tags", status_code=201)
def create_emotion_tag(body: EmotionTagCreate):
    """Create a new emotion tag."""
    db = get_db()
    try:
        existing = db.execute(
            "SELECT id FROM emotion_tags WHERE LOWER(name) = LOWER(?)", [body.name]
        ).fetchone()
        if existing:
            raise HTTPException(status_code=409, detail="An emotion tag with this name already exists.")

        tag_id = str(uuid.uuid4())[:8]
        now = datetime.now(timezone.utc).isoformat()

        db.execute(
            "INSERT INTO emotion_tags (id, name, colour, is_default, created_at) VALUES (?, ?, ?, 0, ?)",
            [tag_id, body.name.strip(), body.colour, now],
        )
        db.commit()

        return EmotionTagOut(
            id=tag_id,
            name=body.name.strip(),
            colour=body.colour,
            is_default=False,
            trade_count=0,
            created_at=now,
        )
    finally:
        db.close()


@router.patch("/emotion-tags/{tag_id}")
def update_emotion_tag(tag_id: str, body: EmotionTagUpdate):
    """Update an emotion tag."""
    db = get_db()
    try:
        existing = db.execute("SELECT * FROM emotion_tags WHERE id = ?", [tag_id]).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Emotion tag not found")

        old_name = existing["name"]
        updates = {}
        if body.name is not None:
            updates["name"] = body.name.strip()
        if body.colour is not None:
            updates["colour"] = body.colour

        if updates:
            set_clause = ", ".join(f"{k} = ?" for k in updates)
            db.execute(
                f"UPDATE emotion_tags SET {set_clause} WHERE id = ?",
                list(updates.values()) + [tag_id],
            )

            if "name" in updates and updates["name"] != old_name:
                db.execute(
                    "UPDATE trades SET emotion_tag = ? WHERE emotion_tag = ?",
                    [updates["name"], old_name],
                )

            db.commit()

        return {"status": "updated", "id": tag_id}
    finally:
        db.close()


@router.delete("/emotion-tags/{tag_id}")
def delete_emotion_tag(tag_id: str, confirm: bool = False):
    """Delete an emotion tag. Trades keep their data but tag is cleared."""
    if not confirm:
        raise HTTPException(
            status_code=400,
            detail="Pass ?confirm=true to confirm deletion. Trades will be untagged.",
        )
    db = get_db()
    try:
        existing = db.execute("SELECT * FROM emotion_tags WHERE id = ?", [tag_id]).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Emotion tag not found")

        db.execute("UPDATE trades SET emotion_tag = NULL WHERE emotion_tag = ?", [existing["name"]])
        db.execute("DELETE FROM emotion_tags WHERE id = ?", [tag_id])
        db.commit()

        return {"status": "deleted", "id": tag_id}
    finally:
        db.close()


@router.get("/emotion-tags-export")
def export_emotion_tags():
    """Export all emotion tags as JSON for backup."""
    db = get_db()
    try:
        rows = db.execute(
            "SELECT name, colour, is_default FROM emotion_tags ORDER BY name"
        ).fetchall()
        tags = [dict(r) for r in rows]
        return {"emotion_tags": tags, "count": len(tags)}
    finally:
        db.close()


@router.post("/emotion-tags-import")
def import_emotion_tags(data: dict):
    """Import emotion tags from a JSON backup. Skips duplicates."""
    tags = data.get("emotion_tags", [])
    if not tags:
        raise HTTPException(status_code=400, detail="No emotion tags found in the import data.")

    db = get_db()
    try:
        imported = 0
        skipped = 0
        for t in tags:
            name = t.get("name", "").strip()
            if not name:
                continue

            existing = db.execute(
                "SELECT id FROM emotion_tags WHERE LOWER(name) = LOWER(?)", [name]
            ).fetchone()
            if existing:
                skipped += 1
                continue

            tag_id = str(uuid.uuid4())[:8]
            now = datetime.now(timezone.utc).isoformat()
            db.execute(
                "INSERT INTO emotion_tags (id, name, colour, is_default, created_at) VALUES (?, ?, ?, 0, ?)",
                [tag_id, name, t.get("colour", "#6c9cfc"), now],
            )
            imported += 1

        db.commit()
        return {"status": "ok", "imported": imported, "skipped": skipped}
    finally:
        db.close()
