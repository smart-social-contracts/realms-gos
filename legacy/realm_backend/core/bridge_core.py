"""Primitives shared by every sandbox capability bridge.

Two bridges sit on top of this module:

  * ``core.codex_bridge`` — codex hooks, host-initiated, no end user.
  * ``core.extension_bridge`` — extension entry points, user-initiated, with an
    authenticated caller the host injects.

They differ in what they expose, not in how the boundary is defended, so the
boundary itself lives here: one strict serializer, one capability check, one
effect-reference resolver.

The serializer is the crown jewel. A single live object leaking into a
subinterpreter reopens a full escape via ``__class__`` / ``__subclasses__``, so
it whitelists JSON-safe types and refuses everything else — entities,
callables, exceptions, sets, bytes. Everything crossing the boundary in either
direction goes through it.
"""

from typing import Any, Dict, List, Optional

# Maximum nesting depth accepted at the boundary (mirrors the Basilisk C
# marshaller's own depth cap; keeps hostile sandboxed code from building a
# payload that blows the host stack during validation).
MAX_DEPTH = 32


class BridgeSerializationError(TypeError):
    """A value at the sandbox boundary is not plain JSON-safe data."""


def to_plain(value: Any, _depth: int = 0) -> Any:
    """Return *value* iff it is strictly plain JSON-safe data, else raise.

    Accepts ``None``, ``bool``, ``int``, ``float``, ``str``, ``list``/``tuple``
    (recursively; tuples become lists) and ``dict`` with ``str`` keys
    (recursively). Everything else — entities, callables, exceptions, sets,
    bytes, custom objects — is rejected. This is the only thing allowed to hand
    data back into the sandbox.
    """
    if _depth > MAX_DEPTH:
        raise BridgeSerializationError(
            f"value nests deeper than the {MAX_DEPTH}-level boundary limit"
        )
    # ``bool`` is a subclass of ``int``; both are fine. Check it explicitly so
    # the intent is clear.
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return [to_plain(item, _depth + 1) for item in value]
    if isinstance(value, dict):
        out: Dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise BridgeSerializationError(
                    f"dict keys must be str at the sandbox boundary, got "
                    f"{type(key).__name__}"
                )
            out[key] = to_plain(item, _depth + 1)
        return out
    raise BridgeSerializationError(
        f"{type(value).__name__} is not permitted across the sandbox boundary; "
        f"verbs must return plain JSON data, never live objects"
    )


def check_capability(
    action: str, capabilities: List[str], known: Any, subject: str = "package"
) -> Optional[str]:
    """Return an error string if *action* is not permitted, else ``None``.

    An action is permitted only when it is a registered verb AND the package
    declared it in its manifest ``capabilities``. Both halves matter: the
    registry bounds what the host can be asked to do at all, and the manifest
    bounds what this particular package may ask for.
    """
    if action not in known:
        return f"unknown verb '{action}'"
    if action not in (capabilities or ()):
        return (
            f"capability '{action}' not granted to this {subject} "
            f"(declare it in the manifest 'capabilities' list)"
        )
    return None
