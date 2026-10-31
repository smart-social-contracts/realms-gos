"""Realm treasury token and NFT canister resolution + Wallet registration."""

import json
from typing import Optional

from _cdk import (
    Async,
    CallResult,
    Opt,
    Principal,
    Record,
    Service,
    Variant,
    blob,
    nat,
    nat8,
    null,
    service_query,
    service_update,
    text,
)
from ic_python_logging import get_logger

logger = get_logger("api.tokens")


# Candid types for the realm token (fungible) canister's authority interface
# (ERC-3643-style: forced_transfer, freeze_account, unfreeze_account).
class TokenAccount(Record):
    owner: Principal
    subaccount: Opt[blob]


class ForcedTransferArgs(Record):
    from_: TokenAccount
    to: TokenAccount
    amount: nat
    memo: Opt[text]


class FreezeAccountArgs(Record):
    account: TokenAccount
    reason: Opt[text]


class TokenAuthorityError(Variant, total=False):
    Unauthorized: null
    InsufficientBalance: null
    InvalidRecipient: null
    GenericError: text


class TokenAuthorityResult(Variant, total=False):
    Ok: nat
    Err: TokenAuthorityError


_TOKEN_ACCOUNT_TYPE = "record { owner : principal; subaccount : opt blob }"
_FORCED_TRANSFER_ARG_TYPE = (
    f"record {{ from : {_TOKEN_ACCOUNT_TYPE}; to : {_TOKEN_ACCOUNT_TYPE}; "
    "amount : nat; memo : opt text }"
)
_FREEZE_ACCOUNT_ARG_TYPE = f"record {{ account : {_TOKEN_ACCOUNT_TYPE}; reason : opt text }}"
_TOKEN_AUTHORITY_RESULT_TYPE = (
    "variant { Ok : nat; "
    "Err : variant { Unauthorized : null; InsufficientBalance : null; "
    "InvalidRecipient : null; GenericError : text } }"
)


class TokenAuthorityService(Service):
    _arg_types = {
        "forced_transfer": _FORCED_TRANSFER_ARG_TYPE,
        "freeze_account": _FREEZE_ACCOUNT_ARG_TYPE,
        "unfreeze_account": _TOKEN_ACCOUNT_TYPE,
    }
    _return_types = {
        "forced_transfer": _TOKEN_AUTHORITY_RESULT_TYPE,
        "freeze_account": _TOKEN_AUTHORITY_RESULT_TYPE,
        "unfreeze_account": _TOKEN_AUTHORITY_RESULT_TYPE,
    }

    @service_update
    def forced_transfer(self, args: ForcedTransferArgs) -> TokenAuthorityResult:
        ...

    @service_update
    def freeze_account(self, args: FreezeAccountArgs) -> TokenAuthorityResult:
        ...

    @service_update
    def unfreeze_account(self, account: TokenAccount) -> TokenAuthorityResult:
        ...


def _token_authority_error_message(error) -> str:
    if error is None:
        return "Token authority operation failed"

    def _has(key):
        if isinstance(error, dict):
            return key in error
        return hasattr(error, key)

    if _has("Unauthorized"):
        return (
            "Realm is not the ledger authority for this token — it must be the "
            "token canister's authority (or a controller)"
        )
    if _has("InsufficientBalance"):
        return "Source account has insufficient balance"
    if _has("InvalidRecipient"):
        return "Source and destination accounts are the same"
    if _has("GenericError"):
        ge = error["GenericError"] if isinstance(error, dict) else error.GenericError
        return str(ge)
    return str(error)


