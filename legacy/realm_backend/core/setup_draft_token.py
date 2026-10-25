"""Leftover-unshadowed treasury apply.

Host ``main.py`` must import this module — never ``api.setup`` — so a leftover
``api/setup.py`` cannot steal the apply body. Catalog symbols resolve through
the realm's shared-token catalog; no REALMS is invented.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from _cdk import Async
from ic_python_logging import get_logger

from core.setup import get_setup_draft, require_setup_authorized, update_setup_config

logger = get_logger("core.setup_draft_token")

def _load_realm():
    from ggg import Realm

    return Realm.load("1")


def _token_symbol_from_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if not isinstance(value, dict):
        return ""
    for key in ("symbol", "id", "existing"):
        raw = value.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    return ""


def _token_record(value: Any) -> Optional[dict]:
    if value is None:
        return None
    if isinstance(value, str):
        symbol = value.strip()
        return {"symbol": symbol} if symbol else None
    if not isinstance(value, dict):
        return None
    out = dict(value)
    if not (out.get("symbol") or "").strip():
        symbol = _token_symbol_from_value(out)
        if symbol:
            out["symbol"] = symbol
    has_ledger = bool((out.get("token_canister_id") or "").strip())
    has_symbol = bool((out.get("symbol") or "").strip())
    if not has_ledger and not has_symbol:
        return None
    return out


def _complete_catalog_token(token: Any) -> Any:
    if token is None:
        return None
    record = _token_record(token)
    if record is None:
        return None
    completed = dict(record)
    if (completed.get("token_canister_id") or "").strip():
        return completed
    symbol = str(completed.get("symbol") or "").strip()
    if not symbol:
        return completed
    from api.tokens import resolve_shared_token

    catalog = resolve_shared_token(symbol)
    if not catalog:
        return completed
    ledger = (catalog.get("ledger") or "").strip()
    if not ledger:
        return completed
    completed["token_canister_id"] = ledger
    if completed.get("decimals") is None and catalog.get("decimals") is not None:
        completed["decimals"] = catalog["decimals"]
    if not (completed.get("indexer_canister_id") or "").strip() and catalog.get("indexer"):
        completed["indexer_canister_id"] = catalog["indexer"]
    return completed


def _apply_configured_token(realm, params: dict) -> Async[dict]:
    token_canister_id = (params.get("token_canister_id") or "").strip()
    if not token_canister_id:
        return {"success": False, "error": "token_canister_id is required"}

    token_record = {
        "token_canister_id": token_canister_id,
        "symbol": (params.get("symbol") or "").strip(),
        "decimals": params.get("decimals"),
        "indexer_canister_id": (params.get("indexer_canister_id") or "").strip(),
        "token_type": (params.get("token_type") or "realm").strip() or "realm",
    }

    from api.tokens import register_treasury_token, resolve_ledger_token_info

    resolved = yield from resolve_ledger_token_info(token_canister_id)
    if not resolved.get("success"):
        return {
            "success": False,
            "error": resolved.get("error") or "Could not resolve ledger metadata",
            "error_code": "ledger_unresolvable",
        }

    sym = str(resolved["symbol"]).strip()
    decimals = int(resolved["decimals"])
    indexer = str(resolved.get("indexer_canister_id") or token_canister_id).strip()
    token_record["symbol"] = sym
    token_record["decimals"] = decimals
    token_record["indexer_canister_id"] = indexer

    realm.token_canister_id = token_canister_id
    realm.accounting_currency = sym[:16]
    realm.accounting_currency_decimals = decimals
    update_setup_config(realm, {"token": token_record})

    try:
        register_treasury_token(
            symbol=sym,
            ledger_canister_id=token_canister_id,
            indexer_canister_id=indexer,
            decimals=decimals,
            token_type=token_record["token_type"],
        )
    except Exception as token_err:
        logger.warning("setup_draft_token treasury registration failed: %s", token_err)

    return {"success": True, "token": token_record}


def _apply_token_record(realm, token: dict) -> Async[Optional[dict]]:
    token_canister_id = (token.get("token_canister_id") or "").strip()
    if not token_canister_id:
        return None
    result = yield from _apply_configured_token(
        realm,
        {
            "token_canister_id": token_canister_id,
            "symbol": token.get("symbol"),
            "decimals": token.get("decimals"),
            "indexer_canister_id": token.get("indexer_canister_id"),
            "token_type": token.get("token_type"),
        },
    )
    return result


def apply_persisted_draft_if_present() -> Async[dict]:
    """After save_draft persist. Apply when draft.token has a resolvable ledger.

    Null / skipped token is a no-op (does not invent REALMS, does not fail the save).
    """
    realm = _load_realm()
    if not realm:
        return {"success": False, "error": "Realm not found"}
    draft = dict(get_setup_draft(realm))
    completed = _complete_catalog_token(draft.get("token"))
    token = _token_record(completed)
    if token is None:
        return {"success": True, "skipped": True}
    result = yield from _apply_token_record(realm, token)
    if result is None:
        return {"success": True, "skipped": True}
    return result


def apply_setup_draft_token_now() -> Async[str]:
    """Candid ``setup_apply_draft_token`` body. Leftover cannot import-steal this."""
    auth_err = require_setup_authorized()
    if auth_err:
        return json.dumps(auth_err)

    realm = _load_realm()
    if not realm:
        return json.dumps({"success": False, "error": "Realm not found"})

    draft = dict(get_setup_draft(realm))
    completed = _complete_catalog_token(draft.get("token"))
    token = _token_record(completed)
    if token is None:
        return json.dumps({"success": False, "error": "token_canister_id is required"})

    result = yield from _apply_token_record(realm, token)
    if result is None:
        return json.dumps(
            {
                "success": False,
                "error": "Could not apply treasury ledger from draft token",
                "error_code": "draft_token_unapplied",
            }
        )
    return json.dumps(result)
