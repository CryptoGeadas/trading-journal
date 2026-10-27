"""Demo data endpoints — load, check and remove the demo journal."""

import sqlite3

from fastapi import APIRouter, HTTPException

from app.database import get_db
from app.services import demo

router = APIRouter()


@router.get("/demo")
def demo_status():
    """Whether demo data is loaded (the page shows a banner while it is)."""
    db = get_db()
    try:
        return {"loaded": demo.is_loaded(db)}
    finally:
        db.close()


@router.post("/demo", status_code=201)
def load_demo():
    db = get_db()
    try:
        try:
            counts = demo.load(db)
        except ValueError as e:
            raise HTTPException(status_code=409, detail=str(e))
        except sqlite3.IntegrityError:  # a second click raced the first load
            raise HTTPException(status_code=409, detail="Demo data is already loaded.")
        return {"status": "loaded", **counts}
    finally:
        db.close()


@router.delete("/demo")
def remove_demo(confirm: bool = False):
    if not confirm:
        raise HTTPException(status_code=400, detail="Pass ?confirm=true to remove the demo data.")
    db = get_db()
    try:
        return {"status": "removed", **demo.remove(db)}
    finally:
        db.close()
