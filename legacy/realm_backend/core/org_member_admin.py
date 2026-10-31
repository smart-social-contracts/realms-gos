"""Department membership administration with org-policy gating (issue #241).

Mirrors :mod:`core.position_admin`: direct apply when policy is 1/1, otherwise
the access_manager endpoint submits a department-scoped Proposal whose inline
code calls the same :func:`apply_member_action` after the vote passes.
"""

import json

from core.extension_errors import not_found_payload, validation_payload
from ic_python_logging import get_logger

logger = get_logger("core.org_member_admin")

ACTIONS = ("add", "remove")


def describe_action(action: dict) -> str:
    """One-line human summary for proposal titles and audit logs."""
    kind = (action.get("action") or "?").strip()
    dept = (action.get("department") or "?").strip()
    principal = (action.get("user_principal") or action.get("principal") or "?").strip()
    if kind == "add":
        return f"Add {principal} to department '{dept}'"
    if kind == "remove":
        return f"Remove {principal} from department '{dept}'"
    return f"Member action '{kind}' on '{dept}'"


def apply_member_action(action: dict) -> dict:
    """Validate and apply one membership mutation.

    Action shapes:
      add:    {action, department, user_principal}
      remove: {action, department, user_principal}
    """
    try:
        from ggg import Department, User

        kind = (action.get("action") or "").strip()
        if kind not in ACTIONS:
            return validation_payload(f"Unknown action '{kind}'")

        dept_name = (action.get("department") or "").strip()
        principal = (action.get("user_principal") or action.get("principal") or "").strip()
        if not dept_name or not principal:
            return validation_payload("department and user_principal are required")

        dept = Department[dept_name]
        if not dept:
            return not_found_payload(
                f"Department '{dept_name}' not found", entity="department"
            )

        user = User[principal]
        if not user:
            return not_found_payload(
                f"No realm member with principal '{principal}'. They must join the realm first.",
                entity="user",
            )

        from core.membership import (
            add_department_member,
            remove_department_member,
            user_in_department,
        )

        if kind == "add":
            if user_in_department(user, dept):
                return {
                    "success": True,
                    "data": {"message": f"User already in '{dept_name}'"},
                }
            add_department_member(dept, user)
            logger.info(f"User {principal} added to department '{dept_name}'")
            return {"success": True, "data": {"message": f"User added to '{dept_name}'"}}

        if kind == "remove":
            if not user_in_department(user, dept):
                return {
                    "success": True,
                    "data": {"message": f"User was not a member of '{dept_name}'"},
                }
            remove_department_member(dept, user)
            logger.info(f"User {principal} removed from department '{dept_name}'")
            return {"success": True, "data": {"message": f"User removed from '{dept_name}'"}}

        return validation_payload(f"Unhandled action '{kind}'")
    except Exception as e:
        logger.error(f"apply_member_action failed: {e}")
        return validation_payload(str(e))


def build_proposal_code(action: dict) -> str:
    """Inline proposal code that replays *action* through the same executor."""
    payload = json.dumps(action, sort_keys=True)
    return (
        "import json\n"
        "from core.org_member_admin import apply_member_action\n"
        "\n"
        "def main():\n"
        f"    action = json.loads({payload!r})\n"
        "    result = apply_member_action(action)\n"
        "    if not result.get('success'):\n"
        "        raise RuntimeError(f\"Member action failed: {result.get('error')}\")\n"
        "    return result\n"
    )
