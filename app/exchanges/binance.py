"""Binance adapter — per-symbol fetch for spot and futures."""

import sqlite3
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

import ccxt

from app import timeutil
from app.exchanges.base import DAY_MS, ExchangeAdapter, QUOTE_CURRENCIES


class BinanceAdapter(ExchangeAdapter):
    exchange_id = "binance"

    def sync_spot(self, client, since, tracked_pairs, extra_pairs):
        print("[sync] Binance: fetching spot trades via per-symbol fetch")
        client.load_markets()
        candidates = self.discover_spot_pairs(client, tracked_pairs, extra_pairs)
        print(f"[sync] Checking {len(candidates)} candidate spot pairs...")
        return self.paginate_per_symbol(client, candidates, since)

    def sync_futures(self, client, since, tracked_pairs, extra_pairs):
        print("[sync] Binance: fetching USDT-M futures trades...")

        futures_client = ccxt.binance({
            "apiKey": client.apiKey,
            "secret": client.secret,
            "enableRateLimit": True,
            "options": {"defaultType": "future"},
        })
        futures_client.load_markets()

        candidates = self._discover_futures_pairs(
            futures_client, tracked_pairs, extra_pairs
        )

        if not candidates:
            print("[sync] No futures pairs to check")
            return []

        print(f"[sync] Checking {len(candidates)} futures pairs...")
        # Binance answers at most 7 days of futures trades per request.
        return self.paginate_per_symbol(futures_client, candidates, since, window_ms=7 * DAY_MS)

    def sync_funding(self, client, db, exchange_id, since):
        """Insert new funding payments. A failed fetch raises; it is never counted as 0."""
        print("[sync] Binance: fetching funding fee history...")
        response = []
        start = since
        while True:  # 1000 rows per request: page until a short page
            params = {"incomeType": "FUNDING_FEE", "limit": 1000}
            if start:
                params["startTime"] = start
            page = client.fapiPrivateGetIncome(params)
            response += page or []
            if not page or len(page) < 1000:
                break
            last = int(page[-1].get("time", 0))
            if not last or last == start:
                break
            start = last  # repeats are skipped by the UNIQUE check below
        if not response:
            print("[sync] No funding fees found")
            return 0

        inserted = 0
        for entry in response:
            fee_id = str(uuid.uuid4())[:12]
            ts = int(entry.get("time", 0))
            if not ts:
                continue
            timestamp_str = timeutil.from_ms(ts)

            symbol_raw = entry.get("symbol", "")
            pair = symbol_raw
            for quote in ["USDT", "BUSD", "USDC"]:
                if symbol_raw.endswith(quote):
                    base = symbol_raw[: -len(quote)]
                    pair = f"{base}/{quote}:{quote}"  # same name as the futures trades
                    break

            amount = float(entry.get("income", 0))
            asset = entry.get("asset", "USDT")

            try:
                db.execute(
                    """INSERT INTO funding_fees
                       (id, exchange_id, exchange, external_id, timestamp,
                        pair, amount, asset, synced_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
                    [
                        fee_id, exchange_id, "binance",
                        str(entry.get("tranId", "")),
                        timestamp_str, pair, amount, asset,
                    ],
                )
                inserted += 1
            except sqlite3.IntegrityError as e:
                if "UNIQUE" in str(e).upper():
                    continue
                raise

        if inserted > 0:
            db.commit()
        print(f"[sync] Inserted {inserted} funding fees")
        return inserted

    def check_permissions(self, client):
        permissions = []
        warnings = []
        is_read_only = True
        has_dangerous = False

        try:
            client.fetch_balance()

            # Fail closed: a key whose permissions can't be read is refused.
            try:
                response = client.sapiGetAccountApiRestrictions()
            except ccxt.AuthenticationError:
                raise  # answered below: bad key, wrong IP or missing permission
            except Exception as e:
                return self._auth_error(
                    "Could not read this key's permissions from Binance, so it was not saved. "
                    f"Try again in a minute. ({type(e).__name__}: {e})"
                )
            if not response or "enableWithdrawals" not in response:
                return self._auth_error(
                    "Binance did not say whether this key can withdraw, so it was not saved. Try again in a minute."
                )
            all_flags = {
                "enableReading": "Read",
                "enableSpotAndMarginTrading": "Spot Trading",
                "enableFutures": "Futures",
                "enableMargin": "Margin Trading",
                "enableVanillaOptions": "Options Trading",
                "enableWithdrawals": "Withdrawals",
                "enableInternalTransfer": "Internal Transfer",
                "permitsUniversalTransfer": "Universal Transfer",
            }
            dangerous_flags = {"enableWithdrawals", "enableInternalTransfer", "permitsUniversalTransfer"}
            trade_flags = {
                "enableSpotAndMarginTrading",
                "enableFutures",
                "enableMargin",
                "enableVanillaOptions",
            }

            for flag, label in all_flags.items():
                if response.get(flag, False):
                    permissions.append(label)
                    if flag in dangerous_flags:
                        has_dangerous = True
                    if flag in trade_flags:
                        is_read_only = False
                        warnings.append(
                            f"{label} permission is enabled. "
                            "This app never places orders, but the key has the capability."
                        )

            if has_dangerous:
                return {
                    "is_read_only": False,
                    "permissions": permissions,
                    "warnings": [],
                    "error": (
                        "This API key has Withdrawal or Transfer permissions enabled. "
                        "These are never needed for a trading journal. "
                        "Please create a new key WITHOUT withdrawal and transfer permissions."
                    ),
                }

            return {
                "is_read_only": is_read_only,
                "permissions": permissions,
                "warnings": warnings,
                "error": None,
            }

        except ccxt.AuthenticationError as e:
            return self._auth_error(f"Authentication failed — check your API key and secret. ({e})")
        except ccxt.PermissionDenied as e:
            return self._auth_error(f"Permission denied — the key may not have read access. ({e})")
        except ccxt.NetworkError as e:
            return self._auth_error(f"Network error — could not reach Binance. ({e})")
        except Exception as e:
            return self._auth_error(f"Unexpected error: {type(e).__name__}: {e}")

    def _discover_futures_pairs(self, futures_client, tracked_pairs, extra_pairs):
        candidates = set()

        # Failures raise: sync_exchange reports futures as not synced.
        balance = futures_client.fetch_balance()
        for currency, amount in balance.get("total", {}).items():
            if amount and float(amount) > 0 and currency not in ("USDT", "USDC", "BUSD"):
                for quote in ["USDT", "USDC"]:
                    pair = f"{currency}/{quote}:{quote}"
                    if pair in futures_client.markets:
                        candidates.add(pair)

        for pos in futures_client.fetch_positions():
            symbol = pos.get("symbol", "")
            if symbol and symbol in futures_client.markets:
                candidates.add(symbol)

        for pair in tracked_pairs:
            if ":" in pair and pair in futures_client.markets:
                candidates.add(pair)

        for pair in extra_pairs:
            if ":" in pair and pair in futures_client.markets:
                candidates.add(pair)

        return candidates

    @staticmethod
    def _auth_error(msg):
        return {"is_read_only": False, "permissions": [], "warnings": [], "error": msg}
