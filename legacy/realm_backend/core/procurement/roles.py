"""Procurement authorization.

Four roles, each answering a different question about the same caller:

  requester  may create RFPs
  vendor     may submit bids
  evaluator  may score bids, and may see them once revealed
  approver   may award and may execute the contract

They are not a hierarchy — a realm admin holds all of them, but an evaluator is
not a vendor. The separation is the point: the party who scores a bid must not be
the party who submitted it.

Membership of the Procurement department confers the evaluator role, which is why
the check is a forward lookup on the caller rather than a scan of every user
(issue #242).
"""

from typing import List

# Extension-local operation strings, so a realm can grant procurement rights
# without the core Operations enum having to know about them.
RFP_CREATE = "procurement.rfp.create"
RFP_PUBLISH = "procurement.rfp.publish"
BID_SUBMIT = "procurement.bid.submit"
EVALUATE = "procurement.evaluate"
AWARD = "procurement.award"
EXECUTE = "procurement.execute"

PROCUREMENT_DEPARTMENT = "Procurement"

# Actor recorded for transitions made by the scheduled sweep rather than a
# person. Never a valid caller — the bridge always passes an authenticated one.
SYSTEM_ACTOR = "system"


def now_epoch() -> int:
    from _cdk import ic

    return int(ic.time() // 1_000_000_000)


def get_user(caller: str):
    from ggg import User

    if not caller:
        raise PermissionError("Not authenticated")
    user = User[caller]
    if not user:
        raise PermissionError(f"User {caller} not found")
    return user


def is_allowed(user, operation: str) -> bool:
    """Whether *user* holds *operation*, via profile, direct grant, or profile
    permission."""
    from ggg.system.user_profile import OPERATIONS_SEPARATOR, Operations

    for profile in (user.profiles or []):
        allowed = str(profile.allowed_to or "").split(OPERATIONS_SEPARATOR)
        if Operations.ALL in allowed or operation in allowed:
            return True
    try:
        for permission in user.permissions:
            if permission.name == operation:
                return True
    except Exception:
        pass
    try:
        for profile in (user.profiles or []):
            for permission in profile.permissions:
                if permission.name == operation:
                    return True
    except Exception:
        pass
    return False


def require_op(user, operation: str) -> None:
    if not is_allowed(user, operation):
        raise PermissionError(
            f"Access denied: user {user.id} lacks permission '{operation}'"
        )


def is_realm_admin(caller: str) -> bool:
    if not caller:
        return False
    try:
        from core.crypto_scopes import production_context

        return bool(production_context().is_realm_admin(caller))
    except Exception:
        return False


def department_member_principals(department: str) -> List[str]:
    try:
        from core.membership import department_member_principals as members
        from ggg import Department

        dept = Department[department]
        if not dept:
            return []
        return list(members(dept, include_head=True))
    except Exception:
        return []


def is_evaluator(user) -> bool:
    if is_realm_admin(str(user.id)):
        return True
    if is_allowed(user, EVALUATE):
        return True
    try:
        from core.membership import user_in_department
        from ggg import Department

        dept = Department[PROCUREMENT_DEPARTMENT]
        if dept is None:
            return False
        head = getattr(dept, "head", None)
        if head is not None and getattr(head, "id", None) == str(user.id):
            return True
        return bool(user_in_department(user, dept))
    except Exception:
        return str(user.id) in department_member_principals(PROCUREMENT_DEPARTMENT)


def is_approver(user) -> bool:
    if is_realm_admin(str(user.id)):
        return True
    return is_allowed(user, AWARD) or is_allowed(user, EXECUTE)


def is_requester(user) -> bool:
    return is_realm_admin(str(user.id)) or is_allowed(user, RFP_CREATE)


def is_vendor(user) -> bool:
    return is_realm_admin(str(user.id)) or is_allowed(user, BID_SUBMIT)


def list_evaluator_principals() -> List[str]:
    """Principals who may evaluate bids.

    A vendor needs this to know who to wrap its bid key for, so it is readable by
    any authenticated caller — it is a list of role holders, not member data.
    """
    principals = set(department_member_principals(PROCUREMENT_DEPARTMENT))
    try:
        from ggg import User

        for user in User.instances():
            uid = str(getattr(user, "id", "") or "")
            if uid and is_evaluator(user):
                principals.add(uid)
    except Exception:
        pass
    return sorted(principals)


def describe(user) -> dict:
    """The caller's own roles, so the UI can hide what it cannot do.

    Advisory only — every verb re-checks. A sandboxed extension being told
    ``can_award`` is not what lets it award.
    """
    return {
        "principal": str(user.id),
        "is_admin": is_realm_admin(str(user.id)),
        "is_requester": is_requester(user),
        "is_vendor": is_vendor(user),
        "is_evaluator": is_evaluator(user),
        "is_approver": is_approver(user),
    }
