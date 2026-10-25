"""
Token Entity - GGG Standard

Re-exported from ic-basilisk-toolkit (canonical source: ic_basilisk_toolkit/entities.py).
The Basilisk Toolkit Token is the single source of truth for ICRC-1 token registry.
GGG adds convenience properties for realm-specific metadata (symbol, token_type, enabled).

Basilisk OS Token fields (stored in DB):
    name        — Token symbol, also serves as alias (e.g. "ckBTC")
    ledger      — Ledger canister principal ID
    indexer     — Indexer canister principal ID
    decimals    — Number of decimal places (default 8)
    fee         — Default transfer fee (default 10)
    balances    — OneToMany -> WalletBalance
    transfers   — OneToMany -> WalletTransfer

GGG adds (via dynamic properties, stored as _prop_ fields in DB):
    symbol       — Display symbol (defaults to name)
    token_type   — "shared" or "realm"
    enabled      — "true" or "false" for UI display
"""
from ic_basilisk_toolkit.entities import Token  # noqa: F401 — canonical source
from ic_python_logging import get_logger

logger = get_logger("entity.token")


# ---------------------------------------------------------------------------
# Extend Basilisk OS Token with GGG convenience methods
# ---------------------------------------------------------------------------

def _token_symbol_getter(self):
    """Token symbol — defaults to name if not set separately."""
    return getattr(self, '_prop_symbol', None) or self.name

def _token_symbol_setter(self, value):
    self._prop_symbol = value

Token.symbol = property(_token_symbol_getter, _token_symbol_setter)


def _token_type_getter(self):
    return getattr(self, '_prop_token_type', None) or "realm"

def _token_type_setter(self, value):
    self._prop_token_type = value

Token.token_type = property(_token_type_getter, _token_type_setter)


def _token_enabled_getter(self):
    return getattr(self, '_prop_enabled', None) or "true"

def _token_enabled_setter(self, value):
    self._prop_enabled = value

Token.enabled = property(_token_enabled_getter, _token_enabled_setter)


def _token_is_enabled(self) -> bool:
    """Check if this token is enabled for display."""
    return self.enabled == "true"

Token.is_enabled = _token_is_enabled


def _token_get_ledger_config(self) -> dict:
    """Get ledger configuration for API calls."""
    return {
        "symbol": self.symbol,
        "name": self.name,
        "ledger": self.ledger,
        "indexer": self.indexer,
        "decimals": self.decimals,
        "token_type": self.token_type,
    }

Token.get_ledger_config = _token_get_ledger_config


# Backward compat: ledger_canister_id -> ledger, indexer_canister_id -> indexer
def _ledger_canister_id_getter(self):
    return self.ledger

def _ledger_canister_id_setter(self, value):
    self.ledger = value

Token.ledger_canister_id = property(_ledger_canister_id_getter, _ledger_canister_id_setter)


def _indexer_canister_id_getter(self):
    return self.indexer

def _indexer_canister_id_setter(self, value):
    self.indexer = value

Token.indexer_canister_id = property(_indexer_canister_id_getter, _indexer_canister_id_setter)
