"""Realm self-upgrade API.

Handles requesting upgrades from the registry and checking status.
"""

import json
from typing import Dict

from ggg import Realm
from _cdk import (
    Async, CallResult, Principal, Record, Service, Variant,
    ic, nat64, service_query, service_update, text,
)
from ic_python_logging import get_logger

logger = get_logger("api.upgrade")


class UserCreditsRecord(Record):
    principal_id: text
    balance: nat64
    total_purchased: nat64
    total_spent: nat64


class GetCreditsResult(Variant, total=False):
    Ok: UserCreditsRecord
    Err: text


class UpgradeResult(Variant, total=False):
    Ok: text
    Err: text


class RealmRegistryUpgradeService(Service):
    @service_update
    def request_upgrade(self, args_json: text) -> text: ...

    @service_query
    def get_credits(self, principal_id: text) -> GetCreditsResult: ...

    @service_query
    def get_latest_version(self) -> UpgradeResult: ...


def _unwrap_call_result(result):
    """Unwrap a CallResult[Variant{Ok, Err}] — handles double nesting.

    Inter-canister calls return CallResult which wraps the response variant.
    Both levels may have Ok/Err, so we unwrap until we reach the final value.
    """
    def _get(obj, key):
        if isinstance(obj, dict) and key in obj:
            return obj[key]
        return getattr(obj, key, None)

    # Level 1: CallResult Ok/Err
    err = _get(result, "Err")
    if err is not None:
        return {"error": str(err)}
    val = _get(result, "Ok")
    if val is None:
        val = result

    # Level 2: inner Variant Ok/Err (e.g. GetCreditsResult, UpgradeResult)
    inner_err = _get(val, "Err")
    if inner_err is not None:
        return {"error": str(inner_err)}
    inner_ok = _get(val, "Ok")
    if inner_ok is not None:
        return inner_ok

    return val


_last_upgrade_job_id: str = ""


def get_last_upgrade_job_id() -> str:
    return _last_upgrade_job_id


def set_last_upgrade_job_id(job_id: str):
    global _last_upgrade_job_id
    _last_upgrade_job_id = job_id


def _get_registry_canister_id() -> str:
    """Get the registry canister ID from stored registry records."""
    try:
        from api.registry import get_registry_info
        info = get_registry_info()
        if info.get("registries"):
            return info["registries"][0].get("principal_id", "")
    except Exception:
        pass
    return ""


def _get_frontend_canister_id() -> str:
    """Get this realm's frontend canister ID from config."""
    try:
        realm = Realm.load("1")
        if realm and realm.frontend_canister_id:
            return str(realm.frontend_canister_id)
    except Exception:
        pass
    return ""


def _get_network() -> str:
    """Determine the IC network this realm is deployed on.

    Reads the network stored in the Realm entity (set via set_canister_config
    during deployment). Falls back to "ic" (mainnet).
    """
    try:
        realm = Realm.load("1")
        if realm and getattr(realm, "network", None):
            return str(realm.network)
    except Exception:
        pass
    return "ic"


def request_upgrade(registry_canister_id: str) -> Async[Dict]:
    """Request a self-upgrade to the latest version via the registry.

    Args:
        registry_canister_id: Canister ID of realm_registry_backend

    Returns:
        Dict with success status, job_id, and target version
    """
    global _last_upgrade_job_id

    backend_id = str(ic.id())
    frontend_id = _get_frontend_canister_id()
    network = _get_network()

    if not registry_canister_id:
        return {"success": False, "error": "No registry canister configured"}

    if not frontend_id:
        return {"success": False, "error": "No frontend canister ID configured"}

    # Get current version
    current_version = ""
    try:
        from api.status import get_status
        status = get_status()
        current_version = status.get("version", "")
    except Exception:
        pass

    # Get cycle balance
    reported_cycles = 0
    try:
        reported_cycles = ic.canister_balance()
    except Exception:
        pass

    manifest = json.dumps({
        "backend_canister_id": backend_id,
        "frontend_canister_id": frontend_id,
        "reported_cycles": reported_cycles,
        "current_version": current_version,
        "network": network,
    })

    logger.info(f"Requesting upgrade: registry={registry_canister_id}, "
                f"backend={backend_id}, frontend={frontend_id}, "
                f"cycles={reported_cycles}, version={current_version}")

    try:
        registry = RealmRegistryUpgradeService(Principal.from_str(registry_canister_id))
        result: CallResult = yield registry.request_upgrade(manifest)

        # Parse response
        if isinstance(result, dict):
            if "Err" in result and result["Err"] is not None:
                return {"success": False, "error": str(result["Err"])}
            if "Ok" in result:
                result = result["Ok"]
        elif hasattr(result, "Err") and getattr(result, "Err", None):
            return {"success": False, "error": str(result.Err)}
        elif hasattr(result, "Ok"):
            result = result.Ok

        if isinstance(result, str):
            parsed = json.loads(result)
        else:
            parsed = result

        if parsed.get("success"):
            job_id = parsed.get("job_id", "")
            _last_upgrade_job_id = job_id
            logger.info(f"Upgrade enqueued: job_id={job_id}, "
                        f"version={parsed.get('target_version', '')}")
        else:
            logger.error(f"Upgrade request failed: {parsed.get('error', '')}")

        return parsed

    except Exception as e:
        logger.error(f"Error requesting upgrade: {e}")
        return {"success": False, "error": f"Inter-canister call failed: {e}"}


def get_realm_credits(registry_canister_id: str) -> Async[Dict]:
    """Query this realm's credit balance from the registry.

    Args:
        registry_canister_id: Canister ID of realm_registry_backend

    Returns:
        Dict with credit balance info
    """
    if not registry_canister_id:
        return {"success": False, "error": "No registry canister configured"}

    realm_principal = str(ic.id())

    try:
        registry = RealmRegistryUpgradeService(Principal.from_str(registry_canister_id))
        result: CallResult[GetCreditsResult] = yield registry.get_credits(realm_principal)

        inner = _unwrap_call_result(result)
        if isinstance(inner, dict) and "error" in inner:
            return {"success": False, "error": inner["error"]}

        if isinstance(inner, dict):
            return {"success": True, "credits": inner}
        return {"success": True, "credits": {
            "principal_id": str(getattr(inner, "principal_id", "") or ""),
            "balance": int(getattr(inner, "balance", 0) or 0),
            "total_purchased": int(getattr(inner, "total_purchased", 0) or 0),
            "total_spent": int(getattr(inner, "total_spent", 0) or 0),
        }}
    except Exception as e:
        logger.error(f"Error getting realm credits: {e}")
        return {"success": False, "error": str(e)}


def get_available_version(registry_canister_id: str) -> Async[Dict]:
    """Query the latest available version from the registry.

    Args:
        registry_canister_id: Canister ID of realm_registry_backend

    Returns:
        Dict with version info
    """
    if not registry_canister_id:
        return {"success": False, "error": "No registry canister configured"}

    try:
        registry = RealmRegistryUpgradeService(Principal.from_str(registry_canister_id))
        result: CallResult[UpgradeResult] = yield registry.get_latest_version()

        inner = _unwrap_call_result(result)
        if isinstance(inner, dict) and "error" in inner:
            return {"success": False, "error": inner["error"]}

        if isinstance(inner, str):
            parsed = json.loads(inner)
            return {"success": True, "version": parsed}
        return {"success": True, "version": inner}
    except Exception as e:
        logger.error(f"Error getting available version: {e}")
        return {"success": False, "error": str(e)}
