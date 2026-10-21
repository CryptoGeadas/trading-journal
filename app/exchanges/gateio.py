"""Gate.io adapter — per-symbol fetch."""

import ccxt

from app.exchanges.base import ExchangeAdapter


class GateioAdapter(ExchangeAdapter):
    exchange_id = "gateio"

    def sync_spot(self, client, since, tracked_pairs, extra_pairs):
        client.load_markets()
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

    @staticmethod
    def _auth_error(msg):
        return {"is_read_only": False, "permissions": [], "warnings": [], "error": msg}
