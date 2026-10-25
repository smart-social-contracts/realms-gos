"""Position lifecycle administration with org-policy gating (issue #241).

Single executor for all Position mutations. Two entry paths, one code path:

- **Direct** (department policy is 1/1, no quorum): the access_manager
  endpoint calls :func:`apply_position_action` immediately.
- **Governed** (any other policy): the endpoint submits a department-scoped
  Proposal whose inline code calls the *same* :func:`apply_position_action`.
  The voting extension tallies it against the department's M/N/quorum/veto
  policy and executes the code on acceptance.

Because both paths converge on one function, an approved vote can never do
something a direct admin edit could not, and vice versa.
"""

import json

from ic_python_logging import get_logger

logger = get_logger("core.position_admin")

# Staff invite defaults for newly created positions (mirror codex seeding).
_INVITE_EXPIRES_HOURS = 720
_INVITE_MAX_USES = 100

ACTIONS = ("create", "update", "close", "reopen", "appoint", "end_appointment")


def policy_is_direct(dept) -> bool:
    """True when the org's policy lets a single manager act without a vote.

    Only the trivial 1/1 policy with no quorum applies directly; anything
    else (M-of-N, quorum, veto principals) requires a proposal + vote.
    """
    m = int(getattr(dept, "policy_threshold_m", 1) or 1)
    n = int(getattr(dept, "policy_threshold_n", 1) or 1)
    q = int(getattr(dept, "policy_quorum_percent", 0) or 0)
    veto = (getattr(dept, "policy_veto_principals", "") or "").strip()
    return m <= 1 and n <= 1 and q == 0 and not veto


def describe_action(action: dict) -> str:
    """One-line human summary for proposal titles and audit logs."""
    kind = action.get("action", "?")
    dept = action.get("department", "?")
    title = action.get("title", "") or (action.get("key", "").split("/")[-1] if action.get("key") else "?")
    if kind == "create":
        return f"Create position '{title}' in {dept}"
    if kind == "update":
        changes = ", ".join(sorted(k for k in action if k in _UPDATABLE_FIELDS))
        return f"Update position '{action.get('key', title)}' ({changes or 'no-op'})"
    if kind == "close":
        return f"Close position '{action.get('key', title)}'"
    if kind == "reopen":
        return f"Reopen position '{action.get('key', title)}'"
    if kind == "appoint":
        return f"Appoint {action.get('principal', '?')} to '{action.get('key', title)}'"
    if kind == "end_appointment":
        return f"End appointment of {action.get('principal', '?')} on '{action.get('key', title)}'"
    return f"Position action '{kind}'"


_UPDATABLE_FIELDS = (
    "new_title",
    "description",
    "profile",
    "headcount",
    "salary_amount",
    "salary_period",
)


