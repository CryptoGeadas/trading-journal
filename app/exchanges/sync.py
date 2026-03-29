"""Trade sync engine — pulls trades from exchanges with pagination and dedup."""

import uuid
import time
import json
from datetime import datetime, timezone
from typing import Optional

import ccxt

from app.database import get_db
from app.exchanges.connector import create_client, ExchangeError
from app.encryption import decrypt


def sync_exchange(exchange_id: str) -> dict:
    """
    Sync trades for a given exchange connection.

    Returns:
        {"status": "success"|"error", "trades_fetched": int, "error": str|None}
    """
    db = get_db()
    log_id = None

    try:
        # Load exchange config
        row = db.execute(
            "SELECT * FROM exchanges WHERE id = ?", [exchange_id]
        ).fetchone()
        if not row:
            raise ExchangeError(f"Exchange {exchange_id} not found")

        # Start sync log entry
        db.execute(
            "INSERT INTO sync_log (exchange_id, status) VALUES (?, 'running')",
            [exchange_id],
        )
        db.commit()
        log_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]

        # Create ccxt client with decrypted credentials
        client = create_client(
            exchange=row["exchange"],
            api_key=decrypt(row["api_key_enc"]),
            api_secret=decrypt(row["api_secret_enc"]),
            passphrase=decrypt(row["passphrase_enc"]) if row["passphrase_enc"] else None,
        )

        # Determine the starting point for sync
        since = _get_sync_since(db, exchange_id, row["last_sync_at"])

        # Fetch trades with pagination
        all_trades = _fetch_all_trades(client, since)

        # Insert into database (with dedup)
        inserted = _insert_trades(db, exchange_id, row["exchange"], all_trades)

        # Update exchange record
        now = datetime.now(timezone.utc).isoformat()
        db.execute(
            "UPDATE exchanges SET last_sync_at = ?, last_sync_status = 'success' WHERE id = ?",
            [now, exchange_id],
        )

        # Complete sync log
        db.execute(
            """UPDATE sync_log
               SET completed_at = ?, status = 'success', trades_fetched = ?
               WHERE id = ?""",
            [now, inserted, log_id],
        )
        db.commit()

        print(f"[sync] {exchange_id}: fetched {len(all_trades)} trades, inserted {inserted} new")
        return {"status": "success", "trades_fetched": inserted, "error": None}

    except Exception as e:
        error_msg = f"{type(e).__name__}: {e}"
        print(f"[sync] {exchange_id}: error — {error_msg}")

        # Update sync log with error
        if log_id:
            now = datetime.now(timezone.utc).isoformat()
            db.execute(
                """UPDATE sync_log
                   SET completed_at = ?, status = 'error', error_message = ?
                   WHERE id = ?""",
                [now, error_msg[:500], log_id],
            )
            db.execute(
                "UPDATE exchanges SET last_sync_status = 'error' WHERE id = ?",
                [exchange_id],
            )
            db.commit()

        return {"status": "error", "trades_fetched": 0, "error": error_msg}

    finally:
        db.close()


def _get_sync_since(db, exchange_id: str, last_sync_at: Optional[str]) -> Optional[int]:
    """
    Determine the `since` timestamp (ms) for fetching trades.
    - If we have existing trades, start from the latest one.
    - If no trades but a last_sync, use that.
    - Otherwise, fetch all available history (None = exchange default).
    """
    # Check latest trade timestamp for this exchange
    row = db.execute(
        "SELECT MAX(timestamp) as latest FROM trades WHERE exchange_id = ?",
        [exchange_id],
    ).fetchone()

    if row and row["latest"]:
        # Start 1 second after the latest trade to avoid re-fetching it
        dt = datetime.fromisoformat(row["latest"].replace("Z", "+00:00"))
        return int(dt.timestamp() * 1000) + 1000

    # No trades yet — try to go back 1 year for initial sync
    three_years_ago = int((time.time() - 3 * 365 * 86400) * 1000)
    return three_years_ago


