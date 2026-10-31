"""JSON-only effect bridge between sandboxed codices and the realm (issue #265).

Only plain data crosses the sandbox boundary. A codex hook follows a
*gather → compute → apply-effects* flow:

  1. the host gathers the reads a hook is likely to need (config, currency,
     time, realm, the triggering user) into a plain-data **context** and passes
     it in;
  2. the hook computes and returns a plain-data list of intended **effects** —
     ``[{"verb": "<domain.verb>", "kwargs": {...}}, ...]`` — via the in-sandbox
     ``ggg_sdk``; and
  3. the host applies that batch here, in ``apply_effects`` — the single trust
     checkpoint for writes.

For reads a hook cannot anticipate (a scoped lookup that depends on values only
known mid-computation), it may also call back into the host synchronously via
the ``rpc`` builtin, handled by ``make_rpc_handler``. Reads are allowed live
because they cannot mutate the realm; **writes are deliberately not**, so that
every mutation arrives as one ordered batch the host authorizes at a single
point rather than interleaved with arbitrary codex computation.

For every effect ``apply_effects``:

  * resolves the verb (unknown verbs are refused);
  * authorizes it against the *codex's declared capabilities* (a codex may only
    invoke verbs it declared in its manifest ``capabilities`` list);
  * leaves a **reserved-domain** hook point for future realm hierarchy — a no-op
    today;
  * resolves ``$eff:<n>:<field>`` references so an effect can use the id an
    earlier effect produced (e.g. an invoice id inside a notification);
  * runs kwargs and the verb result through a **strict plain-data serializer** so
    no live object can ever cross the boundary.

The serializer is a crown-jewel defense: a single leaked live object reopens a
full sandbox escape via ``__class__`` / ``__subclasses__``. It whitelists
JSON-safe types and refuses everything else (entities, callables, exceptions,
sets, bytes, ...), and is unit-tested directly.

Security lives entirely here, on the host side. The in-sandbox ``ggg_sdk`` is
convenience only and is assumed to be fully rewritable by a hostile codex — it
cannot make the host apply an effect for an undeclared capability.
"""

from typing import Any, Callable, Dict, List, Optional

from ic_python_logging import get_logger

logger = get_logger("core.codex_bridge")


# ---------------------------------------------------------------------------
# Strict plain-data serializer (boundary crossing, both directions)
# ---------------------------------------------------------------------------
#
# Shared with ``core.extension_bridge`` — the boundary is defended identically
# whoever is behind it. Re-exported here so the long-standing
# ``codex_bridge.to_plain`` import path keeps working.

from core.bridge_core import MAX_DEPTH as _MAX_DEPTH  # noqa: F401
from core.bridge_core import (
    BridgeSerializationError,
    check_capability,
    to_plain,
)
from core.call_origin import codex_call, dispatch

# ---------------------------------------------------------------------------
# Verb registry — the GGG-public operations a codex may invoke
# ---------------------------------------------------------------------------
#
# A verb name is ``"<domain>.<verb>"`` and is BOTH the ``rpc`` action string
# the sandbox sends AND the token a codex declares in its manifest
# ``capabilities``. Each implementation talks to the realm through the public
# ``ggg`` API, imported lazily so importing this module never pulls in ``ggg``
# (which in turn imports ``core``).


def _v_config_get(**kwargs: Any) -> dict:
    """Return the realm's merged codex configuration (read-only)."""
    from core import codex_hooks

    return codex_hooks.get_config() or {}


