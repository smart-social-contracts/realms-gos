from core import chain
from api import members
from core.db import Conflict, Fail, Forbidden, NotFound, deref, each, find, next_id, page, ref, view
from ggg import Appointment, Authority, Department, Organization, Position, User, UserProfile
from core.op import op
from api.proposals import PENDING, holders, realm_wide, root

ORG_MOVES = {"active": {"suspended", "dissolved"}, "suspended": {"active", "dissolved"}, "dissolved": set()}
SEAT_FIELDS = ("title", "salary", "salary_period")


def body(position):
    return position.department or position.organization


def seated(position):
    return [a for a in position.appointments if a.status == "active"]


def human_seat(principal, owner, ignore=()):
    return any(a.status == "active" and not a.agent and a.id not in ignore and ref(body(a.position)) == ref(owner)
               for a in each(Appointment, "user", principal))


def supervised(owner, agent, supervisor):
    """I-ORG-3: an agent's supervisor holds a human seat in the same body."""
    if agent and not (supervisor and human_seat(supervisor, owner)):
        raise Fail(f"an agent needs a supervisor holding a non-agent seat in {owner.id}")


def fill(position, user, kind="substantive", agent=False, supervisor=""):
    held = seated(position)
    if position.status != "open":
        raise Conflict(f"{position.id} is {position.status}")
    if any(a.user == user for a in held):
        raise Conflict(f"{user} already holds {position.id}")
    if len(held) >= position.headcount:
        raise Conflict(f"{position.id} has no free seat ({position.headcount} filled)")
    supervised(body(position), agent, supervisor)
    a = Appointment(id=next_id(Appointment, "A"), position=position, user=user, kind=kind, agent=bool(agent),
                    supervisor=supervisor if agent else "", started_at=chain.now())
    return {"appointment": a.id}


def release(appointments):
    gone = {a.id for a in appointments}
    for a in appointments:
        owner = body(a.position)
        orphans = [x.user for pos in owner.positions for x in seated(pos)
                   if x.agent and x.supervisor == a.user and x.id not in gone]
        if a.status != "active":
            raise Conflict(f"{a.id} is not active")
        if orphans and not a.agent and not human_seat(a.user, owner, gone):
            raise Conflict(f"{a.user} supervises {', '.join(orphans)}; end or reassign them first")
    for a in appointments:
        a.status, a.ended_at = "ended", chain.now()


def dept(args):
    return find(Department, args["department"])


def parent_of(args):
    return find(Department, args["parent"]) if args.get("parent") else root()


def dept_seat(args):
    pos = find(Position, args["position"])
    if pos.department is None:
        raise Forbidden(f"{pos.id} is an organization seat; its admins manage it")
    return pos


def dept_appointment(args):
    a = find(Appointment, args["appointment"])
    if a.position.department is None:
        raise Forbidden(f"{a.id} is an organization seat; its admins manage it")
    return a


def profile_of(c, args):
    if not args.get("profile"):
        return None
    p = find(UserProfile, args["profile"])
    members.within(c, p.allowed_to or [])
    return p


@op("org.create", governed=parent_of, returns="department")
def department_create(c, args):
    """Create ``department`` (a new name) under ``parent`` (default the root), with an optional ``description``.
    Returns ``department``."""
    if Department[args["department"]]:
        raise Conflict(f"department {args['department']} exists")
    d = Department(id=args["department"], description=args.get("description", ""), parent=parent_of(args))
    return {"department": d.id}


@op("org.set_policy", governed=dept)
def department_edit(c, args):
    """Edit ``department``: ``description``, ``parent`` (never itself or one of its sub-departments; the root has none)
    and ``head`` (someone holding a seat in it, or "" for nobody); a field you leave out stays."""
    d = dept(args)
    parent = find(Department, args["parent"]) if args.get("parent") else None
    up = parent
    while up is not None:
        if up.id == d.id:
            raise Fail(f"{d.id} cannot sit under itself")
        up = up.parent
    if parent and d.is_root:
        raise Fail("the root department has no parent")
    if args.get("head") and args["head"] not in holders(d):
        raise Fail(f"{args['head']} holds no seat in {d.id}")
    d.description, d.head = args.get("description", d.description), args.get("head", d.head)
    if parent:
        d.parent = parent


