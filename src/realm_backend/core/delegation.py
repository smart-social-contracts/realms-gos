"""Principal delegation — grant, accept, revoke, and authorize act-on-behalf calls."""

from __future__ import annotations

import json
import secrets
import time
from dataclasses import dataclass
from typing import Any

from _cdk import ic
from core.access import AccessDenied, _check_access
from ggg import Notification, User
from ggg.governance.delegation import (
    STATUS_ACTIVE,
    STATUS_EXPIRED,
    STATUS_PENDING,
    STATUS_REVOKED,
    Delegation,
)
from ggg.system.user_profile import Operations
from ic_python_logging import get_logger

logger = get_logger("core.delegation")

# Operations whose endpoints honour ``on_behalf_of``. Granting anything else
# would look like power but do nothing, so grants are limited to these.
DELEGABLE_OPERATIONS: dict[str, dict[str, str]] = {
    Operations.PROPOSAL_VOTE: {
        "label": "Vote on proposals",
        "description": "Cast yes / no / abstain votes in your name.",
    },
    Operations.PROPOSAL_CREATE: {
        "label": "Submit proposals",
        "description": "Submit polls and other general proposals in your name.",
    },
    Operations.TRANSFER_CREATE: {
        "label": "Propose treasury transactions",
        "description": (
            "Submit transaction proposals (together with Submit proposals); "
            "they still need the vote to pass."
        ),
    },
}

MAX_DELEGATION_SECONDS = 366 * 24 * 3600
_PAGE = 50


def _now_ts() -> int:
    try:
        t = ic.time()
        if t and t > 0:
            return int(t) // 1_000_000_000
    except Exception:
        pass
    t = time.time()
    return int(t) if t and t > 1_000_000 else 9_999_999_999


def _parse_scope(scope_json: str) -> dict[str, Any]:
    try:
        raw = json.loads(scope_json or "{}")
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def scope_allows(scope: dict[str, Any], operation: str) -> bool:
    if scope.get("all"):
        return True
    ops = scope.get("operations") or []
    if not isinstance(ops, list):
        return False
    return operation in ops or Operations.ALL in ops


def _user_has_operation(user: User, operation: str) -> bool:
    for profile in user.profiles:
        allowed = str(profile.allowed_to or "").split(",")
        if Operations.ALL in allowed or operation in allowed:
            return True
    try:
        for perm in user.permissions:
            if perm.name == operation:
                return True
    except Exception:
        pass
    try:
        for profile in user.profiles:
            for perm in profile.permissions:
                if perm.name == operation:
                    return True
    except Exception:
        pass
    try:
        for department in user.departments:
            for perm in department.permissions:
                if perm.name == operation:
                    return True
    except Exception:
        pass
    return False


def _validate_scope_for_grantor(grantor: User, scope: dict[str, Any]) -> str | None:
    if scope.get("all"):
        if not _user_has_operation(grantor, Operations.ALL):
            return "Grantor lacks admin scope required for all-operations delegation"
        return None
    ops = scope.get("operations") or []
    if not isinstance(ops, list) or not ops:
        return "scope.operations must be a non-empty list (or set all: true)"
    for op in ops:
        if not isinstance(op, str) or not op.strip():
            return "Invalid operation in scope"
        if op != Operations.ALL and op not in DELEGABLE_OPERATIONS:
            return f"'{op}' cannot be delegated"
        if op == Operations.ALL and not _user_has_operation(grantor, Operations.ALL):
            return "Grantor cannot delegate Operations.ALL"
        if op != Operations.ALL and not _user_has_operation(grantor, op):
            return f"Grantor lacks permission '{op}' and cannot delegate it"
    return None


def _delegation_to_dict(d: Delegation) -> dict[str, Any]:
    return {
        "id": d.id,
        "grantor": d.grantor,
        "delegate": d.delegate,
        "scope": _parse_scope(d.scope_json),
        "status": d.status,
        "label": d.label or "",
        "description": d.description or "",
        "requires_acceptance": bool(int(d.requires_acceptance or 0)),
        "granted_by": d.granted_by or "",
        "accepted_at": int(d.accepted_at or 0),
        "starts_at": int(d.starts_at or 0),
        "expires_at": int(d.expires_at or 0),
        "revoked_at": int(d.revoked_at or 0),
        "revoked_by": d.revoked_by or "",
    }


def _is_expired(d: Delegation) -> bool:
    exp = int(d.expires_at or 0)
    if exp and _now_ts() > exp:
        if d.status in (STATUS_ACTIVE, STATUS_PENDING):
            d.status = STATUS_EXPIRED
        return True
    return d.status == STATUS_EXPIRED


