"""Kraken adapter — uses fetch_my_trades(symbol=None) for bulk fetch."""

import time
from typing import Optional

import ccxt

from app.exchanges.base import ExchangeAdapter


class KrakenAdapter(ExchangeAdapter):
    exchange_id = "kraken"

    def sync_spot(self, client, since, tracked_pairs, extra_pairs):
        print("[sync] Kraken: fetching all trades via bulk fetch")
        client.load_markets()

        all_trades = []
        limit = 100
        max_pages = 200
        current_since = since
        page = 0

        while page < max_pages:
            page += 1
            try:
                trades = client.fetch_my_trades(
                    symbol=None, since=current_since, limit=limit
                )
            except (ccxt.BadRequest, ccxt.ArgumentsRequired, TypeError):
                print("[sync] Kraken bulk fetch not supported, falling back to per-symbol")
                candidates = self.discover_spot_pairs(client, tracked_pairs, extra_pairs)
                return self.paginate_per_symbol(client, candidates, since)

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

        print(f"[sync] Kraken: fetched {len(all_trades)} trades")
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
            return self._auth_error(f"Network error — could not reach Kraken. ({e})")
        except Exception as e:
            return self._auth_error(f"Unexpected error: {type(e).__name__}: {e}")

    @staticmethod
    def _auth_error(msg):
        return {"is_read_only": False, "permissions": [], "warnings": [], "error": msg}
