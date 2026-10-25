"""Encrypted litigation contents and the ``litigation:`` sharing scope.

A private litigation is two records. The public ``Case`` carries the procedural
facts — who filed, against whom, in which court, what the status is — with an
empty title and description. The real content is an opaque AES-GCM blob in
``LitigationContent``, keyed by the case id so the ``Case`` schema is untouched.

The canister never holds the plaintext, the data-encryption key, or any vetKey.
Who can decrypt is governed by ``KeyEnvelope`` records at
``litigation:<department>:<submitter>:<case_id>``, managed through the realm's
generic grant/revoke endpoints. That arrangement is unchanged by the port; what
moved host-side is the scope policy that decides who may issue those grants.
"""

from typing import Optional

from core.justice.roles import JUSTICE_DEPARTMENT

EXT_ID = "justice_litigation"
CONTENT_TYPE = "LitigationContent"


def content_class():
    from core.extension_bridge import own_entity_class

    return own_entity_class(EXT_ID, CONTENT_TYPE)


def find(case_id) -> Optional[object]:
    try:
        return content_class()[str(case_id)]
    except Exception:
        return None


def scope_for(submitter: str, case_id) -> str:
    return f"litigation:{JUSTICE_DEPARTMENT}:{submitter}:{case_id}"


def create(case_id, submitter: str):
    """The empty content record that pairs with a newly filed case."""
    scope = scope_for(submitter, case_id)
    content_class()(
        case_id=str(case_id),
        ciphertext="",
        scope=scope,
        created_by=submitter,
    )
    return scope


def register_scope_policy() -> None:
    """Register the ``litigation:`` scope kind.

    Called at import of :mod:`core.justice.verbs`. Kept in a function so the
    decorator's side effect is explicit rather than a surprise of importing this
    module.
    """
    from core.crypto_scopes import ScopeAuthContext, scope_kind

    @scope_kind("litigation")
    def _manage_litigation_scope(parts, caller, ctx: ScopeAuthContext) -> bool:
        """``litigation:<department>:<submitter>:<case_id>``.

        Grant and revoke are for the submitter, the justice department head, or a
        realm admin. Department *members* are recipients of a grant, not issuers
        of one — a member who could re-share would be able to widen access to a
        case beyond the department it was entrusted to.
        """
        if len(parts) < 4 or not parts[1] or not parts[2]:
            return False
        department, submitter = parts[1], parts[2]
        return (
            caller == submitter
            or ctx.is_realm_admin(caller)
            or ctx.is_department_head(department, caller)
        )

    return _manage_litigation_scope
