"""Inter-canister calls for sandboxed extensions, by replay (issue #279).

The capability bridge in :mod:`core.extension_bridge` is synchronous, and it
cannot be made otherwise. ``_basilisk_sandbox.call_in_subinterpreter`` is a C
frame, ``sandbox_rpc`` is another, and an IC outcall only suspends at a *Python
generator's* ``yield`` — the point where ``async_result_handler`` (Rust) takes
over and ends the message. A generator frame is heap-allocated and can be
resumed; a native C frame cannot. So there is no way for sandboxed code to sit
and wait for an outcall: the stack it is standing on has to be gone before the
call can even be made.

What works instead is to let the extension *ask*, and run it again:

    round 0   host calls the entry point.  It requests an effect and returns.
    ...       host performs the outcall (a real ``yield``, message ends here).
    round 1   host calls the entry point again, with the result available.

``ggg_sdk``'s ``ctx.services.query()`` hides the seam. On the pass that has no
result it raises, and the dispatcher turns that into a request; on the pass that
has one it returns the value. So extension code stays straight-line:

    txns = ctx.services.query("registry.get_transactions", limit=20)

The cost is that the body re-runs from the top on every round, which is why
:data:`ASYNC_WRITE_RULE` exists: a function declared async may not write. A
write before the effect point would be applied once per round, and the sandbox
cannot be rolled back. Refusing writes outright is the only version of this that
is safe without a transaction, so async functions are reads and the writes go in
a separate sync call.

Two properties this deliberately does not give the extension:

* **No choice of target.** A verb that dials whatever principal it is handed
  would be a general-purpose outcall primitive — strictly more authority than
  the in-process access being removed, since it reaches every canister on the
  subnet. Each :class:`ServiceSpec` resolves its own target from host state.
* **No unbounded rounds.** ``MAX_ROUNDS`` caps how many times one call can make
  the host go out and come back, so an extension cannot spend the realm's
  cycles in a loop.
"""

from typing import Any, Callable, Dict, FrozenSet, List, Optional

from ic_python_logging import get_logger

logger = get_logger("core.async_bridge")

# One entry point invocation may resolve at most this many effects. Sequential
# outcalls each cost a round, so this is also a bound on outcall depth.
MAX_ROUNDS = 4

ASYNC_WRITE_RULE = (
    'a function declared in "async_functions" runs more than once (its body '
    "replays after each outcall), so it may not use write verbs"
)


class ServiceCallError(Exception):
    """The outcall was made and came back an error."""


# ---------------------------------------------------------------------------
# Registered services
# ---------------------------------------------------------------------------


class ServiceSpec:
    """One outcall an extension may ask the host to make.

    *target* is a zero-argument callable returning the destination principal
    from host-held state — never an extension argument. *params* is the
    allowlist of keys the extension may set; anything else it sends is refused
    rather than ignored, so a typo surfaces instead of silently doing something
    different. *perform* is a generator ``(target, params) -> result`` that owns
    the ``yield``.
    """

    def __init__(
        self,
        name: str,
        target: Callable[[], str],
        params: FrozenSet[str],
        perform: Callable[..., Any],
        operation: Optional[str] = None,
    ):
        self.name = name
        self.target = target
        self.params = frozenset(params)
        self.perform = perform
        self.operation = operation

    @property
    def capability(self) -> str:
        return f"service.call:{self.name}"

    def check_params(self, params: Dict[str, Any]) -> Dict[str, Any]:
        unknown = sorted(set(params) - self.params)
        if unknown:
            raise PermissionError(
                f"service '{self.name}' does not accept {', '.join(unknown)}; "
                f"it accepts {sorted(self.params) or 'no parameters'}"
            )
        return dict(params)


def _registry_principal() -> str:
    """The registry this realm is registered with, from the local DB."""
    from api.registry import get_registry_info

    for entry in get_registry_info().get("registries") or []:
        principal = (entry.get("principal_id") or "").strip()
        if principal:
            return principal
    raise ServiceCallError("no registry canister is configured for this realm")


