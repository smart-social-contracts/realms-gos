"""Inherit capital seat-holders as acting officers on a new quarter (issue #301).

Copy is creation-time only. A later local substantive appointment ends the
acting term (see ``ggg.appoint``). Re-applying is idempotent and never
overwrites a substantive holder.
"""

from __future__ import annotations

from typing import List

from ic_python_logging import get_logger

logger = get_logger("core.acting_appointments")


def dump_position_holders(canister_id: str = "") -> dict:
    """Serialize local seats + active holders for a peer quarter to consume."""
    from ggg import AppointmentKind, Position, appointment_kind

    positions = []
    for pos in Position.instances():
        key = getattr(pos, "key", "") or ""
        if not key:
            continue
        holders = []
        for a in pos.active_appointments():
            user = a.user
            if user is None:
                continue
            holders.append(
                {
                    "principal": getattr(user, "id", "") or "",
                    "kind": appointment_kind(a),
                }
            )
        inherit = bool(getattr(pos, "inherit_from_capital", True))
        positions.append(
            {
                "key": key,
                "inherit_from_capital": inherit,
                "holders": [h for h in holders if h["principal"]],
            }
        )
    return {
        "success": True,
        "canister_id": canister_id,
        "positions": positions,
    }


def _has_substantive(position) -> bool:
    from ggg import AppointmentKind, appointment_kind

    for a in position.active_appointments():
        if appointment_kind(a) == AppointmentKind.SUBSTANTIVE:
            return True
    return False


def apply_inherited_holders(payload: dict, capital_id: str) -> dict:
    """Create acting appointments from a capital ``list_position_holders`` payload.

    - Skips seats with ``inherit_from_capital`` false on *either* side.
    - Skips seats that already have a local substantive holder.
    - Registers missing users with the seat profile (idempotent).
    - ``appoint(kind=acting)`` is idempotent per user+seat.
    """
    from ggg import AppointmentKind, Position, User, appoint
    from ggg.system.user import user_register as register_user

    capital_id = (capital_id or payload.get("canister_id") or "").strip()
    created = 0
    skipped = 0
    errors: List[str] = []

    for spec in payload.get("positions") or []:
        key = (spec.get("key") or "").strip()
        if not key:
            continue
        if spec.get("inherit_from_capital") is False:
            skipped += 1
            continue
        pos = Position[key]
        if pos is None:
            skipped += 1
            continue
        if not bool(getattr(pos, "inherit_from_capital", True)):
            skipped += 1
            continue
        if _has_substantive(pos):
            skipped += 1
            continue

        profile = getattr(pos, "profile", None)
        profile_name = getattr(profile, "name", "") or "member"
        dept = getattr(pos, "department", None)

        for holder in spec.get("holders") or []:
            principal = (holder.get("principal") or "").strip()
            if not principal:
                continue
            try:
                register_user(principal, profile_name)
                user = User[principal]
                if user is None:
                    errors.append(f"{key}: register failed for {principal}")
                    continue
                if dept is not None:
                    from core.membership import add_department_member

                    add_department_member(dept, user)
                appointment = appoint(
                    pos,
                    user,
                    kind=AppointmentKind.ACTING,
                    source_canister_id=capital_id,
                    source_position_key=key,
                )
                if appointment is not None and appointment.is_acting():
                    created += 1
            except Exception as e:
                logger.error(f"inherit {key} / {principal}: {e}")
                errors.append(f"{key}: {principal}: {e}")

    return {
        "success": not errors,
        "created": created,
        "skipped": skipped,
        "errors": errors,
    }
