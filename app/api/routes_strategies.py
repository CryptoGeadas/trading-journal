"""Strategy management endpoints — create, list, update, delete."""

import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException
from app.database import get_db
from app.models import StrategyOut, StrategyCreate, StrategyUpdate

router = APIRouter()


@router.get("/strategies")
def list_strategies():
    """List all strategies with trade counts."""
    db = get_db()
    try:
        rows = db.execute(
            """SELECT s.*,
                      COALESCE(tc.cnt, 0) as trade_count
               FROM strategies s
               LEFT JOIN (
                   SELECT strategy, COUNT(DISTINCT COALESCE(order_id, id)) as cnt
                   FROM trades
                   WHERE strategy IS NOT NULL
                   GROUP BY strategy
               ) tc ON tc.strategy = s.name
               ORDER BY s.name ASC"""
        ).fetchall()
        return [
            StrategyOut(
                id=r["id"],
                name=r["name"],
                description=r["description"],
                colour=r["colour"],
                playbook=r["playbook"],
                trade_count=r["trade_count"],
                created_at=r["created_at"],
                updated_at=r["updated_at"],
            )
            for r in rows
        ]
    finally:
        db.close()


@router.get("/strategies/{strategy_id}", response_model=StrategyOut)
def get_strategy(strategy_id: str):
    """Get a single strategy by ID."""
    db = get_db()
    try:
        r = db.execute("SELECT * FROM strategies WHERE id = ?", [strategy_id]).fetchone()
        if not r:
            raise HTTPException(status_code=404, detail="Strategy not found")

        # Get trade count
        tc = db.execute(
            "SELECT COUNT(DISTINCT COALESCE(order_id, id)) as cnt FROM trades WHERE strategy = ?",
            [r["name"]],
        ).fetchone()

        return StrategyOut(
            id=r["id"],
            name=r["name"],
            description=r["description"],
            colour=r["colour"],
            playbook=r["playbook"],
            trade_count=tc["cnt"] if tc else 0,
            created_at=r["created_at"],
            updated_at=r["updated_at"],
        )
    finally:
        db.close()


@router.post("/strategies", status_code=201)
def create_strategy(body: StrategyCreate):
    """Create a new strategy."""
    db = get_db()
    try:
        # Check for duplicate name
        existing = db.execute(
            "SELECT id FROM strategies WHERE LOWER(name) = LOWER(?)", [body.name]
        ).fetchone()
        if existing:
            raise HTTPException(status_code=409, detail="A strategy with this name already exists.")

        strategy_id = str(uuid.uuid4())[:8]
        now = datetime.now(timezone.utc).isoformat()

        db.execute(
            """INSERT INTO strategies (id, name, description, colour, playbook, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            [strategy_id, body.name.strip(), body.description, body.colour, body.playbook, now, now],
        )
        db.commit()

        return StrategyOut(
            id=strategy_id,
            name=body.name.strip(),
            description=body.description,
            colour=body.colour,
            playbook=body.playbook,
            trade_count=0,
            created_at=now,
            updated_at=now,
        )
    finally:
        db.close()


@router.patch("/strategies/{strategy_id}")
def update_strategy(strategy_id: str, body: StrategyUpdate):
    """Update a strategy."""
    db = get_db()
    try:
        existing = db.execute("SELECT * FROM strategies WHERE id = ?", [strategy_id]).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Strategy not found")

        old_name = existing["name"]
        updates = {}
        if body.name is not None:
            updates["name"] = body.name.strip()
        if body.description is not None:
            updates["description"] = body.description
        if body.colour is not None:
            updates["colour"] = body.colour
        if body.playbook is not None:
            updates["playbook"] = body.playbook

        if updates:
            updates["updated_at"] = datetime.now(timezone.utc).isoformat()
            set_clause = ", ".join(f"{k} = ?" for k in updates)
            db.execute(
                f"UPDATE strategies SET {set_clause} WHERE id = ?",
                list(updates.values()) + [strategy_id],
            )

            # If name changed, update all trades referencing the old name
            if "name" in updates and updates["name"] != old_name:
                db.execute(
                    "UPDATE trades SET strategy = ? WHERE strategy = ?",
                    [updates["name"], old_name],
                )

            db.commit()

        return {"status": "updated", "id": strategy_id}
    finally:
        db.close()


@router.delete("/strategies/{strategy_id}")
def delete_strategy(strategy_id: str, confirm: bool = False):
    """Delete a strategy. Trades keep their data but strategy tag is cleared."""
    if not confirm:
        raise HTTPException(
            status_code=400,
            detail="Pass ?confirm=true to confirm deletion. Trades will be untagged.",
        )
    db = get_db()
    try:
        existing = db.execute("SELECT * FROM strategies WHERE id = ?", [strategy_id]).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Strategy not found")

        # Clear strategy from trades
        db.execute("UPDATE trades SET strategy = NULL WHERE strategy = ?", [existing["name"]])
        # Delete strategy
        db.execute("DELETE FROM strategies WHERE id = ?", [strategy_id])
        db.commit()

        return {"status": "deleted", "id": strategy_id}
    finally:
        db.close()


@router.get("/strategies-export")
def export_strategies():
    """Export all strategies as JSON for backup."""
    db = get_db()
    try:
        rows = db.execute(
            "SELECT name, description, colour, playbook FROM strategies ORDER BY name"
        ).fetchall()
        strategies = [dict(r) for r in rows]
        return {"strategies": strategies, "count": len(strategies)}
    finally:
        db.close()


@router.post("/strategies-import")
def import_strategies(data: dict):
    """
    Import strategies from a JSON backup.
    Skips strategies whose name already exists.
    """
    strategies = data.get("strategies", [])
    if not strategies:
        raise HTTPException(status_code=400, detail="No strategies found in the import data.")

    db = get_db()
    try:
        imported = 0
        skipped = 0
        for s in strategies:
            name = s.get("name", "").strip()
            if not name:
                continue

            # Check if already exists
            existing = db.execute(
                "SELECT id FROM strategies WHERE LOWER(name) = LOWER(?)", [name]
            ).fetchone()
            if existing:
                skipped += 1
                continue

            strategy_id = str(uuid.uuid4())[:8]
            now = datetime.now(timezone.utc).isoformat()
            db.execute(
                """INSERT INTO strategies (id, name, description, colour, playbook, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                [
                    strategy_id,
                    name,
                    s.get("description", ""),
                    s.get("colour", "#6c9cfc"),
                    s.get("playbook", ""),
                    now,
                    now,
                ],
            )
            imported += 1

        db.commit()
        return {"status": "ok", "imported": imported, "skipped": skipped}
    finally:
        db.close()
