"""The IT admin team (docs/codices/layered-codex.md §10): a department the realm admins designate with ``it_team_set``.
Its seats and its policy are whatever the admins set; this module only says which department it is, and which calls
would change it or its seat holders, for the realm to refuse when a codex makes them (§10.3)."""

from core.db import Fail, Forbidden, each

# The permission the team's seat holders hold, to start ``codex_revert_fast`` (the department's own policy decides it).
# Only the seats of the team give it: no profile may list it.
PERMISSION = "codex.revert_fast"
# The operations that change a department, by what each of them names of it.
BY_DEPARTMENT = ("department_edit", "department_delete", "department_set_policy", "department_expel", "position_create")
BY_POSITION = ("position_edit", "position_close", "appoint", "appoint_acting", "invite_create")
BY_APPOINTMENT = ("appointment_end",)
# The operations that take something from a member, by the ``user`` they name: their status, their roles, their account.
BY_USER = ("user_set_status", "user_remove", "role_revoke")
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


def refuse_grant(permissions):
    """Only the seats of the team hold ``PERMISSION``: Fail when a profile would list it."""
    if PERMISSION in (permissions or []):
        raise Fail(f"{PERMISSION} is held by the seats of the IT admin team: no profile lists it")


def _row(cls, value):
    """The row of ``cls`` that ``value`` names, found as the operations find it (``core.db.find``): by the key they are
    given, the internal id before the alias, and an integer as its text. None for none."""
    return cls[str(value)] if value not in (None, "") else None


def _seat_of(position, team):
    """Whether ``position`` is a seat of the department ``team``."""
    return bool(position and position.department and position.department.id == team)


def _holds_seat(user, team):
    """Whether ``user`` holds an active seat in the department ``team``."""
    from ggg import Appointment

    return any(a.status == "active" and _seat_of(a.position, team) for a in each(Appointment, "user", user))


def _held_by_team(profile, team):
    """Whether ``profile`` is one that a seat of the team gives, or that a holder of such a seat has."""
    from ggg import Department, User

    department = Department[team]
    for position in department.positions if department else []:
        if position.profile and position.profile.id == profile:
            return True
        for appointment in position.appointments:
            holder = User[appointment.user] if appointment.status == "active" else None
            if holder and any(p.id == profile for p in holder.profiles):
                return True
    return False


def _removes_from(profile, args):
    """Whether the ``profile_edit`` with ``args`` takes a permission from ``profile``: it replaces ``allowed_to`` by a
    list that lacks one the profile has."""
    given = args.get("allowed_to")
    return "allowed_to" in args and (not isinstance(given, list) or not set(profile.allowed_to or []) <= set(map(str, given)))


def _changes(name, args, team):
    """Whether the call ``name`` with ``args`` designates the team, or changes its department (its policy, its seats, who
    holds them, an invite to one of them, its place in the realm, its existence), or takes something from a member who
    holds a seat in it (their status, their roles, a permission of a profile they have, the authority the team was
    given)."""
    from ggg import Appointment, Authority, Department, Member, Position, User, UserProfile

    if name == "it_team_set":
        return True
    if not team or not isinstance(args, dict):
        return False
    if name in BY_DEPARTMENT:
        named = _row(Department, args.get("department"))
        return named is not None and named.id == team
    if name in BY_POSITION:
        return _seat_of(_row(Position, args.get("position")), team)
    if name in BY_APPOINTMENT:
        appointment = _row(Appointment, args.get("appointment"))
        return bool(appointment) and _seat_of(appointment.position, team)
    if name in BY_USER:
        if name == "user_set_status" and args.get("status") == "active":
            return False
        # Each operation finds its user in its own class, and a member and a user need not share an internal id.
        member = _row(Member if name == "user_set_status" else User, args.get("user"))
        return member is not None and _holds_seat(member.id, team)
    if name == "profile_edit":
        profile = _row(UserProfile, args.get("profile"))
        return profile is not None and _held_by_team(profile.id, team) and _removes_from(profile, args)
    if name == "authority_revoke":
        authority = _row(Authority, args.get("authority"))
        return authority is not None and authority.target == f"Department:{team}"
    return False


def _calls(name, args):
    """The call, and every call it carries to a vote, however deep."""
    yield name, args
    action = args.get("action") if name in CARRIERS and isinstance(args, dict) else None
    if isinstance(action, dict):
        yield from _calls(action.get("op"), action.get("args") or {})


def refuse_change(name, args):
    """A codex never changes the team or who defines it (§10.3): Forbidden for its call ``name`` with ``args`` when it
    changes the team, or carries such a call to a vote."""
    team = department_id()
    for called, with_args in _calls(name, args):
        if _changes(called, with_args, team):
            raise Forbidden(f"{called} changes the IT admin team: no codex may")
