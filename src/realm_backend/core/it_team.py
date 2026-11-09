"""The IT admin team (docs/codices/layered-codex.md §10): a department the realm admins designate with ``it_team_set``.
Its seats and its policy are whatever the admins set; this module only says which department it is, and which calls
would change it, for the realm to refuse when a codex makes them (§10.3)."""

from core.db import Forbidden

# The permission the team's seat holders hold, to start ``codex_revert_fast`` (the department's own policy decides it).
PERMISSION = "codex.revert_fast"
# The operations that change a department, by what each of them names of it.
BY_DEPARTMENT = ("department_edit", "department_delete", "department_set_policy", "department_expel", "position_create")
BY_POSITION = ("position_edit", "position_close", "appoint", "appoint_acting")
BY_APPOINTMENT = ("appointment_end",)
# The operations that carry a call to a vote, by ``action``.
CARRIERS = ("proposal_create", "federal_vote_propose")


def department_id():
    """The id of the designated department, "" for none."""
    from ggg import Realm

    realm = Realm["1"]
    return realm.it_team if realm else ""


def department():
    """The designated department, or None: none designated, or it no longer exists."""
    from ggg import Department

    team = department_id()
    return Department[team] if team else None


def _about(name, args):
    """The id of the department that the call ``name`` changes, or None when it changes none."""
    from ggg import Appointment, Position

    if name in BY_DEPARTMENT:
        return args.get("department")
    if name in BY_POSITION:
        position = Position[str(args.get("position"))]
        return position.department.id if position and position.department else None
    if name in BY_APPOINTMENT:
        appointment = Appointment[str(args.get("appointment"))]
        return appointment.position.department.id if appointment and appointment.position.department else None
    return None


def _changes(name, args, team):
    """Whether the call ``name`` with ``args`` designates the team or changes the department ``team``."""
    return name == "it_team_set" or (bool(team) and isinstance(args, dict) and _about(name, args) == team)


def refuse_change(name, args):
    """A codex never changes the team or who defines it (§10.3): Forbidden for its call ``name`` with ``args`` when it
    designates the team or changes its department, or carries such a call to a vote."""
    team = department_id()
    calls = [(name, args)]
    if name in CARRIERS and isinstance(args.get("action"), dict):
        calls.append((args["action"].get("op"), args["action"].get("args") or {}))
    for called, with_args in calls:
        if _changes(called, with_args, team):
            raise Forbidden(f"{called} changes the IT admin team: no codex may")