def _fetch_all_trades(client: ccxt.Exchange, since: Optional[int]) -> list:
    """
    Fetch all trades using pagination.
    Handles exchange-specific quirks (e.g. Gate.io requires per-symbol queries).
    """
    # Load markets first (required by ccxt)
    client.load_markets()

    # Gate.io requires per-symbol fetching — use smart pair discovery
    if client.id == "gateio":
        print("[sync] Gate.io detected — using per-symbol fetch with pair discovery")
        return _fetch_gateio_trades(client, since)

    # Standard path: fetch all trades at once (Binance, Bybit, etc.)
    all_trades = []
    limit = 100
    max_pages = 200
    current_since = since
    page = 0

    while page < max_pages:
        page += 1

        try:
            trades = client.fetch_my_trades(
                symbol=None,
                since=current_since,
                limit=limit,
            )
        except (ccxt.BadRequest, ccxt.ArgumentsRequired):
            # Exchange requires a symbol — fall back to per-market fetching
            print("[sync] Exchange requires per-symbol fetch — using pair discovery")
            trades = _fetch_discovered_pairs(client, since)
            all_trades.extend(trades)
            break

        if not trades:
            break

        all_trades.extend(trades)

        last_ts = trades[-1]["timestamp"]
        if last_ts == current_since:
            break
        current_since = last_ts + 1

        if len(trades) < limit:
            break

        time.sleep(0.1)

    return all_trades


def _fetch_gateio_trades(client: ccxt.Exchange, since: Optional[int]) -> list:
    """
    Gate.io specific: try the raw API first, then fall back to pair discovery.
    """
    all_trades = []

    # Approach 1: Try Gate.io's raw API endpoint which may support no-symbol queries
    try:
        print("[sync] Trying Gate.io raw API for all trades...")
        page = 1
        limit = 100
        while True:
            response = client.privateGetSpotMyTrades({
                "limit": limit,
                "page": page,
                "from": int(since / 1000) if since else None,
            })
            if not response or len(response) == 0:
                break
            # Parse through ccxt's standard format
            for raw_trade in response:
                try:
                    pair = raw_trade.get("currency_pair", "").replace("_", "/")
                    parsed = client.parse_trade(raw_trade, client.market(pair) if pair in client.markets else None)
                    all_trades.append(parsed)
                except Exception:
                    continue
            if len(response) < limit:
                break
            page += 1
            time.sleep(0.2)

        if all_trades:
            print(f"[sync] Gate.io raw API returned {len(all_trades)} trades")
            return all_trades
    except Exception as e:
        print(f"[sync] Gate.io raw API failed ({e}), falling back to pair discovery")

    # Approach 2: Discover pairs from balance and fetch per-symbol
    # Gate.io doesn't handle the 'since' param well, so skip it
    return _fetch_discovered_pairs(client, since=None)


