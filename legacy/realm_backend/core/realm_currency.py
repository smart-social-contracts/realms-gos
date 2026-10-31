"""The realm's treasury currency symbol — one resolver for the whole canister.

The symbol is derived from the treasury ledger canister's ICRC-1 metadata and
cached on ``Realm.accounting_currency``. It is empty until a ledger has been
configured and resolved, so callers that denominate or move value must refuse
rather than substitute a symbol: a fabricated default names a token the realm
never chose, and in the worst case moves funds on a ledger nobody configured.
"""

from ic_python_logging import get_logger

logger = get_logger("core.realm_currency")

NO_TREASURY_TOKEN = "no_treasury_token"

_NO_TREASURY_MESSAGE = (
    "No treasury currency — set the treasury ledger canister in Realm Settings "
    "so the token symbol can be resolved"
)


def realm_currency() -> str:
    """The treasury token symbol, empty until a treasury ledger resolves."""
    try:
        from ggg import Realm

        realm = Realm.load("1")
        if realm:
            return str(getattr(realm, "accounting_currency", "") or "").strip()
    except Exception as e:
        logger.warning(f"Could not read the realm accounting currency: {e}")
    return ""


def no_treasury_token_error() -> dict:
    """The refusal payload for operations that need a resolved treasury token."""
    return {"error": _NO_TREASURY_MESSAGE, "error_code": NO_TREASURY_TOKEN}


def no_treasury_token_wallet_error() -> dict:
    """The same refusal for wallet paths, which report failure as ``err``."""
    return {"err": _NO_TREASURY_MESSAGE, "error_code": NO_TREASURY_TOKEN}


def require_realm_currency() -> str:
    """The treasury token symbol, raising when none has resolved yet."""
    currency = realm_currency()
    if not currency:
        raise ValueError(_NO_TREASURY_MESSAGE)
    return currency
