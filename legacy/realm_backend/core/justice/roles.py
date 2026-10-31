"""Who may see and do what in the justice system.

A litigation is **private by default**: visible to its submitter and to the
justice department, and to nobody else. The defendant is deliberately not on that
list — being accused does not entitle you to read the accusation's encrypted
contents, which is a decision about the realm's dispute process rather than an
oversight.

Three tiers, and they are about *visibility*:

  submitter        their own cases
  justice member   every case (they are the ones who adjudicate)
  justice head     as above, plus managing sharing and court administration

Separately from visibility there are RBAC operations — ``resolution.issue``,
``fine.apply``, ``appeal.allow`` — which gate the individual actions. Both have to
hold. Being able to see a case is not being able to rule on it.

When no ``Justice`` department has been seeded, membership falls back to the realm
administrators (the ``member_data_readers`` crypto group), so a fresh realm can
still file and read litigations.
"""

from typing import List

from ic_python_logging import get_logger

logger = get_logger("core.justice.roles")

# The department whose members adjudicate, and for whom a litigation's DEK is
# IBE-wrapped. A realm renames its Department to change this.
JUSTICE_DEPARTMENT = "Justice"

# RBAC operations gating the individual actions, mirroring the extension's
# manifest so the entry-point gate and the verb gate cannot drift.
OP_VIEW = "dispute.view"
OP_CREATE = "dispute.create"
OP_ASSIGN = "dispute.assign"
OP_ISSUE = "resolution.issue"
OP_FINE = "fine.apply"
OP_APPEAL = "appeal.allow"
OP_ADMIN = "realm.admin"


def _auth_context():
    from core.crypto_scopes import production_context

    return production_context()


def is_realm_admin(caller: str) -> bool:
    if not caller:
        return False
    try:
        return bool(_auth_context().is_realm_admin(caller))
    except Exception:
        return False


def is_justice_head(caller: str) -> bool:
    if not caller:
        return False
    try:
        return bool(_auth_context().is_department_head(JUSTICE_DEPARTMENT, caller))
    except Exception:
        return False


def justice_principals() -> List[str]:
    """The justice department's members and head.

    These are the recipients — besides the submitter — for whom a litigation's
    data-encryption key is wrapped, so the list has to be readable by anyone who
    can file one. It is a list of role holders, not member data.
    """
    principals: List[str] = []
    try:
        from ggg import Department

        dept = Department[JUSTICE_DEPARTMENT]
        if dept is not None:
            from core.membership import department_member_principals

            principals = list(department_member_principals(dept, include_head=True))
    except Exception as e:
        logger.warning(f"justice_principals: {e}")

    if not principals:
        try:
            from api.crypto import group_members

            for member in group_members("member_data_readers").get("members", []):
                principal = member.get("principal")
                if principal and principal not in principals:
                    principals.append(principal)
        except Exception as e:
            logger.warning(f"justice_principals admin fallback: {e}")

    return principals


def is_justice_member(caller: str) -> bool:
    """Forward membership check — no full user scan (issue #242)."""
    if not caller:
        return False
    try:
        from core.membership import user_in_department
        from ggg import Department, User

        dept = Department[JUSTICE_DEPARTMENT]
        if dept is not None:
            head = getattr(dept, "head", None)
            if head is not None and str(getattr(head, "id", "")) == caller:
                return True
            return bool(user_in_department(User[caller], dept))
    except Exception:
        pass
    return caller in justice_principals()


def sees_all_cases(caller: str) -> bool:
    """Whether *caller* may read every case rather than only their own."""
    return is_realm_admin(caller) or is_justice_member(caller)


def can_manage_courts(caller: str) -> bool:
    """Court administration: realm admins and the justice department head."""
    return is_realm_admin(caller) or is_justice_head(caller)


# ---------------------------------------------------------------------------
# RBAC operations
# ---------------------------------------------------------------------------


def has_operation(caller: str, operation: str) -> bool:
    from core.extension_bridge import caller_has_operation

    return caller_has_operation(caller, operation)


def require_operation(caller: str, operation: str, what: str) -> None:
    """Refuse unless *caller* holds *operation*.

    Realm admins pass without holding it explicitly, matching every other bridge
    family; an admin who could not execute a penalty could not fix a stuck one.
    """
    if is_realm_admin(caller) or has_operation(caller, operation):
        return
    raise PermissionError(f"{what} requires the '{operation}' operation")


# ---------------------------------------------------------------------------
# Users and cases
# ---------------------------------------------------------------------------


def get_user(caller: str):
    from ggg import User

    if not caller:
        raise PermissionError("Not authenticated")
    user = User[caller]
    if not user:
        raise PermissionError(f"User {caller} not found")
    return user


def principal_of(user) -> str:
    """The principal of a ``User`` row, which is its ``id`` field.

    Not ``_id``: that is the ORM's sequential key, and it is what clients pass as
    a *filter* (``resolve_user_id`` returns it). Comparing it to a caller would
    silently never match, so every identity check goes through here.
    """
    return str(getattr(user, "id", "") or "") if user is not None else ""


def case_submitter(case) -> str:
    """The plaintiff's principal, for comparison against a caller."""
    return principal_of(getattr(case, "plaintiff", None))


def can_view_case(case, caller: str) -> bool:
    """Submitter, justice department, or realm admin. Not the defendant."""
    if not caller:
        return False
    return sees_all_cases(caller) or case_submitter(case) == caller


def can_manage_case(case, caller: str) -> bool:
    """Who may set a litigation's ciphertext and manage its sharing.

    Narrower than viewing: a justice *member* reads the case, but only the
    submitter, the department head or an admin may rewrite its contents.
    """
    if not caller:
        return False
    return (
        is_realm_admin(caller)
        or is_justice_head(caller)
        or case_submitter(case) == caller
    )


def describe(caller: str) -> dict:
    """The caller's own standing, so the UI can hide what it cannot do.

    Advisory only — every verb re-checks.
    """
    return {
        "principal": caller,
        "department": JUSTICE_DEPARTMENT,
        "is_admin": is_realm_admin(caller),
        "is_justice_head": is_justice_head(caller),
        "is_justice_member": is_justice_member(caller),
        "can_view_all": sees_all_cases(caller),
        "can_manage_courts": can_manage_courts(caller),
        "can_file": is_realm_admin(caller) or has_operation(caller, OP_CREATE),
        "can_assign": is_realm_admin(caller) or has_operation(caller, OP_ASSIGN),
        "can_issue_verdict": is_realm_admin(caller) or has_operation(caller, OP_ISSUE),
        "can_apply_fine": is_realm_admin(caller) or has_operation(caller, OP_FINE),
        "can_decide_appeal": is_realm_admin(caller) or has_operation(caller, OP_APPEAL),
    }
