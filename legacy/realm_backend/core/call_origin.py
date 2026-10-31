"""Who is asking: host code, a sandboxed extension, or a codex.

Cedar decides on three things — principal, action, resource — plus a *context*
carrying facts about the request rather than the data. The realm's guardrails in
``core/cedar/guardrails.cedar`` key on one such fact: whether a call originated
inside a sandboxed extension. Both cases arrive as the same principal, so
without it no policy can tell them apart.

The awkward part is that the origin is known at the bridge, and needed much later
wherever the Cedar request is built, with ordinary verb code in between. Threading
it through every verb signature would touch all 95 of them and be forgotten on the
96th. So it is ambient for the duration of a dispatch, in the same spirit as
``ic_python_db``'s caller context.

Failing to set it is a *fail-open*: an extension call that arrives without an
origin is indistinguishable from host code and passes guardrails G1 and G2. That
is why ``dispatch`` below exists and why nothing should index a verb registry
directly — see ``tests/backend/test_call_origin.py``, which fails if anything
does.
"""

from typing import Any, Callable, Dict, Mapping, Optional

# The origin of the call currently being dispatched, or None for host code.
# Single-threaded by construction: a canister executes one message at a time.
_CURRENT: Optional[Dict[str, str]] = None


class origin:
    """Declare the origin of the calls made inside this block.

    Restores the previous value on the way out, including when the body raises,
    so a verb that refuses does not leave the next call wearing its origin.

    A context manager written by hand rather than with ``contextlib.contextmanager``:
    the frozen stdlib baked into the canister template does not carry it, and a
    module that fails to import takes the whole canister down with it.
    """

    def __init__(self, **facts: str):
        self._facts = {k: v for k, v in facts.items() if v}
        self._previous: Optional[Dict[str, str]] = None

    def __enter__(self):
        global _CURRENT
        self._previous = _CURRENT
        _CURRENT = self._facts
        return self

    def __exit__(self, *exc: object):
        global _CURRENT
        _CURRENT = self._previous
        return False


def host_call():
    """Origin for a host Candid verb, including ones dispatched from the REPL.

    Host code has nothing to declare. Clearing the ambient origin here is
    what keeps ``api.call`` / ``ext.call`` from inheriting ``context.repl``
    if the shell marked the ingress as REPL-originated. Extension work
    still sets ``context.extension`` via :func:`extension_call` once the
    host method reaches the bridge.
    """
    return origin()


def extension_call(ext_id: str):
    """Origin for a call made from inside a sandboxed extension."""
    return origin(extension=ext_id)


def codex_call(context_id: str):
    """Origin for a call made from inside a codex hook."""
    return origin(codex=context_id)


def current() -> Dict[str, str]:
    """The current origin, as the context record for a Cedar request.

    Empty for host code, which is the correct reading: host code has nothing to
    declare, and the guardrails that key on an extension simply do not apply.
    """
    return dict(_CURRENT) if _CURRENT else {}


def dispatch(
    registry: Mapping[str, Callable[..., Any]],
    action: str,
    _origin,
    /,
    **kwargs: Any,
) -> Any:
    """Invoke ``registry[action]`` with an origin in force.

    The origin is positional and required, so a call site that forgets it is a
    ``TypeError`` at import-time coverage rather than a silent fail-open. This is
    the only sanctioned way to invoke a verb.
    """
    with _origin:
        return registry[action](**kwargs)