def _unwrap_token_authority_result(result) -> dict:
    """Unwrap a CallResult[TokenAuthorityResult] from the typed service call."""
    outer_err = None
    if isinstance(result, dict):
        outer_err = result.get("Err")
    elif hasattr(result, "Err"):
        outer_err = result.Err
    if outer_err is not None:
        logger.error(f"Inter-canister token authority call failed: {outer_err}")
        return {"success": False, "error": f"Call failed: {outer_err}"}

    inner = None
    if isinstance(result, dict):
        inner = result.get("Ok")
    elif hasattr(result, "Ok"):
        inner = result.Ok
    if inner is None:
        logger.error(f"Unexpected token authority response (Ok is None): {result}")
        return {"success": False, "error": "Unexpected empty response from token canister"}

    if isinstance(inner, dict):
        if "Ok" in inner:
            return {"success": True, "block_index": str(inner["Ok"])}
        if "Err" in inner:
            return {"success": False, "error": _token_authority_error_message(inner["Err"])}
    elif hasattr(inner, "Ok"):
        return {"success": True, "block_index": str(inner.Ok)}
    elif hasattr(inner, "Err"):
        return {"success": False, "error": _token_authority_error_message(inner.Err)}

    logger.error(f"Unexpected inner token authority result: {inner}")
    return {"success": False, "error": f"Unexpected result from token canister: {inner}"}


def _token_account(principal_text: str) -> dict:
    return {"owner": Principal.from_str(principal_text), "subaccount": None}


def forced_transfer_tokens(
    ledger_canister_id: str,
    from_principal: str,
    to_principal: str,
    amount: int,
    memo: str = "",
):
    """Force-move tokens between two accounts via the realm's ledger authority."""
    logger.info(
        f"Forced token transfer: {from_principal} -> {to_principal}, "
        f"amount={amount}, memo={memo!r}"
    )
    try:
        args = {
            "from_": _token_account(from_principal),
            "to": _token_account(to_principal),
            "amount": amount,
            "memo": memo or None,
        }
        service = TokenAuthorityService(Principal.from_str(ledger_canister_id))
        result: CallResult[TokenAuthorityResult] = yield service.forced_transfer(args)
        logger.info(f"Token forced_transfer result: {result}")
        return _unwrap_token_authority_result(result)
    except Exception as e:
        logger.error(f"Error in forced token transfer: {e}")
        return {"success": False, "error": f"Failed to force-transfer tokens: {e}"}


def freeze_token_account_call(ledger_canister_id: str, principal_text: str, reason: str = ""):
    """Freeze a token account (blocks outgoing transfers) via the ledger authority."""
    logger.info(f"Freeze token account: {principal_text}, reason={reason!r}")
    try:
        args = {"account": _token_account(principal_text), "reason": reason or None}
        service = TokenAuthorityService(Principal.from_str(ledger_canister_id))
        result: CallResult[TokenAuthorityResult] = yield service.freeze_account(args)
        logger.info(f"Token freeze_account result: {result}")
        return _unwrap_token_authority_result(result)
    except Exception as e:
        logger.error(f"Error freezing token account: {e}")
        return {"success": False, "error": f"Failed to freeze account: {e}"}


def unfreeze_token_account_call(ledger_canister_id: str, principal_text: str):
    """Unfreeze a token account via the ledger authority."""
    logger.info(f"Unfreeze token account: {principal_text}")
    try:
        service = TokenAuthorityService(Principal.from_str(ledger_canister_id))
        result: CallResult[TokenAuthorityResult] = yield service.unfreeze_account(
            _token_account(principal_text)
        )
        logger.info(f"Token unfreeze_account result: {result}")
        return _unwrap_token_authority_result(result)
    except Exception as e:
        logger.error(f"Error unfreezing token account: {e}")
        return {"success": False, "error": f"Failed to unfreeze account: {e}"}


def _valid_canister_id(value: str) -> bool:
    value = (value or "").strip()
    if not value or not value.endswith("-cai"):
        return False
    parts = value.split("-")
    if len(parts) < 2:
        return False
    for part in parts[:-1]:
        if len(part) != 5 or not part.isalnum() or not part.islower():
            return False
    return True

