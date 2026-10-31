"""Pluggable authorization for encrypted-sharing *scopes*.

A *scope* is a colon-delimited string that names an encrypted payload and
determines **who may manage read access** to it (i.e. who can grant/revoke the
wrapped DEK envelopes for other principals). The first segment is the scope
*kind*, which selects an authorization policy:

    user:<principal>:<name>     owned by <principal> (self-service)
    dept:<department>:<name>    managed by the department head or a realm admin
    realm:<name>                managed by realm admins

This keeps the crypto engine (toolkit ``CryptoService`` + IBE root-key) fully
generic: only the *policy* of who-may-manage-a-scope lives here, and new scope
kinds can be added with the :func:`scope_kind` decorator without touching the
canister endpoints.

The policy functions take an injected :class:`ScopeAuthContext` so this module
is free of canister/ggg imports and can be unit-tested in plain CPython. The
production context (:func:`production_context`) wires the predicates to the
realm's RBAC (``core.access``) and ``ggg`` entities lazily.

Note: this governs *management* (grant/revoke). Reading is always self-limited —
a principal can only ever fetch the envelope wrapped for *itself* (see
``crypto_get_my_envelope``), and only the holder of the matching vetKey can
actually decrypt it.
"""

from __future__ import annotations

from typing import Callable, Protocol

SCOPE_SEP = ":"


class ScopeAuthContext(Protocol):
    """Capabilities a scope policy may consult about a caller."""

    def is_realm_admin(self, caller: str) -> bool: ...

    def is_department_head(self, department: str, caller: str) -> bool: ...


# Registry: scope kind -> predicate(parts, caller, ctx) -> bool
_MANAGERS: dict[str, Callable[[list[str], str, ScopeAuthContext], bool]] = {}


def scope_kind(kind: str):
    """Register a management-authorization predicate for a scope *kind*.

    The predicate receives ``(parts, caller, ctx)`` where ``parts`` is the
    scope split on ``:`` (so ``parts[0]`` is the kind), ``caller`` is the
    principal text, and ``ctx`` is a :class:`ScopeAuthContext`.
    """

    def deco(fn):
        _MANAGERS[kind] = fn
        return fn

    return deco


def parse_scope(scope: str) -> tuple[str, list[str]]:
    """Return ``(kind, parts)`` for a scope string."""
    parts = (scope or "").split(SCOPE_SEP)
    return parts[0], parts


def registered_scope_kinds() -> list[str]:
    """List the scope kinds with a registered management policy."""
    return sorted(_MANAGERS.keys())


def caller_can_manage_scope(scope: str, caller: str, ctx: ScopeAuthContext) -> bool:
    """Whether *caller* may grant/revoke access for *scope*.

    Unknown kinds (and malformed scopes) are denied.
    """
    if not scope or not caller:
        return False
    kind, parts = parse_scope(scope)
    handler = _MANAGERS.get(kind)
    if handler is None:
        return False
    try:
        return bool(handler(parts, caller, ctx))
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Built-in scope kinds
# ---------------------------------------------------------------------------


@scope_kind("user")
def _manage_user_scope(parts: list[str], caller: str, ctx: ScopeAuthContext) -> bool:
    """``user:<principal>:<name>`` — only the owning principal may manage."""
    return len(parts) >= 3 and parts[1] == caller


@scope_kind("dept")
def _manage_dept_scope(parts: list[str], caller: str, ctx: ScopeAuthContext) -> bool:
    """``dept:<department>:<name>`` — the department head or a realm admin."""
    if len(parts) < 3 or not parts[1]:
        return False
    department = parts[1]
    return ctx.is_realm_admin(caller) or ctx.is_department_head(department, caller)


@scope_kind("realm")
def _manage_realm_scope(parts: list[str], caller: str, ctx: ScopeAuthContext) -> bool:
    """``realm:<name>`` — realm admins only."""
    return len(parts) >= 2 and bool(parts[1]) and ctx.is_realm_admin(caller)


# ---------------------------------------------------------------------------
# Scope builders (mirror the frontend helpers in crypto/sharing.ts)
# ---------------------------------------------------------------------------


def user_scope(principal: str, name: str = "private") -> str:
    return f"user{SCOPE_SEP}{principal}{SCOPE_SEP}{name}"


def dept_scope(department: str, name: str) -> str:
    return f"dept{SCOPE_SEP}{department}{SCOPE_SEP}{name}"


def realm_scope(name: str) -> str:
    return f"realm{SCOPE_SEP}{name}"


# ---------------------------------------------------------------------------
# Production context (lazy ggg / RBAC wiring)
# ---------------------------------------------------------------------------


def production_context() -> ScopeAuthContext:
    """Build the live :class:`ScopeAuthContext` backed by realm RBAC + ggg."""

    class _ProdContext:
        def is_realm_admin(self, caller: str) -> bool:
            try:
                from core.access import _check_access
                from ggg.system.user_profile import Operations

                return _check_access(caller, Operations.REALM_ADMIN)
            except Exception:
                return False

        def is_department_head(self, department: str, caller: str) -> bool:
            try:
                from ggg import Department

                dept = Department[department]
                head = getattr(dept, "head", None) if dept else None
                return bool(head) and str(getattr(head, "id", "")) == caller
            except Exception:
                return False

    return _ProdContext()
