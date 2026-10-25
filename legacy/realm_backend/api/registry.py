"""Registry API for realm self-registration."""

import json
from typing import Dict

from ggg import Registry
from _cdk import Async, CallResult, Principal, Service, Variant, ic, service_update, text
from ic_python_logging import get_logger

logger = get_logger("api.registry")


# Define the AddRealmResult variant type returned by the registry
class AddRealmResult(Variant, total=False):
    Ok: text
    Err: text


# Define the registry canister service interface
# Note: Basilisk limits service methods to 6 params (including self)
# So we pass canister_ids as a JSON string
class RealmRegistryService(Service):
    @service_update
    def register_realm(
        self,
        name: text,
        url: text,
        logo: text,
        backend_url: text,
        canister_ids_json: text,  # JSON: {frontend, token, nft}
    ) -> AddRealmResult:
        ...


def register_realm(
    registry_canister_id: str,
    realm_name: str,
    realm_url: str = "",
    backend_url: str = "",
    canister_ids: dict = None,  # {frontend_canister_id, token_canister_id, nft_canister_id}
) -> Async[Dict]:
    """
    Register this realm with the central realm registry.

    Makes an inter-canister call to the realm_registry_backend canister.
    The registry uses this canister's principal (ic.caller()) as the unique ID,
    preventing duplicates and enabling upsert behavior.

    Args:
        registry_canister_id: Canister ID of the realm registry backend
        realm_name: Human-readable name for this realm
        realm_url: Frontend canister URL (optional)
        backend_url: Backend canister URL (optional)
        canister_ids: Dict with frontend_canister_id, token_canister_id, nft_canister_id (optional)

    Returns:
        Dictionary with success status and message/error
    """
    realm_id = str(ic.id())  # This canister's ID will also be used by registry's ic.caller()
    logger.info(f"Registering realm {realm_name} (canister {realm_id}) with registry {registry_canister_id}")

    try:
        canister_ids = canister_ids or {}
        canister_ids_packed = "|".join([
            canister_ids.get("frontend_canister_id", ""),
            canister_ids.get("token_canister_id", ""),
            canister_ids.get("nft_canister_id", ""),
        ])
        logger.info(
            f"Calling registry register_realm with args: ({realm_name}, {realm_url}, {backend_url}, {canister_ids_packed})"
        )

        # Logo is always at /images/logo.png by convention; the registry frontend
        # constructs the full URL client-side, so we pass "" here.
        registry = RealmRegistryService(Principal.from_str(registry_canister_id))
        result: CallResult[AddRealmResult] = yield registry.register_realm(
            realm_name, realm_url, "", backend_url, canister_ids_packed
        )

        logger.info(f"Registry call result: {result}")
        logger.info(f"Result type: {type(result)}")

        # Helper to get Ok/Err from result (basilisk may return dict or object)
        def _get(obj, key):
            if isinstance(obj, dict) and key in obj:
                return obj[key]
            return getattr(obj, key, None)

        # Parse the CallResult — may be nested CallResult[AddRealmResult] or flat
        ok_val = _get(result, "Ok")
        err_val = _get(result, "Err")

        if ok_val is not None:
            inner_result = ok_val
            logger.info(f"Inner result: {inner_result}")

            # Inner result may be the AddRealmResult variant (also Ok/Err) or a string
            inner_ok = _get(inner_result, "Ok") if not isinstance(inner_result, str) else inner_result
            inner_err = _get(inner_result, "Err") if not isinstance(inner_result, str) else None

            if inner_ok is not None:
                message = inner_ok
                logger.info(f"Successfully registered realm {realm_name}: {message}")

                # Store registry information locally
                try:
                    registry_record = Registry(
                        name=f"registry_{registry_canister_id}",
                        description=f"Registered with {registry_canister_id}",
                        principal_id=registry_canister_id,
                    )
                    logger.info(f"Stored registry record locally: {registry_record._id}")
                except Exception as e:
                    logger.warning(f"Failed to store registry record locally: {e}")

                return {
                    "success": True,
                    "message": str(message),
                    "realm_id": realm_id,
                    "realm_name": realm_name,
                    "realm_url": realm_url,
                    "backend_url": backend_url,
                    "registry_canister": registry_canister_id,
                }
            elif inner_err is not None:
                logger.error(f"Registry returned error: {inner_err}")
                return {"success": False, "error": str(inner_err)}
            else:
                logger.error(f"Unexpected inner response: {inner_result}")
                return {"success": False, "error": f"Unexpected response: {inner_result}"}
        elif err_val is not None:
            logger.error(f"Inter-canister call failed: {err_val}")
            return {"success": False, "error": f"Call failed: {err_val}"}
        else:
            logger.error(f"Unexpected response from registry: {result}")
            return {"success": False, "error": f"Unexpected response: {result}"}

    except Exception as e:
        logger.error(f"Error registering realm: {str(e)}")
        return {"success": False, "error": f"Failed to register: {str(e)}"}


def get_registry_info() -> Dict:
    """
    Get information about registries this realm is registered with.

    Returns:
        Dictionary with list of registries
    """
    try:
        registries = list(Registry.instances())
        return {
            "success": True,
            "count": len(registries),
            "registries": [
                {
                    "name": r.name,
                    "description": r.description,
                    "principal_id": r.principal_id,
                    "created_at": r.created_at if hasattr(r, "created_at") else None,
                }
                for r in registries
            ],
        }
    except Exception as e:
        logger.error(f"Error getting registry info: {str(e)}")
        return {"success": False, "error": str(e), "registries": []}
