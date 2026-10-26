"""Kraken adapter — uses fetch_my_trades(symbol=None) for bulk fetch."""

import time
from typing import Optional

import ccxt

from app.exchanges.base import CANNOT_CHECK, ExchangeAdapter, SyncCursor


PAGE_SIZE = 50  # trades per TradesHistory answer
MAX_PAGES = 400


class KrakenAdapter(ExchangeAdapter):
    exchange_id = "kraken"

    def sync_spot(self, client, since, tracked_pairs, extra_pairs):
        print("[sync] Kraken: fetching all trades via bulk fetch")
        client.load_markets()

        # One fetch returns every pair, so one start time. An extra pair Kraken
        # lists but never checked yet starts from the start date.
        listed_extra = {p for p in extra_pairs if p in client.markets}
        start = since.bulk_since(False, listed_extra) if isinstance(since, SyncCursor) else since

        # Kraken answers newest first, PAGE_SIZE trades at a time, after `start`;
        # `ofs` walks back through the rest.
        all_trades = []
        offset = 0
        for _ in range(MAX_PAGES):
            try:
                trades = client.fetch_my_trades(symbol=None, since=start, params={"ofs": offset})
            except (ccxt.BadRequest, ccxt.ArgumentsRequired):
                print("[sync] Kraken bulk fetch not supported, falling back to per-symbol")
                candidates = self.discover_spot_pairs(client, tracked_pairs, extra_pairs)
                return self.paginate_per_symbol(client, candidates, since)
            all_trades.extend(trades)
            offset += len(trades)
            if len(trades) < PAGE_SIZE:
                break
            time.sleep(0.1)
        else:
            raise RuntimeError(
                f"Kraken has more than {MAX_PAGES * PAGE_SIZE} trades since the start date; "
                "stopped so nothing is half-synced. Set a later sync start date."
            )

        if isinstance(since, SyncCursor):
            since.checked |= {t["symbol"] for t in all_trades if t.get("symbol")} | listed_extra
        print(f"[sync] Kraken: fetched {len(all_trades)} trades")
        return all_trades

    def check_permissions(self, client):
        try:
            client.fetch_balance()
            return {
                "is_read_only": True,
                "permissions": ["read (verified)", "withdrawal: not checked"],
                # Kraken has no API that reports a key's permissions.
                "warnings": [CANNOT_CHECK.format(name="Kraken")],
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
