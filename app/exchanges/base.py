"""Base class for exchange adapters."""

import time
from abc import ABC, abstractmethod
from typing import Optional

import ccxt

from app import paths



QUOTE_CURRENCIES = ["USDT", "USDC", "BTC", "ETH", "USD"]

CANNOT_CHECK = (
    "{name} does not let apps check a key's permissions, so this app cannot block a key "
    "that can withdraw. Make sure the key is read-only on {name}."
)


DAY_MS = 86_400_000


class SyncCursor:
    """Where to start fetching, per pair, and what this sync has checked.

    A pair starts from the later of its newest stored trade and the time it
    was last checked completely (duplicates are skipped on insert). A pair
    never checked, e.g. one just added to extra_pairs.txt, starts at the
    start date, so its older history is pulled too.

    Adapters add every pair they fetched to `checked` and every symbol the
    exchange rejected to `skipped`; sync_exchange stores and reports them.
    """

    def __init__(self, start_ms: int, latest_by_pair: dict[str, int] | None = None,
                 checked_by_pair: dict[str, int] | None = None):
        self.start_ms = start_ms
        self.latest_by_pair = latest_by_pair or {}
        self.checked_by_pair = checked_by_pair or {}
        self.checked: set[str] = set()
        self.skipped: dict[str, str] = {}

    def known(self, pair: str) -> bool:
        return pair in self.latest_by_pair or pair in self.checked_by_pair

    def since(self, pair: str) -> int:
        if not self.known(pair):
            return self.start_ms
        return max(self.latest_by_pair.get(pair, 0), self.checked_by_pair.get(pair, 0), self.start_ms)

    def bulk_since(self, futures: bool, must_include: set[str] = frozenset()) -> int:
        """One start time for a fetch that returns every pair of a market at once."""
        pairs = {p for p in set(self.latest_by_pair) | set(self.checked_by_pair) if (":" in p) == futures}
        if not pairs or any(not self.known(p) for p in must_include):
            return self.start_ms
        return max(self.since(p) for p in pairs)


def since_for(since, pair: str) -> Optional[int]:
    """`since` may be a SyncCursor or one plain start time (or None)."""
    return since.since(pair) if isinstance(since, SyncCursor) else since


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

        # A failed balance call (revoked key, network) must fail the sync,
        # not quietly find 0 pairs.
        balance = client.fetch_balance()
        for currency, amount in balance.get("total", {}).items():
            if amount and float(amount) > 0 and currency not in QUOTE_CURRENCIES:
                for quote in QUOTE_CURRENCIES:
                    pair = f"{currency}/{quote}"
                    if pair in client.markets:
                        candidates.add(pair)
        print(f"[sync] Balance discovery found {len(candidates)} pairs")

        for pair in tracked_pairs:
            if ":" not in pair and pair in client.markets:
                candidates.add(pair)

        for pair in extra_pairs:
            if pair in client.markets:
                candidates.add(pair)

        return candidates

    def paginate_per_symbol(
        self,
        client: ccxt.Exchange,
        pairs: set[str],
        since,
        limit: int = 100,
        sleep_time: float = 0.1,
        window_ms: Optional[int] = None,
    ) -> list[dict]:
        """Fetch trades per-symbol with pagination. Shared by exchanges that lack bulk fetch.

        `window_ms`: the longest time span the exchange answers in one request
        (Binance futures: 7 days). The fetch then walks from `since` to now
        one window at a time, so a pair quiet for months is still checked up
        to today.

        Only a symbol the exchange rejects (BadSymbol) is skipped; it is noted
        in the cursor. Any other error (key, permission, network, rate limit,
        bad request) stops the sync.
        """
        all_trades = []
        pairs_with_trades = 0
        now = int(time.time() * 1000)

        for checked, pair in enumerate(sorted(pairs), 1):
            if checked % 50 == 0:
                print(f"[sync] Progress: {checked}/{len(pairs)} pairs checked...")
            pair_since = since_for(since, pair)
            try:
                if window_ms and pair_since is not None:
                    trades = []
                    start = pair_since
                    while start <= now:
                        end = min(start + window_ms - 1, now)
                        trades += self._fetch_pages(client, pair, start, limit, sleep_time, until=end)
                        start = end + 1
                else:
                    trades = self._fetch_pages(client, pair, pair_since, limit, sleep_time)
            except ccxt.BadSymbol as e:
                print(f"[sync]   {pair}: skipped, the exchange rejected the symbol ({e})")
                if isinstance(since, SyncCursor):
                    since.skipped[pair] = str(e)
                continue
            if isinstance(since, SyncCursor):
                since.checked.add(pair)
            if trades:
                pairs_with_trades += 1
                all_trades.extend(trades)
                print(f"[sync]   {pair}: found {len(trades)} trades")
            time.sleep(sleep_time)

        print(f"[sync] Found trades in {pairs_with_trades} pairs, {len(all_trades)} total")
        return all_trades

    @staticmethod
    def _fetch_pages(client, pair, since, limit, sleep_time, until=None) -> list[dict]:
        """All trades of one pair from `since` (to `until`), page by page.

        The next page starts AT the last trade's time, not after it: fills of
        one order often share a millisecond, and starting after it would
        lose the rest. The repeats are dropped here (and by the database).
        """
        params = {"until": until} if until is not None else {}
        found, seen = [], set()
        while True:
            kwargs = {"symbol": pair, "limit": limit, "params": params}
            if since is not None:
                kwargs["since"] = since
            page = client.fetch_my_trades(**kwargs)
            fresh = [t for t in page if (t.get("id"), t.get("timestamp")) not in seen]
            for t in fresh:
                seen.add((t.get("id"), t.get("timestamp")))
            found.extend(fresh)
            if len(page) < limit or not fresh:
                return found
            last = page[-1].get("timestamp")
            if last is None:
                return found
            # A full page of one millisecond: step past it rather than loop.
            since = last if last != since else last + 1
            time.sleep(sleep_time)

    @staticmethod
    def load_extra_pairs(futures: bool = False) -> set[str]:
        """Read extra_pairs.txt from the data folder, returning spot or futures pairs."""
        pairs = set()
        extra_pairs_file = paths.extra_pairs_file()
        if not extra_pairs_file.exists():
            return pairs
        try:
            for line in extra_pairs_file.read_text().strip().splitlines():
                pair = line.strip().upper()
                if not pair or "/" not in pair:
                    continue
                is_futures = ":" in pair
                if futures and is_futures:
                    pairs.add(pair)
                elif not futures and not is_futures:
                    pairs.add(pair)
        except (OSError, UnicodeDecodeError) as e:
            # Skipping the file would quietly leave its pairs out of the sync.
            raise RuntimeError(f"Could not read extra_pairs.txt ({e}). Fix or delete the file.") from e
        return pairs
