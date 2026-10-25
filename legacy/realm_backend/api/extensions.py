import json
import traceback
from typing import Any

import core
from core.candid_types_realm import (
    ExtensionsListRecord,
    RealmResponse,
    RealmResponseData,
)
from ggg import User
from _cdk import Async, query, update
from ic_python_logging import get_logger

logger = get_logger("api.extensions")


def get_all_extension_manifests():
    """Get all extension manifests from runtime-installed extensions."""
    from core.runtime_extensions import get_all_extension_manifests as _get_manifests
    return _get_manifests()


@query
def list_extensions(principal: str) -> RealmResponse:
    """List all available extensions with their metadata"""
    try:
        user = User[principal]

        # Use static manifest registry instead of filesystem access
        # (Basilisk canisters don't have filesystem access)
        extension_manifests = get_all_extension_manifests()
        extensions_data = list(extension_manifests.values())

        extensions_json = [json.dumps(ext) for ext in extensions_data]
        logger.info(f"Listed {len(extensions_data)} extensions from static registry")

        return RealmResponse(
            success=True,
            data=RealmResponseData(
                extensionsList=ExtensionsListRecord(extensions=extensions_json)
            ),
        )
    except Exception as e:
        logger.error(f"Error listing extensions: {str(e)}\n{traceback.format_exc()}")
        return RealmResponse(success=False, data=RealmResponseData(error=str(e)))


def extension_sync_call(extension_name: str, function_name: str, args: str) -> Any:
    return core.extensions.call_extension_function(extension_name, function_name, args)


def extension_async_call(
    extension_name: str, function_name: str, args: str
) -> Async[Any]:
    """Drive in-process extension generators so HTTP/outcall yields reach the IC."""
    gen = core.extensions.call_extension_function(
        extension_name, function_name, args, allow_suspend=True
    )
    if not hasattr(gen, "__next__"):
        return gen
    return (yield from core.extensions.drive_suspending_generator(gen))