@op("org.set_policy", governed=lambda a: dept(a).parent or root())
def department_delete(c, args):
    """Delete ``department``; it must not be the root and must have no sub-departments, only closed positions and no
    proposal still to be voted on or run (it would lose its department)."""
    d = dept(args)
    if d.is_root:
        raise Fail("the root department cannot be deleted")
    if list(d.children) or any(p.status != "closed" for p in d.positions):
        raise Conflict(f"{d.id} still has sub-departments or open positions")
    if any(p.status in PENDING for p in d.proposals):
        raise Conflict(f"{d.id} still has proposals to vote on or run")
    d.delete()


@op("org.set_policy", governed=dept)
def department_set_policy(c, args):
    """Set how ``department`` approves: ``m`` of ``n`` approvals (1 ≤ m ≤ n), ``quorum_percent`` (0 to 100, default 0)
    and ``veto``, principals holding a seat in it who can block (default none)."""
    d = dept(args)
    m, n, quorum, veto = int(args["m"]), int(args["n"]), int(args.get("quorum_percent", 0)), args.get("veto", [])
    if not 1 <= m <= n or not 0 <= quorum <= 100:
        raise Fail("a policy needs 1 <= m <= n and a quorum between 0 and 100")
    outsiders = sorted(set(veto) - holders(d))
    if outsiders:
        raise Fail(f"veto holders must hold a seat in {d.id}: {', '.join(outsiders)}")
    d.policy = {"m": m, "n": n, "quorum_percent": quorum, "veto": list(veto)}


def seat_view(pos):
    held = seated(pos)
    return {**view(pos, "id", "title", "headcount", "salary", "salary_period", "status"),
            "profile": pos.profile.id if pos.profile else "", "filled": len(held),
            "holders": [view(a, "id", "user", "kind", "agent", "supervisor") for a in held]}


def chart(d):
    seats = [seat_view(p) for p in d.positions]
    return {"department": d.id, "head": d.head, "policy": d.policy, "positions": seats,
            "seats": sum(s["headcount"] for s in seats), "filled": sum(s["filled"] for s in seats),
            "children": [chart(x) for x in d.children]}


@op("realm.data_view", read=True)
def department_get(c, args):
    """``department`` with its parent, head and policy, and its ``positions`` with their holders."""
    d = dept(args)
    return {"department": {**view(d, "id", "description", "is_root", "head", "policy"),
                           "parent": d.parent.id if d.parent else ""},
            "positions": [seat_view(p) for p in d.positions]}


@op("realm.data_view", read=True, returns="departments")
def department_list(c, args):
    """Departments, ``count`` rows at a time (default 100, at most 500) from row ``from`` (default 1).
    Returns ``departments`` and ``next``, the ``from`` of the next page (null at the end)."""
    rows, nxt = page(Department, args)
    return {"departments": [{**view(d, "id", "description", "is_root", "head"),
                             "parent": d.parent.id if d.parent else ""} for d in rows], "next": nxt}


@op("realm.data_view", read=True)
def department_chart(c, args):
    """The org chart below ``department`` (default the root): head, policy, positions and seats filled, then each
    sub-department the same way."""
    return {"chart": chart(dept(args) if args.get("department") else root())}


@op("org.manage_members", governed=dept, returns="position")
def position_create(c, args):
    """Open seat ``position`` (a new key) in ``department``: ``headcount`` (default 1), optional ``title``, ``salary``,
    ``salary_period`` and ``profile`` its holders get (one whose permissions you hold). Returns ``position``."""
    d = dept(args)
    if Position[args["position"]]:
        raise Conflict(f"position {args['position']} exists")
    if int(args.get("headcount", 1)) < 1:
        raise Fail("headcount must be at least 1")
    pos = Position(id=args["position"], department=d, profile=profile_of(c, args),
                   headcount=int(args.get("headcount", 1)), **{k: args[k] for k in SEAT_FIELDS if k in args})
    return {"position": pos.id}


