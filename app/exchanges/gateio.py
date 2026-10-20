"""Gate.io adapter — raw API bulk fetch with per-symbol fallback."""

import time
from datetime import datetime, timezone
from typing import Optional

import ccxt

from app.exchanges.base import ExchangeAdapter


class GateioAdapter(ExchangeAdapter):
    exchange_id = "gateio"

    def sync_spot(self, client, since, tracked_pairs, extra_pairs):
        client.load_markets()

        trades = self._try_bulk_fetch(client, since)
        if trades is not None:
            return trades

        print("[sync] Gate.io bulk fetch failed, falling back to per-symbol")
        candidates = self.discover_spot_pairs(client, tracked_pairs, extra_pairs)
        all_trades = self.paginate_per_symbol(client, candidates, since=None)

        if since:
            filtered = [t for t in all_trades if (t.get("timestamp") or 0) >= since]
            if len(filtered) < len(all_trades):
                print(f"[sync] Filtered to {len(filtered)} trades after start date (from {len(all_trades)} total)")
            return filtered
        return all_trades

    def check_permissions(self, client):
        try:
            client.fetch_balance()
            return {
                "is_read_only": True,
                "permissions": [
                    "read (verified)",
                    "write status (unable to verify — ensure your key is read-only)",
                ],
                "warnings": [],
                "error": None,
            }
        except ccxt.AuthenticationError as e:
            return self._auth_error(f"Authentication failed — check your API key and secret. ({e})")
        except ccxt.PermissionDenied as e:
            return self._auth_error(f"Permission denied — the key may not have read access. ({e})")
        except ccxt.NetworkError as e:
            return self._auth_error(f"Network error — could not reach Gate.io. ({e})")
        except Exception as e:
            return self._auth_error(f"Unexpected error: {type(e).__name__}: {e}")

    def _try_bulk_fetch(self, client, since):
        """Try Gate.io raw API for all trades at once."""
        try:
            print("[sync] Trying Gate.io raw API for all trades...")
            all_trades = []
            page = 1
            limit = 100
            while True:
                params = {"limit": limit, "page": page}
                if since:
                    params["from"] = int(since / 1000)
                response = client.privateGetSpotMyTrades(params)
                if not response or len(response) == 0:
                    break
                for raw_trade in response:
                    try:
                        pair = raw_trade.get("currency_pair", "").replace("_", "/")
                        market = client.market(pair) if pair in client.markets else None
                        parsed = client.parse_trade(raw_trade, market)
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
            return None

        except Exception as e:
            print(f"[sync] Gate.io raw API failed ({e})")
            return None

    @staticmethod
    def _auth_error(msg):
        return {"is_read_only": False, "permissions": [], "warnings": [], "error": msg}
