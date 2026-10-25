"""Register a principal as an admin User and seat them in the root org.

Used by installer ``register_founder`` (controller) and by ``register_co_admin``
(existing admin, e.g. a dfx founder adding a browser II principal).
"""

from __future__ import annotations

from typing import Any

from ic_python_logging import get_logger

logger = get_logger("core.admin_users")

ANONYMOUS_PRINCIPAL = "2vxsx-fae"


def register_admin_user(principal: str, *, seat_as_root_head: bool) -> dict[str, Any]:
    """Create or upgrade ``User[principal]`` with the admin profile.

    Idempotent. Always adds the user as a root-org member. Only sets
    ``root.head`` when ``seat_as_root_head`` is true and the head is empty —
    a co-admin must not steal the founder's head seat.
    """
    from api.user import user_get_record_fields, user_register

    founder = (principal or "").strip()
    if not founder or founder == ANONYMOUS_PRINCIPAL:
        return {
            "success": False,
            "error": "A non-anonymous principal is required",
        }

    user = user_get_record_fields(user_register(founder, "admin"))
    logger.info(f"Admin {founder} registered with admin profile")

    try:
        from core.membership import add_department_member, user_in_department
        from core.org_policy import ensure_root_org, grant_root_authority_over_local_orgs
        from ggg import User as _GGGUser

        root = ensure_root_org()
        grant_root_authority_over_local_orgs()
        founder_user = _GGGUser[founder]
        if founder_user:
            if seat_as_root_head and not root.head:
                root.head = founder_user
            if not user_in_department(founder_user, root):
                add_department_member(root, founder_user)
            logger.info(
                f"Admin {founder} seated in root org "
                f"(head={bool(seat_as_root_head and root.head == founder_user)})"
            )
    except Exception as root_err:
        logger.warning(f"Could not seat admin {founder} in root org: {root_err}")

    return {"success": True, "user": user}