def _unwrap(result: Any) -> Any:
    """Flatten ``CallResult[Variant{Ok,Err}]``, which may be nested twice."""

    def get(obj, key):
        if isinstance(obj, dict):
            return obj.get(key)
        return getattr(obj, key, None)

    err = get(result, "Err")
    if err is not None:
        raise ServiceCallError(str(err))
    value = get(result, "Ok")
    if value is None:
        return result
    inner_err = get(value, "Err")
    if inner_err is not None:
        raise ServiceCallError(str(inner_err))
    inner_ok = get(value, "Ok")
    return inner_ok if inner_ok is not None else value


def _registry_transaction_service(target: str):
    """Built lazily: ``_cdk`` service classes cannot be defined at import time
    in CPython template mode, where ``Record``/``Variant`` are plain dicts."""
    from _cdk import (
        Principal,
        Record,
        Service,
        Variant,
        Vec,
        float64,
        nat64,
        service_query,
        text,
    )

    class CreditTransactionRecord(Record):
        id: text
        principal_id: text
        amount: nat64
        transaction_type: text
        description: text
        stripe_session_id: text
        timestamp: float64

    class TransactionHistoryResult(Variant, total=False):
        Ok: Vec[CreditTransactionRecord]
        Err: text

    class RegistryTransactionService(Service):
        @service_query
        def get_transactions(
            self, principal_id: text, limit: nat64
        ) -> TransactionHistoryResult: ...

    return RegistryTransactionService(Principal.from_str(target))


def _project_transaction(t: Any) -> Dict[str, Any]:
    if isinstance(t, dict):
        return {
            "id": str(t.get("id", "")),
            "principal_id": str(t.get("principal_id", "")),
            "amount": int(t.get("amount", 0) or 0),
            "transaction_type": str(t.get("transaction_type", "")),
            "description": str(t.get("description", "")),
            "stripe_session_id": str(t.get("stripe_session_id", "")),
            "timestamp": float(t.get("timestamp", 0) or 0),
        }
    return {
        "id": str(getattr(t, "id", "")),
        "principal_id": str(getattr(t, "principal_id", "")),
        "amount": int(getattr(t, "amount", 0) or 0),
        "transaction_type": str(getattr(t, "transaction_type", "")),
        "description": str(getattr(t, "description", "")),
        "stripe_session_id": str(getattr(t, "stripe_session_id", "")),
        "timestamp": float(getattr(t, "timestamp", 0) or 0),
    }


def _perform_registry_transactions(target: str, params: Dict[str, Any]):
    """``limit`` is the extension's; the subject principal is this realm's own,
    so an extension cannot read another realm's billing history."""
    from _cdk import ic

    limit = params.get("limit", 20)
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        raise PermissionError(
            "service 'registry.get_transactions': limit must be an integer"
        )
    limit = max(1, min(limit, 200))

    service = _registry_transaction_service(target)
    raw = yield service.get_transactions(str(ic.id()), limit)

    rows = _unwrap(raw)
    items = rows if isinstance(rows, list) else []
    transactions = [_project_transaction(t) for t in items]
    return {"transactions": transactions, "count": len(transactions)}


SERVICES: Dict[str, ServiceSpec] = {
    "registry.get_transactions": ServiceSpec(
        name="registry.get_transactions",
        target=_registry_principal,
        params=frozenset({"limit"}),
        perform=_perform_registry_transactions,
        operation="treasury.view",
    ),
}


def service_capabilities() -> List[str]:
    """``service.call:*`` capability names a manifest may declare."""
    return sorted(spec.capability for spec in SERVICES.values())


# ---------------------------------------------------------------------------
# Manifest contract
# ---------------------------------------------------------------------------


