"""Public GGG facade for codices (issue #265, Workstream A).

A codex may call the realm only through the public ``ggg`` API. Historically
the shipped codices reached into ``core.*`` for a small set of helpers; that
couples legislation to host internals that can change without notice, and —
once codices run sandboxed (issue #265, Workstream C) — those imports fail
outright.

This module re-exposes exactly that set of helpers as a supported surface.
Implementations still live in ``core``; they are imported lazily inside each
wrapper so that importing ``ggg`` never triggers a package-level
``ggg -> core`` import cycle (``core`` already depends on ``ggg``).
"""

from typing import Any, Iterator


# ---------------------------------------------------------------------------
# Membership (was: core.membership)
# ---------------------------------------------------------------------------


def iter_users() -> Iterator:
    """Iterate every ``User`` via a paginated scan. Admin/display paths only."""
    from core.membership import iter_users as _impl

    return _impl()


def user_has_profile(user, profile) -> bool:
    """Whether *user* holds *profile* (name or entity)."""
    from core.membership import user_has_profile as _impl

    return _impl(user, profile)


def user_in_department(user, dept) -> bool:
    """Whether *user* is a member of *dept* (name or entity)."""
    from core.membership import user_in_department as _impl

    return _impl(user, dept)


# ---------------------------------------------------------------------------
# Access control (was: core.access._check_access)
# ---------------------------------------------------------------------------


def check_access(caller_principal: str, operation: str) -> bool:
    """Public access check: does *caller_principal* hold *operation*?

    Public replacement for the private ``core.access._check_access`` that
    codices previously imported.
    """
    from core.access import _check_access

    return _check_access(caller_principal, operation)


# ---------------------------------------------------------------------------
# Organizations (was: core.org_policy)
# ---------------------------------------------------------------------------


def ensure_root_org(head_user=None):
    """Create the quarter ``root`` org if missing; return the root Department."""
    from core.org_policy import ensure_root_org as _impl

    return _impl(head_user=head_user)


def grant_root_authority_over_local_orgs():
    """Ensure root holds default manage permissions over every other local org."""
    from core.org_policy import grant_root_authority_over_local_orgs as _impl

    return _impl()


# ---------------------------------------------------------------------------
# Extensions (was: core.extensions)
# ---------------------------------------------------------------------------


def extension_call(extension_name: str, function_name: str, args: str) -> Any:
    """Call another extension's backend function.

    Async: drive with ``yield from ggg.extension_call(...)``. Public
    replacement for ``core.extensions.extension_async_call``.
    """
    from core.extensions import extension_async_call

    return extension_async_call(extension_name, function_name, args)


def extension_entity_class(extension_name: str):
    """Return an ``Entity`` base class namespaced to *extension_name*.

    Public replacement for ``core.extensions.create_extension_entity_class``.
    """
    from core.extensions import create_extension_entity_class

    return create_extension_entity_class(extension_name)


FACADE_NAMES = (
    "iter_users",
    "user_has_profile",
    "user_in_department",
    "check_access",
    "ensure_root_org",
    "grant_root_authority_over_local_orgs",
    "extension_call",
    "extension_entity_class",
)
