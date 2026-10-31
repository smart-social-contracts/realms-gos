import json
import traceback
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from _cdk import Async
from ic_python_logging import get_logger

logger = get_logger("core.extensions")


def create_extension_entity_class(extension_name):
    """Create an ExtensionEntity base class that automatically uses extension name as namespace.

    Args:
        extension_name: Name of the extension to use as namespace prefix

    Returns:
        A class that can be used as base for entities with automatic namespacing

    Example:
        # In your extension backend/entry.py:
        from core.extensions import create_extension_entity_class

        ExtensionEntity = create_extension_entity_class("my_extension")

        class AppConfig(ExtensionEntity):
            __alias__ = "key"
            key = String()
            value = String()

        # Usage:
        config = AppConfig["setting_name"]
        if not config:
            config = AppConfig(key="setting_name", value="...")
    """
    from ic_python_db import Entity, TimestampedMixin

    class ExtensionEntity(Entity, TimestampedMixin):
        """Base class for extension-scoped entities with automatic namespacing.

        The entity will be stored with namespace: ext_{extension_name}::EntityClass
        """

        __namespace__ = f"ext_{extension_name}"

    return ExtensionEntity


def _has_backend(ext_id: str) -> bool:
    """True when the extension ships an ``entry.py`` to execute.

    Frontend-only extensions have nothing to isolate, so they skip the sandbox
    branch entirely and take the ordinary "no such function" path rather than
    failing on a spawn that has no source to spawn.
    """
    import os

    try:
        from core.runtime_extensions import EXTENSIONS_DIR
    except Exception:
        return True
    return os.path.exists(os.path.join(EXTENSIONS_DIR, ext_id, "entry.py"))


def _authenticated_caller() -> str:
    """The principal invoking this extension call.

    Read once, here at the host boundary, and handed to the bridge. Everything
    the sandboxed extension is allowed to see or change is scoped to this
    value, and the extension has no way to influence it.
    """
    try:
        from basilisk import ic

        return ic.caller().to_str()
    except Exception:
        return ""


def call_extension_function(
    extension_name: str, function_name: str, args: str, *, allow_suspend: bool = False
):
    logger.debug(f"Calling extension '{extension_name}' function '{function_name}'")

    try:
        from core import runtime_sandbox
        from core.runtime_extensions import get_func, resolve_extension_id

        resolved = resolve_extension_id(extension_name)
        if runtime_sandbox.should_sandbox(resolved) and _has_backend(resolved):
            # No fallback: an extension resolved to ``sandbox`` never runs with
            # host access. Retrying in-process would turn any spawn failure —
            # including one the extension can provoke — into full privilege.
            if not runtime_sandbox.is_sandbox_available():
                raise RuntimeError(
                    f"Extension '{resolved}' resolves to sandboxed execution "
                    f"but this canister image has no _basilisk_sandbox. Install "
                    f"an image with sandbox support, or declare "
                    f"\"runtime\": \"in_process\" in its manifest."
                )
            caller = _authenticated_caller()
            if runtime_sandbox.is_async_extension_function(resolved, function_name):
                # A generator, not a value: the outcall inside it is what ends
                # the message. Only reachable through ``extension_async_call``,
                # since a query cannot make an inter-canister call at all.
                from core import async_bridge

                return async_bridge.run_with_effects(
                    resolved,
                    function_name,
                    args,
                    caller,
                    runtime_sandbox.extension_capabilities(resolved),
                )
            return runtime_sandbox.call_extension_in_sandbox(
                resolved, function_name, args, caller=caller
            )

        func = get_func(extension_name, function_name)
        logger.debug(f"Got function from registry: {func}")

        result = func(args)
        if hasattr(result, "__next__"):
            if allow_suspend:
                # ``extension_async_call`` will ``yield`` this generator so the
                # IC runtime can finish inter-canister / HTTP rounds.
                return result
            # Kybra ``Async`` extension functions are generators: calling one
            # here returns the undriven generator, which json.dumps cannot
            # serialize. A sync caller still deserves an answer when the
            # function never actually suspends, so drive pure-compute
            # generators to completion; a real yield is an inter-canister
            # round that only extension_async_call may finish.
            gen = result
            result = None
            while True:
                try:
                    suspended = next(gen)
                except StopIteration as done:
                    result = done.value
                    break
                if suspended is not None:
                    raise RuntimeError(
                        f"Extension function '{function_name}' suspended "
                        f"mid-call; invoke it via extension_async_call"
                    )
        logger.debug(f"Got result from function: {result}")

    except AttributeError as e:
        # Missing hook only — infrastructure AttributeErrors (e.g. sandbox
        # missing sha256) must propagate.
        msg = str(e)
        if function_name in msg and (
            "has no attribute" in msg or "has no function" in msg
        ):
            logger.warning(f"Extension function not found: {e}")
            return None
        raise
    except PermissionError as e:
        from core.extension_errors import payload_from_permission_error

        logger.warning(f"Extension function permission denied: {e}")
        return json.dumps(payload_from_permission_error(e))
    except Exception as e:
        logger.error(f"Error calling extension function: {e}\n{traceback.format_exc()}")
        raise e

    return result


def drive_suspending_generator(gen):
    """Forward IC outcall yields from an extension generator and resume with results.

    Use inside a Kybra ``Async`` endpoint. After the inner generator yields an
    outcall, the IC resume value must be passed back via ``gen.send``, not
    another ``next`` call.
    """
    try:
        pending = next(gen)
    except StopIteration as done:
        return done.value
    while True:
        try:
            pending = gen.send((yield pending))
        except StopIteration as done:
            return done.value


def extension_async_call(
    extension_name: str, function_name: str, args: str
) -> Async[Any]:
    logger.debug(f"Async calling extension {extension_name}...")

    result_coroutine = call_extension_function(
        extension_name, function_name, args, allow_suspend=True
    )
    logger.debug(
        f"Got coroutine from extension {extension_name} function {function_name}: {result_coroutine}"
    )

    return result_coroutine
