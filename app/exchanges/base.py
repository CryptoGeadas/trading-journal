"""Base class for exchange adapters."""

import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

import ccxt


EXTRA_PAIRS_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "extra_pairs.txt"

QUOTE_CURRENCIES = ["USDT", "USDC", "BTC", "ETH", "USD"]


class ExchangeAdapter(ABC):
    """Each exchange implements its own trade-fetching and permission-checking strategy."""

    exchange_id: str

    @abstractmethod
    def sync_spot(
        self,
        client: ccxt.Exchange,
        since: Optional[int],
        tracked_pairs: set[str],
        extra_pairs: set[str],
    ) -> list[dict]:
        ...

    def sync_futures(
        self,
        client: ccxt.Exchange,
        since: Optional[int],
        tracked_pairs: set[str],
        extra_pairs: set[str],
    ) -> list[dict]:
        return []

    def sync_funding(
        self,
        client: ccxt.Exchange,
        db,
        exchange_id: str,
        since: Optional[int],
    ) -> int:
        return 0

    @abstractmethod
    def check_permissions(self, client: ccxt.Exchange) -> dict:
        ...

    # -- Shared helpers --

    def discover_spot_pairs(
        self,
        client: ccxt.Exchange,
        tracked_pairs: set[str],
        extra_pairs: set[str],
    ) -> set[str]:
        """Build candidate pair set from balance + tracked + extra pairs."""
        client.load_markets()
        candidates = set()

        try:
            balance = client.fetch_balance()
            for currency, amount in balance.get("total", {}).items():
                if amount and float(amount) > 0 and currency not in QUOTE_CURRENCIES:
                    for quote in QUOTE_CURRENCIES:
                        pair = f"{currency}/{quote}"
                        if pair in client.markets:
                            candidates.add(pair)
            print(f"[sync] Balance discovery found {len(candidates)} pairs")
        except Exception as e:
            print(f"[sync] Could not fetch balance for pair discovery: {e}")

        for pair in tracked_pairs:
            if pair in client.markets:
                candidates.add(pair)

        for pair in extra_pairs:
            if pair in client.markets:
                candidates.add(pair)

        return candidates

    def paginate_per_symbol(
        self,
        client: ccxt.Exchange,
        pairs: set[str],
        since: Optional[int],
        limit: int = 100,
        sleep_time: float = 0.1,
    ) -> list[dict]:
        """Fetch trades per-symbol with pagination. Shared by exchanges that lack bulk fetch."""
        all_trades = []
        pairs_with_trades = 0
        checked = 0

        for pair in sorted(pairs):
            checked += 1
            if checked % 50 == 0:
                print(f"[sync] Progress: {checked}/{len(pairs)} pairs checked...")
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
                        time.sleep(sleep_time)
            except (ccxt.BadSymbol, ccxt.BadRequest):
                continue
            except Exception:
                continue
            time.sleep(sleep_time)

        print(f"[sync] Found trades in {pairs_with_trades} pairs, {len(all_trades)} total")
        return all_trades

    @staticmethod
    def load_extra_pairs(futures: bool = False) -> set[str]:
        """Read data/extra_pairs.txt, returning spot or futures pairs."""
        pairs = set()
        if not EXTRA_PAIRS_FILE.exists():
            return pairs
        try:
            for line in EXTRA_PAIRS_FILE.read_text().strip().splitlines():
                pair = line.strip().upper()
                if not pair or "/" not in pair:
                    continue
                is_futures = ":" in pair
                if futures and is_futures:
                    pairs.add(pair)
                elif not futures and not is_futures:
                    pairs.add(pair)
        except Exception as e:
            print(f"[sync] Warning: could not read extra_pairs.txt: {e}")
        return pairs
