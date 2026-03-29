"""Exchange connector — wraps ccxt with read-only permission validation."""

import ccxt
from typing import Optional


SUPPORTED_EXCHANGES = {
    "binance": ccxt.binance,
    "bybit": ccxt.bybit,
    "gateio": ccxt.gateio,
}

_WRITE_KEY_ERROR = (
    "This API key has trading or withdrawal permissions enabled. "
    "Please create a new key with read-only access."
)


class ExchangeError(Exception):
    """Custom error for exchange operations."""
    pass


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
    """
    Validate that an API key is read-only.

    Returns dict with keys: is_read_only, permissions, error
    """
    result = {"is_read_only": False, "permissions": [], "error": None}

    try:
        # Basic connectivity + read access test
        client.fetch_balance()

        # Exchange-specific permission checks
        checkers = {
            "bybit": _check_bybit,
            "binance": _check_binance,
            "gateio": _check_gateio,
        }
        checker = checkers.get(client.id, _check_fallback)
        result = checker(client)

    except ccxt.AuthenticationError as e:
        result["error"] = f"Authentication failed — check your API key and secret. ({e})"
    except ccxt.PermissionDenied as e:
        result["error"] = f"Permission denied — the key may not have read access. ({e})"
    except ccxt.NetworkError as e:
        result["error"] = f"Network error — could not reach {client.id}. ({e})"
    except Exception as e:
        result["error"] = f"Unexpected error: {type(e).__name__}: {e}"

    return result


def _check_bybit(client: ccxt.Exchange) -> dict:
    """Bybit: query API key info for explicit permission flags."""
    permissions = []
    is_read_only = True

    try:
        response = client.privateGetV5UserQueryApi()
        if response and "result" in response:
            perms = response["result"].get("permissions", {})
            for category, actions in perms.items():
                if actions:
                    permissions.append(f"{category}: {actions}")

            dangerous = ["ContractTrade", "Spot", "Wallet", "Exchange",
                         "NFT", "BlockTrade", "Options"]
            for d in dangerous:
                if d in perms and perms[d]:
                    is_read_only = False
        else:
            permissions = ["read (details unavailable)"]
    except Exception:
        permissions = ["read (basic check only)"]

    return {
        "is_read_only": is_read_only,
        "permissions": permissions,
        "error": None if is_read_only else _WRITE_KEY_ERROR,
    }


def _check_binance(client: ccxt.Exchange) -> dict:
    """Binance: check API restrictions endpoint."""
    permissions = []
    is_read_only = True

    try:
        response = client.sapiGetAccountApiRestrictions()
        if response:
            flags = {
                "enableSpotAndMarginTrading": "Spot Trading",
                "enableFutures": "Futures Trading",
                "enableMargin": "Margin Trading",
                "enableVanillaOptions": "Options Trading",
                "enableWithdrawals": "Withdrawals",
                "enableInternalTransfer": "Internal Transfer",
                "enableReading": "Read",
            }
            for flag, label in flags.items():
                if response.get(flag, False):
                    permissions.append(label)
                    if flag != "enableReading":
                        is_read_only = False
    except Exception:
        permissions = ["read (basic check only)"]

    return {
        "is_read_only": is_read_only,
        "permissions": permissions,
        "error": None if is_read_only else _WRITE_KEY_ERROR,
    }


def _check_gateio(client: ccxt.Exchange) -> dict:
    """Gate.io: limited introspection — verify read, warn about write."""
    return {
        "is_read_only": True,
        "permissions": [
            "read (verified)",
            "write status (unable to verify — ensure your key is read-only)",
        ],
        "error": None,
    }


def _check_fallback(client: ccxt.Exchange) -> dict:
    """Generic fallback."""
    return {
        "is_read_only": True,
        "permissions": ["read (unverified)"],
        "error": None,
    }
