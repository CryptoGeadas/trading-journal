"""Binance adapter — per-symbol fetch for spot and futures."""

import time
import uuid
from datetime import datetime, timezone
from typing import Optional

import ccxt

from app.exchanges.base import ExchangeAdapter, QUOTE_CURRENCIES


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
        return self.paginate_per_symbol(futures_client, candidates, since)

    def sync_funding(self, client, db, exchange_id, since):
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
                if not ts:
                    continue
                timestamp_str = datetime.fromtimestamp(
                    ts / 1000, tz=timezone.utc
                ).isoformat()

                symbol_raw = entry.get("symbol", "")
                pair = symbol_raw
                for quote in ["USDT", "BUSD", "USDC"]:
                    if symbol_raw.endswith(quote):
                        base = symbol_raw[: -len(quote)]
                        pair = f"{base}/{quote}"
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

    def check_permissions(self, client):
        permissions = []
        warnings = []
        is_read_only = True
        has_dangerous = False

        try:
            client.fetch_balance()

            try:
                response = client.sapiGetAccountApiRestrictions()
                if response:
                    all_flags = {
                        "enableReading": "Read",
                        "enableSpotAndMarginTrading": "Spot Trading",
                        "enableFutures": "Futures",
                        "enableMargin": "Margin Trading",
                        "enableVanillaOptions": "Options Trading",
                        "enableWithdrawals": "Withdrawals",
                        "enableInternalTransfer": "Internal Transfer",
                    }
                    dangerous_flags = {"enableWithdrawals", "enableInternalTransfer"}
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
            except Exception:
                permissions = ["read (basic check only)"]

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

        try:
            balance = futures_client.fetch_balance()
            for currency, amount in balance.get("total", {}).items():
                if amount and float(amount) > 0 and currency not in ("USDT", "USDC", "BUSD"):
                    for quote in ["USDT", "USDC"]:
                        pair = f"{currency}/{quote}:{quote}"
                        if pair in futures_client.markets:
                            candidates.add(pair)
        except Exception as e:
            print(f"[sync] Could not fetch futures balance: {e}")

        try:
            positions = futures_client.fetch_positions()
            for pos in positions:
                symbol = pos.get("symbol", "")
                if symbol and symbol in futures_client.markets:
                    candidates.add(symbol)
        except Exception as e:
            print(f"[sync] Could not fetch futures positions: {e}")

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
