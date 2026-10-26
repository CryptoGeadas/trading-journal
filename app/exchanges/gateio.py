"""Gate.io adapter — per-symbol fetch."""

import ccxt

from app.exchanges.base import CANNOT_CHECK, DAY_MS, ExchangeAdapter


class GateioAdapter(ExchangeAdapter):
    exchange_id = "gateio"

    def sync_spot(self, client, since, tracked_pairs, extra_pairs):
        client.load_markets()
        candidates = self.discover_spot_pairs(client, tracked_pairs, extra_pairs)
        # Gate.io pages forward from `from`; walk in 7-day windows so the
        # range of each request stays small.
        return self.paginate_per_symbol(client, candidates, since, window_ms=7 * DAY_MS)

    def check_permissions(self, client):
        try:
            client.fetch_balance()
            return {
                "is_read_only": True,
                "permissions": ["read (verified)", "withdrawal: not checked"],
                # Gate.io has no API that reports a key's permissions.
                "warnings": [CANNOT_CHECK.format(name="Gate.io")],
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