def _fetch_discovered_pairs(client: ccxt.Exchange, since: Optional[int]) -> list:
    """
    Discover which pairs the user likely traded by checking their balance,
    then fetch trades only for those pairs. Much faster than scanning all markets.
    """
    all_trades = []
    limit = 100

    # Step 1: Get balance to find currencies the user holds (or has held)
    discovered_currencies = set()
    try:
        balance = client.fetch_balance()
        for currency, amounts in balance.get("total", {}).items():
            if amounts and float(amounts) > 0:
                discovered_currencies.add(currency)
        print(f"[sync] Found {len(discovered_currencies)} currencies in balance: {discovered_currencies}")
    except Exception as e:
        print(f"[sync] Could not fetch balance for pair discovery: {e}")

    # Always include common quote currencies
    quote_currencies = ["USDT", "USDC", "BTC", "ETH", "USD"]

    # Step 2: Build candidate pairs
    candidate_pairs = set()
    for base in discovered_currencies:
        if base in quote_currencies:
            continue  # Don't pair USDT/USDT etc.
        for quote in quote_currencies:
            pair = f"{base}/{quote}"
            if pair in client.markets:
                candidate_pairs.add(pair)

    # Also add quote/quote pairs (e.g. BTC/USDT, ETH/USDT)
    for base in ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "AVAX",
                  "DOT", "MATIC", "LINK", "UNI", "ONDO", "AAVE", "OP", "ARB",
                  "PEPE", "SHIB", "WLD", "SUI", "SEI", "TIA", "JUP", "WIF"]:
        for quote in quote_currencies:
            pair = f"{base}/{quote}"
            if pair in client.markets:
                candidate_pairs.add(pair)

    print(f"[sync] Checking {len(candidate_pairs)} candidate pairs for trades...")

    # Step 3: Fetch trades per pair
    pairs_with_trades = 0
    for pair in sorted(candidate_pairs):
        try:
            # Some exchanges (Gate.io) don't handle 'since' well — omit if None
            fetch_kwargs = {"symbol": pair, "limit": limit}
            if since is not None:
                fetch_kwargs["since"] = since
            trades = client.fetch_my_trades(**fetch_kwargs)
            if trades:
                pairs_with_trades += 1
                all_trades.extend(trades)
                print(f"[sync]   {pair}: found {len(trades)} trades")

                # Paginate within this pair
                while len(trades) == limit:
                    last_ts = trades[-1]["timestamp"] + 1
                    trades = client.fetch_my_trades(
                        symbol=pair,
                        since=last_ts,
                        limit=limit,
                    )
                    if trades:
                        all_trades.extend(trades)
                    time.sleep(0.2)

        except (ccxt.BadSymbol, ccxt.BadRequest):
            continue
        except Exception:
            continue
        time.sleep(0.1)  # Rate limit buffer

    print(f"[sync] Found trades in {pairs_with_trades} pairs, {len(all_trades)} total trades")
    return all_trades


def _insert_trades(db, exchange_id: str, exchange: str, trades: list) -> int:
    """
    Insert trades into the database, skipping duplicates.
    Returns the number of newly inserted trades.
    """
    inserted = 0

    for t in trades:
        trade_id = str(uuid.uuid4())
        external_id = str(t.get("id", t.get("order", trade_id)))

        # Parse the symbol (e.g. "ETH/USDT")
        symbol = t.get("symbol", "")
        parts = symbol.split("/") if "/" in symbol else [symbol, ""]
        base = parts[0] if len(parts) > 0 else ""
        quote = parts[1] if len(parts) > 1 else ""

        # Parse fee
        fee_info = t.get("fee", {}) or {}
        fee_cost = fee_info.get("cost", 0) or 0
        fee_currency = fee_info.get("currency", "") or ""

        # Timestamp
        ts = t.get("timestamp", 0)
        if ts:
            dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
            timestamp_str = dt.isoformat()
        else:
            timestamp_str = t.get("datetime", datetime.now(timezone.utc).isoformat())

        # Calculate total
        amount = float(t.get("amount", 0) or 0)
        price = float(t.get("price", 0) or 0)
        cost = float(t.get("cost", 0) or 0)
        total = cost if cost else amount * price

        try:
            db.execute(
                """INSERT INTO trades
                   (id, exchange_id, exchange, external_id, timestamp,
                    pair, base_currency, quote_currency, side,
                    quantity, price, total, fee, fee_currency,
                    trade_type, synced_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
                [
                    trade_id, exchange_id, exchange, external_id, timestamp_str,
                    symbol, base, quote, t.get("side", "buy"),
                    amount, price, total, float(fee_cost), fee_currency,
                    t.get("type", "spot") or "spot",
                ],
            )
            inserted += 1
        except Exception as e:
            # Most likely a UNIQUE constraint violation (duplicate) — skip
            if "UNIQUE" in str(e).upper():
                continue
            else:
                print(f"[sync] Warning: failed to insert trade {external_id}: {e}")
                continue

    if inserted > 0:
        db.commit()

    return inserted
