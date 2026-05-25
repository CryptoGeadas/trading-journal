"""Exchange connector — wraps ccxt with permission validation."""

import ccxt
from typing import Optional

from app.exchanges.base import ExchangeAdapter
from app.exchanges.binance import BinanceAdapter
from app.exchanges.gateio import GateioAdapter
from app.exchanges.kraken import KrakenAdapter


SUPPORTED_EXCHANGES = {
    "binance": ccxt.binance,
    "gateio": ccxt.gateio,
    "kraken": ccxt.kraken,
}

ADAPTERS: dict[str, ExchangeAdapter] = {
    "binance": BinanceAdapter(),
    "gateio": GateioAdapter(),
    "kraken": KrakenAdapter(),
}


class ExchangeError(Exception):
    """Custom error for exchange operations."""
    pass


def get_adapter(exchange: str) -> ExchangeAdapter:
    """Get the adapter for a given exchange."""
    adapter = ADAPTERS.get(exchange)
    if not adapter:
        raise ExchangeError(
            f"No adapter for exchange: {exchange}. "
            f"Supported: {', '.join(ADAPTERS.keys())}"
        )
    return adapter


def create_client(
    exchange: str,
    api_key: str,
    api_secret: str,
    passphrase: Optional[str] = None,
) -> ccxt.Exchange:
    """Create a ccxt exchange client instance (synchronous)."""
    if exchange not in SUPPORTED_EXCHANGES:
        raise ExchangeError(
            f"Unsupported exchange: {exchange}. "
            f"Supported: {', '.join(SUPPORTED_EXCHANGES.keys())}"
        )

    cls = SUPPORTED_EXCHANGES[exchange]
    config = {
        "apiKey": api_key,
        "secret": api_secret,
        "enableRateLimit": True,
        "options": {"defaultType": "spot"},
    }
    if passphrase:
        config["password"] = passphrase

    return cls(config)


def validate_read_only(client: ccxt.Exchange) -> dict:
    """Validate API key permissions by delegating to the exchange adapter."""
    adapter = ADAPTERS.get(client.id)
    if adapter:
        return adapter.check_permissions(client)

    # Fallback for unknown exchanges
    try:
        client.fetch_balance()
        return {
            "is_read_only": True,
            "permissions": ["read (unverified)"],
            "warnings": [],
            "error": None,
        }
    except Exception as e:
        return {
            "is_read_only": False,
            "permissions": [],
            "warnings": [],
            "error": f"Unexpected error: {type(e).__name__}: {e}",
        }