def _v_time_now(**kwargs: Any) -> dict:
    """Return the current IC time as ``{"epoch": <seconds>, "ns": <nanos>}``.

    Codices should derive due dates etc. from this rather than importing host
    time utilities (unavailable inside the sandbox).
    """
    from _cdk import ic

    ns = int(ic.time())
    return {"epoch": ns // 1_000_000_000, "ns": ns}


def _project_user(user) -> Optional[dict]:
    if user is None:
        return None
    return {
        "id": getattr(user, "id", None),
        "name": getattr(user, "name", None),
    }


def _v_user_get(user_id: str = "", **kwargs: Any) -> Optional[dict]:
    """Look up a user by id; return a plain projection or ``None``."""
    from ggg import User

    if not user_id:
        return None
    return _project_user(User[user_id])


def _v_realm_get(**kwargs: Any) -> Optional[dict]:
    """Plain projection of the realm (identity + lifecycle stage)."""
    from ggg import Realm

    realms = Realm.instances()
    if not realms:
        return None
    realm = realms[0]
    return {
        "id": getattr(realm, "id", None),
        "name": getattr(realm, "name", None),
        "status": getattr(realm, "status", None),
        "accounting_currency": getattr(realm, "accounting_currency", None),
        "open_registration": getattr(realm, "open_registration", None),
    }


def _v_currency_get(**kwargs: Any) -> str:
    """Resolve the invoice/treasury currency symbol.

    Mirrors the codices' ``invoice_currency`` helper: codex-pinned
    ``currency.symbol`` from config, else the realm treasury symbol from
    ``realm_currency()``. Returns empty when neither has resolved.
    """
    from core import codex_hooks
    from core.realm_currency import realm_currency

    config = codex_hooks.get_config() or {}
    block = config.get("currency")
    if isinstance(block, dict):
        symbol = str(block.get("symbol") or "").strip()
        if symbol:
            return symbol[:16]
    acct = realm_currency()
    if acct:
        return acct[:16]
    return ""


# Member attributes a codex may set through ``member.activate`` (the codex
# supplies its own membership policy; the host only whitelists which fields
# may be written).
_MEMBER_FIELDS = (
    "identity_verification",
    "voting_eligibility",
    "public_benefits_eligibility",
    "residence_permit",
    "tax_compliance",
    "criminal_record",
)


def _find_member(user_id: str):
    from ggg import Member

    for member in Member.instances():
        user = getattr(member, "user", None)
        if user is not None and getattr(user, "id", None) == user_id:
            return member
    return None


def _v_member_activate(user_id: str = "", **kwargs: Any) -> dict:
    """Create or update a member for *user_id* (idempotent).

    Applies only whitelisted membership fields supplied by the codex. If a
    member already exists, its fields are updated; otherwise a new member is
    created bound to the user.
    """
    from ggg import Member, User

    if not user_id:
        raise ValueError("member.activate: user_id is required")
    user = User[user_id]
    if user is None:
        raise ValueError(f"member.activate: user '{user_id}' not found")

    fields = {k: kwargs[k] for k in _MEMBER_FIELDS if k in kwargs}
    existing = _find_member(user_id)
    if existing is not None:
        for key, value in fields.items():
            setattr(existing, key, value)
        return {
            "accepted": True,
            "member_id": getattr(existing, "id", None),
            "already_member": True,
        }
    member = Member(user=user, **fields)
    return {
        "accepted": True,
        "member_id": getattr(member, "id", None),
        "already_member": False,
    }


def _v_invoice_create(
    amount: float = 0.0,
    currency: str = "",
    due_date: str = "",
    status: str = "Pending",
    user_id: str = "",
    metadata: str = "",
    **kwargs: Any,
) -> dict:
    """Create an ``Invoice`` for *user_id*; return a plain projection."""
    from ggg import Invoice, User

    user = User[user_id] if user_id else None
    if user is None:
        raise ValueError(f"invoice.create: user '{user_id}' not found")
    invoice = Invoice(
        amount=amount,
        currency=currency,
        due_date=due_date,
        status=status,
        user=user,
        metadata=metadata,
    )
    return {
        "id": getattr(invoice, "id", None),
        "amount": amount,
        "currency": getattr(invoice, "currency", currency) or "",
        "status": status,
        "due_date": due_date,
    }


# Fields a codex may set on a notification (everything else is ignored so a
# hostile codex cannot smuggle constructor kwargs into the entity).
_NOTIFICATION_FIELDS = (
    "topic",
    "title",
    "message",
    "sender",
    "recipient",
    "read",
    "icon",
    "href",
    "color",
    "metadata",
    "timestamp_created",
)


def _v_proposal_find_executed(
    target_principal: str = "",
    profile_name: str = "",
    change: str = "assign",
    **kwargs: Any,
) -> Optional[dict]:
    """Find an executed governance proposal authorizing one role change.

    *change* is ``"assign"`` or ``"revoke"``. It is not called ``action``
    because the rpc bridge spends that name on the verb itself, and a kwarg
    that shadows it would arrive as a duplicate argument.

    Deliberately scoped: the codex asks "is *this* change approved?" and gets a
    small projection or ``None``. The alternative — handing a codex every
    proposal in the realm to filter itself — is both an unbounded read and a
    much wider disclosure than the question requires.

    Recognizes the legacy revocation encoding (a ``role_assignment`` proposal
    whose ``profile_name`` is ``revoke_<profile>``) so existing proposals keep
    authorizing the revocations they were voted for.
    """
    import json as _json

    from ggg import Proposal

    if not target_principal or not profile_name:
        return None

    wanted = "role_assignment" if change == "assign" else "role_revocation"
    legacy_profile = "revoke_" + profile_name

    for proposal in Proposal.instances():
        if getattr(proposal, "status", None) != "executed":
            continue
        raw = getattr(proposal, "metadata", "") or ""
        try:
            meta = _json.loads(raw) if raw else {}
        except Exception:
            continue
        if not isinstance(meta, dict):
            continue
        if meta.get("target_principal") != target_principal:
            continue

        matches = (
            meta.get("proposal_type") == wanted
            and meta.get("profile_name") == profile_name
        ) or (
            change == "revoke"
            and meta.get("proposal_type") == "role_assignment"
            and meta.get("profile_name") == legacy_profile
        )
        if matches:
            return {
                "id": getattr(proposal, "id", None),
                "proposal_type": meta.get("proposal_type"),
                "profile_name": meta.get("profile_name"),
            }
    return None


def _v_member_assign_profile(
    user_id: str = "", profile_name: str = "", **kwargs: Any
) -> dict:
    """Assign *profile_name* to *user_id* (idempotent)."""
    from ggg import User, UserProfile

    if not user_id or not profile_name:
        raise ValueError("member.assign_profile: user_id and profile_name are required")
    target = User[user_id]
    profile = UserProfile[profile_name]
    if target is None:
        raise ValueError(f"member.assign_profile: user '{user_id}' not found")
    if profile is None:
        raise ValueError(f"member.assign_profile: profile '{profile_name}' not found")
    current = [p.name for p in target.profiles]
    if profile_name not in current:
        target.profiles.add(profile)
    return {"success": True, "user_id": user_id, "profile_name": profile_name}


def _v_member_revoke_profile(
    user_id: str = "", profile_name: str = "", **kwargs: Any
) -> dict:
    """Revoke *profile_name* from *user_id* (idempotent)."""
    from ggg import User, UserProfile

    if not user_id or not profile_name:
        raise ValueError("member.revoke_profile: user_id and profile_name are required")
    target = User[user_id]
    profile = UserProfile[profile_name]
    if target is None:
        raise ValueError(f"member.revoke_profile: user '{user_id}' not found")
    if profile is None:
        raise ValueError(f"member.revoke_profile: profile '{profile_name}' not found")
    current = [p.name for p in target.profiles]
    if profile_name in current:
        target.profiles.remove(profile)
    return {"success": True, "user_id": user_id, "profile_name": profile_name}


def _v_realm_apply_init_policy(codex_id: str = "", **kwargs: Any) -> dict:
    """Apply post-install realm policy from an installed codex package."""
    from core.codex_init_host import apply_init_policy

    if not codex_id:
        raise ValueError("realm.apply_init_policy: codex_id is required")
    return apply_init_policy(codex_id)


def _v_org_seed_template(
    codex_id: str = "", template: str = "departments", **kwargs: Any
) -> dict:
    """Seed organizations from a codex package data template."""
    from core.codex_init_host import seed_org_template

    if not codex_id:
        raise ValueError("org.seed_template: codex_id is required")
    return seed_org_template(codex_id, template=template or "departments")


def _v_justice_seed_template(codex_id: str = "", **kwargs: Any) -> dict:
    """Seed the justice court hierarchy from a codex package."""
    from core.codex_init_host import seed_justice_from_package

    if not codex_id:
        raise ValueError("justice.seed_template: codex_id is required")
    return seed_justice_from_package(codex_id)


def _v_tax_policy(**kwargs: Any) -> dict:
    """Tax parameters in force (see ``core.taxation.effective_policy``)."""
    from core.taxation import effective_policy

    return effective_policy()


def _v_notification_create(user_id: str = "", **kwargs: Any) -> dict:
    """Create a ``Notification`` (optionally bound to *user_id*)."""
    from ggg import Notification, User

    fields = {k: kwargs[k] for k in _NOTIFICATION_FIELDS if k in kwargs}
    if user_id:
        user = User[user_id]
        if user is None:
            raise ValueError(f"notification.create: user '{user_id}' not found")
        fields["user"] = user
    notification = Notification(**fields)
    return {"id": getattr(notification, "id", None)}


# name -> implementation. Keep additions here small and reviewed: every entry
# widens the codex-facing API surface.
VERBS: Dict[str, Callable[..., Any]] = {
    "config.get": _v_config_get,
    "currency.get": _v_currency_get,
    "time.now": _v_time_now,
    "user.get": _v_user_get,
    "realm.get": _v_realm_get,
    "proposal.find_executed": _v_proposal_find_executed,
    "tax.policy": _v_tax_policy,
    "member.activate": _v_member_activate,
    "member.assign_profile": _v_member_assign_profile,
    "member.revoke_profile": _v_member_revoke_profile,
    "invoice.create": _v_invoice_create,
    "notification.create": _v_notification_create,
    "realm.apply_init_policy": _v_realm_apply_init_policy,
    "org.seed_template": _v_org_seed_template,
    "justice.seed_template": _v_justice_seed_template,
}

# Effects the host applies asynchronously (vault ICRC calls, etc.) rather than
# inside ``apply_effects``'s synchronous batch.
ASYNC_EFFECT_VERBS = frozenset({"treasury.transfer"})


def _all_verbs():
    return frozenset(VERBS) | ASYNC_EFFECT_VERBS


def known_verbs() -> List[str]:
    """Sorted list of every registered capability name (sync + async effects)."""
    return sorted(_all_verbs())


# ---------------------------------------------------------------------------
# Authorization + reserved-domain hook point
# ---------------------------------------------------------------------------


def authorize(action: str, capabilities: List[str]) -> Optional[str]:
    """Return an error string if *action* is not permitted, else ``None``.

    An action is permitted only when it is a registered verb AND the codex
    declared it in its manifest ``capabilities``.
    """
    return check_capability(action, capabilities, _all_verbs(), subject="codex")


def reserved_domain_denied(action: str, context_id: str) -> Optional[str]:
    """Hook point for future realm hierarchy (issue #265 non-goal).

    A parent (e.g. national) codex layer will be able to claim exclusive
    competency over a domain; a child codex calling into that domain would be
    denied here. Today no domains are reserved, so this always allows.
    """
    return None


# ---------------------------------------------------------------------------
# Live reads over ``rpc`` (host callback from inside the subinterpreter)
# ---------------------------------------------------------------------------

# Verbs that only read realm state. A sandboxed hook may call these live,
# mid-execution, through the ``rpc`` builtin. Writes are deliberately excluded:
# they stay post-hoc effects so the host applies them as one authorized,
# ordered, reviewable batch once the hook has returned, rather than letting a
# hook interleave mutations with arbitrary computation.
READ_VERBS = frozenset(
    {
        "config.get",
        "currency.get",
        "time.now",
        "user.get",
        "realm.get",
        "proposal.find_executed",
        "tax.policy",
    }
)


def readable_capabilities(capabilities: List[str]) -> List[str]:
    """The subset of *capabilities* a hook may invoke over ``rpc``."""
    return sorted(set(capabilities or ()) & READ_VERBS)


def make_rpc_handler(context_id: str, capabilities: List[str]):
    """Build the host-side ``rpc`` handler for one sandboxed hook call.

    Basilisk invokes this synchronously as ``handler(context_id, action,
    kwargs)`` whenever sandboxed code calls ``rpc(...)``. It enforces the same
    checks as ``apply_effects`` — read-only, registered, declared, not
    reserved — and passes both arguments and result through ``to_plain`` so no
    live object can cross the boundary in either direction.

    Raising propagates into the sandbox as an exception, which the codex may
    catch; it can never turn into an unauthorized read.
    """
    caps = list(capabilities or ())

    def handler(ctx_id: str, action: str, kwargs: Any) -> Any:
        if not isinstance(action, str):
            raise PermissionError("rpc action must be a string")
        if action not in READ_VERBS:
            raise PermissionError(
                f"rpc '{action}' is not a read verb; writes must be returned "
                f"as effects so the host applies them after the hook returns"
            )
        auth_error = authorize(action, caps)
        if auth_error:
            logger.warning(
                f"codex_bridge[{context_id}]: denied rpc '{action}': {auth_error}"
            )
            raise PermissionError(f"rpc '{action}' denied: {auth_error}")

        reserved_error = reserved_domain_denied(action, context_id)
        if reserved_error:
            raise PermissionError(f"rpc '{action}' denied: {reserved_error}")

        safe_kwargs = to_plain(kwargs or {})
        if not isinstance(safe_kwargs, dict):
            raise PermissionError(f"rpc '{action}' kwargs must be an object")
        return to_plain(dispatch(VERBS, action, codex_call(context_id), **safe_kwargs))

    return handler


# ---------------------------------------------------------------------------
# Effect application (the single trust checkpoint)
# ---------------------------------------------------------------------------

# ``$eff:<index>:<field>`` — a reference to a field of the result produced by an
# earlier effect in the same batch. Emitted by the in-sandbox SDK's ``create``
# helpers (whose real ids are unknown until the host applies them), then resolved
# here so a hook can, e.g., put a fresh invoice id in a later notification.
#
# Parsed with plain string operations: the frozen basilisk stdlib in the canister
# ships only a stub ``re`` module (no ``re.compile``).
_REF_PREFIX = "$eff:"


def _parse_ref(text: str, start: int):
    """Parse a ``$eff:<n>:<field>`` token at *start* (which must point at the
    prefix). Returns ``(index, field, end)`` or ``None`` if malformed."""
    i = start + len(_REF_PREFIX)
    j = i
    while j < len(text) and text[j].isdigit():
        j += 1
    if j == i or j >= len(text) or text[j] != ":":
        return None
    k = j + 1
    m = k
    while m < len(text) and (text[m].isalnum() or text[m] == "_"):
        m += 1
    if m == k:
        return None
    return int(text[i:j]), text[k:m], m


def _lookup_ref(index: int, field: str, results: List[Any]) -> Any:
    if 0 <= index < len(results):
        res = results[index]
        if isinstance(res, dict):
            return res.get(field)
    return None


def _resolve_str_refs(value: str, results: List[Any]) -> Any:
    """Resolve ``$eff`` tokens in one string.

    A string that is exactly one token becomes the raw referenced value (which
    may be non-string); a token embedded in a larger string is substituted with
    its ``str()`` (``None`` becomes "").
    """
    whole = None
    if value.startswith(_REF_PREFIX):
        whole = _parse_ref(value, 0)
    if whole is not None and whole[2] == len(value):
        return _lookup_ref(whole[0], whole[1], results)

    parts = []
    pos = 0
    while True:
        found = value.find(_REF_PREFIX, pos)
        if found < 0:
            parts.append(value[pos:])
            break
        ref = _parse_ref(value, found)
        if ref is None:
            parts.append(value[pos : found + len(_REF_PREFIX)])
            pos = found + len(_REF_PREFIX)
            continue
        index, field, end = ref
        resolved = _lookup_ref(index, field, results)
        parts.append(value[pos:found])
        parts.append("" if resolved is None else str(resolved))
        pos = end
    return "".join(parts)


def _resolve_refs(value: Any, results: List[Any]) -> Any:
    """Replace ``$eff:n:field`` references in *value* using prior *results*.

    Strings are resolved via ``_resolve_str_refs``; lists/dicts are walked
    recursively.
    """
    if isinstance(value, str):
        return _resolve_str_refs(value, results)
    if isinstance(value, list):
        return [_resolve_refs(item, results) for item in value]
    if isinstance(value, dict):
        return {key: _resolve_refs(item, results) for key, item in value.items()}
    return value


def apply_effects(
    context_id: str,
    capabilities: List[str],
    effects: List[Any],
    defer_async: bool = False,
):
    """Authorize and apply a batch of sandbox-proposed effects, in order.

    ``effects`` is the plain-data list a sandboxed hook returned, each item
    ``{"verb": str, "kwargs": dict}``. For each, this authorizes the verb against
    *capabilities*, resolves ``$eff`` references against earlier results, and
    dispatches the verb (whose kwargs and result are strictly plain-data
    validated). Returns the index-aligned list of per-effect results — the same
    list that backs reference resolution and the hook's return value.

    When *defer_async* is True, effects whose verb is in ``ASYNC_EFFECT_VERBS``
    are authorized and returned for the caller to apply asynchronously instead
    of being dispatched here; the return value is ``(results, deferred)``.

    Raises ``PermissionError`` on any unauthorized/malformed effect; the caller
    decides whether to fall back in-process.
    """
    caps = list(capabilities or ())
    if not isinstance(effects, list):
        raise PermissionError("sandbox effects must be a list")

    results: List[Any] = []
    deferred: List[dict] = []
    for i, effect in enumerate(effects):
        if not isinstance(effect, dict):
            raise PermissionError(f"effect #{i} must be an object")
        verb = effect.get("verb")
        if not isinstance(verb, str):
            raise PermissionError(f"effect #{i} 'verb' must be a string")
        safe_kwargs = to_plain(effect.get("kwargs") or {})
        if not isinstance(safe_kwargs, dict):
            raise PermissionError(f"effect #{i} 'kwargs' must be an object")

        auth_error = authorize(verb, caps)
        if auth_error:
            logger.warning(
                f"codex_bridge[{context_id}]: denied effect '{verb}': {auth_error}"
            )
            raise PermissionError(f"effect '{verb}' denied: {auth_error}")

        reserved_error = reserved_domain_denied(verb, context_id)
        if reserved_error:
            logger.warning(
                f"codex_bridge[{context_id}]: reserved-domain denied '{verb}': "
                f"{reserved_error}"
            )
            raise PermissionError(f"effect '{verb}' denied: {reserved_error}")

        resolved = _resolve_refs(safe_kwargs, results)
        if verb in ASYNC_EFFECT_VERBS:
            if defer_async:
                deferred.append({"verb": verb, "kwargs": resolved})
                results.append(None)
                continue
            raise PermissionError(
                f"effect '{verb}' must be applied asynchronously by the host"
            )
        result = to_plain(dispatch(VERBS, verb, codex_call(context_id), **resolved))
        results.append(result)
    if defer_async:
        return results, deferred
    return results


def resolve_result(result: Any, results: List[Any]) -> Any:
    """Resolve ``$eff`` references in a hook's plain-data return value."""
    if result is None:
        return None
    return _resolve_refs(to_plain(result), results)