def declared_async_functions(manifest: Dict[str, Any]) -> FrozenSet[str]:
    """Entry points allowed to request effects.

    Explicit rather than inferred: whether a function may replay decides
    whether it may write, and that is not a thing to discover at runtime.
    """
    raw = manifest.get("async_functions")
    if raw is None:
        return frozenset()
    if not isinstance(raw, list) or any(not isinstance(n, str) for n in raw):
        raise ValueError("manifest 'async_functions' must be a list of strings")
    return frozenset(raw)


def authorize_service(name: str, capabilities: List[str]) -> ServiceSpec:
    spec = SERVICES.get(name)
    if spec is None:
        raise PermissionError(
            f"unknown service '{name}'; known: {sorted(SERVICES) or 'none'}"
        )
    if spec.capability not in (capabilities or ()):
        raise PermissionError(
            f"service '{name}' requires capability '{spec.capability}', which "
            f"this extension does not declare"
        )
    return spec


def _check_operation(spec: ServiceSpec, caller: str) -> None:
    """A declared capability says the extension may ask; it does not say this
    caller may. Both have to hold, exactly as for the sync verbs."""
    if not spec.operation:
        return
    from core.extension_bridge import caller_has_operation

    if not caller_has_operation(caller, spec.operation):
        raise PermissionError(
            f"service '{spec.name}' requires operation '{spec.operation}'"
        )


# ---------------------------------------------------------------------------
# The replay loop
# ---------------------------------------------------------------------------


def effect_key(name: str, params: Dict[str, Any]) -> str:
    """Stable identity for one requested effect.

    Must match ``ggg_sdk``'s key exactly, or a resolved result would not be
    recognised on the next round and the call would loop until MAX_ROUNDS.
    """
    parts = [f"{k}={params[k]!r}" for k in sorted(params)]
    return name + "(" + ",".join(parts) + ")"


def run_with_effects(
    ext_id: str, function_name: str, args: str, caller: str, capabilities: List[str]
):
    """Drive one async extension call to completion. A generator: the ``yield``
    inside a spec's ``perform`` is what actually ends the message.

    Raises rather than returning partial data — a caller that cannot tell a
    resolved call from an abandoned one would render the abandoned one.
    """
    from core import runtime_sandbox

    resolved: Dict[str, Any] = {}

    for round_index in range(MAX_ROUNDS + 1):
        outcome = runtime_sandbox.call_extension_round(
            ext_id, function_name, args, caller, resolved
        )
        if not isinstance(outcome, dict):
            raise RuntimeError(
                f"{ext_id}.{function_name}: async dispatcher returned "
                f"{type(outcome).__name__}, expected a status dict"
            )

        status = outcome.get("status")
        if status == "ok":
            return outcome.get("value")
        if status != "effect":
            raise RuntimeError(
                f"{ext_id}.{function_name}: unknown dispatcher status " f"{status!r}"
            )

        if round_index >= MAX_ROUNDS:
            break

        request = outcome.get("request") or {}
        name = request.get("service")
        params = request.get("params") or {}
        if not isinstance(name, str) or not isinstance(params, dict):
            raise RuntimeError(
                f"{ext_id}.{function_name}: malformed effect request {request!r}"
            )

        spec = authorize_service(name, capabilities)
        _check_operation(spec, caller)
        checked = spec.check_params(params)
        key = effect_key(name, checked)

        if key in resolved:
            raise RuntimeError(
                f"{ext_id}.{function_name}: re-requested effect {key} that is "
                f"already resolved — the extension is not reading its result"
            )

        logger.debug(f"async_bridge[{ext_id}]: round {round_index} -> {name} {checked}")
        try:
            value = yield from spec.perform(spec.target(), checked)
            resolved[key] = {"value": value}
        except ServiceCallError as e:
            # Hand the failure back to the extension so it can present it,
            # rather than failing the whole call: an unreachable registry is a
            # normal condition, not a bug in the extension.
            resolved[key] = {"error": str(e)}

    raise RuntimeError(
        f"{ext_id}.{function_name}: still requesting effects after "
        f"{MAX_ROUNDS} rounds (limit exists so one call cannot spend the "
        f"realm's cycles in a loop)"
    )