def apply_position_action(action: dict) -> dict:
    """Validate and apply one position mutation. Returns {success, data|error}.

    Action shapes:
      create:  {action, department, title, profile, headcount?, salary_amount?,
                salary_period?, description?}
      update:  {action, key, new_title?, description?, profile?, headcount?,
                salary_amount?, salary_period?}
      close / reopen: {action, key}
      appoint: {action, key, principal}
      end_appointment: {action, key, principal}
    """
    try:
        from ggg import (
            Department,
            Position,
            PositionStatus,
            RegistrationCode,
            User,
            UserProfile,
            appoint,
        )

        kind = (action.get("action") or "").strip()
        if kind not in ACTIONS:
            return {"success": False, "error": f"Unknown action '{kind}'"}

        # ------------------------------------------------------------------
        if kind == "create":
            dept_name = (action.get("department") or "").strip()
            title = (action.get("title") or "").strip()
            profile_name = (action.get("profile") or title).strip()
            if not dept_name or not title:
                return {"success": False, "error": "department and title are required"}

            dept = Department[dept_name]
            if not dept:
                return {"success": False, "error": f"Department '{dept_name}' not found"}

            key = f"{dept_name}/{title}"
            if Position[key]:
                return {"success": False, "error": f"Position '{key}' already exists"}

            profile = UserProfile[profile_name]
            if not profile:
                return {"success": False, "error": f"Profile '{profile_name}' not found — create it first"}

            pos = Position(
                key=key,
                title=title,
                description=action.get("description") or f"{title} at {dept_name}",
                department=dept,
                profile=profile,
                headcount=max(1, int(action.get("headcount", 1) or 1)),
                salary_amount=max(0, int(action.get("salary_amount", 0) or 0)),
                salary_period=action.get("salary_period") or "monthly",
                status=PositionStatus.OPEN,
            )

            # Staff invite so the seat can be filled by URL (mirrors seeding).
            invite_url = ""
            try:
                from ggg import Realm

                realms = Realm.instances()
                fid = (getattr(realms[0], "frontend_canister_id", "") or "").strip() if realms else ""
                frontend_url = f"https://{fid}.icp0.io" if fid else ""
                code = RegistrationCode.create(
                    user_id="",
                    created_by="position_admin",
                    frontend_url=frontend_url,
                    expires_in_hours=_INVITE_EXPIRES_HOURS,
                    profile=profile_name,
                    max_uses=_INVITE_MAX_USES,
                    department=dept_name,
                    position=key,
                )
                invite_url = (
                    f"{frontend_url}/join?invite={code.code}"
                    if code.code and frontend_url
                    else ""
                )
            except Exception as e:
                logger.warning(f"Invite creation for new position '{key}' failed: {e}")

            logger.info(f"Position created: {key}")
            return {"success": True, "data": {"key": pos.key, "invite_url": invite_url}}

        # ------------------------------------------------------------------
        key = (action.get("key") or "").strip()
        if not key:
            return {"success": False, "error": "key is required"}
        pos = Position[key]
        if not pos:
            return {"success": False, "error": f"Position '{key}' not found"}

        if kind == "update":
            # The key is a stable identifier (alias index cannot drop old
            # entries); renames change only the display title.
            new_title = (action.get("new_title") or "").strip()
            if new_title:
                pos.title = new_title

            if "description" in action:
                pos.description = str(action["description"] or "")
            if "profile" in action:
                profile_name = (action.get("profile") or "").strip()
                profile = UserProfile[profile_name]
                if not profile:
                    return {"success": False, "error": f"Profile '{profile_name}' not found"}
                pos.profile = profile
            if "headcount" in action:
                pos.headcount = max(1, int(action["headcount"] or 1))
            if "salary_amount" in action:
                pos.salary_amount = max(0, int(action["salary_amount"] or 0))
            if "salary_period" in action:
                pos.salary_period = str(action["salary_period"] or "monthly")

            logger.info(f"Position updated: {key}")
            return {"success": True, "data": {"key": pos.key}}

        if kind == "close":
            pos.status = PositionStatus.CLOSED
            logger.info(f"Position closed: {key}")
            return {"success": True, "data": {"key": key, "status": pos.status}}

        if kind == "reopen":
            pos.status = PositionStatus.OPEN
            logger.info(f"Position reopened: {key}")
            return {"success": True, "data": {"key": key, "status": pos.status}}

        if kind == "appoint":
            principal = (action.get("principal") or "").strip()
            if not principal:
                return {"success": False, "error": "principal is required"}
            user = User[principal]
            if not user:
                return {"success": False, "error": f"User '{principal}' not found"}

            dept = pos.department
            if dept is not None:
                from core.membership import add_department_member

                add_department_member(dept, user)

            profile = pos.profile
            if profile is not None:
                profile_name = getattr(profile, "name", None) or ""
                if profile_name:
                    current: list[str] = []
                    try:
                        for p in user.profiles:
                            current.append(getattr(p, "name", None) or "")
                    except Exception:
                        pass
                    if profile_name not in current:
                        try:
                            user.profiles.add(profile)
                        except Exception as prof_err:
                            logger.warning(
                                f"Could not assign profile '{profile_name}' to {principal}: {prof_err}"
                            )

            from ggg import AppointmentKind

            appointment = appoint(pos, user, kind=AppointmentKind.SUBSTANTIVE)
            if appointment is None:
                if (pos.status or PositionStatus.OPEN) != PositionStatus.OPEN:
                    return {"success": False, "error": f"Position '{key}' is not open"}
                if pos.vacancies() <= 0:
                    return {
                        "success": False,
                        "error": (
                            f"Position '{key}' is full "
                            f"({pos.filled_count()}/{pos.headcount})"
                        ),
                    }
                return {"success": False, "error": f"Could not appoint to '{key}'"}

            logger.info(f"User {principal} appointed to {key}")
            return {"success": True, "data": {"key": key, "principal": principal}}

        if kind == "end_appointment":
            principal = (action.get("principal") or "").strip()
            if not principal:
                return {"success": False, "error": "principal is required"}
            for a in pos.active_appointments():
                holder = a.user
                if holder is not None and getattr(holder, "id", None) == principal:
                    a.end()
                    logger.info(f"Appointment ended: {principal} on {key}")
                    return {"success": True, "data": {"key": key, "principal": principal}}
            return {"success": False, "error": f"{principal} holds no active appointment on '{key}'"}

        return {"success": False, "error": f"Unhandled action '{kind}'"}
    except Exception as e:
        logger.error(f"apply_position_action failed: {e}")
        return {"success": False, "error": str(e)}


def build_proposal_code(action: dict) -> str:
    """Inline proposal code that replays *action* through the same executor.

    Executed by the voting extension after acceptance; raising on failure
    marks the proposal 'failed' instead of silently succeeding.
    """
    payload = json.dumps(action, sort_keys=True)
    return (
        "import json\n"
        "from core.position_admin import apply_position_action\n"
        "\n"
        "def main():\n"
        f"    action = json.loads({payload!r})\n"
        "    result = apply_position_action(action)\n"
        "    if not result.get('success'):\n"
        "        raise RuntimeError(f\"Position action failed: {result.get('error')}\")\n"
        "    return result\n"
    )
