"""Exchange management endpoints — connect, list, sync, remove."""

import json
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException
from app.database import get_db
from app.models import ExchangeOut, ExchangeCreate
from app.encryption import encrypt
from app.exchanges.connector import create_client, validate_read_only, ExchangeError
from app.exchanges.sync import sync_exchange

router = APIRouter()


@router.get("/exchanges")
def list_exchanges():
    """List all connected exchanges with trade counts."""
    db = get_db()
    try:
        rows = db.execute(
            """SELECT e.*,
                      COALESCE(tc.cnt, 0) as trade_count
               FROM exchanges e
               LEFT JOIN (
                   SELECT exchange_id, COUNT(*) as cnt FROM trades GROUP BY exchange_id
               ) tc ON tc.exchange_id = e.id
               ORDER BY e.created_at DESC"""
        ).fetchall()
        return [
            ExchangeOut(
                id=r["id"],
                exchange=r["exchange"],
                label=r["label"],
                is_read_only=bool(r["is_read_only"]),
                last_sync_at=r["last_sync_at"],
                last_sync_status=r["last_sync_status"],
                trade_count=r["trade_count"],
                created_at=r["created_at"],
            )
            for r in rows
        ]
    finally:
        db.close()


@router.post("/exchanges", status_code=201)
def add_exchange(body: ExchangeCreate):
    """
    Add a new exchange connection.
    1. Creates a ccxt client with the provided credentials.
    2. Validates the key is read-only.
    3. Encrypts and stores the credentials.
    """
    # Validate exchange name
    if body.exchange not in ("binance", "bybit", "gateio"):
        raise HTTPException(status_code=400, detail="Unsupported exchange. Use: binance, bybit, gateio")

    # Create ccxt client and validate permissions
    try:
        client = create_client(
            exchange=body.exchange,
            api_key=body.api_key,
            api_secret=body.api_secret,
            passphrase=body.passphrase,
        )
    except ExchangeError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Validate read-only permissions
    validation = validate_read_only(client)

    if validation["error"] and not validation["is_read_only"]:
        raise HTTPException(
            status_code=403,
            detail=validation["error"],
        )

    if validation["error"]:
        # Non-fatal warning (e.g. couldn't fully verify Gate.io)
        print(f"[exchange] Warning during validation: {validation['error']}")

    # Generate a unique ID for this connection
    exchange_id = f"{body.exchange}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"

    # Check for duplicate
    db = get_db()
    try:
        existing = db.execute(
            "SELECT id FROM exchanges WHERE exchange = ? AND api_key_enc = ?",
            [body.exchange, encrypt(body.api_key)],
        ).fetchone()

        if existing:
            raise HTTPException(
                status_code=409,
                detail="This API key is already connected.",
            )

        # Encrypt and store
        db.execute(
            """INSERT INTO exchanges
               (id, exchange, label, api_key_enc, api_secret_enc, passphrase_enc,
                permissions, is_read_only, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
            [
                exchange_id,
                body.exchange,
                body.label or f"{body.exchange.title()} Account",
                encrypt(body.api_key),
                encrypt(body.api_secret),
                encrypt(body.passphrase) if body.passphrase else None,
                json.dumps(validation["permissions"]),
                1 if validation["is_read_only"] else 0,
            ],
        )
        db.commit()

        return {
            "status": "connected",
            "exchange_id": exchange_id,
            "is_read_only": validation["is_read_only"],
            "permissions": validation["permissions"],
        }
    finally:
        db.close()


@router.post("/exchanges/{exchange_id}/sync")
def trigger_sync(exchange_id: str):
    """Trigger a manual sync for an exchange."""
    db = get_db()
    try:
        existing = db.execute(
            "SELECT id FROM exchanges WHERE id = ?", [exchange_id]
        ).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Exchange not found")
    finally:
        db.close()

    # Run sync (this is synchronous — blocks until complete)
    result = sync_exchange(exchange_id)

    if result["status"] == "error":
        raise HTTPException(
            status_code=502,
            detail=f"Sync failed: {result['error']}",
        )

    return result


@router.delete("/exchanges/{exchange_id}")
def remove_exchange(exchange_id: str, confirm: bool = False):
    """Remove an exchange connection. Requires confirm=true. Keeps trade data."""
    if not confirm:
        raise HTTPException(
            status_code=400,
            detail="Pass ?confirm=true to confirm removal. Trade data will be retained.",
        )
    db = get_db()
    try:
        existing = db.execute(
            "SELECT * FROM exchanges WHERE id = ?", [exchange_id]
        ).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Exchange not found")

        db.execute("DELETE FROM exchanges WHERE id = ?", [exchange_id])
        db.commit()
        return {"status": "removed", "exchange_id": exchange_id}
    finally:
        db.close()


@router.get("/sync-log")
def get_sync_log(exchange_id: str = None, limit: int = 20):
    """Recent sync history."""
    db = get_db()
    try:
        if exchange_id:
            rows = db.execute(
                "SELECT * FROM sync_log WHERE exchange_id = ? ORDER BY started_at DESC LIMIT ?",
                [exchange_id, limit],
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT * FROM sync_log ORDER BY started_at DESC LIMIT ?", [limit]
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        db.close()
