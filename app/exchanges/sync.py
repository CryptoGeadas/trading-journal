"""Trade sync engine — pulls trades from exchanges with pagination and dedup."""

import uuid
import time
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import ccxt

from app.database import get_db
from app.exchanges.connector import create_client, ExchangeError
from app.encryption import decrypt


def sync_exchange(exchange_id: str) -> dict:
    """Sync trades for a given exchange connection."""
    db = get_db()
    log_id = None

    try:
        row = db.execute(
            "SELECT * FROM exchanges WHERE id = ?", [exchange_id]
        ).fetchone()
        if not row:
            raise ExchangeError(f"Exchange {exchange_id} not found")

        db.execute(
            "INSERT INTO sync_log (exchange_id, status) VALUES (?, 'running')",
            [exchange_id],
        )
        db.commit()
        log_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]

        client = create_client(
            exchange=row["exchange"],
            api_key=decrypt(row["api_key_enc"]),
            api_secret=decrypt(row["api_secret_enc"]),
            passphrase=decrypt(row["passphrase_enc"]) if row["passphrase_enc"] else None,
        )

        since = _get_sync_since(db, exchange_id, row["last_sync_at"])

        # Fetch spot trades
        all_trades = _fetch_all_trades(client, since)

        # Fetch futures trades + funding fees (Binance only)
        futures_trades = []
        funding_count = 0
        if row["exchange"] == "binance":
            futures_trades = _fetch_binance_futures(client, since)
            funding_count = _sync_funding_fees(client, db, exchange_id, row["exchange"], since)

        inserted_spot = _insert_trades(db, exchange_id, row["exchange"], all_trades, "spot")
        inserted_futures = _insert_trades(db, exchange_id, row["exchange"], futures_trades, "futures")
        inserted = inserted_spot + inserted_futures

        now = datetime.now(timezone.utc).isoformat()
        db.execute(
            "UPDATE exchanges SET last_sync_at = ?, last_sync_status = 'success' WHERE id = ?",
            [now, exchange_id],
        )
        db.execute(
            """UPDATE sync_log SET completed_at = ?, status = 'success', trades_fetched = ?
               WHERE id = ?""",
            [now, inserted, log_id],
        )
        db.commit()

        parts = []
        if inserted_spot: parts.append(f"{inserted_spot} spot")
        if inserted_futures: parts.append(f"{inserted_futures} futures")
        if funding_count: parts.append(f"{funding_count} funding fees")
        detail = ", ".join(parts) if parts else "0 new"
        print(f"[sync] {exchange_id}: {detail}")
        return {"status": "success", "trades_fetched": inserted, "error": None}

    except Exception as e:
        error_msg = f"{type(e).__name__}: {e}"
        print(f"[sync] {exchange_id}: error — {error_msg}")

        if log_id:
            now = datetime.now(timezone.utc).isoformat()
            db.execute(
                """UPDATE sync_log SET completed_at = ?, status = 'error', error_message = ?
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
    row = db.execute(
        "SELECT MAX(timestamp) as latest FROM trades WHERE exchange_id = ?",
        [exchange_id],
    ).fetchone()

    if row and row["latest"]:
        dt = datetime.fromisoformat(row["latest"].replace("Z", "+00:00"))
        return int(dt.timestamp() * 1000) + 1000

    start_of_year = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    return start_of_year


def _fetch_all_trades(client: ccxt.Exchange, since: Optional[int]) -> list:
    client.load_markets()

    if client.id == "gateio":
        print("[sync] Gate.io detected — using per-symbol fetch with pair discovery")
        return _fetch_gateio_trades(client, since)

    if client.id == "binance":
        print("[sync] Binance detected — using per-symbol fetch with pair discovery")
        return _fetch_discovered_pairs(client, since)

    all_trades = []
    limit = 100
    max_pages = 200
    current_since = since
    page = 0

    while page < max_pages:
        page += 1
        try:
            trades = client.fetch_my_trades(symbol=None, since=current_since, limit=limit)
        except (ccxt.BadRequest, ccxt.ArgumentsRequired, TypeError):
            print("[sync] Exchange requires per-symbol fetch — using pair discovery")
            trades = _fetch_discovered_pairs(client, since)
            all_trades.extend(trades)
            break

        if not trades:
            break
        all_trades.extend(trades)
        last_ts = trades[-1].get("timestamp")
        if not last_ts or last_ts == current_since:
            break
        current_since = last_ts + 1
        if len(trades) < limit:
            break
        time.sleep(0.1)

    return all_trades


def _fetch_gateio_trades(client: ccxt.Exchange, since: Optional[int]) -> list:
    all_trades = []
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

    trades = _fetch_discovered_pairs(client, since=None)
    start_of_year_ms = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    filtered = [t for t in trades if (t.get("timestamp") or 0) >= start_of_year_ms]
    if len(filtered) < len(trades):
        print(f"[sync] Filtered to {len(filtered)} trades in 2026 (from {len(trades)} total)")
    return filtered


def _fetch_discovered_pairs(client: ccxt.Exchange, since: Optional[int]) -> list:
    all_trades = []
    limit = 100
    discovered_currencies = set()

    try:
        balance = client.fetch_balance()
        for currency, amounts in balance.get("total", {}).items():
            if amounts and float(amounts) > 0:
                discovered_currencies.add(currency)
        print(f"[sync] Found {len(discovered_currencies)} currencies in balance: {discovered_currencies}")
    except Exception as e:
        print(f"[sync] Could not fetch balance for pair discovery: {e}")

    quote_currencies = ["USDT", "USDC", "BTC", "ETH", "USD"]
    candidate_pairs = set()

    for base in discovered_currencies:
        if base in quote_currencies:
            continue
        for quote in quote_currencies:
            pair = f"{base}/{quote}"
            if pair in client.markets:
                candidate_pairs.add(pair)

    for base in ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "AVAX",
                  "DOT", "MATIC", "LINK", "UNI", "ONDO", "AAVE", "OP", "ARB",
                  "PEPE", "SHIB", "WLD", "SUI", "SEI", "TIA", "JUP", "WIF"]:
        for quote in quote_currencies:
            pair = f"{base}/{quote}"
            if pair in client.markets:
                candidate_pairs.add(pair)

    extra_pairs_file = Path(__file__).resolve().parent.parent.parent / "data" / "extra_pairs.txt"
    if extra_pairs_file.exists():
        try:
            lines = extra_pairs_file.read_text().strip().splitlines()
            for line in lines:
                pair = line.strip().upper()
                if "/" in pair and ":" not in pair and pair in client.markets:
                    candidate_pairs.add(pair)
                    print(f"[sync]   Added extra pair: {pair}")
        except Exception as e:
            print(f"[sync] Warning: could not read extra_pairs.txt: {e}")

    print(f"[sync] Checking {len(candidate_pairs)} candidate pairs for trades...")

    pairs_with_trades = 0
    checked = 0
    for pair in sorted(candidate_pairs):
        checked += 1
        if checked % 50 == 0:
            print(f"[sync] Progress: {checked}/{len(candidate_pairs)} pairs checked...")
        try:
            fetch_kwargs = {"symbol": pair, "limit": limit}
            if since is not None:
                fetch_kwargs["since"] = since
            trades = client.fetch_my_trades(**fetch_kwargs)
            if trades:
                pairs_with_trades += 1
                all_trades.extend(trades)
                print(f"[sync]   {pair}: found {len(trades)} trades")
                while len(trades) == limit:
                    last_ts = trades[-1]["timestamp"] + 1
                    trades = client.fetch_my_trades(symbol=pair, since=last_ts, limit=limit)
                    if trades:
                        all_trades.extend(trades)
                    time.sleep(0.2)
        except (ccxt.BadSymbol, ccxt.BadRequest):
            continue
        except Exception:
            continue
        time.sleep(0.1)

    print(f"[sync] Found trades in {pairs_with_trades} pairs, {len(all_trades)} total trades")
    return all_trades


# ---- Binance Futures ----

def _fetch_binance_futures(client: ccxt.Exchange, since: Optional[int]) -> list:
    print("[sync] Binance: fetching USDT-M futures trades...")

    futures_client = ccxt.binance({
        "apiKey": client.apiKey,
        "secret": client.secret,
        "enableRateLimit": True,
        "options": {"defaultType": "future"},
    })
    futures_client.load_markets()

    candidate_pairs = set()

    try:
        balance = futures_client.fetch_balance()
        for currency, amounts in balance.get("total", {}).items():
            if amounts and float(amounts) > 0 and currency not in ("USDT", "USDC", "BUSD"):
                # Check both USDT and USDC margined
                for quote in ["USDT", "USDC"]:
                    pair = f"{currency}/{quote}:{quote}"
                    if pair in futures_client.markets:
                        candidate_pairs.add(pair)
    except Exception as e:
        print(f"[sync] Could not fetch futures balance: {e}")

    try:
        positions = futures_client.fetch_positions()
        for pos in positions:
            symbol = pos.get("symbol", "")
            if symbol and symbol in futures_client.markets:
                candidate_pairs.add(symbol)
    except Exception as e:
        print(f"[sync] Could not fetch futures positions: {e}")

    for base in ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "AVAX",
                  "DOT", "MATIC", "LINK", "UNI", "ONDO", "AAVE", "OP", "ARB",
                  "PEPE", "SHIB", "WLD", "SUI", "SEI", "TIA", "JUP", "WIF",
                  "DYDX", "ORDI", "WOO", "EIGEN"]:
        # Check both USDT and USDC margined
        for quote in ["USDT", "USDC"]:
            pair = f"{base}/{quote}:{quote}"
            if pair in futures_client.markets:
                candidate_pairs.add(pair)

    extra_pairs_file = Path(__file__).resolve().parent.parent.parent / "data" / "extra_pairs.txt"
    if extra_pairs_file.exists():
        try:
            lines = extra_pairs_file.read_text().strip().splitlines()
            for line in lines:
                pair = line.strip().upper()
                if (":" in pair) and pair in futures_client.markets:
                    candidate_pairs.add(pair)
        except Exception:
            pass

    if not candidate_pairs:
        print("[sync] No futures pairs to check")
        return []

    print(f"[sync] Checking {len(candidate_pairs)} futures pairs...")

    all_trades = []
    limit = 100
    pairs_with_trades = 0

    for pair in sorted(candidate_pairs):
        try:
            fetch_kwargs = {"symbol": pair, "limit": limit}
            if since is not None:
                fetch_kwargs["since"] = since
            trades = futures_client.fetch_my_trades(**fetch_kwargs)
            if trades:
                pairs_with_trades += 1
                all_trades.extend(trades)
                print(f"[sync]   {pair}: found {len(trades)} futures trades")
                while len(trades) == limit:
                    last_ts = trades[-1]["timestamp"] + 1
                    trades = futures_client.fetch_my_trades(symbol=pair, since=last_ts, limit=limit)
                    if trades:
                        all_trades.extend(trades)
                    time.sleep(0.2)
        except (ccxt.BadSymbol, ccxt.BadRequest):
            continue
        except Exception:
            continue
        time.sleep(0.1)

    print(f"[sync] Found futures trades in {pairs_with_trades} pairs, {len(all_trades)} total")
    return all_trades


def _sync_funding_fees(client: ccxt.Exchange, db, exchange_id: str, exchange: str, since: Optional[int]) -> int:
    print("[sync] Binance: fetching funding fee history...")
    try:
        params = {"incomeType": "FUNDING_FEE", "limit": 1000}
        if since:
            params["startTime"] = since

        response = client.fapiPrivateGetIncome(params)

        if not response:
            print("[sync] No funding fees found")
            return 0

        inserted = 0
        for entry in response:
            fee_id = str(uuid.uuid4())[:12]
            ts = int(entry.get("time", 0))
            timestamp_str = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).isoformat() if ts else None
            if not timestamp_str:
                continue

            symbol_raw = entry.get("symbol", "")
            pair = symbol_raw
            for quote in ["USDT", "BUSD", "USDC"]:
                if symbol_raw.endswith(quote):
                    base = symbol_raw[:-len(quote)]
                    pair = f"{base}/{quote}"
                    break

            amount = float(entry.get("income", 0))
            asset = entry.get("asset", "USDT")

            try:
                db.execute(
                    """INSERT INTO funding_fees
                       (id, exchange_id, exchange, external_id, timestamp, pair, amount, asset, synced_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
                    [fee_id, exchange_id, exchange, str(entry.get("tranId", "")),
                     timestamp_str, pair, amount, asset],
                )
                inserted += 1
            except Exception as e:
                if "UNIQUE" in str(e).upper():
                    continue

        if inserted > 0:
            db.commit()
        print(f"[sync] Inserted {inserted} funding fees")
        return inserted

    except Exception as e:
        print(f"[sync] Funding fee fetch failed: {e}")
        return 0


# ---- Insert Trades ----

def _insert_trades(db, exchange_id: str, exchange: str, trades: list, trade_type: str = "spot") -> int:
    inserted = 0
    for t in trades:
        trade_id = str(uuid.uuid4())
        external_id = str(t.get("id", t.get("order", trade_id)))
        order_id = str(t.get("order", "")) if t.get("order") else None

        symbol = t.get("symbol", "")
        display_symbol = symbol.split(":")[0] if ":" in symbol else symbol
        parts = display_symbol.split("/") if "/" in display_symbol else [display_symbol, ""]
        base = parts[0] if len(parts) > 0 else ""
        quote = parts[1] if len(parts) > 1 else ""

        fee_info = t.get("fee", {}) or {}
        fee_cost = fee_info.get("cost", 0) or 0
        fee_currency = fee_info.get("currency", "") or ""

        ts = t.get("timestamp", 0)
        if ts:
            dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
            timestamp_str = dt.isoformat()
        else:
            timestamp_str = t.get("datetime", datetime.now(timezone.utc).isoformat())

        amount = float(t.get("amount", 0) or 0)
        price = float(t.get("price", 0) or 0)
        cost = float(t.get("cost", 0) or 0)
        total = cost if cost else amount * price

        try:
            db.execute(
                """INSERT INTO trades
                   (id, exchange_id, exchange, external_id, order_id, timestamp,
                    pair, base_currency, quote_currency, side,
                    quantity, price, total, fee, fee_currency,
                    trade_type, synced_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
                [trade_id, exchange_id, exchange, external_id, order_id, timestamp_str,
                 display_symbol, base, quote, t.get("side", "buy"),
                 amount, price, total, float(fee_cost), fee_currency, trade_type],
            )
            inserted += 1
        except Exception as e:
            if "UNIQUE" in str(e).upper():
                continue
            else:
                print(f"[sync] Warning: failed to insert trade {external_id}: {e}")

    if inserted > 0:
        db.commit()
    return inserted