def _realm_entity():
    try:
        from ggg import Realm

        return Realm.load("1")
    except Exception as e:
        logger.warning(f"Could not load Realm entity: {e}")
        return None


def get_token_canister_id() -> Optional[str]:
    """Return the realm treasury token ledger ID (set by the installer)."""
    realm = _realm_entity()
    token_id = (getattr(realm, "token_canister_id", "") or "").strip() if realm else ""
    return token_id or None


def get_nft_canister_id() -> Optional[str]:
    """Return the realm land NFT canister ID (set by the installer)."""
    realm = _realm_entity()
    nft_id = (getattr(realm, "nft_canister_id", "") or "").strip() if realm else ""
    return nft_id or None


def shared_token_catalog() -> dict:
    """{symbol: {ledger, indexer, decimals, name?}}: the shared ledgers this realm
    may adopt, handed over by the installer (`set_canister_config_json
    shared_tokens`) from the environment's casals.json. The canister holds no
    per-network table of its own."""
    realm = _realm_entity()
    try:
        catalog = json.loads(getattr(realm, "shared_tokens_json", "") or "{}") if realm else {}
    except (TypeError, ValueError):
        return {}
    return catalog if isinstance(catalog, dict) else {}


def _catalog_entry(symbol: str, cfg: dict) -> dict:
    out = dict(cfg)
    out["symbol"] = symbol
    out.setdefault("indexer", cfg.get("ledger"))
    out.setdefault("name", symbol)
    return out


def resolve_shared_token(symbol: str) -> Optional[dict]:
    """Catalog entry for a symbol (case-insensitive), or None."""
    sym = (symbol or "").strip().upper()
    if not sym:
        return None
    for key, cfg in shared_token_catalog().items():
        if key.upper() == sym and isinstance(cfg, dict):
            return _catalog_entry(key, cfg)
    return None


def resolve_shared_token_by_ledger(ledger_canister_id: str) -> Optional[dict]:
    """Reverse lookup: ledger canister id -> catalog entry, or None."""
    ledger = (ledger_canister_id or "").strip()
    if not ledger:
        return None
    for key, cfg in shared_token_catalog().items():
        if isinstance(cfg, dict) and (cfg.get("ledger") or "").strip() == ledger:
            return _catalog_entry(key, cfg)
    return None


def get_treasury_token_indexer(symbol: str = "", ledger_canister_id: str = "") -> str:
    """Return the registered indexer for the treasury token, else ledger ID."""
    sym = (symbol or "").strip()
    if sym:
        try:
            from ggg import Token

            token = Token[sym]
            if token:
                indexer = (getattr(token, "indexer", "") or "").strip()
                if indexer:
                    return indexer
        except Exception as e:
            logger.warning(f"Could not load Token[{sym}] indexer: {e}")
    ledger = (ledger_canister_id or "").strip()
    if ledger:
        shared = resolve_shared_token_by_ledger(ledger)
        if shared and shared.get("indexer"):
            return str(shared["indexer"])
    return ledger


class Icrc1MetadataService(Service):
    """Minimal ICRC-1 metadata queries against a ledger canister."""

    @service_query
    def icrc1_symbol(self) -> text:
        ...

    @service_query
    def icrc1_name(self) -> text:
        ...

    @service_query
    def icrc1_decimals(self) -> nat8:
        ...


def _unwrap_query_result(result):
    """Normalize a basilisk inter-canister query result."""
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        return result.get("Ok", result.get("ok", result))
    if hasattr(result, "Ok"):
        return result.Ok
    return result


def _indexer_for_ledger(ledger: str) -> str:
    shared = resolve_shared_token_by_ledger(ledger)
    if shared and shared.get("indexer"):
        return str(shared["indexer"]).strip()
    return ledger


