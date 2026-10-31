"""The realm's Cedar decision point.

Authorization rules the realm cannot afford to get wrong live in
``core/cedar/guardrails.cedar`` as data, not as Python spread across ninety-five
verbs. This module is what makes them bite: it loads them at startup and asks
Cedar before a call proceeds.

Since ic-basilisk-toolkit 0.5.1 the machinery lives in the toolkit
(``CedarEngine`` — extracted from this module — and ``Slicer``); what remains
here is the realm's *configuration* of that machinery: the ``Realm`` namespace,
the embedded guardrails and policies, the ambient call origin as request
context, and the realm's verb-to-action collapsing rule. The module-level
function API is unchanged, so call sites in ``main.py`` and
``core/extension_bridge.py`` are unaffected.

Two properties matter more than anything else here.

**It fails closed.** If policies did not load, if the entity slice is malformed,
if the native module is missing in a build that expected it — every one of those
denies. The tempting alternative, letting calls through when the authorizer is
unavailable, produces a realm that looks enforced and is not, and nothing about
its behaviour would tell you which one you had.

**Enforcement is opt-in per deployment, and honest about it.** ``_basilisk_cedar``
only exists in a canister built on the Cedar template. On a stock build there is
no authorizer at all, so :func:`enabled` is False and the existing Python checks
remain the only gate — exactly as before this module existed. What must never
happen is a deployment that believes it has Cedar and silently does not, which is
why :func:`require_enforcement` exists for a realm that wants that guarantee.
"""

from typing import Any, List, Optional

from core.call_origin import current as current_origin
from core.cedar_policies import GUARDRAILS, POLICIES

try:  # pragma: no cover - exercised by which artifact the canister is built on
    from ic_basilisk_toolkit import cedar as _cedar
    from ic_basilisk_toolkit.cedar import CedarError
    from ic_basilisk_toolkit.cedar_engine import CedarEngine
    from ic_basilisk_toolkit.cedar_slicing import Slicer
except ImportError:  # pragma: no cover
    _cedar = None
    CedarEngine = None
    Slicer = None

    class CedarError(Exception):
        """Placeholder so callers can catch one exception type either way."""


_engine: Optional["CedarEngine"] = None


def schema() -> str:
    """The effective Cedar schema text, generated from ggg entity definitions."""
    from core.cedar_schema_runtime import generate_realm_cedar_schema

    return generate_realm_cedar_schema()


def _get_engine() -> "CedarEngine":
    """The realm's configured engine, created on first use.

    ``fail_open_when_unavailable`` matches this realm's longstanding contract:
    on a stock build ``check`` is a no-op and the Python checks remain the only
    gate, while ``is_authorized`` still denies. The engine's own ``check`` is
    not used here because the realm's denial message predates it.
    """
    global _engine
    if _engine is None:
        if CedarEngine is None or Slicer is None:
            raise RuntimeError("ic-basilisk-toolkit Cedar modules unavailable")
        schema_text = schema()
        _engine = CedarEngine(
            namespace="Realm",
            principal_type="User",
            schema=schema_text,
            policies=f"{GUARDRAILS}\n\n{POLICIES}",
            slicer=Slicer("Realm", schema_text, "User"),
            context_provider=current_origin,
            fail_open_when_unavailable=True,
        )
    return _engine


def available() -> bool:
    """Whether this build carries the native Cedar module at all."""
    if _cedar is None:
        return False
    try:
        import _basilisk_cedar  # noqa: F401
    except ImportError:
        return False
    return True


def enabled() -> bool:
    """Whether Cedar is actually deciding calls in this deployment."""
    return _engine is not None and _engine.enabled()


def status() -> dict:
    """What the authorizer is doing, for an operator who needs to know.

    A realm running without enforcement should be able to say so out loud rather
    than leaving it to be inferred from behaviour.
    """
    if _engine is None:
        return {
            "available": available(),
            "enforcing": False,
            "attempted": False,
            "error": "",
            "warnings": [],
        }
    out = _engine.status()
    out.pop("has_extra_policies", None)
    return out


def load(extra_policies: str = "") -> bool:
    """Parse the schema and policies and hold them for later decisions.

    Called once at startup. Deliberately *not* from ``post_upgrade``: the WASI
    filesystem is not mounted during that hook, so reading policy files there
    finds nothing (smart-social-contracts/realms-gos#281). Call it from the deferred
    timer that already performs extension discovery.
    """
    if CedarEngine is None or Slicer is None:
        return False
    return _get_engine().load(extra_policies)


def declared_actions() -> frozenset:
    """Action names the schema declares, read from the schema itself.

    Naming an undeclared action makes Cedar error, which this module turns into a
    denial — so a verb mapped to an action nobody declared would deny every call
    and look like a policy decision. Deriving the set from the schema means
    adding an action in one place cannot silently break the other.
    """
    return _get_engine().declared_actions()


def action_for(verb: str, is_read: bool) -> str:
    """The Cedar action a bridge verb maps to.

    Verbs Cedar names specifically — ``entity.get``, ``appeal.decide`` — map to
    themselves, so a policy can single them out. Everything else collapses to
    ``read`` or ``write``, which is enough for the guardrails: G1 forbids
    extensions from *writing* core state and does not care which verb did it.

    Collapsing rather than declaring all ninety-five verbs is deliberate. A
    schema listing every verb is a promise to update it, and the verb someone
    forgets to add is the one that ends up unconstrained.
    """
    if verb in declared_actions():
        return verb
    return "read" if is_read else "write"


def is_authorized(
    principal_id: str,
    action: str,
    resource_type: str = "",
    resource_id: str = "",
    resource_row: Any = None,
    entities: Optional[List[dict]] = None,
) -> bool:
    """Decide one call. Denies on any failure.

    The context comes from :mod:`core.call_origin` rather than from an argument,
    because the guardrails key on whether a call came from an extension and a
    caller here has no way of knowing. Passing it explicitly would mean every
    call site could get it wrong; taking it from the ambient origin means only
    the bridge can.
    """
    if _engine is None:
        return False
    return _engine.is_authorized(
        principal_id, action, resource_type, resource_id, resource_row,
        entities=entities,
    )


def check(
    principal_id: str,
    action: str,
    resource_type: str = "",
    resource_id: str = "",
    resource_row: Any = None,
) -> None:
    """Raise ``PermissionError`` unless the call is authorized.

    A no-op when Cedar is not enforcing in this deployment, so a stock build
    behaves exactly as it did before — the Python checks are still there and are
    still the gate.
    """
    if not enabled():
        return
    if not is_authorized(
        principal_id, action, resource_type, resource_id, resource_row
    ):
        raise PermissionError(f"'{action}' denied by realm policy")


def require_enforcement() -> None:
    """Refuse to run without Cedar. For a realm that has decided it needs it.

    The failure this guards against is a realm built on the wrong artifact:
    everything works, nothing is enforced, and no request looks different.
    """
    if _engine is None:
        raise RuntimeError(
            "Cedar enforcement required but unavailable: "
            "load() was never called. Build the canister on the Cedar "
            "template artifact."
        )
    _engine.require_enforcement()


def reset_for_tests() -> None:
    global _engine
    _engine = None
    from core import cedar_entities, cedar_schema_runtime

    cedar_schema_runtime.reset_for_tests()
    cedar_entities.reset_for_tests()