def _is_active(d: Delegation) -> bool:
    if d.status != STATUS_ACTIVE or _is_expired(d):
        return False
    return int(d.starts_at or 0) <= _now_ts()


def _delegations_by(field: str, principal: str):
    """Yield every Delegation whose indexed ``field`` equals ``principal``, a page at a time."""
    cursor = 1
    while cursor is not None:
        page, cursor = Delegation.find_by(field, principal, from_id=cursor, count=_PAGE)
        yield from page


def _open_delegation(grantor: str, delegate: str) -> Delegation | None:
    """The pair's grant that is pending, active or not yet started, if any."""
    for d in _delegations_by("delegate", delegate):
        if (d.grantor or "").strip() != grantor:
            continue
        if d.status in (STATUS_ACTIVE, STATUS_PENDING) and not _is_expired(d):
            return d
    return None


def find_active_delegation(grantor: str, delegate: str) -> Delegation | None:
    d = _open_delegation(grantor, delegate)
    return d if d and _is_active(d) else None


def delegable_operations_for(principal: str) -> list[dict[str, str]]:
    """Operations ``principal`` holds in this realm and may delegate."""
    user = User[principal]
    if not user:
        return []
    return [
        {"operation": op, **info}
        for op, info in DELEGABLE_OPERATIONS.items()
        if _user_has_operation(user, op)
    ]


@dataclass
class ActingContext:
    actor: str
    subject: str
    subject_user: User
    delegation_id: str | None
    is_delegated: bool


def resolve_acting_context(args: dict | None, operation: str) -> ActingContext:
    """Resolve effective subject for a call; enforce direct or delegated access."""
    caller = ic.caller().to_str()
    grantor = ((args or {}).get("on_behalf_of") or "").strip()

    if not grantor or grantor == caller:
        if not _check_access(caller, operation):
            raise AccessDenied(
                f"Access denied: user {caller} lacks permission '{operation}'"
            )
        user = User[caller]
        if not user:
            raise AccessDenied(f"User {caller} not registered in this realm")
        return ActingContext(
            actor=caller,
            subject=caller,
            subject_user=user,
            delegation_id=None,
            is_delegated=False,
        )

    delegation = find_active_delegation(grantor, caller)
    if not delegation:
        raise AccessDenied(f"No active delegation from {grantor} to {caller}")
    scope = _parse_scope(delegation.scope_json)
    if not scope_allows(scope, operation):
        raise AccessDenied(
            f"Delegation {delegation.id} does not include permission '{operation}'"
        )
    grantor_user = User[grantor]
    if not grantor_user:
        raise AccessDenied(f"Grantor user {grantor} not found")
    if not _user_has_operation(grantor_user, operation) and not scope.get("all"):
        raise AccessDenied(
            f"Grantor {grantor} no longer holds permission '{operation}'"
        )

    return ActingContext(
        actor=caller,
        subject=grantor,
        subject_user=grantor_user,
        delegation_id=delegation.id,
        is_delegated=True,
    )


def grant_delegation(
    grantor: str,
    delegate: str,
    scope: dict[str, Any],
    *,
    label: str = "",
    description: str = "",
    starts_at: int = 0,
    expires_at: int | None = None,
    expires_in_hours: int = 168,
    requires_acceptance: bool = True,
    granted_by: str | None = None,
) -> dict[str, Any]:
    """Create a grant. ``expires_at`` (epoch seconds) wins over ``expires_in_hours``.

    A delegate that is not a realm member (e.g. an AI connection) has no inbox to
    accept from, so its grant is active from ``starts_at`` without acceptance.
    """
    grantor = (grantor or "").strip()
    delegate = (delegate or "").strip()
    if not grantor or not delegate:
        return {"success": False, "error": "grantor and delegate are required"}
    if grantor == delegate:
        return {"success": False, "error": "grantor and delegate must differ"}
    try:
        from _cdk import Principal

        Principal.from_str(delegate)
    except Exception:
        return {"success": False, "error": f"'{delegate}' is not a valid principal"}

    grantor_user = User[grantor]
    if not grantor_user:
        return {"success": False, "error": f"Grantor user {grantor} not found"}
    delegate_user = User[delegate]
    if not delegate_user:
        requires_acceptance = False

    err = _validate_scope_for_grantor(grantor_user, scope)
    if err:
        return {"success": False, "error": err}

    now = _now_ts()
    starts_at = max(int(starts_at or 0), 0)
    if expires_at is None:
        expires_at = (starts_at or now) + int(expires_in_hours) * 3600 if expires_in_hours else 0
    expires_at = int(expires_at or 0)
    if not expires_at:
        return {"success": False, "error": "An expiry is required"}
    if expires_at <= max(starts_at, now):
        return {"success": False, "error": "The expiry must be after the start and in the future"}
    if expires_at - max(starts_at, now) > MAX_DELEGATION_SECONDS:
        return {"success": False, "error": "A delegation can last at most one year"}

    existing = _open_delegation(grantor, delegate)
    if existing:
        return {
            "success": False,
            "error": f"A delegation to this principal already exists ({existing.id}); revoke it first",
        }

    delegation_id = secrets.token_hex(16)
    status = STATUS_PENDING if requires_acceptance else STATUS_ACTIVE

    d = Delegation(
        id=delegation_id,
        grantor=grantor,
        delegate=delegate,
        scope_json=json.dumps(scope),
        status=status,
        label=(label or "").strip()[:256] or f"{grantor[:8]}… → {delegate[:8]}…",
        description=(description or "").strip()[:2048],
        requires_acceptance=1 if requires_acceptance else 0,
        granted_by=(granted_by or ic.caller().to_str()),
        accepted_at=now if not requires_acceptance else 0,
        starts_at=starts_at,
        expires_at=expires_at,
    )

    if requires_acceptance:
        _notify_delegate_pending(d)

    logger.info(
        f"Delegation {delegation_id} created: {grantor} -> {delegate} status={status}"
    )
    return {"success": True, "data": _delegation_to_dict(d)}