def resolve_ledger_token_info(ledger_canister_id: str) -> "Async[dict]":
    """Resolve symbol/decimals/indexer from live ICRC-1 ledger metadata.

    Queries ``icrc1_symbol``, ``icrc1_name``, and ``icrc1_decimals`` on the
    canister. Falls back to the shared-token catalog only when the ledger does
    not expose ICRC-1 metadata (e.g. legacy ledgers).
    """
    ledger = (ledger_canister_id or "").strip()
    if not ledger:
        return {"success": False, "error": "ledger_canister_id is required"}
    if not _valid_canister_id(ledger):
        return {"success": False, "error": "Invalid ledger canister ID format"}

    ledger_error = None
    try:
        service = Icrc1MetadataService(Principal.from_str(ledger))
        symbol_res: CallResult = yield service.icrc1_symbol()
        name_res: CallResult = yield service.icrc1_name()
        decimals_res: CallResult = yield service.icrc1_decimals()

        symbol = str(_unwrap_query_result(symbol_res) or "").strip()
        name = str(_unwrap_query_result(name_res) or "").strip()
        decimals_raw = _unwrap_query_result(decimals_res)
        decimals = int(decimals_raw) if decimals_raw is not None else 8

        if symbol:
            shared = resolve_shared_token_by_ledger(ledger)
            display_name = name or (shared or {}).get("name") or symbol
            return {
                "success": True,
                "ledger_canister_id": ledger,
                "symbol": symbol[:16],
                "name": str(display_name)[:64],
                "decimals": decimals,
                "indexer_canister_id": _indexer_for_ledger(ledger),
                "source": "ledger",
            }
        ledger_error = "empty icrc1_symbol response"
    except Exception as e:
        ledger_error = str(e)
        logger.warning(f"ICRC-1 metadata query failed for {ledger}: {e}")

    shared = resolve_shared_token_by_ledger(ledger)
    symbol = str((shared or {}).get("symbol") or (shared or {}).get("name") or "").strip()
    if symbol:
        decimals = int(shared.get("decimals", 8))
        return {
            "success": True,
            "ledger_canister_id": ledger,
            "symbol": symbol[:16],
            "decimals": decimals,
            "indexer_canister_id": (shared.get("indexer") or ledger).strip(),
            "source": "shared_registry_fallback",
            "warning": ledger_error,
        }

    return {
        "success": False,
        "error": ledger_error or "Could not resolve ledger metadata",
    }


def register_treasury_token(
    symbol: str,
    ledger_canister_id: str,
    indexer_canister_id: str = "",
    decimals: int = 8,
    token_type: str = "realm",
) -> None:
    """Register (or update) the realm treasury token in the Basilisk Wallet."""
    sym = (symbol or "").strip()
    ledger = (ledger_canister_id or "").strip()
    if not sym or not ledger:
        return
    indexer = (indexer_canister_id or ledger).strip()
    try:
        from ggg import Token

        existing = Token[sym]
        if existing:
            existing.ledger = ledger
            existing.indexer = indexer
            existing.decimals = int(decimals)
            existing.symbol = sym
            existing.token_type = token_type
            existing.enabled = "true"
            logger.info(f"Updated treasury token {sym} -> {ledger}")
        else:
            token = Token(
                name=sym,
                ledger=ledger,
                indexer=indexer,
                decimals=int(decimals),
            )
            token.symbol = sym
            token.token_type = token_type
            token.enabled = "true"
            logger.info(f"Registered treasury token {sym} -> {ledger}")

        # The vault focuses on the realm's treasury currency; disable other
        # registered tokens so they are not refreshed by default.
        try:
            for other in Token.instances():
                if other.name != sym and getattr(other, "is_enabled", lambda: True)():
                    other.enabled = "false"
                    logger.info(f"Disabled non-treasury token {other.name}")
        except Exception as disable_err:
            logger.warning(f"Could not disable old tokens: {disable_err}")
    except Exception as e:
        logger.warning(f"Could not register treasury token {sym}: {e}")