@op("org.manage_members", governed=lambda a: dept_seat(a).department)
def position_edit(c, args):
    """Edit department seat ``position``: ``status`` open or frozen, ``headcount`` (not below its holders), ``profile``,
    ``title``, ``salary`` and ``salary_period``; a field you leave out stays. Closing is position_close."""
    pos = dept_seat(args)
    status, count = args.get("status", pos.status), int(args.get("headcount", pos.headcount))
    if pos.status == "closed" or status not in ("open", "frozen"):
        raise Fail(f"{pos.id} can only move between open and frozen; use position_close to close it")
    if count < max(1, len(seated(pos))):
        raise Conflict(f"{pos.id} has {len(seated(pos))} holders; headcount cannot go below that")
    if args.get("profile"):
        pos.profile = profile_of(c, args)
    pos.status, pos.headcount = status, count
    for k in SEAT_FIELDS:
        if k in args:
            setattr(pos, k, args[k])


@op("org.manage_members", governed=lambda a: dept_seat(a).department)
def position_close(c, args):
    """Close department seat ``position`` for good; it must have no holders."""
    pos = dept_seat(args)
    if pos.status == "closed" or seated(pos):
        raise Conflict(f"{pos.id} is closed already or still has holders")
    pos.status = "closed"


def appoint_as(args, kind):
    if not args.get("agent") and not User[args["user"]]:
        raise NotFound(f"{args['user']} is not a user of this realm")
    return fill(dept_seat(args), args["user"], kind, args.get("agent", False), args.get("supervisor", ""))


@op("org.appoint", governed=lambda a: dept_seat(a).department, returns="appointment")
def appoint(c, args):
    """Seat ``user`` (a user of the realm, unless an agent) in department seat ``position``; ``agent`` true (default
    false) needs a ``supervisor`` holding a non-agent seat in the same department. Returns ``appointment``."""
    return appoint_as(args, "substantive")


@op("org.appoint", governed=lambda a: dept_seat(a).department, returns="appointment")
def appoint_acting(c, args):
    """Like appoint, but as an acting holder: ``user`` in ``position``, with ``agent`` and ``supervisor`` as there.
    Returns ``appointment``."""
    return appoint_as(args, "acting")


@op("org.appoint", governed=lambda a: dept_appointment(a).position.department)
def appointment_end(c, args):
    """End ``appointment``, an active department seat; whoever supervises agents there must keep another non-agent
    seat in that department, or end or reassign those agents first."""
    release([dept_appointment(args)])


@op("org.expel", governed=dept, parties=lambda a: {a["user"]})
def department_expel(c, args):
    """End every seat ``user`` holds in ``department``, and remove them as its head if they are."""
    d = dept(args)
    seats = [a for pos in d.positions for a in seated(pos) if a.user == args["user"]]
    if not seats:
        raise Fail(f"{args['user']} holds no seat in {d.id}")
    release(seats)
    if d.head == args["user"]:
        d.head = ""


@op("org.grant_authority", governed=dept, returns="authority")
def authority_grant(c, args):
    """Let ``target`` ("Department:<id>" or "Quarter:<id>", not ``department`` itself) use ``operations``,
    permissions that ``department`` holds itself or through its positions' profiles. Returns ``authority``."""
    d, target, ops = dept(args), deref(args["target"]), args["operations"]
    if type(target).__name__ not in ("Department", "Quarter") or ref(target) == ref(d):
        raise Fail("authority goes to another department or a quarter")
    lacking = sorted(set(ops) - members.powers(d))
    if not ops or lacking:
        raise Forbidden(f"{d.id} cannot pass on what it does not hold: {', '.join(lacking) or 'nothing named'}")
    a = Authority(id=next_id(Authority, "AUTH"), grantor=d, target=ref(target), operations=ops)
    return {"authority": a.id}