def accept_delegation(delegation_id: str) -> dict[str, Any]:
    caller = ic.caller().to_str()
    d = Delegation[delegation_id]
    if not d:
        return {"success": False, "error": "Delegation not found"}
    if d.delegate != caller:
        return {
            "success": False,
            "error": "Only the delegate may accept this delegation",
        }
    if d.status != STATUS_PENDING:
        return {
            "success": False,
            "error": f"Delegation is not pending (status={d.status})",
        }
    d.status = STATUS_ACTIVE
    d.accepted_at = _now_ts()
    return {"success": True, "data": _delegation_to_dict(d)}


def revoke_delegation(delegation_id: str) -> dict[str, Any]:
    caller = ic.caller().to_str()
    d = Delegation[delegation_id]
    if not d:
        return {"success": False, "error": "Delegation not found"}
    if d.status in (STATUS_REVOKED, STATUS_EXPIRED):
        return {
            "success": True,
            "data": _delegation_to_dict(d),
            "message": "Already inactive",
        }

    allowed = (
        caller == d.grantor
        or caller == d.delegate
        or _check_access(caller, Operations.REALM_ADMIN)
    )
    if not allowed:
        return {"success": False, "error": "Not authorized to revoke this delegation"}

    d.status = STATUS_REVOKED
    d.revoked_at = _now_ts()
    d.revoked_by = caller
    return {"success": True, "data": _delegation_to_dict(d)}


def _page_for(field: str, principal: str, cursor: int, count: int):
    page, nxt = Delegation.find_by(field, principal, from_id=max(int(cursor or 1), 1), count=count)
    records = []
    for d in page:
        _is_expired(d)
        records.append(_delegation_to_dict(d))
    return records, nxt


def list_delegations_for_caller(
    grantor_cursor: int = 1, delegate_cursor: int = 1, count: int = _PAGE
) -> dict[str, Any]:
    """One page of the caller's grants on each side; pass ``next_*`` back to continue."""
    caller = ic.caller().to_str()
    count = min(max(int(count or _PAGE), 1), 100)
    as_grantor, next_grantor = ([], None)
    as_delegate, next_delegate = ([], None)
    if grantor_cursor:
        as_grantor, next_grantor = _page_for("grantor", caller, grantor_cursor, count)
    if delegate_cursor:
        as_delegate, next_delegate = _page_for("delegate", caller, delegate_cursor, count)
    return {
        "success": True,
        "data": {
            "as_grantor": as_grantor,
            "as_delegate": as_delegate,
            "pending_inbox": [r for r in as_delegate if r["status"] == STATUS_PENDING],
            "next_grantor_cursor": next_grantor,
            "next_delegate_cursor": next_delegate,
        },
    }


def _notify_delegate_pending(d: Delegation) -> None:
    delegate_user = User[d.delegate]
    if not delegate_user:
        return
    Notification(
        topic="delegation",
        title="Power of Attorney granted",
        message=f"You were granted delegation from {d.grantor[:12]}… "
        f"({d.label or 'scoped access'}). Review and accept to act on their behalf.",
        sender=d.grantor,
        recipient=d.delegate,
        visibility="private",
        audience_type="user",
        user=delegate_user,
        read=False,
        read_by="",
        icon="user-shield",
        href=f"/delegations?accept={d.id}",
        color="blue",
        metadata=json.dumps({"delegation_id": d.id}),
    )
