"""Exchange connector — wraps ccxt with permission validation."""

import ccxt
from typing import Optional


SUPPORTED_EXCHANGES = {
    "binance": ccxt.binance,
    "gateio": ccxt.gateio,
}


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
    Validate API key permissions.

    Hard-blocks: withdrawals, internal transfers (genuinely dangerous).
    Allows: futures read (Binance bundles read+trade together for futures).
    Warns: spot/margin trading permissions (the app never calls order functions,
           but the key technically has the capability).

    The app's codebase contains NO order placement, modification, or cancellation
    functions — this is the primary safety mechanism. Permission checking is a
    second layer of defense.

    Returns dict with keys: is_read_only, permissions, warnings, error
    """
    result = {"is_read_only": False, "permissions": [], "warnings": [], "error": None}

    try:
        # Basic connectivity + read access test
        client.fetch_balance()

        # Exchange-specific permission checks
        checkers = {
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


def _check_binance(client: ccxt.Exchange) -> dict:
    """
    Binance permission check.

    Hard-blocks: Withdrawals, Internal Transfer (dangerous, never needed).
    Allows with warning: Futures, Spot Trading (may be needed for read access;
        the app contains no order-placement code).
    """
    permissions = []
    warnings = []
    is_read_only = True
    has_dangerous = False

    try:
        response = client.sapiGetAccountApiRestrictions()
        if response:
            # Permissions we detect
            all_flags = {
                "enableReading": "Read",
                "enableSpotAndMarginTrading": "Spot Trading",
                "enableFutures": "Futures",
                "enableMargin": "Margin Trading",
                "enableVanillaOptions": "Options Trading",
                "enableWithdrawals": "Withdrawals",
                "enableInternalTransfer": "Internal Transfer",
            }

            # These are HARD BLOCKED — never needed for reading
            dangerous_flags = {"enableWithdrawals", "enableInternalTransfer"}

            # These are ALLOWED with a warning — may be needed for read access
            # The app contains no order-placement code, so these are safe in practice
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


def _check_gateio(client: ccxt.Exchange) -> dict:
    """Gate.io: limited introspection — verify read, warn about write."""
    return {
        "is_read_only": True,
        "permissions": [
            "read (verified)",
            "write status (unable to verify — ensure your key is read-only)",
        ],
        "warnings": [],
        "error": None,
    }


def _check_fallback(client: ccxt.Exchange) -> dict:
    """Generic fallback."""
    return {
        "is_read_only": True,
        "permissions": ["read (unverified)"],
        "warnings": [],
        "error": None,
    }