@op("org.revoke_authority", governed=lambda a: find(Authority, a["authority"]).grantor)
def authority_revoke(c, args):
    """Withdraw ``authority``, a department's grant of its permissions to another department or a quarter."""
    find(Authority, args["authority"]).delete()


def admin_of(c, args):
    org = find(Organization, args["organization"])
    if c.user not in org.admins:
        raise Forbidden(f"only {org.id}'s admins can do this")
    if org.status != "active":
        raise Conflict(f"{org.id} is {org.status}")
    return org


@op("organization.add", returns="organization")
def organization_register(c, args):
    """Register ``organization`` (a new name) with an optional ``description``; you become its only admin.
    Returns ``organization``."""
    if Organization[args["organization"]]:
        raise Conflict(f"organization {args['organization']} exists")
    Organization(id=args["organization"], description=args.get("description", ""), admins=[c.user])
    return {"organization": args["organization"]}


@op("organization.edit", returns="organization")
def organization_edit(c, args):
    """As an admin of active ``organization``, change its ``description`` or replace its ``admins`` (at least one);
    a field you leave out stays."""
    org = admin_of(c, args)
    if "admins" in args and not args["admins"]:
        raise Conflict("an organization keeps at least one admin")
    org.description, org.admins = args.get("description", org.description), args.get("admins", org.admins)
    return {"organization": org.id}


@op("organization.edit", returns="appointment")
def representative_add(c, args):
    """As an admin of ``organization``, seat ``user`` in its seat ``position``, created if new with ``title``,
    ``profile`` and ``headcount`` (default 1); ``agent`` true needs a ``supervisor`` holding a non-agent seat there.
    Returns ``appointment``."""
    org, pos = admin_of(c, args), Position[args["position"]]
    if pos is not None and ref(pos.organization) != ref(org):
        raise Fail(f"{pos.id} is not a seat of {org.id}")
    if pos is None:
        supervised(org, args.get("agent"), args.get("supervisor", ""))
        pos = Position(id=args["position"], organization=org, profile=profile_of(c, args),
                       title=args.get("title", ""), headcount=max(1, int(args.get("headcount", 1))))
    return fill(pos, args["user"], "substantive", args.get("agent", False), args.get("supervisor", ""))


@op("organization.edit", returns="appointment")
def representative_remove(c, args):
    """As an admin of ``organization``, end ``appointment``, one of its active seats; whoever supervises agents there
    must keep another non-agent seat in it, or end or reassign those agents first."""
    org, a = admin_of(c, args), find(Appointment, args["appointment"])
    if ref(a.position.organization) != ref(org):
        raise Fail(f"{a.id} is not a seat of {org.id}")
    release([a])
    return {"appointment": a.id}


@op("organization.delete", governed=realm_wide)
def organization_set_status(c, args):
    """Set ``organization``'s ``status``: active, suspended, or dissolved, which is final and ends all its seats.
    Returns ``status``."""
    org = find(Organization, args["organization"])
    if args["status"] not in ORG_MOVES[org.status]:
        raise Conflict(f"a {org.status} organization cannot become {args['status']}")
    if args["status"] == "dissolved":
        release([a for pos in org.positions for a in seated(pos)])
    org.status = args["status"]
    return {"status": org.status}


@op("realm.data_view", read=True)
def organization_get(c, args):
    """``organization`` with its status and admins, and its ``positions`` with their holders."""
    org = find(Organization, args["organization"])
    return {"organization": view(org, "id", "description", "status", "admins"),
            "positions": [seat_view(p) for p in org.positions]}


@op("realm.data_view", read=True, returns="organizations")
def organization_list(c, args):
    """Organizations, ``count`` rows at a time (default 100, at most 500) from row ``from``, optionally only those with
    ``status``. Returns ``organizations`` and ``next``; a filtered page can be short or empty, so follow ``next``."""
    rows, nxt = page(Organization, args, lambda o: not args.get("status") or o.status == args["status"])
    return {"organizations": [view(o, "id", "description", "status") for o in rows], "next": nxt}
