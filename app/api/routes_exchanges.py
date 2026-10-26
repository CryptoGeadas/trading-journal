"""Exchange management endpoints — connect, list, sync, remove."""

import json
import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException
from app.database import get_db
from app.models import ExchangeOut, ExchangeCreate
from cryptography.fernet import InvalidToken

from app import timeutil
from app.encryption import decrypt, encrypt, fingerprint
from app.exchanges.connector import SUPPORTED_EXCHANGES, create_client, validate_read_only, ExchangeError
from app.exchanges.sync import sync_exchange

router = APIRouter()


@router.get("/exchanges")
def list_exchanges(include_manual: bool = False):
    """List all connected exchanges with trade counts (manual sources only when asked)."""
    db = get_db()
    try:
        rows = db.execute(
            """SELECT e.*,
                      COALESCE(tc.cnt, 0) as trade_count
               FROM exchanges e
               LEFT JOIN (
                   SELECT exchange_id, COUNT(*) as cnt FROM trades GROUP BY exchange_id
               ) tc ON tc.exchange_id = e.id
               WHERE e.removed_at IS NULL AND ((e.is_manual = 0 AND e.is_demo = 0) OR ?)
               ORDER BY e.created_at DESC""",
            [int(include_manual)],
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
                sync_start_date=r["sync_start_date"],
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

    Permission model:
    - Hard-blocks keys with Withdrawal or Transfer permissions (never needed).
    - Allows Futures/Spot trading permissions with a warning (Binance requires
      these for reading futures trade history; the app contains no order
      placement code).
    """
    if body.exchange not in SUPPORTED_EXCHANGES:
        raise HTTPException(status_code=400, detail=f"Unsupported exchange. Use: {', '.join(SUPPORTED_EXCHANGES)}")

    try:
        client = create_client(
            exchange=body.exchange,
            api_key=body.api_key,
            api_secret=body.api_secret,
            passphrase=body.passphrase,
        )
    except ExchangeError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Validate permissions
    validation = validate_read_only(body.exchange, client)

    # Hard block: keys with dangerous permissions (withdrawals, transfers)
    if validation.get("error"):
        raise HTTPException(status_code=403, detail=validation["error"])

    # Log warnings (trading permissions that are allowed but noted)
    warnings = validation.get("warnings", [])
    for w in warnings:
        print(f"[exchange] Warning: {w}")

    # Generate a unique ID for this connection
    # The random part keeps two connections added in the same second apart.
    exchange_id = f"{body.exchange}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"

    db = get_db()
    try:
        # Encryption is randomised, so compare the decrypted keys. Connecting
        # one account twice would import every trade twice.
        stored = db.execute(
            "SELECT api_key_enc FROM exchanges WHERE exchange = ? AND removed_at IS NULL AND api_key_enc != ''",
            [body.exchange],
        ).fetchall()
        try:
            existing = any(decrypt(r["api_key_enc"]) == body.api_key for r in stored)
        except InvalidToken:
            raise HTTPException(
                status_code=500,
                detail="Your stored API keys cannot be read with the current .encryption_key file, so the "
                       "app cannot check for a duplicate. Put the original key file back (it is in your backups).",
            )

        if existing:
            raise HTTPException(
                status_code=409,
                detail="This API key is already connected.",
            )

        key_fingerprint = fingerprint(body.api_key)
        removed = db.execute(
            """SELECT id FROM exchanges WHERE exchange = ? AND removed_at IS NOT NULL
               AND api_key_fingerprint = ?""",
            [body.exchange, key_fingerprint],
        ).fetchone()
        if removed:
            # Same account as a removed connection: bring it back, so its kept
            # trades are not imported again under a new connection.
            db.execute(
                """UPDATE exchanges SET removed_at = NULL, api_key_enc = ?, api_secret_enc = ?,
                          passphrase_enc = ?, permissions = ?, label = COALESCE(?, label)
                   WHERE id = ?""",
                [encrypt(body.api_key), encrypt(body.api_secret),
                 encrypt(body.passphrase) if body.passphrase else None,
                 json.dumps(validation["permissions"]), body.label, removed["id"]],
            )
            db.commit()
            return {
                "status": "reconnected",
                "exchange_id": removed["id"],
                "permissions": validation["permissions"],
                "warnings": warnings,
            }

        # Connections removed before fingerprints existed can't be matched.
        unmatched_removed = db.execute(
            """SELECT label FROM exchanges WHERE exchange = ? AND removed_at IS NOT NULL
               AND api_key_fingerprint IS NULL AND is_manual = 0""",
            [body.exchange],
        ).fetchall()
        if unmatched_removed:
            names = ", ".join(r["label"] or body.exchange for r in unmatched_removed)
            warnings = warnings + [
                f"You removed a {body.exchange} connection earlier ({names}). If this key is for the same "
                "account, its trades are now in the journal twice. Remove the duplicate trades or this connection."
            ]

        # Encrypt and store
        db.execute(
            """INSERT INTO exchanges
               (id, exchange, label, api_key_enc, api_secret_enc, passphrase_enc,
                permissions, is_read_only, sync_start_date, api_key_fingerprint, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
            [
                exchange_id,
                body.exchange,
                body.label or f"{body.exchange.title()} Account",
                encrypt(body.api_key),
                encrypt(body.api_secret),
                encrypt(body.passphrase) if body.passphrase else None,
                json.dumps(validation["permissions"]),
                1,  # Always stored as "accepted" — dangerous perms are hard-blocked above
                body.sync_start_date,
                key_fingerprint,
            ],
        )
        db.commit()

        return {
            "status": "connected",
            "exchange_id": exchange_id,
            "permissions": validation["permissions"],
            "warnings": warnings,
        }
    finally:
        db.close()


@router.post("/exchanges/{exchange_id}/sync")
def trigger_sync(exchange_id: str):
    """Trigger a manual sync for an exchange."""
    db = get_db()
    try:
        existing = db.execute(
            "SELECT id FROM exchanges WHERE id = ? AND removed_at IS NULL AND is_manual = 0 AND is_demo = 0", [exchange_id]
        ).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Exchange not found")
    finally:
        db.close()

    result = sync_exchange(exchange_id)

    if result["status"] == "error":
        raise HTTPException(
            status_code=502,
            detail=f"Sync failed: {result['error']}",
        )

    return result


@router.delete("/exchanges/{exchange_id}")
def remove_exchange(exchange_id: str, confirm: bool = False):
    """Remove an exchange connection. Requires confirm=true.

    Its API keys are wiped for good and it disappears from the app; its trades
    are kept (they still reference the connection, so the row stays, marked removed).
    """
    if not confirm:
        raise HTTPException(
            status_code=400,
            detail="Pass ?confirm=true to confirm removal. Trade data will be retained.",
        )
    db = get_db()
    try:
        existing = db.execute(
            "SELECT * FROM exchanges WHERE id = ? AND removed_at IS NULL AND is_manual = 0 AND is_demo = 0", [exchange_id]
        ).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Exchange not found")

        db.execute("DELETE FROM tracked_pairs WHERE exchange_id = ?", [exchange_id])
        key_fingerprint = existing["api_key_fingerprint"]
        if not key_fingerprint and existing["api_key_enc"]:  # connected before fingerprints existed
            key_fingerprint = fingerprint(decrypt(existing["api_key_enc"]))
        db.execute(
            """UPDATE exchanges
               SET api_key_enc = '', api_secret_enc = '', passphrase_enc = NULL,
                   removed_at = ?, api_key_fingerprint = ?
               WHERE id = ?""",
            [timeutil.now_utc(), key_fingerprint, exchange_id],
        )
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
